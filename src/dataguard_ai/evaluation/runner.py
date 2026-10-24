"""Read committed ground truth and fresh predictions in one read-only snapshot."""

from collections import Counter

import psycopg
from psycopg import sql

from dataguard_ai.corruption.storage import content_digest, require_experiment_schema
from dataguard_ai.evaluation.metrics import evaluate_findings
from dataguard_ai.evaluation.policy import finding_signature
from dataguard_ai.ingestion.database import DatabaseConfig
from dataguard_ai.quality.engine import validate_snapshot


def evaluate_connection(
    connection: psycopg.Connection, *, schema: str = "experiment_pilot"
) -> dict:
    require_experiment_schema(schema)
    if not connection.autocommit:
        raise ValueError(
            "Evaluation requires an autocommit connection before its transaction"
        )
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '30s'")
        connection.execute("SET LOCAL lock_timeout = '3s'")
        runs = connection.execute(
            sql.SQL("SELECT experiment_id, details FROM {}").format(
                sql.Identifier(schema, "_run"),
            )
        ).fetchall()
        if len(runs) != 1:
            raise ValueError("Expected exactly one committed experiment run")
        experiment_id, stored = runs[0]
        if (
            stored["experiment_id"] != experiment_id
            or stored["experiment_schema"] != schema
        ):
            raise ValueError(
                "Stored run identity disagrees with the selected experiment"
            )
        manifest = [
            row[0]
            for row in connection.execute(
                sql.SQL("SELECT details FROM {} ORDER BY mutation_index").format(
                    sql.Identifier(schema, "_mutations")
                )
            ).fetchall()
        ]
        if manifest != stored["manifest"]:
            raise ValueError(
                "Database manifest disagrees with the committed run record"
            )
        if stored["config"]["mutation_budget"] != len(manifest):
            raise ValueError("Manifest event count disagrees with the recorded budget")
        if any(
            event.get("random_seed") != stored["config"]["seed"] for event in manifest
        ):
            raise ValueError("Manifest random seeds disagree with the recorded config")
        digest = content_digest(connection, schema)
        if digest != stored["corrupted_content_sha256"]:
            raise ValueError(
                "Experiment content changed since the manifest was committed"
            )
        live = validate_snapshot(connection, schema=schema)
        report = evaluate_findings(
            manifest,
            stored["pilot_baseline"]["findings"],
            live["findings"],
            schema=schema,
            experiment_id=experiment_id,
        )
        report["provenance"] = {
            "transaction_read_only": live["transaction_read_only"],
            "content_digest_verified": True,
            "corrupted_content_sha256": digest,
            "manifest_source": f"{schema}._mutations (cross-checked with _run)",
            "fresh_predictions_match_recorded": Counter(
                map(finding_signature, live["findings"])
            )
            == Counter(map(finding_signature, stored["pilot_after"]["findings"])),
            "original_full_baseline_context": stored["original_baseline"]["summary"],
            "config": stored["config"],
        }
        return report


def evaluate_database(config: DatabaseConfig) -> dict:
    with config.connect() as connection:
        return evaluate_connection(connection)
