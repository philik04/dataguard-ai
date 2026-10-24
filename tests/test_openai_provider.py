"""Mock the HTTP boundary of the real SDK; no API key or network is used."""

import json
from dataclasses import replace

import httpx
import pytest
from openai import OpenAI
from test_explanations import PRIVATE, RAW_VALUE, finding, saved_report

from dataguard_ai.ai.explanations import explain_findings, explain_saved_report
from dataguard_ai.ai.grouping import group_findings
from dataguard_ai.ai.openai_provider import OpenAIConfig, OpenAIProvider, fixed_fields
from dataguard_ai.ai.providers import OfflineProvider
from dataguard_ai.ai.reporting import write_explanation_reports


def mocked_provider(config=None, change=None, failure=None):
    config = config or OpenAIConfig(allow_paid=True)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        assert PRIVATE not in request.content.decode()
        assert RAW_VALUE not in request.content.decode()
        if failure == "timeout":
            raise httpx.ReadTimeout("SECRET KEY must never appear", request=request)
        if failure == "http":
            return httpx.Response(500, json={"error": {"message": "SECRET KEY"}})
        data = json.loads(requests[-1]["input"])
        group = group_findings([finding()])[0]
        value = fixed_fields(group) | OfflineProvider().explain(group)
        if change:
            change(value)
        body = {
            "id": "resp_mock",
            "object": "response",
            "created_at": 0,
            "model": config.model,
            "status": "completed",
            "output": [
                {
                    "id": "msg_mock",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "annotations": [],
                            "text": json.dumps(value),
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 600, "output_tokens": 250, "total_tokens": 850},
        }
        if failure == "incomplete":
            body["status"] = "incomplete"
        elif failure == "refusal":
            body["output"][0]["content"] = [
                {"type": "refusal", "refusal": "Cannot comply"}
            ]
        elif failure == "malformed":
            body["output"][0]["content"][0]["text"] = "not JSON"
        elif failure == "duplicate":
            body["output"][0]["content"][0]["text"] = '{"summary":1,"summary":2}'
        assert data["aggregate_evidence"]["finding_count"] == 1
        return httpx.Response(200, json=body)

    client = OpenAI(
        api_key="mock-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return OpenAIProvider(config, client=client), requests


def test_success_real_sdk_mock_transport_and_separate_reports(tmp_path):
    provider, requests = mocked_provider()
    report = explain_findings([finding()], provider)
    assert len(requests) == 1
    assert requests[0]["store"] is False
    assert requests[0]["max_output_tokens"] == 1200
    assert requests[0]["text"]["format"]["strict"] is True
    assert report["api_metadata"]["usage"][0]["input_tokens"] == 600
    assert report["api_metadata"]["requests_attempted"] == 1
    assert report["evidence_groups"][0]["rule_id"] == "DQ007"
    assert (
        report["explanations"]
        == explain_findings([finding()], OfflineProvider())["explanations"]
    )
    write_explanation_reports(report, tmp_path, input_path=tmp_path / "quality.json")
    assert (tmp_path / "explanations_openai.json").exists()
    assert "600" in (tmp_path / "explanations_openai.md").read_text()
    assert not (tmp_path / "explanations.json").exists()


@pytest.mark.parametrize(
    "failure",
    [
        "timeout",
        "http",
        "incomplete",
        "refusal",
        "malformed",
        "duplicate",
    ],
)
def test_failure_is_sanitized_never_retried_or_silently_falls_back(failure):
    provider, requests = mocked_provider(failure=failure)
    with pytest.raises(ValueError, match="no retry") as error:
        explain_findings([finding()], provider)
    assert "SECRET" not in str(error.value)
    assert error.value.__suppress_context__
    assert len(requests) == provider.requests == 1
    assert provider.reserved_cost_usd > 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("summary", "The warehouse caused this. Confirmed."),
        ("evidence_based_explanation", "Financial loss measured at $10000"),
        ("hypothesis_ids", ["The supplier is responsible"]),
        ("confidence_limitations", ["There is no uncertainty"]),
        ("extra", "Unprovided fact"),
        ("impact_ids", []),
        ("investigation_ids", "review_clocks"),
    ],
)
def test_unsupported_claims_and_malformed_fields(field, value):
    provider, requests = mocked_provider(change=lambda obj: obj.update({field: value}))
    with pytest.raises(ValueError, match="validation failed"):
        explain_findings([finding()], provider)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("rule_id", "DQ001"),
        ("severity", "warning"),
        ("finding_count", 999),
        ("affected_row_count", True),
        ("columns", ["customer_id"]),
    ],
)
def test_evidence_cannot_change(field, value):
    provider, _ = mocked_provider(
        change=lambda obj: obj["evidence"].update({field: value})
    )
    with pytest.raises(ValueError):
        explain_findings([finding()], provider)


@pytest.mark.parametrize(
    "config",
    [
        OpenAIConfig(),
        OpenAIConfig(allow_paid=True, max_cost_usd=0.000001),
        OpenAIConfig(allow_paid=True, max_input_tokens=1),
    ],
)
def test_preflight_blocks_before_transport(config):
    provider, requests = mocked_provider(config)
    with pytest.raises(ValueError):
        explain_findings([finding()], provider)
    assert not requests
    assert provider.requests == 0


def test_request_budget_is_shared_and_failures_keep_reservation():
    config = OpenAIConfig(allow_paid=True, max_requests=1)
    provider, requests = mocked_provider(config, failure="timeout")
    with pytest.raises(ValueError):
        provider.explain(group_findings([finding()])[0])
    with pytest.raises(ValueError, match="request budget"):
        provider.explain(group_findings([finding()])[0])
    assert len(requests) == 1


def test_entire_report_preflight_and_invalid_source_precede_transport(tmp_path):
    provider, requests = mocked_provider(OpenAIConfig(allow_paid=True, max_requests=1))
    second = finding(
        evidence={
            "earlier_column": "order_delivered_carrier_date",
            "later_column": "order_delivered_customer_date",
            "reason": "later_event_precedes_earlier",
        }
    )
    with pytest.raises(ValueError, match="request budget"):
        explain_findings([finding(), second], provider)
    source = saved_report([finding()])
    source["summary"]["finding_count"] = 999
    path = tmp_path / "quality.json"
    path.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="summary disagrees"):
        explain_saved_report(path, provider)
    assert not requests


@pytest.mark.parametrize(
    "changes",
    [
        {"max_requests": 0},
        {"timeout_seconds": float("nan")},
        {"max_cost_usd": float("inf")},
        {"input_usd_per_million": -1},
        {"max_output_tokens": True},
        {"model": " "},
    ],
)
def test_invalid_config(changes):
    with pytest.raises(ValueError):
        replace(OpenAIConfig(), **changes)


def test_cli_explicit_fallback(monkeypatch, tmp_path, capsys):
    from dataguard_ai.cli import main

    provider, requests = mocked_provider(failure="timeout")
    monkeypatch.setattr(
        "dataguard_ai.ai.openai_provider.OpenAIProvider", lambda config: provider
    )
    path = tmp_path / "quality.json"
    path.write_text(json.dumps(saved_report([finding()])))
    monkeypatch.setattr(
        "sys.argv",
        [
            "dataguard-ai",
            "explain",
            "--provider",
            "openai",
            "--allow-paid-api",
            "--fallback-offline",
            "--input",
            str(path),
            "--output-dir",
            str(tmp_path),
        ],
    )
    main()
    assert len(requests) == 1
    result = json.loads((tmp_path / "explanations_openai_fallback.json").read_text())
    assert result["provider"] == "offline" and "fallback" in result
    assert result["api_metadata"]["requests_attempted"] == 1
    assert "may have incurred cost" in capsys.readouterr().out
