"""Synthetic evidence, imperfect input and no-network explanation contracts."""

import json
import subprocess
import sysconfig
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest

from dataguard_ai.ai.explanations import explain_findings, explain_saved_report
from dataguard_ai.ai.grouping import group_findings
from dataguard_ai.ai.openai_provider import OpenAIProvider, response_schema
from dataguard_ai.ai.providers import (
    OfflineProvider,
    validate_choices,
)
from dataguard_ai.ai.reporting import render_explanations, write_explanation_reports

PRIVATE = "private-customer@example.invalid"
RAW_VALUE = "PRIVATE_SOURCE_VALUE do not send this text"


def finding(
    rule: str = "DQ007",
    *,
    table: str = "orders",
    locator: str | None = "(0,1)",
    severity: str = "error",
    evidence: dict | None = None,
) -> dict:
    return {
        "rule_id": rule,
        "table": f"raw.{table}",
        "severity": severity,
        "classification": "confirmed_violation" if severity == "error" else "warning",
        "row_identifier": {"snapshot_ctid": locator, "order_id": PRIVATE},
        "evidence": evidence
        if evidence is not None
        else {
            "earlier_column": "order_purchase_timestamp",
            "later_column": "order_delivered_carrier_date",
            "earlier_value": RAW_VALUE,
            "later_value": "PRIVATE_TIMESTAMP",
            "reason": "later_event_precedes_earlier",
        },
    }


def saved_report(findings: list) -> dict:
    return {
        "status": "completed",
        "completed_at_utc": "2026-10-19T12:00:00+00:00",
        "findings": findings,
        "summary": {
            "finding_count": len(findings),
            "confirmed_violations": sum(f["severity"] == "error" for f in findings),
            "warnings": sum(f["severity"] == "warning" for f in findings),
        },
        "observations": [{"optional_review_comment": PRIVATE}],
        "arbitrary_metadata": RAW_VALUE,
    }


def test_grouping_counts_findings_and_distinct_snapshot_rows_separately() -> None:
    first = finding()
    second = finding(locator="(0,2)")
    second["evidence"]["later_value"] = "DIFFERENT_PRIVATE_VALUE"
    inputs = [first, second, deepcopy(first)]
    before = deepcopy(inputs)
    groups = group_findings(inputs)
    assert len(groups) == 1
    assert groups[0].finding_count == 3
    assert groups[0].affected_row_count == 2
    assert group_findings(list(reversed(inputs))) == groups
    assert inputs == before
    assert PRIVATE not in json.dumps([asdict(group) for group in groups])


def test_chronology_pairs_and_timezone_warnings_are_separate_groups() -> None:
    other_pair = finding(
        evidence={
            "earlier_column": "order_delivered_carrier_date",
            "later_column": "order_delivered_customer_date",
            "reason": "later_event_precedes_earlier",
        }
    )
    timezone = finding(
        severity="warning",
        evidence={
            "earlier_column": "order_purchase_timestamp",
            "later_column": "order_delivered_carrier_date",
            "reason": "mixed_timezone_context",
        },
    )
    report = explain_findings([finding(), other_pair, timezone], OfflineProvider())
    assert report["summary"] == {
        "finding_count": 3,
        "confirmed_violations": 2,
        "warnings": 1,
        "group_count": 3,
    }
    assert all(group["affected_row_count"] == 1 for group in report["evidence_groups"])
    mixed = next(
        item
        for item in report["explanations"]
        if "timezone awareness" in item["issue_summary"]
    )
    assert "does not claim" in mixed["evidence_based_explanation"]


def test_missing_locators_are_unknown_not_guessed_from_business_keys() -> None:
    groups = group_findings([finding(locator=None), finding()])
    assert groups[0].finding_count == 2
    assert groups[0].affected_row_count is None
    item = finding()
    item["table"] = "experiment_unit.orders"
    item["row_identifier"] = {
        "experiment_row_id": "stable:1",
        "snapshot_ctid": "untrusted",
    }
    assert group_findings([item, deepcopy(item)])[0].affected_row_count == 1


@pytest.mark.parametrize(
    ("rule", "table", "severity", "evidence", "expected_pattern"),
    [
        (
            "DQ001",
            "customers",
            "error",
            {"column": "customer_id", "value": None},
            "required_identifier_missing",
        ),
        (
            "DQ002",
            "order_items",
            "error",
            {"key_columns": ["order_id", "order_item_id"], "group_size": 2},
            "duplicate_candidate_key",
        ),
        (
            "DQ003",
            "order_items",
            "error",
            {"column": "price", "value": RAW_VALUE},
            "invalid_monetary_text",
        ),
        (
            "DQ004",
            "order_payments",
            "error",
            {"column": "payment_value", "value": "-1"},
            "negative_amount",
        ),
        (
            "DQ005",
            "orders",
            "error",
            {
                "column": "customer_id",
                "value": PRIVATE,
                "parent": "raw.customers.customer_id",
            },
            "missing_parent_reference",
        ),
        (
            "DQ006",
            "order_reviews",
            "error",
            {"column": "review_creation_date", "value": RAW_VALUE},
            "invalid_timestamp_text",
        ),
        (
            "DQ008",
            "orders",
            "warning",
            {
                "column": "order_delivered_customer_date",
                "reason": "expected_event_not_provided",
                "order_status": "delivered",
            },
            "delivered_event_not_provided",
        ),
        (
            "DQ008",
            "orders",
            "warning",
            {
                "column": "order_purchase_timestamp",
                "reason": "expected_event_not_provided",
                "order_status": "processing",
            },
            "purchase_event_not_provided",
        ),
        (
            "DQ008",
            "order_items",
            "warning",
            {"column": "price", "reason": "amount_not_provided"},
            "amount_not_provided",
        ),
    ],
)
def test_catalog_covers_engine_contracts(
    rule: str, table: str, severity: str, evidence: dict, expected_pattern: str
) -> None:
    report = explain_findings(
        [finding(rule, table=table, severity=severity, evidence=evidence)],
        OfflineProvider(),
    )
    assert report["evidence_groups"][0]["pattern"] == expected_pattern
    interpretation = report["explanations"][0]
    assert all(
        cause["status"] == "hypothesis"
        for cause in interpretation["possible_root_causes"]
    )
    assert all(
        impact["status"] == "potential"
        for impact in interpretation["potential_business_impact"]
    )
    serialized = json.dumps(report)
    assert PRIVATE not in serialized and RAW_VALUE not in serialized
    if expected_pattern == "delivered_event_not_provided":
        assert (
            "does not prove that delivery did not occur"
            in interpretation["evidence_based_explanation"]
        )


@pytest.mark.parametrize(
    "change",
    [
        "rule",
        "table",
        "column",
        "reason",
        "severity",
        "classification",
        "parent",
        "mixed_schema",
    ],
)
def test_unknown_patterns_fail_without_echoing_private_input(change: str) -> None:
    item = finding()
    items = [item]
    if change == "rule":
        item["rule_id"] = PRIVATE
    elif change == "table":
        item["table"] = f"raw.{PRIVATE}"
    elif change == "column":
        item["evidence"]["later_column"] = PRIVATE
    elif change == "reason":
        item["evidence"]["reason"] = PRIVATE
    elif change == "severity":
        item["severity"] = "warning"
        item["classification"] = "warning"
    elif change == "classification":
        item["classification"] = PRIVATE
    elif change == "parent":
        items = [
            finding("DQ005", evidence={"column": "customer_id", "parent": PRIVATE})
        ]
    else:
        other = deepcopy(item)
        other["table"] = "experiment_unit.orders"
        items.append(other)
    with pytest.raises(ValueError) as error:
        explain_findings(items, OfflineProvider())
    assert PRIVATE not in str(error.value)


@pytest.mark.parametrize(
    "change",
    [
        "extra_count",
        "loss",
        "confirmed_cause",
        "unknown_id",
        "missing",
        "empty",
        "duplicate",
        "wrong_type",
    ],
)
def test_schema_rejects_unsupported_claims_and_malformed_output(change: str) -> None:
    group = group_findings([finding()])[0]
    choices = OfflineProvider().explain(group)
    if change == "extra_count":
        choices["affected_rows"] = 999
    elif change == "loss":
        choices["monetary_loss"] = "BRL 100000"
    elif change == "confirmed_cause":
        choices["confirmed_root_cause"] = "The warehouse caused the issue."
    elif change == "unknown_id":
        choices["hypothesis_ids"] = ["The warehouse definitely caused the issue."]
    elif change == "missing":
        del choices["impact_ids"]
    elif change == "empty":
        choices["hypothesis_ids"] = []
    elif change == "duplicate":
        choices["hypothesis_ids"] = ["capture_error", "capture_error"]
    else:
        choices["impact_ids"] = "duration_risk"
    with pytest.raises(ValueError):
        validate_choices(choices, group)


def test_provider_can_select_subset_but_cannot_author_evidence() -> None:
    class SubsetProvider:
        name = "test"

        def explain(self, group: object) -> dict:
            return {
                "hypothesis_ids": ["clock_context"],
                "impact_ids": ["duration_risk"],
                "investigation_ids": ["review_clocks"],
            }

    report = explain_findings([finding(), finding(locator="(0,2)")], SubsetProvider())
    assert report["evidence_groups"][0]["affected_row_count"] == 2
    assert len(report["explanations"][0]["possible_root_causes"]) == 1


def test_openai_preparation_is_aggregate_only_and_execution_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Explanation must not access the network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder-never-used")
    group = group_findings([finding()])[0]
    provider = OpenAIProvider()
    request = provider.prepare_request(group, model="example-model-not-called")
    assert request["store"] is False
    assert request["text"]["format"]["schema"] == response_schema(group)
    assert request["text"]["format"]["strict"] is True
    aggregate = json.loads(request["input"])["aggregate_evidence"]
    assert aggregate["finding_count"] == aggregate["affected_row_count"] == 1
    assert set(aggregate) == set(asdict(group))
    for private in (
        PRIVATE,
        RAW_VALUE,
        "PRIVATE_TIMESTAMP",
        "snapshot_ctid",
        "test-placeholder-never-used",
    ):
        assert private not in json.dumps(request)
    with pytest.raises(ValueError, match="disabled"):
        provider.explain(group)
    with pytest.raises(ValueError, match="explicit model"):
        provider.prepare_request(group, model=" ")


def test_saved_report_preserves_input_and_json_markdown_consistency(
    tmp_path: Path,
) -> None:
    source = tmp_path / "quality.json"
    source.write_text(json.dumps(saved_report([finding(), finding(locator="(0,2)")])))
    original = source.read_bytes()
    report = explain_saved_report(source, OfflineProvider())
    output = tmp_path / "output"
    write_explanation_reports(report, output, input_path=source)
    assert source.read_bytes() == original
    persisted = json.loads((output / "explanations.json").read_text())
    assert persisted == json.loads(json.dumps(report))
    markdown = (output / "explanations.md").read_text()
    assert "2 findings: 2 rule violations and 0 warnings; 1 groups." in markdown
    assert "group_001" in markdown and "Hypothesis:" in markdown
    assert (
        report["source"]["validation_completed_at_utc"] == "2026-10-19T12:00:00+00:00"
    )
    assert len(report["source"]["input_sha256"]) == 64
    assert len(persisted["explanations"]) == len(persisted["evidence_groups"])
    assert PRIVATE not in markdown + json.dumps(persisted)
    assert RAW_VALUE not in markdown + json.dumps(persisted)
    with pytest.raises(ValueError, match="outside data/raw"):
        write_explanation_reports(
            report, Path("data/raw/explanation-test"), input_path=source
        )
    with pytest.raises(ValueError, match="overwrite"):
        write_explanation_reports(
            report, output, input_path=output / "explanations.json"
        )


@pytest.mark.parametrize("change", ["incomplete", "summary", "findings", "timestamp"])
def test_bad_input_report_is_rejected(tmp_path: Path, change: str) -> None:
    source = saved_report([finding()])
    if change == "incomplete":
        source["status"] = "failed"
    elif change == "summary":
        source["summary"]["finding_count"] = 999
    elif change == "findings":
        source["findings"] = {}
    else:
        source["completed_at_utc"] = PRIVATE
    path = tmp_path / "quality.json"
    path.write_text(json.dumps(source))
    with pytest.raises(ValueError):
        explain_saved_report(path, OfflineProvider())


def test_empty_negative_control_does_not_create_issues_from_observations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "quality.json"
    path.write_text(json.dumps(saved_report([])))
    report = explain_saved_report(path, OfflineProvider())
    assert report["summary"]["finding_count"] == report["summary"]["group_count"] == 0
    assert report["evidence_groups"] == report["explanations"] == []
    assert "0 findings" in render_explanations(report)


def test_cli_offline_and_openai_fail_closed_without_database(tmp_path: Path) -> None:
    source = tmp_path / "quality.json"
    source.write_text(json.dumps(saved_report([finding()])))
    executable = Path(sysconfig.get_path("scripts")) / "dataguard-ai"
    offline = subprocess.run(
        [
            str(executable),
            "explain",
            "--provider",
            "offline",
            "--input",
            str(source),
            "--output-dir",
            str(tmp_path / "reports"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Explained 1 findings in 1 groups" in offline.stdout
    blocked = subprocess.run(
        [
            str(executable),
            "explain",
            "--provider",
            "openai",
            "--input",
            str(source),
            "--output-dir",
            str(tmp_path / "blocked"),
        ],
        capture_output=True,
        text=True,
    )
    assert blocked.returncode == 1 and "disabled" in blocked.stderr
    assert not (tmp_path / "blocked").exists()
