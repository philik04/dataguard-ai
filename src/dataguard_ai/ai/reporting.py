"""Keep measured aggregate evidence visibly separate from reviewed interpretations."""

import json
from pathlib import Path


def render_explanations(report: dict) -> str:
    summary = report["summary"]
    source = report.get("source", {})
    lines = [
        f"# DataGuard AI — {report['phase']} explanations",
        "",
        f"Provider: `{report['provider']}`; mode: reviewed catalog selection.",
        "Interpretations use reviewed text; selected hypotheses remain unconfirmed.",
        f"Generated (UTC): {report['completed_at_utc']}",
        "Validation snapshot: "
        + source.get("validation_completed_at_utc", "not supplied"),
        f"Input SHA-256: `{source.get('input_sha256', 'not supplied')}`.",
        "",
        f"{summary['finding_count']} findings: {summary['confirmed_violations']} "
        f"rule violations and {summary['warnings']} warnings; "
        f"{summary['group_count']} groups.",
        report["counting_policy"],
        "",
        "## Deterministic aggregate evidence",
        "",
        (
            "| Group | Rule | Table | Severity | Pattern / columns | "
            "Findings | Group rows |"
        ),
        "| --- | --- | --- | --- | --- | ---: | ---: |",
    ]
    for group in report["evidence_groups"]:
        rows = group["affected_row_count"]
        lines.append(
            f"| {group['group_id']} | {group['rule_id']} | {group['table']} "
            f"| {group['severity']} | {group['pattern']}: "
            f"{', '.join(group['columns'])} "
            f"| {group['finding_count']} | {rows if rows is not None else 'N/A'} |"
        )
    lines += ["", "## Interpretations and investigation guidance", ""]
    for explanation in report["explanations"]:
        lines += [
            f"### {explanation['group_id']} — {explanation['issue_summary']}",
            "",
            "Evidence-based description (deterministic template): "
            + explanation["evidence_based_explanation"],
            "",
            "Possible root causes — hypotheses, not confirmed:",
            "",
            *[
                f"- Hypothesis: {item['text']}"
                for item in explanation["possible_root_causes"]
            ],
            "",
            "Potential business impact — not measured:",
            "",
            *[
                f"- Potential: {item['text']}"
                for item in explanation["potential_business_impact"]
            ],
            "",
            "Recommended investigation steps (no automatic changes):",
            "",
            *[f"- {step}" for step in explanation["recommended_investigation_steps"]],
            "",
            "Confidence limitations:",
            "",
            *[f"- {note}" for note in explanation["confidence_limitations"]],
            "",
        ]
    if "api_metadata" in report:
        lines += [
            "## Model and usage metadata",
            "",
            "```json",
            json.dumps(report["api_metadata"], indent=2),
            "```",
            "",
        ]
    if "fallback" in report:
        lines += [
            "Explicit offline fallback: OpenAI failed; may have incurred cost.",
            "",
        ]
    return "\n".join(lines)


def write_explanation_reports(
    report: dict, output_dir: Path, *, input_path: Path
) -> None:
    if output_dir.resolve().is_relative_to(Path("data/raw").resolve()):
        raise ValueError("Explanation reports must be outside data/raw")
    stem = "explanations_openai" if report["provider"] == "openai" else "explanations"
    if "fallback" in report:
        stem = "explanations_openai_fallback"
    texts = {
        f"{stem}.json": json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        f"{stem}.md": render_explanations(report),
    }
    for name in texts:
        if (output_dir / name).resolve() == input_path.resolve():
            raise ValueError("Explanation output must not overwrite the input report")
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, text in texts.items():
        temporary = output_dir / f"{name}.tmp"
        if temporary.resolve() == input_path.resolve():
            raise ValueError("Temporary output must not overwrite the input report")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(output_dir / name)
