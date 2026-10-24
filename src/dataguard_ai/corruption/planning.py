"""Pure mutation planning: explicit budget, eligibility and repeatable randomness."""

import random
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from dataguard_ai.quality.rules import is_missing, parse_money, parse_timestamp

MUTATIONS = {
    "missing_identifier": ("order_items", "order_item_id", "DQ001"),
    "duplicate_key": ("order_items", "__row__", "DQ002"),
    "invalid_money": ("order_items", "price", "DQ003"),
    "negative_money": ("order_items", "freight_value", "DQ004"),
    "broken_reference": ("order_items", "product_id", "DQ005"),
    "invalid_timestamp": ("order_items", "shipping_limit_date", "DQ006"),
    "reversed_chronology": ("orders", "order_delivered_carrier_date", "DQ007"),
}


@dataclass(frozen=True)
class ExperimentConfig:
    orders: int = 1000
    seed: int = 42
    rate: Decimal = Decimal("0.07")

    def __post_init__(self) -> None:
        if not 1 <= self.orders <= 1000:
            raise ValueError("Pilot order count must be between 1 and 1000")
        if not self.rate.is_finite() or not 0 < self.rate <= 1:
            raise ValueError("Mutation rate must be finite and between 0 and 1")
        if self.budget < len(MUTATIONS):
            raise ValueError(
                "Budget must allow at least one event for each of seven types"
            )

    @property
    def budget(self) -> int:
        return int(self.rate * self.orders)

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithm": "pilot-v1",
            "orders": self.orders,
            "seed": self.seed,
            "rate": str(self.rate.normalize()),
            "mutation_budget": self.budget,
            "rate_denominator": "selected orders (events, not fraction of copied rows)",
        }


def eligible(row: dict[str, Any], mutation_type: str) -> bool:
    """Only replace valid originals; baseline-affected rows are excluded separately."""
    if mutation_type == "reversed_chronology":
        purchase = parse_timestamp(row.get("order_purchase_timestamp") or "")
        carrier = parse_timestamp(row.get("order_delivered_carrier_date") or "")
        return purchase is not None and carrier is not None and purchase.year > 1
    _, field, _ = MUTATIONS[mutation_type]
    if mutation_type == "duplicate_key":
        return not is_missing(row.get("order_id")) and not is_missing(
            row.get("order_item_id")
        )
    value = row.get(field)
    if is_missing(value):
        return False
    if mutation_type in ("invalid_money", "negative_money"):
        return parse_money(value) is not None
    if mutation_type == "invalid_timestamp":
        return parse_timestamp(value) is not None
    return True


def plan_mutations(
    records: dict[str, list[dict[str, Any]]],
    baseline: dict[str, Any],
    experiment_id: str,
    config: ExperimentConfig,
) -> list[dict[str, Any]]:
    """Use each original row at most once; never mask a pilot-baseline finding."""
    blocked = {
        (
            finding["table"].split(".")[-1],
            finding["row_identifier"]["experiment_row_id"],
        )
        for finding in baseline["findings"]
    }
    used = set(blocked)
    rng = random.Random(config.seed)
    types = tuple(MUTATIONS)
    manifest = []
    for index in range(config.budget):
        mutation_type = types[index % len(types)]
        table, field, expected_rule = MUTATIONS[mutation_type]
        candidates = sorted(
            (
                row
                for row in records[table]
                if (table, row["_experiment_row_id"]) not in used
                and eligible(row, mutation_type)
            ),
            key=lambda row: row["_experiment_row_id"],
        )
        if not candidates:
            raise ValueError(f"Insufficient clean eligible rows for {mutation_type}")
        row = rng.choice(candidates)
        source_id = row["_experiment_row_id"]
        used.add((table, source_id))
        row_id = source_id
        original = row.get(field)
        if mutation_type == "missing_identifier":
            mutated = None
        elif mutation_type == "duplicate_key":
            row_id = f"{source_id}:duplicate:{index + 1:04d}"
            original = None  # No row existed under this new locator before insertion.
            mutated = {
                key: value for key, value in row.items() if key != "_experiment_row_id"
            }
        elif mutation_type == "invalid_money":
            mutated = "not-a-number"
        elif mutation_type == "negative_money":
            mutated = "-1.00"
        elif mutation_type == "broken_reference":
            mutated = f"__missing_product_{experiment_id}"
        elif mutation_type == "invalid_timestamp":
            mutated = "not-a-timestamp"
        else:
            purchase = parse_timestamp(row["order_purchase_timestamp"])
            mutated = (purchase - timedelta(days=1)).isoformat(sep=" ")
        manifest.append(
            {
                "mutation_id": f"{experiment_id}:{index + 1:04d}",
                "experiment_id": experiment_id,
                "target_table": table,
                "row_locator": row_id,
                "source_row_locator": source_id,
                "field": field,
                "original_value": original,
                "mutated_value": mutated,
                "mutation_type": mutation_type,
                "random_seed": config.seed,
                "expected_rule_id": expected_rule,
            }
        )
    return manifest


def compare_findings(baseline: dict, after: dict, manifest: list[dict]) -> dict:
    """Describe stable-row set differences; deliberately do not compute accuracy."""
    import json

    def signature(finding: dict) -> str:
        return json.dumps(
            {
                "rule": finding["rule_id"],
                "table": finding["table"],
                "row": finding["row_identifier"]["experiment_row_id"],
                "severity": finding["severity"],
                "evidence": finding["evidence"],
            },
            sort_keys=True,
        )

    before = {signature(f): f for f in baseline["findings"]}
    current = {signature(f): f for f in after["findings"]}
    associations = {}
    for event in manifest:
        for locator in {event["row_locator"], event["source_row_locator"]}:
            associations.setdefault((event["target_table"], locator), []).append(
                event["mutation_id"]
            )
    new = []
    for key in sorted(current.keys() - before.keys()):
        finding = current[key]
        locator = (
            finding["table"].split(".")[-1],
            finding["row_identifier"]["experiment_row_id"],
        )
        new.append(
            {**finding, "associated_mutation_ids": associations.get(locator, [])}
        )
    return {
        "pre_existing": [
            current[key] for key in sorted(current.keys() & before.keys())
        ],
        "new": new,
        "no_longer_reported": [
            before[key] for key in sorted(before.keys() - current.keys())
        ],
        "note": "Descriptive differences, not accuracy or validated causal evaluation.",
    }


def compare_runs(previous: dict | None, current: dict) -> dict:
    """Verify a comparable persisted rerun, excluding completion times."""
    import json

    if previous is None or previous["experiment_id"] != current["experiment_id"]:
        return {
            "comparable_previous_run": False,
            "checks": {},
            "note": "Run the same pilot again to verify reproducibility.",
        }
    return {
        "comparable_previous_run": True,
        "checks": {
            "selected_orders_equal": previous["selected_order_ids"]
            == current["selected_order_ids"],
            "manifest_equal": previous["manifest"] == current["manifest"],
            "source_digest_equal": previous["pilot_source_sha256"]
            == current["pilot_source_sha256"],
            "corrupted_digest_equal": previous["corrupted_content_sha256"]
            == current["corrupted_content_sha256"],
            "findings_equal": json.dumps(
                previous["pilot_after"]["findings"], sort_keys=True
            )
            == json.dumps(current["pilot_after"]["findings"], sort_keys=True),
        },
    }
