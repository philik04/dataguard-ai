"""Stream CSV records through PostgreSQL COPY in one all-table transaction."""

import csv
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from dataguard_ai.ingestion.database import DatabaseConfig
from dataguard_ai.ingestion.tables import TABLE_FILES


@dataclass(frozen=True)
class CsvInfo:
    """Preflight observations; no source data rows are retained in memory."""

    path: Path
    columns: tuple[str, ...]
    row_count: int


def read_header(reader: Iterator[list[str]], path: Path) -> tuple[str, ...]:
    """Reject ambiguous headers instead of guessing a column mapping."""
    columns = tuple(next(reader, []))
    if not columns or any(not name for name in columns):
        raise ValueError(f"{path.name}: missing or empty column header")
    if len(set(columns)) != len(columns):
        raise ValueError(f"{path.name}: duplicate column names")
    return columns


def validated_rows(
    reader: Iterator[list[str]], column_count: int, path: Path
) -> Iterator[list[str]]:
    """Count CSV records, including quoted multiline fields, not physical lines."""
    for record_number, row in enumerate(reader, start=2):
        if not row:  # Ignore completely empty lines, as the profiler does.
            continue
        if len(row) != column_count:
            raise ValueError(
                f"{path.name}: wrong field count at record {record_number}"
            )
        yield row


def inspect_csv(path: Path) -> CsvInfo:
    """Read each record once to establish an independent expected row count."""
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source, strict=True)
        columns = read_header(reader, path)
        count = sum(1 for _ in validated_rows(reader, len(columns), path))
    return CsvInfo(path, columns, count)


def ensure_schema(cursor: psycopg.Cursor, sources: dict[str, CsvInfo]) -> None:
    """Create the raw tables and reject incompatible preexisting layouts."""
    ddl = (
        files("dataguard_ai.ingestion")
        .joinpath("raw_schema.sql")
        .read_text(encoding="utf-8")
    )
    cursor.execute(ddl)
    for table, source in sources.items():
        cursor.execute(
            """SELECT column_name, data_type, is_nullable
               FROM information_schema.columns
               WHERE table_schema = 'raw' AND table_name = %s
               ORDER BY ordinal_position""",
            (table,),
        )
        columns = cursor.fetchall()
        if tuple(column[0] for column in columns) != source.columns:
            raise ValueError(f"{source.path.name}: headers do not match raw.{table}")
        if any(kind != "text" or nullable != "YES" for _, kind, nullable in columns):
            raise ValueError(f"raw.{table}: expected nullable TEXT columns")


def copy_csv(cursor: psycopg.Cursor, table: str, source: CsvInfo) -> int:
    """Use one COPY stream per table, with no individual INSERT statements."""
    command = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier("raw", table),
        sql.SQL(", ").join(sql.Identifier(name) for name in source.columns),
    )
    with source.path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        if read_header(reader, source.path) != source.columns:
            raise ValueError(f"{source.path.name}: header changed after preflight")
        count = 0
        with cursor.copy(command) as copy:
            for row in validated_rows(reader, len(source.columns), source.path):
                # Same missing-field policy as Phase 1A. Other strings stay verbatim.
                copy.write_row([None if value == "" else value for value in row])
                count += 1
    return count


def load_tables(
    connection: psycopg.Connection, sources: dict[str, CsvInfo]
) -> dict[str, dict[str, Any]]:
    """Replace all nine tables atomically; leave existing data intact on failure."""
    if not connection.autocommit:
        raise ValueError("Loader requires autocommit=True with an explicit transaction")
    if set(sources) != set(TABLE_FILES):
        raise ValueError("An import must include all nine Olist tables")
    results = {}
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '10s'")
            ensure_schema(cursor, sources)
            tables = sql.SQL(", ").join(
                sql.Identifier("raw", table) for table in TABLE_FILES
            )
            cursor.execute(sql.SQL("TRUNCATE TABLE {}").format(tables))
            for table in TABLE_FILES:
                source = sources[table]
                copied_count = copy_csv(cursor, table, source)
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}").format(
                        sql.Identifier("raw", table)
                    )
                )
                database_count = cursor.fetchone()[0]
                if not source.row_count == copied_count == database_count:
                    raise ValueError(
                        f"raw.{table}: CSV/COPY/database row counts differ"
                    )
                results[table] = {
                    "file": source.path.name,
                    "csv_rows": source.row_count,
                    "copied_rows": copied_count,
                    "database_rows": database_count,
                    "counts_match": True,
                }
    # The transaction context has committed before a success result is returned.
    return results


def ingest_dataset(config: DatabaseConfig, input_dir: Path) -> dict[str, Any]:
    """Preflight all CSVs, connect once, and return only after database commit."""
    started = datetime.now(UTC).isoformat()
    sources = {
        table: inspect_csv(input_dir / filename)
        for table, filename in TABLE_FILES.items()
    }
    with config.connect() as connection:
        results = load_tables(connection, sources)
    return {
        "phase": "1B — PostgreSQL foundation and ingestion",
        "status": "committed",
        "strategy": "replace all nine raw tables in one transaction",
        "database": config.dbname,
        "input_directory": str(input_dir),
        "started_at_utc": started,
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "missing_policy": "Empty fields become SQL NULL; other text is unchanged.",
        "tables": results,
    }


def write_ingestion_report(report: dict[str, Any], output_dir: Path) -> Path:
    """Publish a JSON report after commit; exclude credentials and source values."""
    if output_dir.resolve().is_relative_to(Path(report["input_directory"]).resolve()):
        raise ValueError("Output directory must be outside the raw input directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "ingestion.json"
    temporary = output_dir / "ingestion.json.tmp"
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination
