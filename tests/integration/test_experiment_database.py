"""Small disposable experiment schemas and TEMP sources in the dedicated test DB."""

import json
import os
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from dataguard_ai.corruption import experiment
from dataguard_ai.corruption.planning import ExperimentConfig
from dataguard_ai.corruption.reporting import write_experiment_reports
from dataguard_ai.ingestion.database import DatabaseConfig
from dataguard_ai.quality.engine import validate_connection
from dataguard_ai.quality.rules import PRIMARY_KEYS, selected_columns

pytestmark = pytest.mark.integration
SCHEMA = "experiment_test_pilot"
CONFIG = ExperimentConfig(20, 42, Decimal("0.35"))


@pytest.fixture
def pilot_database() -> Iterator[psycopg.Connection]:
    name = os.environ.get("DATAGUARD_TEST_DB", "")
    if not name.endswith("_test"):
        pytest.fail("DATAGUARD_TEST_DB must name the dedicated _test database")
    config = replace(DatabaseConfig.from_environment(), dbname=name)
    with config.connect() as connection:
        assert connection.execute("SELECT current_database()").fetchone()[0] == name
        assert (
            connection.execute("SELECT to_regnamespace(%s)", (SCHEMA,)).fetchone()[0]
            is None
        )
        for table in PRIMARY_KEYS:
            names = selected_columns(table)
            connection.execute(
                sql.SQL("CREATE TEMP TABLE {} ({})").format(
                    sql.Identifier(table),
                    sql.SQL(", ").join(
                        sql.SQL("{} TEXT").format(sql.Identifier(c)) for c in names
                    ),
                )
            )
            count = (
                20
                if table
                in (
                    "orders",
                    "customers",
                    "order_items",
                    "order_payments",
                    "order_reviews",
                )
                else 1
            )
            for index in range(count):
                values = {
                    "customer_id": f"c{index:03d}",
                    "customer_unique_id": f"u{index:03d}",
                    "order_id": f"o{index:03d}",
                    "order_item_id": "01",
                    "payment_sequential": "01",
                    "review_id": f"r{index:03d}",
                    "product_id": "p1",
                    "seller_id": "s1",
                    "product_category_name": "books",
                    "order_status": "delivered",
                    "price": "10.50",
                    "freight_value": "0",
                    "payment_value": "10.50",
                    "order_purchase_timestamp": "2020-01-02 10:00:00",
                    "order_approved_at": "2020-01-02 11:00:00",
                    "order_delivered_carrier_date": "2020-01-03 10:00:00",
                    "order_delivered_customer_date": "2020-01-04 10:00:00",
                }
                if table == "orders" and index == 0:
                    values["order_delivered_carrier_date"] = "2020-01-01 10:00:00"
                row = [values.get(c, "2020-01-05 10:00:00") for c in names]
                connection.execute(
                    sql.SQL("INSERT INTO {} VALUES ({})").format(
                        sql.Identifier("pg_temp", table),
                        sql.SQL(", ").join(sql.Placeholder() for _ in names),
                    ),
                    row,
                )
        try:
            yield connection
        finally:
            # Remove only our exact test objects; never DROP/CASCADE raw.
            for table in [*PRIMARY_KEYS, "_mutations", "_run"]:
                connection.execute(
                    sql.SQL("DROP TABLE IF EXISTS {}").format(
                        sql.Identifier(SCHEMA, table)
                    )
                )
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {}").format(sql.Identifier(SCHEMA))
            )


def test_pilot_preserves_relationships_stable_locators_and_reproduces(
    pilot_database: psycopg.Connection,
    tmp_path: Path,
) -> None:
    first = experiment.create_experiment(
        pilot_database, CONFIG, schema=SCHEMA, source="pg_temp"
    )
    assert first["source_integrity"]["unchanged"]
    assert sum(first["mutation_counts"].values()) == 7
    assert len(first["comparison"]["pre_existing"]) == 1
    assert len(first["comparison"]["new"]) == 8
    assert first["comparison"]["no_longer_reported"] == []
    assert (
        first["pilot_baseline"]["rules"][4]["finding_count"] == 0
    )  # No subset-created FK gaps.
    counts = {
        rule["rule_id"]: rule["finding_count"] for rule in first["pilot_after"]["rules"]
    }
    assert counts == {
        "DQ001": 1,
        "DQ002": 2,
        "DQ003": 1,
        "DQ004": 1,
        "DQ005": 1,
        "DQ006": 1,
        "DQ007": 2,
        "DQ008": 0,
    }
    read_only = validate_connection(pilot_database, schema=SCHEMA)
    assert read_only["transaction_read_only"]
    assert read_only["findings"] == first["pilot_after"]["findings"]
    assert all(
        "snapshot_ctid" not in f["row_identifier"] for f in read_only["findings"]
    )
    second = experiment.create_experiment(
        pilot_database, CONFIG, schema=SCHEMA, source="pg_temp"
    )
    for key in (
        "experiment_id",
        "manifest",
        "pilot_source_sha256",
        "corrupted_content_sha256",
    ):
        assert first[key] == second[key]
    assert first["pilot_after"]["findings"] == second["pilot_after"]["findings"]
    assert not first["reproducibility"]["comparable_previous_run"]
    assert second["reproducibility"]["comparable_previous_run"]
    assert all(second["reproducibility"]["checks"].values())
    write_experiment_reports(second, tmp_path)
    exported = json.loads((tmp_path / "mutation_manifest.json").read_text())
    assert exported["mutations"] == second["manifest"]
    assert "| DQ002 | 0 | 0 | 2 | 2 |" in (tmp_path / "experiment.md").read_text()
    stored = pilot_database.execute(
        sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(SCHEMA, "_mutations"))
    ).fetchone()[0]
    assert stored == 7  # Replaced, not appended.


def test_late_failure_restores_previous_experiment(
    pilot_database: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = experiment.create_experiment(
        pilot_database, CONFIG, schema=SCHEMA, source="pg_temp"
    )
    actual_apply = experiment.apply_mutations

    def fail_after_updates(*args: object) -> None:
        actual_apply(*args)
        raise ValueError("synthetic late failure")

    monkeypatch.setattr(experiment, "apply_mutations", fail_after_updates)
    with pytest.raises(ValueError, match="synthetic late failure"):
        experiment.create_experiment(
            pilot_database, replace(CONFIG, seed=43), schema=SCHEMA, source="pg_temp"
        )
    record = pilot_database.execute(
        sql.SQL("SELECT details FROM {}").format(sql.Identifier(SCHEMA, "_run"))
    ).fetchone()[0]
    assert record["experiment_id"] == before["experiment_id"]
    assert record["manifest"] == before["manifest"]
    assert (
        validate_connection(pilot_database, schema=SCHEMA)["findings"]
        == before["pilot_after"]["findings"]
    )


def test_failed_first_run_leaves_no_partial_schema(
    pilot_database: psycopg.Connection,
) -> None:
    pilot_database.execute(
        "UPDATE pg_temp.orders SET order_delivered_carrier_date = NULL"
    )
    with pytest.raises(ValueError, match="Insufficient clean eligible"):
        experiment.create_experiment(
            pilot_database, CONFIG, schema=SCHEMA, source="pg_temp"
        )
    assert (
        pilot_database.execute("SELECT to_regnamespace(%s)", (SCHEMA,)).fetchone()[0]
        is None
    )


def test_unowned_schema_is_not_overwritten(pilot_database: psycopg.Connection) -> None:
    pilot_database.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(SCHEMA)))
    with pytest.raises(ValueError, match="ownership marker"):
        experiment.create_experiment(
            pilot_database, CONFIG, schema=SCHEMA, source="pg_temp"
        )
    assert (
        pilot_database.execute("SELECT to_regnamespace(%s)", (SCHEMA,)).fetchone()[0]
        is not None
    )
