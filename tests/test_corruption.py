"""Pure planning tests: reproducibility, explicit budgets and protected targets."""

from collections import Counter
from copy import deepcopy
from decimal import Decimal

import pytest

from dataguard_ai.corruption.planning import (
    MUTATIONS,
    ExperimentConfig,
    compare_findings,
    eligible,
    plan_mutations,
)
from dataguard_ai.corruption.storage import apply_mutations, require_experiment_schema


def records() -> dict:
    return {
        "order_items": [
            {
                "_experiment_row_id": f"order_items:{i:08d}",
                "order_id": f"o{i}",
                "order_item_id": "01",
                "product_id": "p1",
                "seller_id": "s1",
                "price": "10.50",
                "freight_value": "0",
                "shipping_limit_date": "2020-01-04 10:00:00",
            }
            for i in range(20)
        ],
        "orders": [
            {
                "_experiment_row_id": f"orders:{i:08d}",
                "order_id": f"o{i}",
                "order_purchase_timestamp": "2020-01-02 10:00:00",
                "order_delivered_carrier_date": "2020-01-03 10:00:00",
            }
            for i in range(20)
        ],
    }


@pytest.mark.parametrize(
    "orders,rate",
    [
        (1001, "0.07"),
        (0, "0.07"),
        (1000, "NaN"),
        (1000, "Infinity"),
        (1000, "-1"),
        (1000, "2"),
        (20, "0.01"),
    ],
)
def test_budget_limits(orders: int, rate: str) -> None:
    with pytest.raises(ValueError):
        ExperimentConfig(orders=orders, rate=Decimal(rate))


def test_default_budget_is_exactly_ten_per_type() -> None:
    config = ExperimentConfig()
    assert config.budget == 70
    assert config.as_dict()["rate"] == "0.07"


def test_plan_reproduces_and_does_not_modify_input() -> None:
    source = records()
    original = deepcopy(source)
    config = ExperimentConfig(20, 42, Decimal("0.35"))
    first = plan_mutations(source, {"findings": []}, "pilot_test", config)
    assert first == plan_mutations(source, {"findings": []}, "pilot_test", config)
    assert source == original
    assert Counter(e["mutation_type"] for e in first) == {kind: 1 for kind in MUTATIONS}
    assert len({(e["target_table"], e["source_row_locator"]) for e in first}) == 7
    assert first != plan_mutations(
        source,
        {"findings": []},
        "pilot_test",
        ExperimentConfig(20, 43, Decimal("0.35")),
    )
    duplicate = next(e for e in first if e["mutation_type"] == "duplicate_key")
    assert duplicate["row_locator"] != duplicate["source_row_locator"]
    assert duplicate["original_value"] is None
    assert "_experiment_row_id" not in duplicate["mutated_value"]
    assert (
        next(e for e in first if e["mutation_type"] == "reversed_chronology")[
            "mutated_value"
        ]
        == "2020-01-01 10:00:00"
    )


def test_preexisting_findings_exclude_targets_and_insufficient_candidates_fail() -> (
    None
):
    source = records()
    blocked = source["order_items"][0]["_experiment_row_id"]
    baseline = {
        "findings": [
            {
                "table": "experiment_pilot.order_items",
                "row_identifier": {"experiment_row_id": blocked},
            }
        ]
    }
    events = plan_mutations(
        source, baseline, "pilot_test", ExperimentConfig(20, 42, Decimal("0.35"))
    )
    assert all(e["source_row_locator"] != blocked for e in events)
    source["orders"] = []
    with pytest.raises(ValueError, match="Insufficient clean eligible"):
        plan_mutations(
            source, baseline, "pilot_test", ExperimentConfig(20, 42, Decimal("0.35"))
        )
    assert not eligible({"price": "bad"}, "invalid_money")


@pytest.mark.parametrize(
    "schema",
    ["raw", "public", "pg_temp", "experiment_", "experiment_x;DROP SCHEMA raw"],
)
def test_write_guards_reject_nonexperiment_names(schema: str) -> None:
    with pytest.raises(ValueError, match="experiment_"):
        require_experiment_schema(schema)
    with pytest.raises(ValueError):
        apply_mutations(None, schema, {}, [])


def test_comparison_uses_stable_ids_and_labels_disappearing_findings() -> None:
    original = {
        "rule_id": "DQ007",
        "table": "experiment_pilot.orders",
        "row_identifier": {"experiment_row_id": "orders:1"},
        "severity": "error",
        "evidence": {"reason": "old"},
    }
    new = {**original, "row_identifier": {"experiment_row_id": "orders:2"}}
    manifest = [
        {
            "mutation_id": "m1",
            "target_table": "orders",
            "row_locator": "orders:2",
            "source_row_locator": "orders:2",
        }
    ]
    compared = compare_findings(
        {"findings": [original]}, {"findings": [original, new]}, manifest
    )
    assert compared["pre_existing"] == [original]
    assert compared["new"][0]["associated_mutation_ids"] == ["m1"]
    assert compared["no_longer_reported"] == []
    removed = compare_findings({"findings": [original]}, {"findings": []}, [])
    assert removed["no_longer_reported"] == [original]
