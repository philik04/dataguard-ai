"""Readable metrics and complete match evidence, with explicit undefined ratios."""

import json
from pathlib import Path


def percentage(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def render_evaluation(report: dict) -> str:
    metrics = report["finding_level"]
    events = report["mutation_event_level"]
    rows = report["row_counts"]
    provenance = report.get("provenance", {})
    lines = [
        "# DataGuard AI — Phase 3 detection evaluation",
        "",
        f"Experiment: `{report['experiment_id']}`. "
        f"Policy: `{report['policy_version']}`.",
        "",
        f"Completed (UTC): {report['completed_at_utc']}",
        "",
        "## Finding-level results (one-to-one obligations)",
        "",
        f"TP={metrics['tp']}, FP={metrics['fp']}, FN={metrics['fn']}.",
        f"Expected obligations: {metrics['expected_obligations']}; "
        f"new predictions: {metrics['new_predictions']}.",
        f"Precision: {percentage(metrics['precision'])} = TP / (TP + FP).",
        f"Recall: {percentage(metrics['recall'])} = TP / (TP + FN).",
        f"F1: {percentage(metrics['f1'])} = 2TP / (2TP + FP + FN).",
        "",
        "N/A means a zero denominator, not perfect performance.",
        "",
        "| Rule | Expected | New predictions | TP | FP | FN "
        "| Precision | Recall | F1 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rule in report["per_rule"]:
        lines.append(
            f"| {rule['rule_id']} | {rule['expected_findings']} "
            f"| {rule['new_predictions']} | {rule['tp']} | {rule['fp']} "
            f"| {rule['fn']} | {percentage(rule['precision'])} "
            f"| {percentage(rule['recall'])} | {percentage(rule['f1'])} |"
        )
    lines += [
        "",
        "## Separate event and row units",
        "",
        f"Mutation-event detection: {events['detected']} / {events['total']} "
        f"= {percentage(events['detection_rate'])} (at least one matched obligation).",
        f"Fully detected events: {events['fully_detected']}; "
        f"outside rule contract: {events['outside_rule_contract']}.",
        f"Mutation target rows: {rows['mutation_target_rows']}; "
        f"affected rows including duplicate sources: {rows['manifest_affected_rows']}.",
        f"Rows with expected findings: {rows['expected_finding_rows']}; "
        f"rows with new predictions: {rows['new_finding_rows']}.",
        "Duplicate events expect two findings; partial detection is not full recovery.",
        "",
        "## Baseline and provenance",
        "",
        f"Pilot baseline retained: {report['baseline']['retained_count']}; "
        f"disappeared: {report['baseline']['disappeared_count']}.",
        "Full raw baseline is context only; it is not subtracted from pilot counts.",
        f"Read-only transaction: {provenance.get('transaction_read_only', 'N/A')}.",
        "Pilot content SHA-256 verified: "
        f"{provenance.get('content_digest_verified', 'N/A')}.",
        "Fresh predictions agree with recorded predictions: "
        f"{provenance.get('fresh_predictions_match_recorded', 'N/A')}.",
        f"Manifest: `{provenance.get('manifest_source', 'not provided')}`.",
        "",
        "## Matching and interpretation",
        "",
        "Matches require stable ID, table, rule, error severity and relevant evidence.",
        "Predictions and obligations are used once; extra predictions are FP.",
        "Exact pre-existing findings are excluded with multiplicity preserved.",
        "evaluation.json contains TP links, unmatched alarms and missed obligations.",
        "",
    ]
    lines += [f"- {note}" for note in report["limitations"]]
    return "\n".join(lines) + "\n"


def write_evaluation_reports(report: dict, output_dir: Path) -> None:
    if output_dir.resolve().is_relative_to(Path("data/raw").resolve()):
        raise ValueError("Evaluation reports must be outside data/raw")
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, text in {
        "evaluation.json": json.dumps(report, indent=2, ensure_ascii=False),
        "evaluation.md": render_evaluation(report),
    }.items():
        temporary = output_dir / f"{name}.tmp"
        temporary.write_text(text + "\n", encoding="utf-8")
        temporary.replace(output_dir / name)
