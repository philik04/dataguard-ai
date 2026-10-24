"""A privacy boundary: turn local findings into allowlisted aggregate evidence."""

import re
from dataclasses import dataclass

from dataguard_ai.quality.rules import (
    CHRONOLOGY_PAIRS,
    FOREIGN_KEYS,
    MONEY_COLUMNS,
    PRIMARY_KEYS,
    REQUIRED_IDS,
    RULE_BY_ID,
    TIMESTAMP_COLUMNS,
)


@dataclass(frozen=True)
class EvidenceGroup:
    group_id: str
    rule_id: str
    table: str
    severity: str
    pattern: str
    columns: tuple[str, ...]
    parent_reference: str | None
    finding_count: int
    affected_row_count: int | None


def evidence_pattern(
    rule: str, table: str, severity: str, evidence: dict, schema: str
) -> tuple[str, tuple[str, ...], str | None]:
    """Accept known structural patterns, never copy an arbitrary evidence value."""
    column = evidence.get("column")
    if rule not in ("DQ002", "DQ007") and not isinstance(column, str):
        raise ValueError("Evidence requires an allowlisted column name")
    parent = None
    columns = (column,)
    if rule == "DQ001" and column in REQUIRED_IDS[table]:
        pattern = "required_identifier_missing"
    elif rule == "DQ002" and evidence.get("key_columns") in (
        PRIMARY_KEYS[table],
        list(PRIMARY_KEYS[table]),
    ):
        size = evidence.get("group_size")
        if type(size) is not int or size < 2:
            raise ValueError("Duplicate evidence requires a repeated complete key")
        columns = PRIMARY_KEYS[table]
        pattern = "duplicate_candidate_key"
    elif rule in ("DQ003", "DQ004") and column in MONEY_COLUMNS.get(table, ()):
        pattern = "invalid_monetary_text" if rule == "DQ003" else "negative_amount"
    elif rule == "DQ005" and column in FOREIGN_KEYS.get(table, {}):
        parent_table, parent_column = FOREIGN_KEYS[table][column]
        parent = f"{parent_table}.{parent_column}"
        if evidence.get("parent") != f"{schema}.{parent}":
            raise ValueError("Foreign-key evidence has an unsupported parent")
        pattern = "missing_parent_reference"
    elif rule == "DQ006" and column in TIMESTAMP_COLUMNS.get(table, ()):
        pattern = "invalid_timestamp_text"
    elif rule == "DQ007":
        columns = (evidence.get("earlier_column"), evidence.get("later_column"))
        pattern = evidence.get("reason")
        if columns not in CHRONOLOGY_PAIRS.get(table, ()) or pattern not in (
            "later_event_precedes_earlier",
            "mixed_timezone_context",
        ):
            raise ValueError("Unsupported chronological evidence pattern")
        expected_severity = (
            "warning" if pattern == "mixed_timezone_context" else "error"
        )
        if severity != expected_severity:
            raise ValueError("Chronology severity disagrees with its evidence pattern")
    elif (
        rule == "DQ008"
        and evidence.get("reason") == "amount_not_provided"
        and (column in MONEY_COLUMNS.get(table, ()))
    ):
        pattern = "amount_not_provided"
    elif (
        rule == "DQ008"
        and table == "orders"
        and (evidence.get("reason") == "expected_event_not_provided")
    ):
        if column == "order_purchase_timestamp":
            pattern = "purchase_event_not_provided"
        elif column == "order_delivered_customer_date" and (
            evidence.get("order_status") == "delivered"
        ):
            pattern = "delivered_event_not_provided"
        else:
            raise ValueError("Unsupported missing-event evidence pattern")
    else:
        raise ValueError("Unsupported rule/column evidence pattern")
    return pattern, columns, parent


def group_findings(findings: list[dict]) -> list[EvidenceGroup]:
    """Count findings and snapshot rows locally; identifiers never enter groups."""
    buckets: dict[tuple, list[dict]] = {}
    schemas = set()
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("Each finding must be an object")
        rule = finding.get("rule_id")
        qualified = finding.get("table", "")
        if (
            not isinstance(rule, str)
            or rule not in RULE_BY_ID
            or not isinstance(qualified, str)
        ):
            raise ValueError("Unknown rule or table")
        parts = qualified.split(".")
        if len(parts) != 2:
            raise ValueError("Findings require qualified tables")
        schema, table = parts
        if schema not in ("raw", "pg_temp") and not re.fullmatch(
            r"experiment_[a-z0-9_]{1,40}", schema
        ):
            raise ValueError("Unsupported source schema")
        if table not in RULE_BY_ID[rule].tables:
            raise ValueError("Rule does not apply to this table")
        schemas.add(schema)
        severity = finding.get("severity")
        if severity not in ("error", "warning"):
            raise ValueError("Unknown finding severity")
        if rule != "DQ007" and severity != RULE_BY_ID[rule].severity:
            raise ValueError("Severity disagrees with the rule contract")
        classification = "confirmed_violation" if severity == "error" else "warning"
        if finding.get("classification") != classification:
            raise ValueError("Classification disagrees with severity")
        evidence = finding.get("evidence")
        if not isinstance(evidence, dict):
            raise ValueError("Finding evidence must be an object")
        pattern, columns, parent = evidence_pattern(
            rule, table, severity, evidence, schema
        )
        key = (rule, table, severity, pattern, columns, parent)
        buckets.setdefault(key, []).append(finding)
    if len(schemas) > 1:
        raise ValueError("Explain one source schema at a time")

    groups = []
    for index, (key, members) in enumerate(sorted(buckets.items()), 1):
        rule, table, severity, pattern, columns, parent = key
        locator_key = (
            "experiment_row_id"
            if next(iter(schemas)).startswith("experiment_")
            else "snapshot_ctid"
        )
        identifiers = [member.get("row_identifier") for member in members]
        locators = [
            value.get(locator_key) if isinstance(value, dict) else None
            for value in identifiers
        ]
        known_rows = all(isinstance(value, str) and value.strip() for value in locators)
        groups.append(
            EvidenceGroup(
                group_id=f"group_{index:03d}",
                rule_id=rule,
                table=table,
                severity=severity,
                pattern=pattern,
                columns=columns,
                parent_reference=parent,
                finding_count=len(members),
                affected_row_count=len(set(locators)) if known_rows else None,
            )
        )
    return groups
