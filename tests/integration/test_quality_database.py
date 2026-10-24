"""Use tiny session TEMP tables; never reset or change the raw schema."""

import os
from collections.abc import Iterator
from dataclasses import replace

import psycopg
import pytest
from psycopg import sql

from dataguard_ai.ingestion.database import DatabaseConfig
from dataguard_ai.quality.engine import validate_connection
from dataguard_ai.quality.rules import PRIMARY_KEYS, selected_columns

pytestmark = pytest.mark.integration


def insert_record(connection: psycopg.Connection, table: str, values: dict) -> None:
    columns = selected_columns(table)
    connection.execute(
        sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier("pg_temp", table),
            sql.SQL(", ").join(sql.Identifier(c) for c in columns),
            sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        ),
        [values.get(c) for c in columns],
    )


def base_record() -> dict:
    return {
        "customer_id": "c1",
        "customer_unique_id": "u1",
        "order_id": "o1",
        "product_id": "p1",
        "seller_id": "s1",
        "order_item_id": "01",
        "payment_sequential": "01",
        "review_id": "r1",
        "product_category_name": "books",
        "order_status": "processing",
        "price": "0",
        "freight_value": "0",
        "payment_value": "10.050",
        "order_purchase_timestamp": "2020-01-02 10:00:00",
    }


@pytest.fixture
def quality_database() -> Iterator[psycopg.Connection]:
    name = os.environ.get("DATAGUARD_TEST_DB", "")
    if not name.endswith("_test"):
        pytest.fail("Set DATAGUARD_TEST_DB to a dedicated _test database")
    config = replace(DatabaseConfig.from_environment(), dbname=name)
    with config.connect() as connection:
        assert connection.execute("SELECT current_database()").fetchone()[0] == name
        for table in PRIMARY_KEYS:
            columns = sql.SQL(", ").join(
                sql.SQL("{} TEXT").format(sql.Identifier(c))
                for c in selected_columns(table)
            )
            connection.execute(
                sql.SQL("CREATE TEMP TABLE {} ({})").format(
                    sql.Identifier(table),
                    columns,
                )
            )
            insert_record(connection, table, base_record())
        yield connection  # TEMP tables disappear when this session closes.


def snapshot(connection: psycopg.Connection) -> dict:
    return {
        table: connection.execute(
            sql.SQL("SELECT * FROM {}").format(
                sql.Identifier("pg_temp", table),
            )
        ).fetchall()
        for table in PRIMARY_KEYS
    }


def test_read_only_engine_allows_valid_data_and_keeps_source_unchanged(
    quality_database: psycopg.Connection,
) -> None:
    before = snapshot(quality_database)
    report = validate_connection(quality_database, schema="pg_temp")
    assert report["transaction_read_only"] is True
    assert report["summary"]["finding_count"] == 0
    assert sum(report["scanned_rows"].values()) == 8
    assert snapshot(quality_database) == before


def test_each_rule_on_real_sql_with_synthetic_invalid_records(
    quality_database: psycopg.Connection,
) -> None:
    connection = quality_database
    base = base_record()
    insert_record(connection, "order_items", {**base, "order_item_id": "02"})
    insert_record(
        connection,
        "order_items",
        {**base, "order_item_id": "03", "price": "bad", "freight_value": "-1"},
    )
    insert_record(
        connection,
        "order_payments",
        {
            **base,
            "payment_sequential": "02",
            "order_id": "orphan",
            "payment_value": "-1",
        },
    )
    insert_record(
        connection,
        "order_payments",
        {**base, "payment_sequential": "03", "payment_value": None},
    )
    insert_record(connection, "customers", {**base, "customer_id": None})
    insert_record(connection, "products", base)  # Two rows in the duplicated key group.
    insert_record(
        connection,
        "orders",
        {
            **base,
            "order_id": "o2",
            "order_delivered_customer_date": "2020-01-01 10:00:00",
            "order_approved_at": "garbage",
        },
    )
    insert_record(
        connection, "orders", {**base, "order_id": "o3", "order_status": "delivered"}
    )
    before = snapshot(connection)
    report = validate_connection(connection, schema="pg_temp")
    counts = {rule["rule_id"]: rule["finding_count"] for rule in report["rules"]}
    assert counts == {
        "DQ001": 1,
        "DQ002": 2,
        "DQ003": 1,
        "DQ004": 2,
        "DQ005": 1,
        "DQ006": 1,
        "DQ007": 1,
        "DQ008": 2,
    }
    assert all(f["row_identifier"]["snapshot_ctid"] for f in report["findings"])
    assert snapshot(connection) == before


def test_engine_rejects_implicit_outer_transaction(
    quality_database: psycopg.Connection,
) -> None:
    quality_database.autocommit = False
    try:
        with pytest.raises(ValueError, match="autocommit"):
            validate_connection(quality_database, schema="pg_temp")
    finally:
        quality_database.autocommit = True
