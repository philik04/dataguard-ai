"""Synthetic fixture writes only; the evaluator must leave the fixture unchanged."""

import os
from collections.abc import Iterator
from dataclasses import replace

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from dataguard_ai.corruption.storage import content_digest
from dataguard_ai.evaluation.runner import evaluate_connection
from dataguard_ai.ingestion.database import DatabaseConfig
from dataguard_ai.quality.engine import validate_connection
from dataguard_ai.quality.rules import PRIMARY_KEYS, selected_columns

pytestmark = pytest.mark.integration
SCHEMA = "experiment_evaluation_test"
EXPERIMENT_ID = "evaluation-test"


def insert(connection: psycopg.Connection, table: str, record: dict) -> None:
    columns = [*selected_columns(table), "_experiment_row_id"]
    connection.execute(
        sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(SCHEMA, table),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        ),
        [record.get(column) for column in columns],
    )


@pytest.fixture
def database() -> Iterator[psycopg.Connection]:
    name = os.environ.get("DATAGUARD_TEST_DB", "")
    if not name.endswith("_test"):
        pytest.fail("Set DATAGUARD_TEST_DB to a dedicated _test database")
    config = replace(DatabaseConfig.from_environment(), dbname=name)
    with config.connect() as connection:
        assert connection.execute("SELECT current_database()").fetchone()[0] == name
        # CREATE fails if the name exists: never reset somebody else's schema.
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(SCHEMA)))
        try:
            for table in PRIMARY_KEYS:
                columns = sql.SQL(", ").join(
                    sql.SQL("{} TEXT").format(sql.Identifier(column))
                    for column in selected_columns(table)
                )
                connection.execute(
                    sql.SQL(
                        "CREATE TABLE {} ({}, _experiment_row_id TEXT PRIMARY KEY)"
                    ).format(sql.Identifier(SCHEMA, table), columns)
                )
                insert(
                    connection,
                    table,
                    {
                        "customer_id": "c1",
                        "customer_unique_id": "u1",
                        "order_id": "o1",
                        "order_item_id": "01",
                        "product_id": "p1",
                        "seller_id": "s1",
                        "review_id": "r1",
                        "payment_sequential": "01",
                        "product_category_name": "books",
                        "order_status": "processing",
                        "price": "10.50",
                        "freight_value": "0",
                        "payment_value": "10.50",
                        "order_purchase_timestamp": "2020-01-02 10:00:00",
                        "_experiment_row_id": f"{table}:1",
                    },
                )
            connection.execute(
                sql.SQL(
                    "CREATE TABLE {} "
                    "(mutation_index INTEGER PRIMARY KEY, details JSONB)"
                ).format(sql.Identifier(SCHEMA, "_mutations"))
            )
            connection.execute(
                sql.SQL(
                    "CREATE TABLE {} (experiment_id TEXT PRIMARY KEY, details JSONB)"
                ).format(sql.Identifier(SCHEMA, "_run"))
            )
            yield connection
        finally:
            # Only tables created by this fixture; no CASCADE or raw writes.
            for table in [*PRIMARY_KEYS, "_run", "_mutations"]:
                connection.execute(
                    sql.SQL("DROP TABLE IF EXISTS {}").format(
                        sql.Identifier(SCHEMA, table)
                    )
                )
            connection.execute(sql.SQL("DROP SCHEMA {}").format(sql.Identifier(SCHEMA)))


def snapshot(connection: psycopg.Connection) -> dict:
    return {
        table: connection.execute(
            sql.SQL("SELECT * FROM {} ORDER BY 1").format(sql.Identifier(SCHEMA, table))
        ).fetchall()
        for table in [*PRIMARY_KEYS, "_run", "_mutations"]
    }


def publish(connection: psycopg.Connection, manifest: list, baseline: dict) -> None:
    after = validate_connection(connection, schema=SCHEMA)
    with connection.transaction():
        digest = content_digest(connection, SCHEMA)
    stored = {
        "experiment_id": EXPERIMENT_ID,
        "experiment_schema": SCHEMA,
        "config": {"seed": 42, "mutation_budget": len(manifest)},
        "manifest": manifest,
        "pilot_baseline": baseline,
        "pilot_after": after,
        "original_baseline": {"summary": baseline["summary"]},
        "corrupted_content_sha256": digest,
    }
    for index, event in enumerate(manifest, 1):
        connection.execute(
            sql.SQL("INSERT INTO {} VALUES (%s, %s)").format(
                sql.Identifier(SCHEMA, "_mutations")
            ),
            (index, Jsonb(event)),
        )
    connection.execute(
        sql.SQL("INSERT INTO {} VALUES (%s, %s)").format(
            sql.Identifier(SCHEMA, "_run")
        ),
        (EXPERIMENT_ID, Jsonb(stored)),
    )


def event(
    kind: str,
    locator: str,
    field: str,
    old: object,
    new: object,
    rule: str | None,
    source: str | None = None,
) -> dict:
    return {
        "mutation_id": locator,
        "experiment_id": EXPERIMENT_ID,
        "target_table": "order_items",
        "field": field,
        "row_locator": locator,
        "source_row_locator": source or locator,
        "original_value": old,
        "mutated_value": new,
        "mutation_type": kind,
        "random_seed": 42,
        "expected_rule_id": rule,
    }


def test_clean_negative_control_is_read_only(database: psycopg.Connection) -> None:
    baseline = validate_connection(database, schema=SCHEMA)
    assert baseline["findings"] == []
    publish(database, [], baseline)
    before = snapshot(database)
    report = evaluate_connection(database, schema=SCHEMA)
    assert report["finding_level"] == {
        "tp": 0,
        "fp": 0,
        "fn": 0,
        "precision": None,
        "recall": None,
        "f1": None,
        "expected_obligations": 0,
        "new_predictions": 0,
    }
    assert report["provenance"]["transaction_read_only"] is True
    assert snapshot(database) == before


def test_real_detector_detects_duplicates_but_evades_plausible_price(
    database: psycopg.Connection,
) -> None:
    base = {
        "order_id": "o1",
        "product_id": "p1",
        "seller_id": "s1",
        "price": "10.50",
        "freight_value": "0",
    }
    for number in (2, 3):
        insert(
            database,
            "order_items",
            {
                **base,
                "order_item_id": f"0{number}",
                "_experiment_row_id": f"order_items:{number}",
            },
        )
    baseline = validate_connection(database, schema=SCHEMA)
    assert baseline["findings"] == []
    database.execute(
        sql.SQL("UPDATE {} SET price = %s WHERE _experiment_row_id = %s").format(
            sql.Identifier(SCHEMA, "order_items")
        ),
        ("not-a-number", "order_items:1"),
    )
    database.execute(
        sql.SQL("UPDATE {} SET price = %s WHERE _experiment_row_id = %s").format(
            sql.Identifier(SCHEMA, "order_items")
        ),
        ("10.51", "order_items:2"),
    )
    duplicate = {**base, "order_item_id": "03", "shipping_limit_date": None}
    insert(
        database, "order_items", {**duplicate, "_experiment_row_id": "order_items:4"}
    )
    manifest = [
        event(
            "invalid_money", "order_items:1", "price", "10.50", "not-a-number", "DQ003"
        ),
        event(
            "plausible_money_change", "order_items:2", "price", "10.50", "10.51", None
        ),
        event(
            "duplicate_key",
            "order_items:4",
            "__row__",
            None,
            duplicate,
            "DQ002",
            "order_items:3",
        ),
    ]
    publish(database, manifest, baseline)
    before = snapshot(database)
    report = evaluate_connection(database, schema=SCHEMA)
    assert report["finding_level"]["tp"] == 3
    assert report["finding_level"]["fp"] == report["finding_level"]["fn"] == 0
    assert report["finding_level"]["recall"] == 1
    assert report["mutation_event_level"]["detected"] == 2
    assert report["mutation_event_level"]["total"] == 3
    assert report["mutation_event_level"]["detection_rate"] == pytest.approx(2 / 3)
    assert report["mutation_event_level"]["outside_rule_contract"] == 1
    assert report["row_counts"]["manifest_affected_rows"] == 4
    assert snapshot(database) == before


def test_stale_content_is_rejected_without_repair(database: psycopg.Connection) -> None:
    publish(database, [], validate_connection(database, schema=SCHEMA))
    database.execute(
        sql.SQL("UPDATE {} SET price = '99'").format(
            sql.Identifier(SCHEMA, "order_items")
        )
    )
    before = snapshot(database)
    with pytest.raises(ValueError, match="content changed"):
        evaluate_connection(database, schema=SCHEMA)
    assert snapshot(database) == before


def test_inconsistent_manifest_is_rejected(database: psycopg.Connection) -> None:
    publish(database, [], validate_connection(database, schema=SCHEMA))
    database.execute(
        sql.SQL("INSERT INTO {} VALUES (1, %s)").format(
            sql.Identifier(SCHEMA, "_mutations")
        ),
        (Jsonb({"unexpected": "event"}),),
    )
    before = snapshot(database)
    with pytest.raises(ValueError, match="manifest disagrees"):
        evaluate_connection(database, schema=SCHEMA)
    assert snapshot(database) == before
