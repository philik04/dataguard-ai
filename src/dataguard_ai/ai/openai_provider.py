"""Opt-in Responses API adapter: reviewed selections, never unrestricted prose."""

import json
import math
import os
from dataclasses import asdict, dataclass
from typing import Any

from dataguard_ai.ai.catalog import (
    CONFIDENCE_LIMITATIONS,
    catalog_for,
    describe_evidence,
)
from dataguard_ai.ai.grouping import EvidenceGroup
from dataguard_ai.ai.providers import (
    Choices,
    allowed_choices,
    selection_schema,
    validate_choices,
)


@dataclass(frozen=True)
class OpenAIConfig:
    model: str = "gpt-4o-mini-2024-07-18"
    allow_paid: bool = False
    timeout_seconds: float = 30
    max_output_tokens: int = 1200
    max_input_tokens: int = 16000
    max_requests: int = 3
    max_cost_usd: float = 0.10
    # User-supplied conservative rate ceilings, NOT a current pricing quotation.
    input_usd_per_million: float = 1.0
    output_usd_per_million: float = 4.0

    def __post_init__(self) -> None:
        if not self.model.strip() or len(self.model) > 100:
            raise ValueError("Invalid model configuration")
        for value in (
            self.timeout_seconds,
            self.max_cost_usd,
            self.input_usd_per_million,
            self.output_usd_per_million,
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Timeout, budget and rate ceilings must be positive")
        for value in (
            self.max_output_tokens,
            self.max_input_tokens,
            self.max_requests,
        ):
            if type(value) is not int or value <= 0:
                raise ValueError("Token and request limits must be positive integers")


def fixed_fields(group: EvidenceGroup) -> dict:
    """Only these exact values can be returned alongside reviewed choice IDs."""
    return {
        "summary": catalog_for(group).issue_summary,
        "evidence_based_explanation": describe_evidence(group),
        "evidence": json.loads(json.dumps(asdict(group))),
        "confidence_limitations": list(CONFIDENCE_LIMITATIONS),
    }


def response_schema(group: EvidenceGroup) -> dict:
    schema = selection_schema(group)
    for name, value in fixed_fields(group).items():
        # Scalar enums freeze facts; bounded array choices constrain content.
        # Local equality additionally checks array ordering and exact contents.
        if isinstance(value, dict):
            properties = {}
            for key, item in value.items():
                kind = (
                    "null"
                    if item is None
                    else "integer"
                    if type(item) is int
                    else "array"
                    if isinstance(item, list)
                    else "string"
                )
                properties[key] = {"type": kind, "enum": [item]}
                if kind == "array":
                    properties[key] = {
                        "type": "array",
                        "items": {"type": "string", "enum": item},
                        "minItems": len(item),
                        "maxItems": len(item),
                    }
            field = {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            }
        elif isinstance(value, list):
            field = {
                "type": "array",
                "items": {"type": "string", "enum": value},
                "minItems": len(value),
                "maxItems": len(value),
            }
        else:
            field = {"type": "string", "enum": [value]}
        schema["properties"][name] = field
    schema["required"] = list(schema["properties"])
    return schema


def validate_response(value: object, group: EvidenceGroup) -> Choices:
    fixed = fixed_fields(group)
    expected = set(fixed) | set(allowed_choices(group))
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("OpenAI output has missing or unsupported fields")
    # Serialized comparison also distinguishes booleans/floats from integer counts.
    for name, item in fixed.items():
        if json.dumps(value[name], sort_keys=True) != json.dumps(item, sort_keys=True):
            raise ValueError("OpenAI output changed evidence or introduced a claim")
    return validate_choices({key: value[key] for key in allowed_choices(group)}, group)


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON fields")
        result[key] = value
    return result


class OpenAIProvider:
    name = "openai"

    def __init__(self, config: OpenAIConfig | None = None, *, client: Any = None):
        self.config = config or OpenAIConfig()
        self._client = client
        self.requests = 0
        self.reserved_cost_usd = 0.0
        self.usage: list[dict] = []

    def prepare_request(
        self, group: EvidenceGroup, *, model: str | None = None
    ) -> dict:
        selected_model = self.config.model if model is None else model
        if not selected_model.strip():
            raise ValueError("Request preparation requires an explicit model")
        return {
            "model": selected_model,
            "store": False,
            "max_output_tokens": self.config.max_output_tokens,
            "instructions": (
                "Explain only the supplied aggregate evidence by selecting relevant "
                "reviewed option IDs. Copy all fixed fields exactly. Causes are "
                "unconfirmed hypotheses; impacts are potential and unmeasured. "
                "Do not introduce facts, confirmed causes or financial losses."
            ),
            "input": json.dumps(
                {
                    "aggregate_evidence": asdict(group),
                    "fixed_fields": fixed_fields(group),
                    "reviewed_options": allowed_choices(group),
                },
                sort_keys=True,
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "explanation_choices",
                    "strict": True,
                    "schema": response_schema(group),
                }
            },
        }

    def _reservation(self, request: dict) -> float:
        # UTF-8 byte count + framing allowance is deliberately conservative for
        # this text-only request, not an exact server tokenizer measurement.
        tokens = len(json.dumps(request, ensure_ascii=False).encode()) + 4096
        if tokens > self.config.max_input_tokens:
            raise ValueError("OpenAI input token budget exceeded before request")
        return (
            tokens * self.config.input_usd_per_million
            + self.config.max_output_tokens * self.config.output_usd_per_million
        ) / 1_000_000

    def preflight(self, groups: list[EvidenceGroup]) -> None:
        """Reject an unaffordable whole report before paying for its first group."""
        if not self.config.allow_paid:
            raise ValueError(
                "OpenAI calls disabled; explicit --allow-paid-api required"
            )
        costs = [self._reservation(self.prepare_request(group)) for group in groups]
        if self.requests + len(groups) > self.config.max_requests:
            raise ValueError("OpenAI request budget exceeded before request")
        if self.reserved_cost_usd + sum(costs) > self.config.max_cost_usd:
            raise ValueError("OpenAI cost budget exceeded before request")

    def explain(self, group: EvidenceGroup) -> Choices:
        self.preflight([group])
        request = self.prepare_request(group)
        if self._client is None:
            from openai import OpenAI

            key = os.environ.get("OPENAI_API_KEY")
            if not key or key == "replace_me":
                raise ValueError("OPENAI_API_KEY must be configured locally")
            # Fixed official endpoint; do not inherit OPENAI_BASE_URL overrides.
            self._client = OpenAI(
                api_key=key,
                base_url="https://api.openai.com/v1",
                timeout=self.config.timeout_seconds,
                max_retries=0,
            )
        # Reserve before dispatch, retaining reservation on timeout/invalid output:
        # a failed or ambiguous request may still have incurred a charge.
        self.requests += 1
        self.reserved_cost_usd += self._reservation(request)
        try:
            response = self._client.with_options(
                max_retries=0,
                timeout=self.config.timeout_seconds,
            ).responses.create(**request)
            if response.status != "completed" or not response.output_text:
                raise ValueError("Incomplete or refused response")
            if response.model != self.config.model:
                raise ValueError("Unexpected model; use an explicit model snapshot")
            usage = response.usage
            if (
                usage is None
                or type(usage.input_tokens) is not int
                or type(usage.output_tokens) is not int
                or usage.input_tokens < 0
                or usage.output_tokens < 0
            ):
                raise ValueError("Missing or invalid usage")
            self.usage.append(
                {
                    "group_id": group.group_id,
                    "model": response.model,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "estimated_cost_usd": (
                        usage.input_tokens * self.config.input_usd_per_million
                        + usage.output_tokens * self.config.output_usd_per_million
                    )
                    / 1_000_000,
                }
            )
            if len(response.output_text.encode()) > self.config.max_output_tokens * 16:
                raise ValueError("Oversized response")
            value = json.loads(response.output_text, object_pairs_hook=_unique_object)
            return validate_response(value, group)
        except Exception:
            # Never echo SDK bodies, model prose, request details or credentials.
            raise ValueError(
                "OpenAI request or output validation failed; no retry performed. "
                "The request may have been charged."
            ) from None

    def metadata(self) -> dict:
        return {
            "requested_model": self.config.model,
            "requests_attempted": self.requests,
            "reserved_cost_usd": self.reserved_cost_usd,
            "limits": asdict(self.config) | {"allow_paid": self.config.allow_paid},
            "usage": list(self.usage),
            "cost_basis": "Configured rate ceilings; estimates, not billing records",
        }
