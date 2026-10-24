"""Known valid/invalid records test each rule without PostgreSQL or real data."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from dataguard_ai.quality.reporting import write_quality_reports
from dataguard_ai.quality.rules import (
    PRIMARY_KEYS,
    RULES,
    check_row,
    is_missing,
    parse_money,
    parse_timestamp,
    selected_columns,
)

PARENTS = {
    ("orders", "order_id"): {"o1"},
    ("customers", "customer_id"): {"c1"},
    ("products", "product_id"): {"p1"},
    ("sellers", "seller_id"): {"s1"},
}


def valid_row(table: str) -> dict[str, str | None]:
    values = {
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
        "price": "0.00",
        "freight_value": "0",
        "payment_value": "10.050",
        "_row_locator": "(0,1)",
    }
    return {
        **{
            column: values.get(column, "2020-01-01 10:00:00")
            for column in selected_columns(table)
        },
        "_row_locator": "(0,1)",
    }


@pytest.mark.parametrize("table", list(PRIMARY_KEYS))
def test_valid_record_has_no_findings(table: str) -> None:
    assert check_row(table, valid_row(table), PARENTS, {}) == []


@pytest.mark.parametrize("value", [None, "", " ", "\t\r\n", "\u00a0"])
def test_required_identifier_and_missing_fk_are_not_double_reported(
    value: str | None,
) -> None:
    row = {**valid_row("orders"), "customer_id": value}
    findings = check_row("orders", row, PARENTS, {})
    assert [f["rule_id"] for f in findings] == ["DQ001"]
    assert is_missing(value)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("10.050", Decimal("10.050")),
        (" -2 ", Decimal("-2")),
        ("1e2", Decimal("100")),
        ("NaN", None),
        ("Infinity", None),
        ("1_000", None),
        ("$1", None),
        ("1,20", None),
        ("", None),
    ],
)
def test_decimal_conversion_is_safe(value: str, expected: Decimal | None) -> None:
    assert parse_money(value) == expected


def test_invalid_negative_and_missing_money_are_distinct() -> None:
    row = {**valid_row("order_items"), "price": "bad", "freight_value": "-0.01"}
    assert [f["rule_id"] for f in check_row("order_items", row, PARENTS, {})] == [
        "DQ003",
        "DQ004",
    ]
    row = {**valid_row("order_payments"), "payment_value": None}
    findings = check_row("order_payments", row, PARENTS, {})
    assert findings[0]["rule_id"] == "DQ008"
    assert findings[0]["classification"] == "warning"


def test_nonmissing_orphan_and_exact_identifier_matching() -> None:
    for value in ("missing", " c1 "):
        findings = check_row(
            "orders", {**valid_row("orders"), "customer_id": value}, PARENTS, {}
        )
        assert [f["rule_id"] for f in findings] == ["DQ005"]


def test_duplicate_composite_key_and_expected_one_to_many() -> None:
    row = valid_row("order_items")
    assert not check_row("order_items", row, PARENTS, {("o1", "02"): 2})
    findings = check_row("order_items", row, PARENTS, {("o1", "01"): 2})
    assert findings[0]["rule_id"] == "DQ002"
    assert findings[0]["evidence"]["group_size"] == 2
    assert findings[0]["row_identifier"]["snapshot_ctid"] == "(0,1)"
    row["order_item_id"] = None
    assert [f["rule_id"] for f in check_row("order_items", row, PARENTS, {})] == [
        "DQ001"
    ]
    # review_id alone repeating across orders is not the candidate key.
    assert not check_row(
        "order_reviews", valid_row("order_reviews"), PARENTS, {("r1", "o2"): 2}
    )


@pytest.mark.parametrize(
    "value,valid",
    [
        ("2020-02-29 00:00:00", True),
        ("2021-02-29 00:00:00", False),
        ("2020-01-01T12:00:00.123456Z", True),
        ("2020-01-01 12:00:00+02:00", True),
        ("2020-01-01", False),
        ("2020-01-01 24:00:00", False),
        ("garbage", False),
    ],
)
def test_timestamp_format_and_calendar_validation(value: str, valid: bool) -> None:
    assert (parse_timestamp(value) is not None) == valid


def test_chronology_skips_invalid_input_and_compares_only_valid_events() -> None:
    row = {
        **valid_row("orders"),
        "order_delivered_customer_date": "2019-12-31 10:00:00",
        "order_delivered_carrier_date": None,
    }
    findings = check_row("orders", row, PARENTS, {})
    assert [f["rule_id"] for f in findings] == ["DQ007"]
    row["order_delivered_customer_date"] = "2021-02-29 00:00:00"
    assert [f["rule_id"] for f in check_row("orders", row, PARENTS, {})] == ["DQ006"]


def test_optional_comments_and_undelivered_orders_do_not_fail() -> None:
    row = {
        **valid_row("orders"),
        "order_status": "canceled",
        "order_approved_at": None,
        "order_delivered_carrier_date": None,
        "order_delivered_customer_date": None,
    }
    assert check_row("orders", row, PARENTS, {}) == []
    row["order_status"] = "delivered"
    findings = check_row("orders", row, PARENTS, {})
    assert [f["rule_id"] for f in findings] == ["DQ008"]
    assert findings[0]["severity"] == "warning"
    assert not any("comment" in column for column in selected_columns("order_reviews"))


def test_mixed_timezones_warn_without_claiming_chronological_violation() -> None:
    row = {
        **valid_row("order_reviews"),
        "review_answer_timestamp": "2020-01-01T10:00:00Z",
    }
    findings = check_row("order_reviews", row, PARENTS, {})
    assert findings[0]["rule_id"] == "DQ007"
    assert findings[0]["severity"] == "warning"


def test_registry_and_reports(tmp_path: Path) -> None:
    assert len({rule.rule_id for rule in RULES}) == len(RULES) == 8
    report = {
        "completed_at_utc": "2026-10-19T00:00:00+00:00",
        "transaction_read_only": True,
        "summary": {"confirmed_violations": 0, "warnings": 0},
        "rules": [],
        "scanned_rows": {"orders": 1},
        "observations": [],
        "findings": [],
        "limitations": ["Test limitation"],
    }
    write_quality_reports(report, tmp_path)
    assert json.loads((tmp_path / "quality.json").read_text()) == report
    assert "raw.orders | 1" in (tmp_path / "quality.md").read_text()
    with pytest.raises(ValueError, match="outside data/raw"):
        write_quality_reports(report, Path("data/raw/reports"))
