"""Small read-only SQL orchestration around pure Python quality rules."""

import re
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from dataguard_ai.ingestion.database import DatabaseConfig
from dataguard_ai.quality.rules import (
    FOREIGN_KEYS,
    PRIMARY_KEYS,
    RULES,
    TIMESTAMP_COLUMNS,
    check_row,
    is_missing,
    selected_columns,
)


def find_duplicate_keys(
    connection: psycopg.Connection,
    table: str,
    schema: str,
) -> dict[tuple[str, ...], int]:
    """Group the full candidate key; incomplete keys are handled by DQ001."""
    columns = sql.SQL(", ").join(sql.Identifier(c) for c in PRIMARY_KEYS[table])
    query = sql.SQL(
        "SELECT {}, COUNT(*) FROM {} GROUP BY {} HAVING COUNT(*) > 1"
    ).format(columns, sql.Identifier(schema, table), columns)
    duplicates = {}
    with connection.cursor(name="quality_duplicate_keys") as cursor:
        cursor.execute(query)
        for record in cursor:
            key = tuple(record[:-1])
            if all(not is_missing(value) for value in key):
                duplicates[key] = record[-1]
    return duplicates


def validate_snapshot(
    connection: psycopg.Connection,
    *,
    schema: str = "raw",
) -> dict[str, Any]:
    """SELECT-only checks inside a caller-owned transaction; rules stay unchanged."""
    if schema not in ("raw", "pg_temp") and not re.fullmatch(
        r"experiment_[a-z0-9_]{1,40}", schema
    ):
        raise ValueError("Quality source must be raw, pg_temp or experiment_<name>")
    if connection.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError("Snapshot checks require an active transaction")
    stable_ids = schema.startswith("experiment_")
    started = datetime.now(UTC).isoformat()
    findings = []
    observations = []
    scanned = {}
    read_only = connection.execute("SHOW transaction_read_only").fetchone()[0] == "on"
    parent_keys = {}
    parents = {
        parent for references in FOREIGN_KEYS.values() for parent in references.values()
    }
    for table, column in sorted(parents):
        query = sql.SQL("SELECT DISTINCT {} FROM {}").format(
            sql.Identifier(column),
            sql.Identifier(schema, table),
        )
        with connection.cursor(name="quality_parent_keys") as cursor:
            cursor.execute(query)
            parent_keys[(table, column)] = {
                record[0] for record in cursor if not is_missing(record[0])
            }
    for table in PRIMARY_KEYS:
        duplicates = find_duplicate_keys(connection, table, schema)
        columns = sql.SQL(", ").join(sql.Identifier(c) for c in selected_columns(table))
        locator = (
            sql.Identifier("_experiment_row_id")
            if stable_ids
            else sql.SQL("ctid::text")
        )
        query = sql.SQL("SELECT {} AS _row_locator, {} FROM {}").format(
            locator,
            columns,
            sql.Identifier(schema, table),
        )
        count = 0
        missing_timestamps = Counter()
        with connection.cursor(
            name="quality_table_rows", row_factory=dict_row
        ) as cursor:
            cursor.itersize = 2000
            cursor.execute(query)
            for row in cursor:
                row_findings = check_row(table, row, parent_keys, duplicates)
                for finding in row_findings:
                    finding["table"] = f"{schema}.{table}"
                    if stable_ids:
                        identifier = finding["row_identifier"]
                        identifier["experiment_row_id"] = identifier.pop(
                            "snapshot_ctid"
                        )
                    if "parent" in finding["evidence"]:
                        finding["evidence"]["parent"] = finding["evidence"][
                            "parent"
                        ].replace("raw.", f"{schema}.", 1)
                findings.extend(row_findings)
                for column in TIMESTAMP_COLUMNS.get(table, ()):
                    if is_missing(row[column]):
                        missing_timestamps[column] += 1
                count += 1
        scanned[table] = count
        for column, missing in sorted(missing_timestamps.items()):
            observations.append(
                {
                    "table": f"{schema}.{table}",
                    "column": column,
                    "missing_rows": missing,
                    "classification": "observation",
                    "note": "Missing event; interpret using lifecycle context.",
                }
            )
    per_rule = []
    for rule in RULES:
        matches = [
            finding for finding in findings if finding["rule_id"] == rule.rule_id
        ]
        per_rule.append(
            {
                **asdict(rule),
                "finding_count": len(matches),
                "confirmed_violations": sum(f["severity"] == "error" for f in matches),
                "warnings": sum(f["severity"] == "warning" for f in matches),
            }
        )
    return {
        "phase": "2A — Deterministic Data Quality Engine",
        "status": "completed",
        "started_at_utc": started,
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "transaction_read_only": read_only,
        "source_schema": schema,
        "scanned_rows": scanned,
        "rules": per_rule,
        "findings": findings,
        "observations": observations,
        "summary": {
            "finding_count": len(findings),
            "confirmed_violations": sum(f["severity"] == "error" for f in findings),
            "warnings": sum(f["severity"] == "warning" for f in findings),
        },
        "limitations": [
            "Findings count rule/row/field matches, not distinct affected rows.",
            "Duplicate findings include every row in a repeated complete key group.",
            "Experiment locators are stable local IDs; raw CTIDs are snapshot-only.",
            "Geolocation has no proposed primary key and no rules in this phase.",
            "Optional review comments are not selected or checked.",
            "Missing events are observations; delivered-date and purchase gaps warn.",
            "Missing timestamp observations can overlap warnings; do not add counts.",
            "Category-reference and postal-prefix coverage are outside this phase.",
            "Parent keys, duplicate groups and emitted findings must fit in memory.",
        ],
    }


def validate_connection(
    connection: psycopg.Connection,
    *,
    schema: str = "raw",
) -> dict[str, Any]:
    """Public validation always runs inside its own read-only snapshot."""
    if not connection.autocommit:
        raise ValueError(
            "Validation requires an autocommit connection before its transaction"
        )
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '30s'")
        connection.execute("SET LOCAL lock_timeout = '3s'")
        return validate_snapshot(connection, schema=schema)


def validate_database(config: DatabaseConfig) -> dict[str, Any]:
    with config.connect() as connection:
        return validate_connection(connection)
