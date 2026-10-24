"""JSON with full evidence, Markdown with summaries and bounded examples."""

import json
from pathlib import Path
from typing import Any


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# DataGuard AI — Phase 2A quality report",
        "",
        f"Completed (UTC): {report['completed_at_utc']}",
        "",
        f"Read-only transaction: {report['transaction_read_only']}",
        "",
        f"Summary: {report['summary']['confirmed_violations']:,} "
        "confirmed rule violations; "
        f"{report['summary']['warnings']:,} warnings.",
        "",
        "## Rules",
        "",
        "| Rule | Description | Default severity | Findings | Violations | Warnings |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for rule in report["rules"]:
        lines.append(
            f"| {rule['rule_id']} | {rule['description']} | {rule['severity']} "
            f"| {rule['finding_count']} | {rule['confirmed_violations']} "
            f"| {rule['warnings']} |"
        )
    lines += ["", "## Scanned tables", "", "| Table | Rows |", "| --- | ---: |"]
    lines += [
        f"| raw.{table} | {count:,} |"
        for table, count in report["scanned_rows"].items()
    ]
    lines += [
        "",
        "## Missing timestamp observations",
        "",
        "These may overlap warnings. Missing alone does not imply invalid data.",
        "",
    ]
    for observation in report["observations"]:
        lines.append(
            f"- {observation['table']}.{observation['column']}: "
            f"{observation['missing_rows']:,} missing."
        )
    lines += [
        "",
        "## Evidence examples",
        "",
        "Up to three examples per rule; quality.json contains every finding.",
        "",
    ]
    for rule in report["rules"]:
        examples = [f for f in report["findings"] if f["rule_id"] == rule["rule_id"]][
            :3
        ]
        if examples:
            lines += [
                f"### {rule['rule_id']}",
                "",
                "```json",
                json.dumps(examples, indent=2, ensure_ascii=False),
                "```",
                "",
            ]
    lines += ["## Limits and interpretation", ""]
    lines += [f"- {note}" for note in report["limitations"]]
    return "\n".join(lines) + "\n"


def write_quality_reports(report: dict[str, Any], output_dir: Path) -> None:
    if output_dir.resolve().is_relative_to(Path("data/raw").resolve()):
        raise ValueError("Quality reports must be outside data/raw")
    output_dir.mkdir(parents=True, exist_ok=True)
    contents = {
        "quality.json": json.dumps(report, indent=2, ensure_ascii=False),
        "quality.md": render_markdown(report),
    }
    for name, content in contents.items():
        temporary = output_dir / f"{name}.tmp"
        temporary.write_text(content + "\n", encoding="utf-8")
        temporary.replace(output_dir / name)
