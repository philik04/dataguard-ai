"""Shared synthetic sources and explicit opt-in for database integration tests."""

import csv
import os
from dataclasses import replace
from pathlib import Path

import pytest

from dataguard_ai.ingestion.tables import TABLE_FILES


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-db-tests",
        action="store_true",
        default=False,
        help="Allow integration tests to reset raw in a dedicated _test database",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if not config.getoption("--run-db-tests"):
        skip = pytest.mark.skip(
            reason="Requires --run-db-tests and a dedicated test DB"
        )
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def ingestion_dir(tmp_path: Path) -> Path:
    """Two source records per table, using every original header and no real data."""
    headers = {
        "customers": (
            "customer_id,customer_unique_id,customer_zip_code_prefix,"
            "customer_city,customer_state"
        ),
        "geolocation": (
            "geolocation_zip_code_prefix,geolocation_lat,geolocation_lng,"
            "geolocation_city,geolocation_state"
        ),
        "order_items": (
            "order_id,order_item_id,product_id,seller_id,shipping_limit_date,"
            "price,freight_value"
        ),
        "order_payments": (
            "order_id,payment_sequential,payment_type,payment_installments,"
            "payment_value"
        ),
        "order_reviews": (
            "review_id,order_id,review_score,review_comment_title,"
            "review_comment_message,review_creation_date,review_answer_timestamp"
        ),
        "orders": (
            "order_id,customer_id,order_status,order_purchase_timestamp,"
            "order_approved_at,order_delivered_carrier_date,"
            "order_delivered_customer_date,order_estimated_delivery_date"
        ),
        "products": (
            "product_id,product_category_name,product_name_lenght,"
            "product_description_lenght,product_photos_qty,product_weight_g,"
            "product_length_cm,product_height_cm,product_width_cm"
        ),
        "sellers": "seller_id,seller_zip_code_prefix,seller_city,seller_state",
        "category_translation": "product_category_name,product_category_name_english",
    }
    special_values = {
        "customer_id": "001",
        "customer_zip_code_prefix": "01234",
        "customer_unique_id": "NULL",
        "customer_city": " ",
        "customer_state": "\\N",
        "order_id": "0001",
        "price": "10.050",
        "review_comment_title": "",
        "review_score": "not-a-number",
        "review_comment_message": 'hello,\n"world" café',
        "order_purchase_timestamp": "not-a-timestamp",
    }
    raw = tmp_path / "raw"
    raw.mkdir()
    for table, header in headers.items():
        columns = header.split(",")
        row = [special_values.get(column, "example") for column in columns]
        with (raw / TABLE_FILES[table]).open(
            "w", encoding="utf-8-sig", newline=""
        ) as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerow(row)
            writer.writerow(row)  # Duplicate records must be retained.
    return raw


def require_disposable_database(name: str, protected: str) -> None:
    """A suffix alone must never authorize resetting the configured source DB."""
    if not name.endswith("_test") or name == protected:
        raise ValueError("Select a separate disposable database ending in _test")


@pytest.fixture(scope="session", autouse=True)
def integration_database_preflight(request: pytest.FixtureRequest) -> None:
    """Refuse any pre-existing user schema/objects before integration fixtures run."""
    if not request.config.getoption("--run-db-tests"):
        return
    if not any("integration" in item.keywords for item in request.session.items):
        return
    from dataguard_ai.ingestion.database import DatabaseConfig

    config = DatabaseConfig.from_environment()
    name = os.environ.get("DATAGUARD_TEST_DB", "")
    require_disposable_database(name, config.dbname)
    with replace(config, dbname=name).connect() as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            actual = connection.execute("SELECT current_database()").fetchone()[0]
            if actual != name:
                pytest.fail("Dedicated database identity check failed")
            schemas = connection.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' "
                "AND nspname NOT IN ('information_schema', 'public')"
            ).fetchall()
            objects = connection.execute(
                "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname NOT LIKE 'pg_%' "
                "AND n.nspname <> 'information_schema' LIMIT 1"
            ).fetchall()
            routines = connection.execute(
                "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname NOT LIKE 'pg_%' "
                "AND n.nspname <> 'information_schema' LIMIT 1"
            ).fetchall()
            if schemas or objects or routines:
                pytest.fail(
                    "Dedicated test database is not empty; no fixture writes allowed. "
                    "Use a fresh disposable test database; do not reset existing data."
                )
