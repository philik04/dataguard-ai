"""Reviewed possibilities, never confirmed causes or estimated monetary losses."""

from dataclasses import dataclass

from dataguard_ai.ai.grouping import EvidenceGroup


@dataclass(frozen=True)
class Catalog:
    issue_summary: str
    hypotheses: dict[str, str]
    impacts: dict[str, str]
    investigations: dict[str, str]


CATALOGS = {
    "DQ001": Catalog(
        "A required identifier is missing or blank.",
        {
            "export_gap": "An upstream export may have omitted an identifier.",
            "mapping_gap": "A source mapping may have left an identifier blank.",
        },
        {
            "join_risk": (
                "Missing identifiers could prevent reliable joins or record tracing."
            )
        },
        {
            "inspect_source": (
                "Compare flagged fields with authorized source extracts locally."
            ),
            "review_mapping": "Review identifier mappings and missing-value handling.",
        },
    ),
    "DQ002": Catalog(
        "A complete candidate key appears on multiple rows.",
        {
            "repeated_load": (
                "A source export or loading process may have repeated records."
            ),
            "key_assumption": (
                "The candidate-key assumption may need additional business context."
            ),
        },
        {
            "count_risk": (
                "Repeated keys could multiply joins or overstate aggregated activity."
            )
        },
        {
            "inspect_group": "Inspect all members of each flagged key group locally.",
            "review_grain": (
                "Review table grain and source-load history before deciding on changes."
            ),
        },
    ),
    "DQ003": Catalog(
        "A monetary field fails the declared decimal parsing contract.",
        {
            "format_mismatch": (
                "A source may use a monetary format outside the declared syntax."
            ),
            "mapping_error": (
                "A mapping may have placed nonnumeric text in an amount field."
            ),
        },
        {
            "calculation_risk": (
                "Unparseable amounts could prevent or bias monetary aggregations."
            )
        },
        {
            "inspect_format": (
                "Inspect source formatting for the flagged amount field locally."
            ),
            "review_contract": (
                "Review the expected numeric format with the source owner."
            ),
        },
    ),
    "DQ004": Catalog(
        "A parsed monetary value is below zero.",
        {
            "sign_error": (
                "A source sign convention or mapping may differ from the rule contract."
            ),
            "adjustment": (
                "An adjustment may have been exported into a field "
                "expected to be nonnegative."
            ),
        },
        {
            "total_risk": (
                "Negative values could alter totals if their business "
                "meaning is misunderstood."
            )
        },
        {
            "inspect_sign": "Compare flagged values with source documents locally.",
            "review_meaning": (
                "Check sign conventions and adjustment semantics with the source owner."
            ),
        },
    ),
    "DQ005": Catalog(
        "A populated core reference has no matching parent in this snapshot.",
        {
            "extract_gap": (
                "The parent extract may be incomplete or from a different snapshot."
            ),
            "reference_mapping": (
                "A reference mapping may differ between child and parent sources."
            ),
        },
        {
            "join_risk": (
                "Unmatched references could omit records from inner joins "
                "or leave missing attributes."
            )
        },
        {
            "inspect_parent": (
                "Check the referenced parent and matching source snapshot locally."
            ),
            "review_reference": (
                "Review reference mappings without normalizing source "
                "evidence in place."
            ),
        },
    ),
    "DQ006": Catalog(
        "A timestamp fails the declared syntax or calendar contract.",
        {
            "format_mismatch": "A source may use an unsupported timestamp format.",
            "capture_error": (
                "A timestamp may have been truncated or captured incorrectly."
            ),
        },
        {
            "time_risk": (
                "Invalid timestamps could exclude records from time-based analysis."
            )
        },
        {
            "inspect_timestamp": (
                "Compare flagged timestamp text with source logs locally."
            ),
            "review_format": "Review expected date format and timezone conventions.",
        },
    ),
    "DQ007": Catalog(
        "Recorded event order violates the declared chronology rule.",
        {
            "capture_error": (
                "A source event timestamp may have been captured or mapped incorrectly."
            ),
            "clock_context": (
                "Source clocks or timezone conventions may differ between "
                "event systems."
            ),
        },
        {
            "duration_risk": (
                "Reversed timestamps could distort fulfillment-duration or "
                "service-level analysis."
            )
        },
        {
            "inspect_events": (
                "Compare the flagged event pair with source event logs locally."
            ),
            "review_clocks": (
                "Review timestamp mappings, source clocks and timezone conventions."
            ),
        },
    ),
    "DQ008": Catalog(
        "Expected information is missing and needs lifecycle context.",
        {
            "export_gap": (
                "An event or amount may be absent from the export despite "
                "existing upstream."
            ),
            "status_timing": (
                "Status and event extracts may have been captured at different times."
            ),
        },
        {
            "coverage_risk": (
                "Missing evidence could reduce coverage of completion or "
                "monetary analysis."
            )
        },
        {
            "inspect_missing": (
                "Check source availability for the flagged field locally."
            ),
            "review_lifecycle": (
                "Review order status, extraction timing and field expectations."
            ),
        },
    ),
    "mixed_timezone_context": Catalog(
        "Event timestamps have incompatible timezone awareness.",
        {
            "mixed_format": "Source systems may export different timezone conventions.",
            "mapping_gap": (
                "A mapping may have removed an offset from an event timestamp."
            ),
        },
        {
            "comparison_risk": (
                "Comparing these events without context could misstate "
                "event order or duration."
            )
        },
        {
            "review_zones": (
                "Establish the source timezone of each event before comparing instants."
            ),
            "inspect_mapping": (
                "Inspect timestamp transformations in the source mapping locally."
            ),
        },
    ),
}

CONFIDENCE_LIMITATIONS = (
    "Evidence describes the supplied detector snapshot, not a fresh database scan.",
    "Possible causes are hypotheses; none is confirmed or assigned a probability.",
    "Business impacts are potential; monetary loss has not been measured.",
    (
        "Within-group affected-row counts may overlap across "
        "groups and must not be summed."
    ),
    "Reviewed catalog text has limited coverage and requires human investigation.",
)


def catalog_for(group: EvidenceGroup) -> Catalog:
    key = (
        "mixed_timezone_context"
        if group.pattern == "mixed_timezone_context"
        else group.rule_id
    )
    return CATALOGS[key]


def describe_evidence(group: EvidenceGroup) -> str:
    """Descriptions use allowlisted field names; never interpolate raw values."""
    columns = group.columns
    if group.pattern == "later_event_precedes_earlier":
        return f"{columns[1]} precedes {columns[0]} in the supplied findings."
    if group.pattern == "mixed_timezone_context":
        return (
            f"{columns[0]} and {columns[1]} mix offset-aware and "
            "offset-free timestamps; "
            "the detector does not claim that their chronological order is reversed."
        )
    if group.pattern == "delivered_event_not_provided":
        return (
            f"{columns[0]} is missing on orders marked delivered. "
            "This warning does not prove that delivery did not occur."
        )
    if group.pattern == "missing_parent_reference":
        return f"{columns[0]} has no exact match in {group.parent_reference}."
    if group.pattern == "duplicate_candidate_key":
        return (
            f"The complete key ({', '.join(columns)}) repeats; "
            "all group members are flagged."
        )
    descriptions = {
        "required_identifier_missing": "is NULL, empty or whitespace-only",
        "invalid_monetary_text": (
            "cannot be parsed as a finite decimal under the declared syntax"
        ),
        "negative_amount": "parses as a value below zero",
        "invalid_timestamp_text": (
            "fails the declared timestamp syntax or calendar validation"
        ),
        "amount_not_provided": (
            "is missing where an amount is expected; this is a warning"
        ),
        "purchase_event_not_provided": (
            "is missing where a purchase event is expected; this is a warning"
        ),
    }
    return f"{columns[0]} {descriptions[group.pattern]}."
