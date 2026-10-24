"""Ground-truth obligations independent of the detector's observed outputs."""

import json
from dataclasses import dataclass, field
from typing import Any

POLICY_VERSION = "pilot-finding-v1"

# Independent evaluation contracts: never infer expected labels from predictions.
CONTRACTS = {
    "missing_identifier": ("order_items", "order_item_id", "DQ001"),
    "duplicate_key": ("order_items", "__row__", "DQ002"),
    "invalid_money": ("order_items", "price", "DQ003"),
    "negative_money": ("order_items", "freight_value", "DQ004"),
    "broken_reference": ("order_items", "product_id", "DQ005"),
    "invalid_timestamp": ("order_items", "shipping_limit_date", "DQ006"),
    "reversed_chronology": ("orders", "order_delivered_carrier_date", "DQ007"),
    # Evaluation sensitivity control only; does not extend the corruption simulator.
    "plausible_money_change": ("order_items", "price", None),
}


def canonical(value: Any) -> str:
    """Normalize JSON-compatible evidence, including tuple/list equivalence."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def row_key(finding: dict) -> tuple[str, str]:
    locator = finding.get("row_identifier", {}).get("experiment_row_id")
    table = finding.get("table")
    if (
        not isinstance(locator, str)
        or not locator
        or not isinstance(table, str)
        or len(table.split(".")) != 2
        or not all(table.split("."))
    ):
        raise ValueError(
            "Evaluation requires qualified tables and stable experiment row IDs"
        )
    return table, locator


def condition_key(finding: dict) -> tuple:
    """A condition excludes value evidence so changed baseline evidence is visible."""
    evidence = finding["evidence"]
    return (
        *row_key(finding),
        finding["rule_id"],
        canonical(
            {
                key: evidence.get(key)
                for key in ("column", "earlier_column", "later_column", "key_columns")
            }
        ),
    )


def finding_signature(finding: dict) -> str:
    return canonical(
        {
            "row": row_key(finding),
            "rule": finding["rule_id"],
            "severity": finding["severity"],
            "evidence": finding["evidence"],
        }
    )


@dataclass(frozen=True)
class ExpectedFinding:
    expectation_id: str
    mutation_id: str
    rule_id: str
    table: str
    row_id: str
    required_evidence: dict[str, Any]
    required_identifiers: dict[str, Any] = field(default_factory=dict)
    minimum_group_size: int | None = None

    @property
    def lookup_key(self) -> tuple[str, str, str]:
        return self.table, self.row_id, self.rule_id

    def matches(self, finding: dict) -> bool:
        if (*row_key(finding), finding["rule_id"]) != self.lookup_key:
            return False
        if finding["severity"] != "error":
            return False
        evidence = finding["evidence"]
        for key, expected in self.required_evidence.items():
            if key not in evidence or canonical(evidence[key]) != canonical(expected):
                return False
        for key, expected in self.required_identifiers.items():
            if finding["row_identifier"].get(key) != expected:
                return False
        if self.minimum_group_size is not None:
            size = evidence.get("group_size")
            if type(size) is not int or size < self.minimum_group_size:
                return False
        return True


def build_expectations(
    manifest: list[dict], schema: str, experiment_id: str
) -> list[ExpectedFinding]:
    """One obligation per field event, two per isolated duplicate event."""
    obligations = []
    mutation_ids = set()
    occupied_rows = set()
    for event in manifest:
        required = {
            "mutation_type",
            "target_table",
            "field",
            "expected_rule_id",
            "mutation_id",
            "experiment_id",
            "row_locator",
            "source_row_locator",
            "original_value",
            "mutated_value",
            "random_seed",
        }
        if not isinstance(event, dict) or not required <= event.keys():
            raise ValueError("Malformed manifest: required event fields are missing")
        kind = event["mutation_type"]
        if kind not in CONTRACTS:
            raise ValueError(f"Unsupported mutation contract: {kind}")
        table, column, rule = CONTRACTS[kind]
        if (event["target_table"], event["field"], event.get("expected_rule_id")) != (
            table,
            column,
            rule,
        ):
            raise ValueError(
                "Manifest field/table/rule disagrees with evaluation policy"
            )
        mutation_id = event["mutation_id"]
        if (
            not mutation_id
            or mutation_id in mutation_ids
            or event["experiment_id"] != experiment_id
        ):
            raise ValueError(
                "Manifest experiment/mutation IDs must be consistent and unique"
            )
        mutation_ids.add(mutation_id)
        locators = {event["row_locator"], event["source_row_locator"]}
        if any(not isinstance(row, str) or not row for row in locators):
            raise ValueError("Manifest requires stable row locators")
        targets = {(f"{schema}.{table}", row) for row in locators}
        if occupied_rows & targets:
            raise ValueError("Overlapping mutation rows are ambiguous under policy v1")
        occupied_rows.update(targets)
        if canonical(event["original_value"]) == canonical(event["mutated_value"]):
            raise ValueError("Manifest entry must describe an actual change")
        if kind != "duplicate_key" and len(locators) != 1:
            raise ValueError("Field mutations must retain their stable row identity")
        if rule is None:
            continue  # Out-of-contract events remain in the event denominator.
        if kind == "duplicate_key":
            record = event["mutated_value"]
            if len(locators) != 2 or not isinstance(record, dict):
                raise ValueError(
                    "Duplicate event needs distinct source/inserted rows and a record"
                )
            keys = {name: record.get(name) for name in ("order_id", "order_item_id")}
            if any(value is None or not str(value).strip() for value in keys.values()):
                raise ValueError(
                    "Duplicate expectation requires a complete business key"
                )
            for role, locator in (
                ("source", event["source_row_locator"]),
                ("inserted", event["row_locator"]),
            ):
                obligations.append(
                    ExpectedFinding(
                        f"{mutation_id}:{role}",
                        mutation_id,
                        rule,
                        f"{schema}.{table}",
                        locator,
                        {"key_columns": ["order_id", "order_item_id"]},
                        keys,
                        2,
                    )
                )
        else:
            evidence = {"column": column, "value": event["mutated_value"]}
            if kind == "broken_reference":
                evidence["parent"] = f"{schema}.products.product_id"
            if kind == "reversed_chronology":
                evidence = {
                    "earlier_column": "order_purchase_timestamp",
                    "later_column": column,
                    "later_value": event["mutated_value"],
                    "reason": "later_event_precedes_earlier",
                }
            obligations.append(
                ExpectedFinding(
                    f"{mutation_id}:target",
                    mutation_id,
                    rule,
                    f"{schema}.{table}",
                    event["row_locator"],
                    evidence,
                )
            )
    return obligations
