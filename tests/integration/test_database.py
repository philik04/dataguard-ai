"""Real PostgreSQL tests; reset only raw in an explicitly selected _test DB."""

import json
import os
import subprocess
import sysconfig
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from dataguard_ai.ingestion.database import DatabaseConfig
from dataguard_ai.ingestion.loader import ingest_dataset, inspect_csv, load_tables
from dataguard_ai.ingestion.tables import TABLE_FILES

pytestmark = pytest.mark.integration


@pytest.fixture
def test_config() -> DatabaseConfig:
    name = os.environ.get("DATAGUARD_TEST_DB", "")
    if not name.endswith("_test"):
        pytest.fail("Set DATAGUARD_TEST_DB to a dedicated database ending in _test")
    return replace(DatabaseConfig.from_environment(), dbname=name)


@pytest.fixture
def database(test_config: DatabaseConfig) -> Iterator[psycopg.Connection]:
    with test_config.connect() as connection:
        actual_name = connection.execute("SELECT current_database()").fetchone()[0]
        assert actual_name == test_config.dbname and actual_name.endswith("_test")
        connection.execute("DROP SCHEMA IF EXISTS raw CASCADE")
        try:
            yield connection
        finally:
            connection.execute("DROP SCHEMA IF EXISTS raw CASCADE")


def snapshot(connection: psycopg.Connection) -> dict[str, list[tuple]]:
    """Read actual stored values for rollback comparisons, not just counts."""
    return {
        table: connection.execute(
            sql.SQL("SELECT * FROM {}").format(sql.Identifier("raw", table))
        ).fetchall()
        for table in TABLE_FILES
    }


def test_bulk_load_preserves_values_and_is_repeatable(
    database: psycopg.Connection, test_config: DatabaseConfig, ingestion_dir: Path
) -> None:
    original = {path.name: path.read_bytes() for path in ingestion_dir.iterdir()}
    first = ingest_dataset(test_config, ingestion_dir)
    before = snapshot(database)
    second = ingest_dataset(test_config, ingestion_dir)
    assert first["status"] == second["status"] == "committed"
    assert snapshot(database) == before
    assert all(len(rows) == 2 for rows in before.values())
    assert all(counts["database_rows"] == 2 for counts in second["tables"].values())
    customer = database.execute("SELECT * FROM raw.customers LIMIT 1").fetchone()
    assert customer == ("001", "NULL", "01234", " ", "\\N")
    review = database.execute(
        "SELECT review_score, review_comment_title, review_comment_message "
        "FROM raw.order_reviews LIMIT 1"
    ).fetchone()
    assert review == ("not-a-number", None, 'hello,\n"world" café')
    assert database.execute("SELECT price FROM raw.order_items LIMIT 1").fetchone()[
        0
    ] == ("10.050")
    assert (
        database.execute(
            "SELECT order_purchase_timestamp FROM raw.orders LIMIT 1"
        ).fetchone()[0]
        == "not-a-timestamp"
    )
    assert {
        path.name: path.read_bytes() for path in ingestion_dir.iterdir()
    } == original


def test_late_copy_failure_restores_every_previous_table(
    database: psycopg.Connection, test_config: DatabaseConfig, ingestion_dir: Path
) -> None:
    ingest_dataset(test_config, ingestion_dir)
    before = snapshot(database)
    sources = {
        table: inspect_csv(ingestion_dir / filename)
        for table, filename in TABLE_FILES.items()
    }
    # Change an early table, then fail during a later COPY after TRUNCATE.
    customer_path = ingestion_dir / TABLE_FILES["customers"]
    customer_path.write_text(customer_path.read_text().replace("001", "999"))
    with (ingestion_dir / TABLE_FILES["order_reviews"]).open("a") as stream:
        stream.write("too,few,fields\n")
    with pytest.raises(ValueError, match="wrong field count"):
        load_tables(database, sources)
    assert snapshot(database) == before


def test_count_mismatch_rolls_back_even_first_time_schema_creation(
    database: psycopg.Connection, ingestion_dir: Path
) -> None:
    sources = {
        table: inspect_csv(ingestion_dir / filename)
        for table, filename in TABLE_FILES.items()
    }
    sources["category_translation"] = replace(
        sources["category_translation"], row_count=3
    )
    with pytest.raises(ValueError, match="row counts differ"):
        load_tables(database, sources)
    assert database.execute("SELECT to_regnamespace('raw')").fetchone()[0] is None


def test_schema_mismatch_keeps_previous_data(
    database: psycopg.Connection, test_config: DatabaseConfig, ingestion_dir: Path
) -> None:
    ingest_dataset(test_config, ingestion_dir)
    before = snapshot(database)
    sources = {
        table: inspect_csv(ingestion_dir / filename)
        for table, filename in TABLE_FILES.items()
    }
    sources["orders"] = replace(sources["orders"], columns=("wrong_column",))
    with pytest.raises(ValueError, match="headers do not match"):
        load_tables(database, sources)
    assert snapshot(database) == before


def test_ingest_cli_publishes_committed_report(
    database: psycopg.Connection,
    test_config: DatabaseConfig,
    ingestion_dir: Path,
    tmp_path: Path,
) -> None:
    environment = dict(os.environ, POSTGRES_DB=test_config.dbname)
    executable = Path(sysconfig.get_path("scripts")) / "dataguard-ai"
    output_dir = tmp_path / "reports"
    result = subprocess.run(
        [
            str(executable),
            "ingest",
            "--input-dir",
            str(ingestion_dir),
            "--output-dir",
            str(output_dir),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Committed all nine tables" in result.stdout
    assert result.stderr == ""
    report = json.loads((output_dir / "ingestion.json").read_text())
    assert report["status"] == "committed"
    assert len(report["tables"]) == 9
    assert all(
        counts["csv_rows"] == counts["database_rows"] == 2
        for counts in report["tables"].values()
    )
    assert all(len(rows) == 2 for rows in snapshot(database).values())
