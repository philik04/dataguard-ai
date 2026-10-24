"""Read saved reports and project only validated, public-facing fields.

Raw report dictionaries never reach Streamlit. Missing/invalid sections remain
unavailable rather than becoming zeros or invented successful results.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dataguard_ai.ai.catalog import (
    CONFIDENCE_LIMITATIONS,
    catalog_for,
    describe_evidence,
)
from dataguard_ai.ai.grouping import EvidenceGroup, group_findings
from dataguard_ai.ingestion.tables import TABLE_FILES
from dataguard_ai.quality.rules import RULE_BY_ID

LIVE_DIRECTORY = "openai_live_test_2026-10-23"
BENCHMARK_NOTE = (
    "Predefined synthetic corruption benchmark only. These results do not measure "
    "general real-world accuracy or coverage of unknown issues."
)


@dataclass
class Report:
    data: dict | None = None
    timestamp: str | None = None
    digest: str | None = None
    error: str | None = None
    notices: list[str] = field(default_factory=list)


def count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Invalid count")
    return value


def number(value: object) -> float:
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid nonnegative number")
    return float(value)


def content_digest(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Invalid content provenance")
    return value


def percentage(value: float | None) -> str:
    """Undefined denominators stay N/A; zero remains a real 0.0%."""
    if value is None:
        return "N/A"
    if number(value) > 1:
        raise ValueError("Invalid ratio")
    return f"{value:.1%}"


def parse_ingestion(raw: dict) -> dict:
    if raw["status"] != "committed" or set(raw["tables"]) != set(TABLE_FILES):
        raise ValueError("Incomplete ingestion")
    rows = []
    for table in TABLE_FILES:
        item = raw["tables"][table]
        values = [
            count(item[key]) for key in ("csv_rows", "copied_rows", "database_rows")
        ]
        rows.append(
            {
                "Table": table,
                "CSV rows": values[0],
                "Copied rows": values[1],
                "Database rows": values[2],
                "Counts match": len(set(values)) == 1,
            }
        )
    return {"rows": rows, "total": sum(row["Database rows"] for row in rows)}


def parse_quality(raw: dict) -> dict:
    if raw["status"] != "completed" or not isinstance(raw["findings"], list):
        raise ValueError("Incomplete quality report")
    findings = raw["findings"]
    groups = group_findings(findings)
    summary = {
        "finding_count": len(findings),
        "confirmed_violations": sum(f["severity"] == "error" for f in findings),
        "warnings": sum(f["severity"] == "warning" for f in findings),
    }
    if raw["summary"] != summary:
        raise ValueError("Inconsistent quality summary")
    rows = []
    for index, finding in enumerate(findings, 1):
        group = group_findings([finding])[0]
        rows.append(
            {
                "Finding": index,
                "Rule": group.rule_id,
                "Table": group.table,
                "Severity": group.severity,
                "Columns": ", ".join(group.columns),
                "Pattern": group.pattern,
                "Description": describe_evidence(group),
            }
        )
    return {
        "summary": summary,
        "rows": rows,
        "groups": [asdict(group) for group in groups],
        "rule_count": len(RULE_BY_ID),
    }


def parse_experiment(raw: dict) -> dict:
    if raw["status"] != "committed":
        raise ValueError("Incomplete experiment")
    allowed = {
        "missing_identifier",
        "duplicate_key",
        "invalid_money",
        "negative_money",
        "broken_reference",
        "invalid_timestamp",
        "reversed_chronology",
    }
    if set(raw["mutation_counts"]) - allowed:
        raise ValueError("Unknown mutation type")
    mutations = [
        {"Mutation type": k, "Events": count(v)}
        for k, v in raw["mutation_counts"].items()
    ]
    before = parse_quality(raw["pilot_baseline"])
    after = parse_quality(raw["pilot_after"])
    total = sum(row["Events"] for row in mutations)
    if total != count(raw["config"]["mutation_budget"]):
        raise ValueError("Inconsistent mutation budget")
    return {
        "mutations": mutations,
        "events": total,
        "selected_orders": count(raw["config"]["orders"]),
        "benchmark_digest": content_digest(raw["corrupted_content_sha256"]),
        "before": before["summary"],
        "after": after["summary"],
        "after_rows": after["rows"],
    }


def metrics(raw: dict) -> dict:
    tp, fp, fn = (count(raw[k]) for k in ("tp", "fp", "fn"))
    expected = {
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }
    for key, value in expected.items():
        supplied = raw[key]
        if value is None:
            if supplied is not None:
                raise ValueError("Undefined benchmark metric")
        elif not math.isclose(number(supplied), value, abs_tol=1e-9):
            raise ValueError("Inconsistent benchmark metric")
    return {"tp": tp, "fp": fp, "fn": fn, **expected}


def parse_evaluation(raw: dict) -> dict:
    overall = metrics(raw["finding_level"])
    if raw["provenance"]["content_digest_verified"] is not True:
        raise ValueError("Unverified evaluation source")
    for key, expected in (
        ("expected_obligations", overall["tp"] + overall["fn"]),
        ("new_predictions", overall["tp"] + overall["fp"]),
    ):
        if count(raw["finding_level"][key]) != expected:
            raise ValueError("Inconsistent evaluation denominator")
    event = raw["mutation_event_level"]
    total, detected = count(event["total"]), count(event["detected"])
    if detected > total:
        raise ValueError("Invalid event denominator")
    per_rule = []
    seen = set()
    for item in raw["per_rule"]:
        rule = item["rule_id"]
        if rule not in RULE_BY_ID or rule in seen:
            raise ValueError("Unknown or duplicate evaluation rule")
        seen.add(rule)
        result = metrics(item)
        per_rule.append(
            {
                "Rule": rule,
                "TP": result["tp"],
                "FP": result["fp"],
                "FN": result["fn"],
                **{
                    k.title(): percentage(result[k])
                    for k in ("precision", "recall", "f1")
                },
            }
        )
    if seen != set(RULE_BY_ID) or any(
        sum(row[k.upper()] for row in per_rule) != overall[k]
        for k in ("tp", "fp", "fn")
    ):
        raise ValueError("Inconsistent rule totals")
    return {
        "metrics": overall,
        "per_rule": per_rule,
        "events": total,
        "detected_events": detected,
        "benchmark_digest": content_digest(
            raw["provenance"]["corrupted_content_sha256"]
        ),
    }


def same(left: object, right: object) -> bool:
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def parse_ai(raw: dict, quality: Report, *, provider: str) -> dict:
    if (
        quality.data is None
        or raw["status"] != "completed"
        or raw["provider"] != provider
    ):
        raise ValueError("Unavailable source or provider")
    if not same(raw["evidence_groups"], quality.data["groups"]):
        raise ValueError("Evidence does not match current snapshot")
    if raw["source"]["input_sha256"] != quality.digest:
        raise ValueError("Source hash does not match current quality report")
    explanations = raw["explanations"]
    if len(explanations) != len(quality.data["groups"]):
        raise ValueError("Incomplete explanation coverage")
    safe = []
    for group_dict, explanation in zip(
        quality.data["groups"], explanations, strict=True
    ):
        group = EvidenceGroup(
            **(group_dict | {"columns": tuple(group_dict["columns"])})
        )
        catalog = catalog_for(group)
        fixed = {
            "group_id": group.group_id,
            "issue_summary": catalog.issue_summary,
            "evidence_based_explanation": describe_evidence(group),
            "confidence_limitations": list(CONFIDENCE_LIMITATIONS),
        }
        if any(not same(explanation[k], v) for k, v in fixed.items()):
            raise ValueError("Unreviewed explanation text")
        choices = {}
        options = {
            "possible_root_causes": [
                {"status": "hypothesis", "text": v} for v in catalog.hypotheses.values()
            ],
            "potential_business_impact": [
                {"status": "potential", "text": v} for v in catalog.impacts.values()
            ],
            "recommended_investigation_steps": list(catalog.investigations.values()),
        }
        for key, allowed in options.items():
            selected = explanation[key]
            if (
                not isinstance(selected, list)
                or not selected
                or any(item not in allowed for item in selected)
                or len({json.dumps(x, sort_keys=True) for x in selected})
                != len(selected)
            ):
                raise ValueError("Unreviewed explanation choice")
            choices[key] = selected
        safe.append(fixed | choices)
    result = {
        "explanations": safe,
        "provider": provider,
        "source_hash": quality.digest,
        "source_timestamp": quality.timestamp,
    }
    if provider == "openai":
        meta = raw["api_metadata"]
        usage = meta["usage"]
        models = {"gpt-4o-mini-2024-07-18"}
        if len(usage) != len(safe) or meta["requested_model"] not in models:
            raise ValueError("Unrecognized model or missing usage")
        if [u["group_id"] for u in usage] != [e["group_id"] for e in safe]:
            raise ValueError("Usage groups do not match")
        rates = meta["limits"]
        input_rate = number(rates["input_usd_per_million"])
        output_rate = number(rates["output_usd_per_million"])
        if not input_rate or not output_rate:
            raise ValueError("Missing cost basis")
        safe_usage = []
        for item in usage:
            if item["model"] not in models:
                raise ValueError("Unrecognized actual model")
            expected_cost = (
                count(item["input_tokens"]) * input_rate
                + count(item["output_tokens"]) * output_rate
            ) / 1_000_000
            if not math.isclose(
                number(item["estimated_cost_usd"]), expected_cost, abs_tol=1e-12
            ):
                raise ValueError("Inconsistent usage cost")
            safe_usage.append(
                {
                    "Group": item["group_id"],
                    "Model": item["model"],
                    "Input tokens": count(item["input_tokens"]),
                    "Output tokens": count(item["output_tokens"]),
                    "Estimated USD": number(item["estimated_cost_usd"]),
                }
            )
        requests = count(meta["requests_attempted"])
        if requests != len(usage):
            raise ValueError("Incomplete request usage")
        result.update(
            {
                "usage": safe_usage,
                "requests": requests,
                "estimated_cost": sum(x["Estimated USD"] for x in safe_usage),
            }
        )
    return result


def read_report(path: Path, parser, *, now: datetime | None = None) -> Report:
    """Errors deliberately omit raw data, parser exceptions and filesystem paths."""
    result = Report()
    try:
        content = path.read_bytes()
        raw = json.loads(content)
        result.data = parser(raw)
        if "publication" in raw:
            if raw["publication"].get("projection") != "portfolio-projection-v1":
                raise ValueError("Unrecognized public report projection")
            result.notices.append(
                "Privacy-projected portfolio snapshot; display surrogates replace "
                "private row locators. Historical metrics, not a fresh scan."
            )
        timestamp = datetime.fromisoformat(raw["completed_at_utc"])
        if timestamp.tzinfo is None:
            raise ValueError("Timestamp requires timezone")
        result.timestamp = timestamp.astimezone(UTC).isoformat()
        result.digest = hashlib.sha256(content).hexdigest()
        current = now or datetime.now(UTC)
        if timestamp > current + timedelta(minutes=5):
            result.notices.append(
                "Report timestamp is in the future; check provenance."
            )
        elif current - timestamp > timedelta(days=7):
            result.notices.append(
                "Snapshot is over seven days old; it is not live data."
            )
    except FileNotFoundError:
        return Report(error="Report missing. This view is unavailable.")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return Report(
            error="Report invalid, inconsistent or unreadable. View unavailable."
        )
    return result


def load_dashboard(directory: Path) -> dict[str, Report]:
    reports = {}
    for key, filename, parser in (
        ("ingestion", "ingestion.json", parse_ingestion),
        ("quality", "quality.json", parse_quality),
        ("experiment", "experiment.json", parse_experiment),
        ("evaluation", "evaluation.json", parse_evaluation),
    ):
        reports[key] = read_report(directory / filename, parser)
    for key, filename, provider in (
        ("offline", "explanations.json", "offline"),
        ("live", f"{LIVE_DIRECTORY}/explanations_openai.json", "openai"),
    ):
        reports[key] = read_report(
            directory / filename,
            lambda raw, p=provider: parse_ai(raw, reports["quality"], provider=p),
        )
    experiment, evaluation = reports["experiment"], reports["evaluation"]
    if experiment.data and evaluation.data:
        if (
            experiment.data["benchmark_digest"] != evaluation.data["benchmark_digest"]
            or experiment.data["events"] != evaluation.data["events"]
        ):
            reports["evaluation"] = Report(
                error="Evaluation does not match the saved pilot. View unavailable."
            )
    elif evaluation.data:
        evaluation.notices.append(
            "Pilot report unavailable; cross-report provenance cannot be checked."
        )
    return reports


def filter_findings(
    rows: list[dict],
    *,
    rules: list[str],
    severities: list[str],
    tables: list[str],
    query: str,
) -> list[dict]:
    query = query.strip().casefold()
    return [
        row
        for row in rows
        if row["Rule"] in rules
        and row["Severity"] in severities
        and row["Table"] in tables
        and query in " ".join(str(v) for v in row.values()).casefold()
    ]
