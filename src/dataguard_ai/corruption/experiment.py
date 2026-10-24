"""One bounded, atomic experiment: copy, baseline, mutate, compare and persist."""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from dataguard_ai.corruption.planning import (
    ExperimentConfig,
    compare_findings,
    compare_runs,
    plan_mutations,
)
from dataguard_ai.corruption.storage import (
    apply_mutations,
    content_digest,
    copy_pilot,
    previous_run,
    require_experiment_schema,
    reset_owned_schema,
    source_checksums,
)
from dataguard_ai.ingestion.database import DatabaseConfig
from dataguard_ai.ingestion.tables import TABLE_FILES
from dataguard_ai.quality.engine import validate_snapshot
from dataguard_ai.quality.rules import PRIMARY_KEYS


def create_experiment(
    connection: psycopg.Connection,
    config: ExperimentConfig,
    *,
    schema: str = "experiment_pilot",
    source: str = "raw",
) -> dict[str, Any]:
    """Only experiment-owned objects are writable; raw/pg_temp are SELECT sources."""
    require_experiment_schema(schema)
    if source not in ("raw", "pg_temp") or not connection.autocommit:
        raise ValueError("Use raw/pg_temp source and an autocommit connection")
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        connection.execute("SET LOCAL statement_timeout = '30s'")
        connection.execute("SET LOCAL lock_timeout = '3s'")
        # Serializes reruns of this owned namespace, released on commit/rollback.
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (schema,)
        )
        source_tables = list(TABLE_FILES if source == "raw" else PRIMARY_KEYS)
        before_source = source_checksums(connection, source, source_tables)
        original_baseline = validate_snapshot(connection, schema=source)
        selected = connection.execute(
            sql.SQL(
                "SELECT order_id FROM {} WHERE order_id IS NOT NULL "
                "AND order_id !~ '^[[:space:]]*$' GROUP BY order_id "
                "HAVING COUNT(*) = 1 "
                'ORDER BY md5(order_id || %s) COLLATE "C", '
                'order_id COLLATE "C" LIMIT %s'
            ).format(sql.Identifier(source, "orders")),
            (str(config.seed), config.orders),
        ).fetchall()
        order_ids = [record[0] for record in selected]
        if len(order_ids) != config.orders:
            raise ValueError(
                "Not enough orders with unique, nonblank order IDs for this pilot"
            )
        previous = previous_run(connection, schema)
        columns = reset_owned_schema(connection, schema, source)
        copy_pilot(connection, schema, source, columns, order_ids)
        pilot_before_digest = content_digest(connection, schema)
        identity = json.dumps(
            {"config": config.as_dict(), "pilot_source": pilot_before_digest},
            sort_keys=True,
        )
        experiment_id = "pilot_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        baseline = validate_snapshot(connection, schema=schema)
        records = {}
        for table in ("order_items", "orders"):
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    sql.SQL("SELECT * FROM {} ORDER BY _experiment_row_id").format(
                        sql.Identifier(schema, table),
                    )
                )
                records[table] = cursor.fetchall()
        manifest = plan_mutations(records, baseline, experiment_id, config)
        sentinel = f"__missing_product_{experiment_id}"
        if connection.execute(
            sql.SQL("SELECT EXISTS(SELECT 1 FROM {} WHERE product_id = %s)").format(
                sql.Identifier(schema, "products"),
            ),
            (sentinel,),
        ).fetchone()[0]:
            raise ValueError("Broken-reference sentinel unexpectedly exists")
        apply_mutations(connection, schema, columns, manifest)
        after = validate_snapshot(connection, schema=schema)
        after_source = source_checksums(connection, source, source_tables)
        if before_source != after_source:
            raise ValueError(
                "Source count/content guard changed; rolling back experiment"
            )
        report = {
            "phase": "2B — Controlled Corruption Pilot",
            "experiment_id": experiment_id,
            "experiment_schema": schema,
            "source_schema": source,
            "config": config.as_dict(),
            "selected_order_ids": order_ids,
            "completed_at_utc": datetime.now(UTC).isoformat(),
            "original_baseline": original_baseline,
            "pilot_baseline": baseline,
            "pilot_after": after,
            "manifest": manifest,
            "mutation_counts": dict(Counter(e["mutation_type"] for e in manifest)),
            "comparison": compare_findings(baseline, after, manifest),
            "pilot_source_sha256": pilot_before_digest,
            "corrupted_content_sha256": content_digest(connection, schema),
            "source_integrity": {
                "unchanged": True,
                "before": before_source,
                "after": after_source,
                "method": "counts and order-independent non-cryptographic checksums",
            },
        }
        report["reproducibility"] = compare_runs(previous, report)
        if report["reproducibility"]["comparable_previous_run"] and not all(
            report["reproducibility"]["checks"].values()
        ):
            raise ValueError("Comparable rerun failed reproducibility checks")
        # Durable artifact recovery if filesystem report writing later fails.
        connection.execute(
            sql.SQL("INSERT INTO {} VALUES (%s, %s)").format(
                sql.Identifier(schema, "_run"),
            ),
            (experiment_id, Jsonb(report)),
        )
    report["status"] = "committed"
    return report


def run_experiment(config: DatabaseConfig, pilot: ExperimentConfig) -> dict:
    with config.connect() as connection:
        return create_experiment(connection, pilot)
