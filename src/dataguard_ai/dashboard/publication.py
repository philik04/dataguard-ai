"""Export reviewed report projections for a reproducible, credential-free demo.

No database/API access. Original reports and dataset files remain untouched.
Public row locators are new display surrogates, never business IDs or raw CTIDs.
"""

import argparse
import hashlib
import json
from pathlib import Path

from dataguard_ai.ai.grouping import group_findings
from dataguard_ai.dashboard.reports import LIVE_DIRECTORY, load_dashboard
from dataguard_ai.ingestion.tables import TABLE_FILES

PROJECTION_VERSION = "portfolio-projection-v1"


def project_quality(raw: dict) -> dict:
    """Preserve structural evidence/counts; discard all source values and IDs."""
    groups = group_findings(raw["findings"])
    locators: dict[tuple, str] = {}
    findings = []
    for finding in raw["findings"]:
        group = group_findings([finding])[0]
        columns = group.columns
        pattern = group.pattern
        if pattern == "duplicate_candidate_key":
            evidence = {
                "key_columns": list(columns),
                "group_size": finding["evidence"]["group_size"],
            }
        elif pattern in ("later_event_precedes_earlier", "mixed_timezone_context"):
            evidence = {
                "earlier_column": columns[0],
                "later_column": columns[1],
                "reason": pattern,
            }
        else:
            evidence = {"column": columns[0]}
            if pattern == "missing_parent_reference":
                evidence["parent"] = (
                    f"{finding['table'].split('.')[0]}.{group.parent_reference}"
                )
            elif pattern == "amount_not_provided":
                evidence["reason"] = "amount_not_provided"
            elif pattern in (
                "purchase_event_not_provided",
                "delivered_event_not_provided",
            ):
                evidence["reason"] = "expected_event_not_provided"
                if pattern == "delivered_event_not_provided":
                    evidence["order_status"] = "delivered"
        locator_key = (
            "experiment_row_id"
            if finding["table"].startswith("experiment_")
            else "snapshot_ctid"
        )
        original_identifier = finding.get("row_identifier")
        locator = (
            original_identifier.get(locator_key)
            if isinstance(original_identifier, dict)
            else None
        )
        identifier = {}
        if isinstance(locator, str) and locator.strip():
            identity = (finding["table"], locator_key, locator)
            # The private mapping exists in memory only and is never exported.
            public = locators.setdefault(
                identity, f"display-row-{len(locators) + 1:05d}"
            )
            identifier[locator_key] = public
        findings.append(
            {
                "rule_id": group.rule_id,
                "table": finding["table"],
                "severity": group.severity,
                "classification": "confirmed_violation"
                if group.severity == "error"
                else "warning",
                "row_identifier": identifier,
                "evidence": evidence,
            }
        )
    if group_findings(findings) != groups:
        raise ValueError("Projection changed grouped evidence; export aborted")
    return {
        "status": "completed",
        "completed_at_utc": raw["completed_at_utc"],
        "summary": raw["summary"],
        "findings": findings,
    }


def export_reports(source: Path, output: Path) -> None:
    source, output = source.resolve(), output.resolve()
    if (
        output == source
        or output.is_relative_to(source)
        or output.is_relative_to(Path("data/raw").resolve())
    ):
        raise ValueError("Export destination must be separate from private inputs")
    if output.exists():
        raise ValueError("Export destination already exists; choose a new directory")
    loaded = load_dashboard(source)
    if any(report.data is None for report in loaded.values()):
        raise ValueError("All six source reports must pass dashboard validation")

    def read(name):
        return json.loads((source / name).read_text())

    original = {
        key: read(name)
        for key, name in (
            ("ingestion", "ingestion.json"),
            ("quality", "quality.json"),
            ("experiment", "experiment.json"),
            ("evaluation", "evaluation.json"),
            ("offline", "explanations.json"),
            ("live", f"{LIVE_DIRECTORY}/explanations_openai.json"),
        )
    }
    payloads = {}
    ingestion = loaded["ingestion"].data
    payloads["ingestion.json"] = {
        "status": "committed",
        "completed_at_utc": original["ingestion"]["completed_at_utc"],
        "tables": {
            row["Table"]: {
                "file": TABLE_FILES[row["Table"]],
                "csv_rows": row["CSV rows"],
                "copied_rows": row["Copied rows"],
                "database_rows": row["Database rows"],
            }
            for row in ingestion["rows"]
        },
    }
    payloads["quality.json"] = project_quality(original["quality"])
    exp = original["experiment"]
    payloads["experiment.json"] = {
        "status": "committed",
        "completed_at_utc": exp["completed_at_utc"],
        "config": {
            "orders": loaded["experiment"].data["selected_orders"],
            "mutation_budget": loaded["experiment"].data["events"],
        },
        "mutation_counts": {
            r["Mutation type"]: r["Events"]
            for r in loaded["experiment"].data["mutations"]
        },
        "pilot_baseline": project_quality(exp["pilot_baseline"]),
        "pilot_after": project_quality(exp["pilot_after"]),
        "corrupted_content_sha256": loaded["experiment"].data["benchmark_digest"],
    }
    evaluation = original["evaluation"]
    # These numeric structures have already reconciled in parse_evaluation.
    evaluation_keys = ("tp", "fp", "fn", "precision", "recall", "f1")
    payloads["evaluation.json"] = {
        "completed_at_utc": evaluation["completed_at_utc"],
        "finding_level": {
            k: evaluation["finding_level"][k]
            for k in (*evaluation_keys, "expected_obligations", "new_predictions")
        },
        "mutation_event_level": {
            "total": loaded["evaluation"].data["events"],
            "detected": loaded["evaluation"].data["detected_events"],
        },
        "per_rule": [
            {k: row[k] for k in ("rule_id", *evaluation_keys)}
            for row in evaluation["per_rule"]
        ],
        "provenance": {
            "content_digest_verified": True,
            "corrupted_content_sha256": loaded["evaluation"].data["benchmark_digest"],
        },
    }
    # Hash the final public quality bytes, rather than claiming they are the raw report.
    for payload in payloads.values():
        payload["publication"] = {
            "projection": PROJECTION_VERSION,
            "note": (
                "Measured snapshot; display surrogates replace private row locators. "
                "No raw values or business IDs."
            ),
        }
    public_quality_hash = hashlib.sha256(encode(payloads["quality.json"])).hexdigest()
    for key, filename in (
        ("offline", "explanations.json"),
        ("live", f"{LIVE_DIRECTORY}/explanations_openai.json"),
    ):
        value = loaded[key].data
        payload = {
            "status": "completed",
            "provider": value["provider"],
            "completed_at_utc": original[key]["completed_at_utc"],
            "evidence_groups": loaded["quality"].data["groups"],
            "explanations": value["explanations"],
            "source": {
                "kind": "privacy_projected_quality_report",
                "input_sha256": public_quality_hash,
                "original_input_sha256": loaded["quality"].digest,
            },
            "publication": {
                "projection": PROJECTION_VERSION,
                "note": (
                    "Historical selections and usage retained; source hash points to "
                    "public projection. No new API call."
                ),
            },
        }
        if key == "live":
            rates = original[key]["api_metadata"]["limits"]
            payload["api_metadata"] = {
                "requested_model": value["usage"][0]["Model"],
                "requests_attempted": value["requests"],
                "limits": {
                    k: rates[k]
                    for k in ("input_usd_per_million", "output_usd_per_million")
                },
                "usage": [
                    {
                        "group_id": row["Group"],
                        "model": row["Model"],
                        "input_tokens": row["Input tokens"],
                        "output_tokens": row["Output tokens"],
                        "estimated_cost_usd": row["Estimated USD"],
                    }
                    for row in value["usage"]
                ],
            }
        payloads[filename] = payload
    output.mkdir(parents=True)
    for name, payload in payloads.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encode(payload))
    if any(report.data is None for report in load_dashboard(output).values()):
        raise ValueError("Export failed validation; do not publish this directory")


def encode(value: dict) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("reports/generated"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/portfolio"))
    args = parser.parse_args()
    export_reports(args.input_dir, args.output_dir)
    print(
        "Exported six validated privacy-projected reports; no API or database access."
    )


if __name__ == "__main__":
    main()
