"""Pure display helpers over already validated report projections.

No file loading, evaluation changes, database connections or API requests.
"""

from datetime import datetime

from dataguard_ai.dashboard.reports import Report


def report_context(
    reports: list[tuple[str, Report]],
) -> tuple[str, list[str], list[dict]]:
    """One shared disclosure; group exceptional notices without losing sources."""
    projected = any(
        notice.startswith("Privacy-projected")
        for _, report in reports
        for notice in report.notices
    )
    context = (
        "Historical privacy-projected reports"
        if projected
        else "Saved report snapshots"
    ) + ". Identifiers and source values are omitted. No API calls or database writes."
    grouped: dict[str, list[str]] = {}
    sources = []
    for label, report in reports:
        sources.append(
            {
                "Report": label,
                "Completed (UTC)": datetime.fromisoformat(report.timestamp).strftime(
                    "%Y-%m-%d %H:%M"
                )
                if report.timestamp
                else "Unavailable",
                "Status": "Available" if report.data is not None else "Unavailable",
            }
        )
        for notice in report.notices:
            if not notice.startswith("Privacy-projected"):
                grouped.setdefault(notice, []).append(label)
    alerts = [
        f"{notice} Sources: {', '.join(labels)}." for notice, labels in grouped.items()
    ]
    return context, alerts, sources


def ingestion_rows(rows: list[dict], *, logarithmic: bool) -> list[dict]:
    """Keep original counts; omit zero only when a log axis cannot represent it."""
    return [
        {"Table": row["Table"], "Rows": row["Database rows"]}
        for row in rows
        if not logarithmic or row["Database rows"] > 0
    ]


def rule_coverage(rows: list[dict]) -> list[dict]:
    """Re-express validated TP/FN counts using the existing obligation identity."""
    return [
        {
            "Rule": row["Rule"],
            "Expected": row["TP"] + row["FN"],
            "Matched": row["TP"],
            "Missed": row["FN"],
        }
        for row in rows
    ]
