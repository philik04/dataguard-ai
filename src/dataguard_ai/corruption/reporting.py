"""Experiment summaries and complete machine-readable mutation evidence."""

import json
from pathlib import Path


def render_experiment(report: dict) -> str:
    comparison = report["comparison"]
    lines = [
        "# DataGuard AI — Phase 2B controlled pilot",
        "",
        f"Experiment: `{report['experiment_id']}` in `{report['experiment_schema']}`.",
        "",
        f"Config: {json.dumps(report['config'], sort_keys=True)}",
        "",
        "Rate = mutation events / selected orders, not percentage of every copied row.",
        "",
        "## Mutation counts",
        "",
        "| Type | Events |",
        "| --- | ---: |",
    ]
    lines += [
        f"| {kind} | {count} |" for kind, count in report["mutation_counts"].items()
    ]
    lines += [
        "",
        "## Copied records",
        "",
        "| Table | Before mutation | After mutation |",
        "| --- | ---: | ---: |",
    ]
    for table, count in report["pilot_baseline"]["scanned_rows"].items():
        after_count = report["pilot_after"]["scanned_rows"][table]
        lines.append(f"| {table} | {count:,} | {after_count:,} |")
    lines += [
        "",
        "## Findings by rule",
        "",
        "| Rule | Original full baseline | Pilot before | Pilot after | New |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for original, before, after in zip(
        report["original_baseline"]["rules"],
        report["pilot_baseline"]["rules"],
        report["pilot_after"]["rules"],
        strict=True,
    ):
        new = sum(f["rule_id"] == original["rule_id"] for f in comparison["new"])
        lines.append(
            f"| {original['rule_id']} | {original['finding_count']} | "
            f"{before['finding_count']} | {after['finding_count']} | {new} |"
        )
    lines += [
        "",
        f"Pre-existing retained: {len(comparison['pre_existing'])}; "
        f"new: {len(comparison['new'])}; "
        f"no longer reported: {len(comparison['no_longer_reported'])}.",
        "",
        "Differences use stable experiment row IDs, rule ID, severity and evidence.",
        "Full findings are context; differences use the matching pilot baseline.",
        "",
        "## Source integrity and reproducibility",
        "",
        "Source counts/content checksums unchanged: "
        f"{report['source_integrity']['unchanged']}.",
        f"Pilot source SHA-256: `{report['pilot_source_sha256']}`.",
        f"Corrupted content SHA-256: `{report['corrupted_content_sha256']}`.",
        "",
        "Reproducibility verification: "
        f"`{json.dumps(report['reproducibility'], sort_keys=True)}`.",
        "Completion times are excluded from reproducibility comparisons.",
        "",
        "## Limits",
        "",
        "- No precision/recall is reported. This is a descriptive comparison only.",
        "- A duplicate insertion creates two DQ002 findings (source and new row).",
        "- Distinct, baseline-clean targets reduce masking and cascading effects.",
        "- This pilot does not represent every real-world corruption pattern.",
        "- Plausible wrong values and swapped valid references may evade rules.",
        "- Consistent date shifts and optional missing fields may go undetected.",
        "- Geolocation is omitted; the small category lookup is copied in full.",
        "- Local IDs are stable for this source, not across source revisions.",
        "- Source database checksums are non-cryptographic guards, not archival proof.",
        "- Database atomicity does not extend to filesystem exports.",
        "",
    ]
    return "\n".join(lines)


def write_experiment_reports(report: dict, output_dir: Path) -> None:
    if output_dir.resolve().is_relative_to(Path("data/raw").resolve()):
        raise ValueError("Experiment reports must be outside data/raw")
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "mutation_manifest.json": {
            "experiment_id": report["experiment_id"],
            "experiment_schema": report["experiment_schema"],
            "config": report["config"],
            "mutations": report["manifest"],
        },
        "experiment.json": report,
    }
    for name, value in outputs.items():
        temporary = output_dir / f"{name}.tmp"
        temporary.write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        temporary.replace(output_dir / name)
    temporary = output_dir / "experiment.md.tmp"
    temporary.write_text(render_experiment(report), encoding="utf-8")
    temporary.replace(output_dir / "experiment.md")
