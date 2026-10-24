"""Assemble structured explanations without letting providers author evidence."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from dataguard_ai.ai.catalog import (
    CONFIDENCE_LIMITATIONS,
    catalog_for,
    describe_evidence,
)
from dataguard_ai.ai.grouping import group_findings
from dataguard_ai.ai.providers import (
    ExplanationProvider,
    validate_choices,
)


@dataclass(frozen=True)
class Explanation:
    group_id: str
    issue_summary: str
    evidence_based_explanation: str
    possible_root_causes: tuple[dict[str, str], ...]
    potential_business_impact: tuple[dict[str, str], ...]
    recommended_investigation_steps: tuple[str, ...]
    confidence_limitations: tuple[str, ...]


def explain_findings(findings: list[dict], provider: ExplanationProvider) -> dict:
    """Public interface: local findings in, aggregate evidence/interpretations out."""
    groups = group_findings(findings)
    if hasattr(provider, "preflight"):
        provider.preflight(groups)
    explanations = []
    for group in groups:
        choices = validate_choices(provider.explain(group), group)
        catalog = catalog_for(group)
        explanations.append(
            Explanation(
                group_id=group.group_id,
                issue_summary=catalog.issue_summary,
                evidence_based_explanation=describe_evidence(group),
                possible_root_causes=tuple(
                    {"status": "hypothesis", "text": catalog.hypotheses[key]}
                    for key in choices["hypothesis_ids"]
                ),
                potential_business_impact=tuple(
                    {"status": "potential", "text": catalog.impacts[key]}
                    for key in choices["impact_ids"]
                ),
                recommended_investigation_steps=tuple(
                    catalog.investigations[key] for key in choices["investigation_ids"]
                ),
                confidence_limitations=CONFIDENCE_LIMITATIONS,
            )
        )
    report = {
        "phase": "4A — AI Explanation Foundation",
        "schema_version": "explanation-v1",
        "catalog_version": "reviewed-catalog-v1",
        "status": "completed",
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "provider": provider.name,
        "generation_mode": "reviewed_catalog_selection",
        "summary": {
            "finding_count": len(findings),
            "confirmed_violations": sum(f["severity"] == "error" for f in findings),
            "warnings": sum(f["severity"] == "warning" for f in findings),
            "group_count": len(groups),
        },
        "counting_policy": (
            "Finding counts preserve repeated results. Affected-row "
            "counts are distinct "
            "within each group only; null means unavailable. Groups may overlap."
        ),
        "field_origins": {
            "evidence_groups": (
                "Deterministic grouping and counts from supplied findings."
            ),
            "issue_summary": "Reviewed rule template, not provider-authored prose.",
            "evidence_based_explanation": (
                "Reviewed template using allowlisted evidence patterns."
            ),
            "possible_root_causes": (
                "Provider-selected reviewed hypotheses; unconfirmed."
            ),
            "potential_business_impact": (
                "Provider-selected reviewed potential effects; unmeasured."
            ),
            "recommended_investigation_steps": (
                "Provider-selected reviewed investigation guidance."
            ),
            "confidence_limitations": (
                "Fixed application limitations; no confidence score."
            ),
        },
        "evidence_groups": [asdict(group) for group in groups],
        "explanations": [asdict(explanation) for explanation in explanations],
    }
    if provider.name == "openai":
        report["phase"] = "4B — OpenAI Explanation Provider"
        report["api_metadata"] = provider.metadata()
    return report


def explain_saved_report(path: Path, provider: ExplanationProvider) -> dict:
    """Read a completed quality report; never access the database or alter input."""
    content = path.read_bytes()
    source = json.loads(content)
    if not isinstance(source, dict) or source.get("status") != "completed":
        raise ValueError("Input must be a completed quality report")
    findings = source.get("findings")
    if not isinstance(findings, list):
        raise ValueError("Quality report findings must be a list")
    try:
        completed = datetime.fromisoformat(source["completed_at_utc"]).isoformat()
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "Quality report requires a valid completion timestamp"
        ) from error
    # Validate the entire saved input before any external request.
    group_findings(findings)
    # Recompute counts and reject inconsistent reports instead of repeating a claim.
    expected_summary = {
        "finding_count": len(findings),
        "confirmed_violations": sum(f["severity"] == "error" for f in findings),
        "warnings": sum(f["severity"] == "warning" for f in findings),
    }
    if source.get("summary") != expected_summary:
        raise ValueError("Quality report summary disagrees with its findings")
    report = explain_findings(findings, provider)
    report["source"] = {
        "kind": "saved_quality_report",
        "input_sha256": hashlib.sha256(content).hexdigest(),
        "validation_completed_at_utc": completed,
    }
    return report
