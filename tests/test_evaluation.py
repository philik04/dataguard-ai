"""Independent examples for denominators, evidence matching and imperfect results."""

from copy import deepcopy
from pathlib import Path

import pytest

from dataguard_ai.evaluation.metrics import evaluate_findings, finding_metrics
from dataguard_ai.evaluation.policy import build_expectations
from dataguard_ai.evaluation.reporting import write_evaluation_reports

SCHEMA = "experiment_unit"
EXPERIMENT = "unit-pilot"


def event(
    kind: str = "invalid_money", locator: str = "items:1", mutation_id: str = "m1"
) -> dict:
    fields = {
        "missing_identifier": ("order_items", "order_item_id", "DQ001", None),
        "invalid_money": ("order_items", "price", "DQ003", "not-a-number"),
        "negative_money": ("order_items", "freight_value", "DQ004", "-1.00"),
        "broken_reference": ("order_items", "product_id", "DQ005", "absent-product"),
        "invalid_timestamp": (
            "order_items",
            "shipping_limit_date",
            "DQ006",
            "not-a-timestamp",
        ),
        "reversed_chronology": (
            "orders",
            "order_delivered_carrier_date",
            "DQ007",
            "2020-01-01 10:00:00",
        ),
        "duplicate_key": (
            "order_items",
            "__row__",
            "DQ002",
            {"order_id": "o1", "order_item_id": "01"},
        ),
        "plausible_money_change": ("order_items", "price", None, "10.51"),
    }
    table, field, rule, value = fields[kind]
    return {
        "experiment_id": EXPERIMENT,
        "mutation_id": mutation_id,
        "target_table": table,
        "field": field,
        "expected_rule_id": rule,
        "original_value": None if kind == "duplicate_key" else "old-value",
        "mutated_value": value,
        "mutation_type": kind,
        "random_seed": 42,
        "source_row_locator": locator,
        "row_locator": locator + ":copy" if kind == "duplicate_key" else locator,
    }


def finding(
    rule: str = "DQ003", locator: str = "items:1", evidence: dict | None = None
) -> dict:
    return {
        "rule_id": rule,
        "table": f"{SCHEMA}.order_items",
        "severity": "error",
        "row_identifier": {
            "experiment_row_id": locator,
            "order_id": "o1",
            "order_item_id": "01",
        },
        "evidence": {"column": "price", "value": "not-a-number"}
        if evidence is None
        else evidence,
    }


def score(manifest: list, after: list, baseline: list | None = None) -> dict:
    return evaluate_findings(
        manifest, baseline or [], after, schema=SCHEMA, experiment_id=EXPERIMENT
    )


def test_clean_negative_control_and_zero_denominators(tmp_path: Path) -> None:
    report = score([], [])
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
    assert report["mutation_event_level"]["detection_rate"] is None
    write_evaluation_reports(report, tmp_path)
    assert "N/A" in (tmp_path / "evaluation.md").read_text()
    with pytest.raises(ValueError, match="outside data/raw"):
        write_evaluation_reports(report, Path("data/raw/results"))


def test_false_positive_on_clean_control_is_not_hidden() -> None:
    report = score([], [finding()])
    assert report["finding_level"]["fp"] == 1
    assert report["finding_level"]["precision"] == 0.0
    assert report["finding_level"]["recall"] is None
    assert report["finding_level"]["f1"] == 0.0


def test_missing_expected_prediction_is_a_false_negative() -> None:
    report = score([event()], [])
    assert report["finding_level"]["fn"] == 1
    assert report["finding_level"]["precision"] is None
    assert report["finding_level"]["recall"] == report["finding_level"]["f1"] == 0.0
    assert report["mutation_event_level"]["detection_rate"] == 0.0


@pytest.mark.parametrize("change", ["field", "value", "rule", "row", "severity"])
def test_same_row_alarm_is_not_enough_without_relevant_evidence(change: str) -> None:
    prediction = finding()
    if change == "field":
        prediction["evidence"]["column"] = "freight_value"
    elif change == "value":
        prediction["evidence"]["value"] = "a-different-value"
    elif change == "rule":
        prediction["rule_id"] = "DQ004"
    elif change == "row":
        prediction["row_identifier"]["experiment_row_id"] = "items:elsewhere"
    else:
        prediction["severity"] = "warning"
    report = score([event()], [prediction])
    assert [report["finding_level"][key] for key in ("tp", "fp", "fn")] == [0, 1, 1]


def test_one_to_one_matching_does_not_reward_repeated_predictions() -> None:
    report = score([event()], [finding(), finding()])
    assert [report["finding_level"][key] for key in ("tp", "fp", "fn")] == [1, 1, 0]
    assert report["finding_level"]["precision"] == 0.5
    assert report["false_positives"][0]["reason"] == "surplus_prediction"


def test_duplicate_event_has_two_labels_but_one_event() -> None:
    duplicate = event("duplicate_key")
    evidence = {"key_columns": ("order_id", "order_item_id"), "group_size": 2}
    original = finding("DQ002", evidence=evidence)
    inserted = finding("DQ002", locator="items:1:copy", evidence=evidence)
    partial = score([duplicate], [original])
    assert [partial["finding_level"][key] for key in ("tp", "fp", "fn")] == [1, 0, 1]
    assert partial["finding_level"]["precision"] == 1.0
    assert partial["finding_level"]["recall"] == 0.5
    assert partial["finding_level"]["f1"] == pytest.approx(2 / 3)
    assert partial["mutation_event_level"]["detection_rate"] == 1.0
    assert partial["mutation_event_level"]["fully_detected"] == 0
    complete = score([duplicate], [original, inserted])
    assert complete["finding_level"]["tp"] == 2
    assert complete["mutation_event_level"]["total"] == 1
    assert complete["row_counts"]["manifest_affected_rows"] == 2
    wrong_key = deepcopy(inserted)
    wrong_key["row_identifier"]["order_item_id"] = "99"
    assert score([duplicate], [original, wrong_key])["finding_level"]["fn"] == 1


def test_baseline_is_excluded_with_multiplicity_and_changes_are_visible() -> None:
    baseline = finding(
        "DQ006", "items:old", {"column": "shipping_limit_date", "value": "old-bad"}
    )
    report = score([event()], [baseline, finding()], [baseline])
    assert report["finding_level"]["tp"] == 1
    assert report["finding_level"]["fp"] == 0
    assert report["baseline"]["retained_count"] == 1
    repeated = score([event()], [baseline, baseline, finding()], [baseline])
    assert repeated["finding_level"]["fp"] == 1
    removed = score([event()], [finding()], [baseline])
    assert removed["baseline"]["disappeared_count"] == 1
    assert removed["finding_level"]["fn"] == 0
    changed = deepcopy(baseline)
    changed["evidence"]["value"] = "changed-bad"
    altered = score([event()], [changed, finding()], [baseline])
    assert altered["false_positives"][0]["changed_baseline_condition"]


def test_outside_contract_mutation_is_not_silently_counted_as_covered() -> None:
    report = score([event("plausible_money_change")], [])
    assert report["finding_level"]["expected_obligations"] == 0
    assert report["finding_level"]["recall"] is None
    assert report["mutation_event_level"]["detection_rate"] == 0.0
    assert report["mutation_event_level"]["outside_rule_contract"] == 1


@pytest.mark.parametrize(
    "change", ["overlap", "unknown", "wrong_rule", "missing_field", "duplicate_id"]
)
def test_ambiguous_and_malformed_ground_truth_is_rejected(change: str) -> None:
    events = [event()]
    if change == "overlap":
        events.append(event("negative_money", mutation_id="m2"))
    elif change == "unknown":
        events[0]["mutation_type"] = "unknown"
    elif change == "wrong_rule":
        events[0]["expected_rule_id"] = "DQ004"
    elif change == "missing_field":
        del events[0]["field"]
    else:
        events.append(event(locator="items:2"))
    with pytest.raises(ValueError):
        score(events, [])


def test_every_pilot_contract_has_explicit_labels_and_no_baseline_target_overlap() -> (
    None
):
    for kind in (
        "missing_identifier",
        "invalid_money",
        "negative_money",
        "broken_reference",
        "invalid_timestamp",
        "reversed_chronology",
    ):
        assert len(build_expectations([event(kind)], SCHEMA, EXPERIMENT)) == 1
    with pytest.raises(ValueError, match="baseline-clean"):
        score([event()], [finding()], [finding()])
    with pytest.raises(ValueError, match="stable experiment"):
        invalid = finding()
        invalid["row_identifier"] = {"snapshot_ctid": "(0,1)"}
        score([], [invalid])


def test_metric_counts_are_not_assumed_perfect() -> None:
    assert finding_metrics(3, 1, 2) == {
        "tp": 3,
        "fp": 1,
        "fn": 2,
        "precision": 0.75,
        "recall": 0.6,
        "f1": pytest.approx(2 / 3),
    }
