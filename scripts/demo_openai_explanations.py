"""Demonstrate the real SDK with an in-memory HTTP transport, never the network.

Run: uv run python scripts/demo_openai_explanations.py
The synthetic model returns the first reviewed option for each category.
"""

import json
from pathlib import Path

import httpx
from openai import OpenAI

from dataguard_ai.ai.explanations import explain_saved_report
from dataguard_ai.ai.openai_provider import OpenAIConfig, OpenAIProvider
from dataguard_ai.ai.reporting import write_explanation_reports


def mock_response(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    evidence = json.loads(payload["input"])
    result = evidence["fixed_fields"] | {
        name: [next(iter(options))]
        for name, options in evidence["reviewed_options"].items()
    }
    return httpx.Response(
        200,
        json={
            "id": "resp_mock",
            "object": "response",
            "created_at": 0,
            "model": payload["model"],
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
                            "text": json.dumps(result),
                            "annotations": [],
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 1000, "output_tokens": 350, "total_tokens": 1350},
        },
    )


def main() -> None:
    source = Path("reports/generated/quality.json")
    # The SDK has no network transport and no real credential. allow_paid=True
    # exercises the guard in this mock only; it is not permission for a live call.
    with httpx.Client(transport=httpx.MockTransport(mock_response)) as http_client:
        client = OpenAI(api_key="mock-only-no-real-key", http_client=http_client)
        provider = OpenAIProvider(OpenAIConfig(allow_paid=True), client=client)
        report = explain_saved_report(source, provider)
    report["api_metadata"]["demonstration"] = (
        "MOCK ONLY: synthetic responses and token usage; no API call or charge"
    )
    output = Path("reports/generated/openai_mock")
    write_explanation_reports(report, output, input_path=source)
    print(
        f"Mock only: {report['summary']['finding_count']} findings, "
        f"{report['summary']['group_count']} groups, {provider.requests} mock requests."
    )
    print(f"Reports: {output}/explanations_openai.json and explanations_openai.md")


if __name__ == "__main__":
    main()
