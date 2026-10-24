"""A small provider contract and strict validation of reviewed option choices."""

from typing import Protocol

from dataguard_ai.ai.catalog import catalog_for
from dataguard_ai.ai.grouping import EvidenceGroup

Choices = dict[str, list[str]]


class ExplanationProvider(Protocol):
    name: str

    def explain(self, group: EvidenceGroup) -> Choices:
        """Select reviewed interpretation IDs for aggregate evidence."""
        ...


def allowed_choices(group: EvidenceGroup) -> dict[str, dict[str, str]]:
    catalog = catalog_for(group)
    return {
        "hypothesis_ids": catalog.hypotheses,
        "impact_ids": catalog.impacts,
        "investigation_ids": catalog.investigations,
    }


def selection_schema(group: EvidenceGroup) -> dict:
    """JSON Schema for an optional model response; no arbitrary prose fields."""
    options = allowed_choices(group)
    return {
        "type": "object",
        "properties": {
            name: {
                "type": "array",
                "items": {"type": "string", "enum": list(values)},
                "minItems": 1,
                "maxItems": len(values),
            }
            for name, values in options.items()
        },
        "required": list(options),
        "additionalProperties": False,
    }


def validate_choices(value: object, group: EvidenceGroup) -> Choices:
    """Validate locally even when a remote provider promises schema adherence."""
    options = allowed_choices(group)
    if not isinstance(value, dict) or set(value) != set(options):
        raise ValueError("Explanation choices have missing or unsupported fields")
    for name, allowed in options.items():
        ids = value[name]
        if not isinstance(ids, list) or not 1 <= len(ids) <= len(allowed):
            raise ValueError("Each explanation choice must be a nonempty bounded list")
        if any(not isinstance(item, str) or item not in allowed for item in ids):
            raise ValueError("Explanation contains an unsupported claim or choice")
        if len(set(ids)) != len(ids):
            raise ValueError("Explanation choices must not repeat")
    return {name: list(value[name]) for name in options}


class OfflineProvider:
    """Deterministic catalog selection: no model, network or API key."""

    name = "offline"

    def explain(self, group: EvidenceGroup) -> Choices:
        return {name: list(values) for name, values in allowed_choices(group).items()}
