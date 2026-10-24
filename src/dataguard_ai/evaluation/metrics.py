"""One-to-one matching, explicit denominators and auditable unmatched records."""

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime

from dataguard_ai.evaluation.policy import (
    POLICY_VERSION,
    build_expectations,
    condition_key,
    finding_signature,
    row_key,
)
from dataguard_ai.quality.rules import RULES


def finding_metrics(tp: int, fp: int, fn: int) -> dict:
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }


def evaluate_findings(
    manifest: list[dict],
    baseline: list[dict],
    after: list[dict],
    *,
    schema: str,
    experiment_id: str,
) -> dict:
    """Never equate number of mutations with number of expected predictions."""
    expected = build_expectations(manifest, schema, experiment_id)
    touched = {
        (f"{schema}.{e['target_table']}", locator)
        for e in manifest
        for locator in (e["row_locator"], e["source_row_locator"])
    }
    if any(row_key(finding) in touched for finding in baseline):
        raise ValueError(
            "Policy v1 requires baseline-clean mutation target/source rows"
        )
    if any(
        row_key(finding)[0].split(".")[0] != schema for finding in [*baseline, *after]
    ):
        raise ValueError(
            "Baseline and after findings must belong to the same experiment schema"
        )
    # A Counter preserves repeated findings; a set would hide surplus alarms.
    remaining_baseline = Counter(finding_signature(f) for f in baseline)
    baseline_conditions = {condition_key(f) for f in baseline}
    retained = []
    predictions = []
    for index, finding in enumerate(after):
        signature = finding_signature(finding)
        if remaining_baseline[signature]:
            remaining_baseline[signature] -= 1
            retained.append(finding)
        else:
            predictions.append((index, finding))
    disappeared = []
    for finding in baseline:
        signature = finding_signature(finding)
        if remaining_baseline[signature]:
            disappeared.append(finding)
            remaining_baseline[signature] -= 1

    by_key = defaultdict(list)
    for obligation in expected:
        by_key[obligation.lookup_key].append(obligation)
    # Consuming expectation IDs enforces one-to-one matching.
    matched_ids = set()
    matches = []
    false_positives = []
    for index, finding in predictions:
        candidates = [
            e
            for e in by_key[(*row_key(finding), finding["rule_id"])]
            if e.matches(finding)
        ]
        available = [e for e in candidates if e.expectation_id not in matched_ids]
        if len(available) > 1:
            raise ValueError("Prediction has ambiguous ground-truth attribution")
        if available:
            obligation = available[0]
            matched_ids.add(obligation.expectation_id)
            matches.append(
                {
                    "expectation": asdict(obligation),
                    "finding_index": index,
                    "finding": finding,
                }
            )
        else:
            false_positives.append(
                {
                    "finding_index": index,
                    "finding": finding,
                    "reason": "surplus_prediction"
                    if candidates
                    else "no_matching_expectation",
                    "changed_baseline_condition": condition_key(finding)
                    in baseline_conditions,
                }
            )
    false_negatives = [
        asdict(e) for e in expected if e.expectation_id not in matched_ids
    ]
    events = []
    for event in manifest:
        labels = [e for e in expected if e.mutation_id == event["mutation_id"]]
        matched = sum(e.expectation_id in matched_ids for e in labels)
        events.append(
            {
                "mutation_id": event["mutation_id"],
                "mutation_type": event["mutation_type"],
                "expected_findings": len(labels),
                "matched_findings": matched,
                "detected": matched > 0,
                "fully_detected": bool(labels) and matched == len(labels),
                "outside_rule_contract": not labels,
            }
        )
    per_rule = []
    rule_ids = sorted(
        {rule.rule_id for rule in RULES}
        | {e.rule_id for e in expected}
        | {f["rule_id"] for _, f in predictions}
    )
    for rule_id in rule_ids:
        tp = sum(m["expectation"]["rule_id"] == rule_id for m in matches)
        fp = sum(m["finding"]["rule_id"] == rule_id for m in false_positives)
        fn = sum(e["rule_id"] == rule_id for e in false_negatives)
        per_rule.append(
            {
                "rule_id": rule_id,
                **finding_metrics(tp, fp, fn),
                "expected_findings": tp + fn,
                "new_predictions": tp + fp,
            }
        )
    detected = sum(event["detected"] for event in events)
    return {
        "phase": "3 — Detection Evaluation",
        "policy_version": POLICY_VERSION,
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "experiment_id": experiment_id,
        "experiment_schema": schema,
        "finding_level": {
            **finding_metrics(len(matches), len(false_positives), len(false_negatives)),
            "expected_obligations": len(expected),
            "new_predictions": len(predictions),
        },
        "mutation_event_level": {
            "total": len(events),
            "detected": detected,
            "fully_detected": sum(e["fully_detected"] for e in events),
            "undetected": len(events) - detected,
            "outside_rule_contract": sum(e["outside_rule_contract"] for e in events),
            "detection_rate": detected / len(events) if events else None,
        },
        "row_counts": {
            "mutation_target_rows": len(
                {(e["target_table"], e["row_locator"]) for e in manifest}
            ),
            "manifest_affected_rows": len(touched),
            "expected_finding_rows": len({(e.table, e.row_id) for e in expected}),
            "new_finding_rows": len({row_key(f) for _, f in predictions}),
        },
        "baseline": {
            "before_count": len(baseline),
            "retained_count": len(retained),
            "disappeared_count": len(disappeared),
            "disappeared": disappeared,
        },
        "per_rule": per_rule,
        "events": events,
        "true_positives": matches,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "limitations": [
            "Synthetic pilot policy; results do not establish real-world performance.",
            "Unmatched alarms are benchmark FP, not proven false real-world alarms.",
            "Expected labels trust declared contracts and recorded ground truth.",
            "Overlapping mutation targets and unsupported contracts are rejected.",
            "Finding metrics exclude outside-contract events; event rate includes all.",
            "No true-negative universe is defined; no accuracy percentage is reported.",
            "Undefined metric denominators are null, not zero or one.",
        ],
    }
