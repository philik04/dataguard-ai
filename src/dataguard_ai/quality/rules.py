"""Domain assumptions and pure Python checks, independent of PostgreSQL."""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

Severity = Literal["error", "warning"]
Row = dict[str, str | None]

# One key per table: repeats of order_id alone are legitimate in child tables.
PRIMARY_KEYS = {
    "customers": ("customer_id",),
    "orders": ("order_id",),
    "order_items": ("order_id", "order_item_id"),
    "order_payments": ("order_id", "payment_sequential"),
    "order_reviews": ("review_id", "order_id"),
    "products": ("product_id",),
    "sellers": ("seller_id",),
    "category_translation": ("product_category_name",),
}
REQUIRED_IDS = {
    **PRIMARY_KEYS,
    "customers": ("customer_id", "customer_unique_id"),
    "orders": ("order_id", "customer_id"),
    "order_items": ("order_id", "order_item_id", "product_id", "seller_id"),
}
FOREIGN_KEYS = {
    "orders": {"customer_id": ("customers", "customer_id")},
    "order_items": {
        "order_id": ("orders", "order_id"),
        "product_id": ("products", "product_id"),
        "seller_id": ("sellers", "seller_id"),
    },
    "order_payments": {"order_id": ("orders", "order_id")},
    "order_reviews": {"order_id": ("orders", "order_id")},
}
MONEY_COLUMNS = {
    "order_items": ("price", "freight_value"),
    "order_payments": ("payment_value",),
}
TIMESTAMP_COLUMNS = {
    "orders": (
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ),
    "order_items": ("shipping_limit_date",),
    "order_reviews": ("review_creation_date", "review_answer_timestamp"),
}
CHRONOLOGY_PAIRS = {
    "orders": (
        ("order_purchase_timestamp", "order_approved_at"),
        ("order_purchase_timestamp", "order_delivered_carrier_date"),
        ("order_purchase_timestamp", "order_delivered_customer_date"),
        ("order_delivered_carrier_date", "order_delivered_customer_date"),
    ),
    "order_reviews": (("review_creation_date", "review_answer_timestamp"),),
}


@dataclass(frozen=True)
class Rule:
    rule_id: str
    description: str
    severity: Severity
    tables: tuple[str, ...]


RULES = (
    Rule(
        "DQ001",
        "Required key/identifier is NULL, empty or whitespace-only",
        "error",
        tuple(REQUIRED_IDS),
    ),
    Rule(
        "DQ002",
        "Complete candidate primary key occurs more than once",
        "error",
        tuple(PRIMARY_KEYS),
    ),
    Rule(
        "DQ003",
        "Non-missing monetary value is not a finite decimal",
        "error",
        tuple(MONEY_COLUMNS),
    ),
    Rule("DQ004", "Monetary value is negative", "error", tuple(MONEY_COLUMNS)),
    Rule(
        "DQ005",
        "Non-missing core foreign key has no matching parent",
        "error",
        tuple(FOREIGN_KEYS),
    ),
    Rule(
        "DQ006",
        "Non-missing timestamp is invalid under the declared ISO format",
        "error",
        tuple(TIMESTAMP_COLUMNS),
    ),
    Rule(
        "DQ007",
        "Chronological ordering is reversed; mixed timezone context warns",
        "error",
        tuple(CHRONOLOGY_PAIRS),
    ),
    Rule(
        "DQ008",
        "Expected amount/purchase/delivered timestamp is missing",
        "warning",
        ("order_items", "order_payments", "orders"),
    ),
)
RULE_BY_ID = {rule.rule_id: rule for rule in RULES}

DECIMAL_PATTERN = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
)
TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?"
)


def is_missing(value: str | None) -> bool:
    """Blank identifiers include tabs/newlines and other Python whitespace."""
    return value is None or not value.strip()


def parse_money(value: str) -> Decimal | None:
    """Parse unformatted decimal text without float rounding or SQL casts."""
    if not DECIMAL_PATTERN.fullmatch(value.strip()):
        return None
    try:
        amount = Decimal(value.strip())
    except InvalidOperation:
        return None
    return amount if amount.is_finite() else None


def parse_timestamp(value: str) -> datetime | None:
    """Validate ISO date AND time; date-only strings are not event timestamps."""
    if not TIMESTAMP_PATTERN.fullmatch(value.strip()):
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def selected_columns(table: str) -> tuple[str, ...]:
    """Read only fields used by rules; omit optional review text entirely."""
    columns = set(REQUIRED_IDS[table])
    columns.update(MONEY_COLUMNS.get(table, ()))
    columns.update(TIMESTAMP_COLUMNS.get(table, ()))
    if table == "orders":
        columns.add("order_status")
    return tuple(sorted(columns))


def check_row(
    table: str,
    row: Row,
    parent_keys: dict[tuple[str, str], set[str]],
    duplicate_keys: dict[tuple[str, ...], int],
) -> list[dict[str, Any]]:
    """Evaluate one record; no mutation, database calls, or hidden global state."""
    findings = []
    identifier = {column: row.get(column) for column in PRIMARY_KEYS[table]}
    identifier["snapshot_ctid"] = row.get("_row_locator")

    def add(
        rule_id: str, evidence: dict[str, Any], severity: Severity | None = None
    ) -> None:
        level = severity or RULE_BY_ID[rule_id].severity
        findings.append(
            {
                "rule_id": rule_id,
                "table": f"raw.{table}",
                "row_identifier": identifier,
                "severity": level,
                "classification": "confirmed_violation"
                if level == "error"
                else "warning",
                "evidence": evidence,
            }
        )

    for column in REQUIRED_IDS[table]:
        if is_missing(row.get(column)):
            add("DQ001", {"column": column, "value": row.get(column)})

    key = tuple(row.get(column) for column in PRIMARY_KEYS[table])
    if all(not is_missing(value) for value in key) and key in duplicate_keys:
        add(
            "DQ002",
            {"key_columns": PRIMARY_KEYS[table], "group_size": duplicate_keys[key]},
        )

    for column in MONEY_COLUMNS.get(table, ()):
        value = row.get(column)
        if is_missing(value):
            add("DQ008", {"column": column, "reason": "amount_not_provided"})
        else:
            amount = parse_money(value)
            if amount is None:
                add("DQ003", {"column": column, "value": value})
            elif amount < 0:
                add("DQ004", {"column": column, "value": value})

    for column, parent in FOREIGN_KEYS.get(table, {}).items():
        value = row.get(column)
        if not is_missing(value) and value not in parent_keys[parent]:
            add(
                "DQ005",
                {
                    "column": column,
                    "value": value,
                    "parent": f"raw.{parent[0]}.{parent[1]}",
                },
            )

    timestamps = {}
    for column in TIMESTAMP_COLUMNS.get(table, ()):
        value = row.get(column)
        if not is_missing(value):
            parsed = parse_timestamp(value)
            if parsed is None:
                add("DQ006", {"column": column, "value": value})
            else:
                timestamps[column] = parsed

    for earlier, later in CHRONOLOGY_PAIRS.get(table, ()):
        if earlier not in timestamps or later not in timestamps:
            continue  # Missing/invalid values cannot prove reversed chronology.
        start, end = timestamps[earlier], timestamps[later]
        evidence = {
            "earlier_column": earlier,
            "earlier_value": row[earlier],
            "later_column": later,
            "later_value": row[later],
        }
        if (start.tzinfo is None) != (end.tzinfo is None):
            add("DQ007", {**evidence, "reason": "mixed_timezone_context"}, "warning")
        elif end < start:
            add("DQ007", {**evidence, "reason": "later_event_precedes_earlier"})

    if table == "orders":
        expected = ["order_purchase_timestamp"]
        if row.get("order_status") == "delivered":
            expected += ["order_delivered_customer_date"]
        for column in expected:
            if is_missing(row.get(column)):
                add(
                    "DQ008",
                    {
                        "column": column,
                        "order_status": row.get("order_status"),
                        "reason": "expected_event_not_provided",
                    },
                )
    return findings
