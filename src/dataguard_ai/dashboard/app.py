"""Streamlit presentation only. Run from the repository root; no DB/API imports."""

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from dataguard_ai.dashboard.presentation import (
    ingestion_rows,
    report_context,
    rule_coverage,
)
from dataguard_ai.dashboard.reports import (
    BENCHMARK_NOTE,
    filter_findings,
    load_dashboard,
    percentage,
)
from dataguard_ai.quality.rules import RULE_BY_ID

VIEWS = ("Overview", "Findings", "Corruption & evaluation", "AI explanations")


def cards(items: list[tuple[str, str]]) -> None:
    # Two columns keep long counts/costs legible in the desktop side panel too.
    width = 2 if len(items) == 4 else len(items)
    for start in range(0, len(items), width):
        chunk = items[start : start + width]
        for column, (label, value) in zip(st.columns(len(chunk)), chunk, strict=True):
            with column.container(border=True):
                st.metric(label, value)


def context(reports: dict, labels: dict[str, str]) -> None:
    summary, alerts, sources = report_context(
        [(label, reports[key]) for key, label in labels.items()]
    )
    st.caption(summary)
    for alert in alerts:
        st.warning(alert)
    with st.expander("Sources & report times"):
        table(sources)
        st.caption(
            "Times are UTC, displayed to the minute. Reload rereads saved files; "
            "it does not refresh database evidence. Aggregates may still be sensitive."
        )
        if "privacy-projected" in summary:
            st.caption(
                "Display surrogates preserve row multiplicity without exposing "
                "private locators. The publication snapshot retains measured "
                "results; no underlying report is modified by this viewer."
            )


def available(report, label: str) -> bool:
    if report.data is None:
        st.info(f"{label}: {report.error}")
        return False
    return True


def table(rows: list[dict], *, key: str | None = None) -> None:
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", key=key)


def horizontal_counts(
    rows: list[dict], category: str, count: str, *, height=260, logarithmic=False
) -> None:
    st.vega_lite_chart(
        pd.DataFrame(rows),
        {
            "height": height,
            "mark": (
                {"type": "point", "filled": True, "size": 85, "color": "#2563eb"}
                if logarithmic
                else {"type": "bar", "color": "#2563eb", "cornerRadiusEnd": 3}
            ),
            "encoding": {
                "y": {
                    "field": category,
                    "type": "nominal",
                    "sort": "-x",
                    "axis": {"labelLimit": 220},
                    "title": None,
                },
                "x": {
                    "field": count,
                    "type": "quantitative",
                    "scale": {"type": "log", "base": 10, "domainMin": 1}
                    if logarithmic
                    else {"zero": True},
                    "axis": {
                        "format": "~s" if logarithmic else ",d",
                        "grid": not logarithmic,
                        "tickCount": 6,
                    },
                },
                "tooltip": [
                    {"field": category, "type": "nominal"},
                    {"field": count, "type": "quantitative", "format": ",d"},
                ],
            },
        },
        width="stretch",
    )


def overview(reports: dict) -> None:
    st.title("From source data to evidence")
    st.write("A reproducible data quality workflow for the Olist e-commerce dataset.")
    context(reports, {"ingestion": "Ingestion", "quality": "Original validation"})
    ingestion, quality = reports["ingestion"], reports["quality"]
    cards(
        [
            (
                "Imported records",
                f"{ingestion.data['total']:,}" if ingestion.data else "N/A",
            ),
            (
                "Deterministic rules",
                str(quality.data["rule_count"]) if quality.data else "N/A",
            ),
            (
                "Rule violations",
                str(quality.data["summary"]["confirmed_violations"])
                if quality.data
                else "N/A",
            ),
            (
                "Warnings",
                str(quality.data["summary"]["warnings"]) if quality.data else "N/A",
            ),
        ]
    )
    if available(ingestion, "Ingestion"):
        st.subheader("Rows imported by table")
        scale = st.radio("Row-count scale", ("Logarithmic", "Linear"), horizontal=True)
        logarithmic = scale == "Logarithmic"
        rows = ingestion_rows(ingestion.data["rows"], logarithmic=logarithmic)
        st.caption(
            "Logarithmic dot plot: equal spacing represents ratios, not equal row "
            "differences. Tooltips and the table retain exact counts."
            if logarithmic
            else "Linear axis: bar lengths represent row counts."
        )
        if rows:
            horizontal_counts(rows, "Table", "Rows", logarithmic=logarithmic)
        else:
            st.info("No positive row counts to show on a logarithmic axis.")
        if logarithmic and len(rows) < len(ingestion.data["rows"]):
            st.caption(
                "Zero-row tables are omitted from the log chart and retained below."
            )
        table(ingestion.data["rows"])
        if not all(row["Counts match"] for row in ingestion.data["rows"]):
            st.warning("Some imported table counts do not reconcile.")
        st.caption(
            "Rows are summed across nine tables; this is not a unique-order count."
        )
    with st.expander("How the workflow fits together"):
        st.write(
            "Ingest → validate → inject controlled corruption → evaluate → explain"
        )
        st.write(
            "Deterministic checks produce evidence. AI selects reviewed "
            "interpretations of existing findings."
        )
        st.caption(BENCHMARK_NOTE)


def findings(reports: dict) -> None:
    st.title("Deterministic findings")
    st.write("Explore saved rule violations and warnings.")
    context(reports, {"quality": "Original validation"})
    report = reports["quality"]
    if not available(report, "Original validation"):
        return
    data = report.data
    cards(
        [
            ("Findings", str(data["summary"]["finding_count"])),
            ("Rule violations", str(data["summary"]["confirmed_violations"])),
            ("Warnings", str(data["summary"]["warnings"])),
        ]
    )
    rows = data["rows"]
    if not rows:
        st.info("This saved validation snapshot contains no findings.")
        return
    frame = pd.DataFrame(rows)
    dimension = st.selectbox("Compare findings by", ("Rule", "Severity", "Table"))
    counts = frame.groupby(dimension).size().rename("Findings")
    if dimension == "Rule":
        counts = counts.reindex(list(RULE_BY_ID), fill_value=0)
    horizontal_counts(counts.reset_index().to_dict("records"), dimension, "Findings")
    st.subheader("Findings explorer")
    rules = st.multiselect("Rule", list(RULE_BY_ID), default=list(RULE_BY_ID))
    left, right = st.columns(2)
    severities = left.multiselect(
        "Severity", ["error", "warning"], default=["error", "warning"]
    )
    tables = sorted(frame["Table"].unique())
    selected = right.multiselect("Table", tables, default=tables)
    query = st.text_input(
        "Search findings", placeholder="Try DQ007, timestamp or carrier"
    )
    filtered = filter_findings(
        rows, rules=rules, severities=severities, tables=selected, query=query
    )
    st.caption(
        f"{len(filtered):,} of {len(rows):,} findings · filters affect this table only"
    )
    if filtered:
        table(filtered, key="findings_table")
    else:
        st.info("No findings match these filters.")
    st.caption(
        "Finding numbers are display positions. Findings may overlap across rows; "
        "a warning is not a confirmed violation."
    )
    with st.expander("Eight rule contracts"):
        table(
            [
                {
                    "Rule": rule.rule_id,
                    "Description": rule.description,
                    "Severity": rule.severity,
                }
                for rule in RULE_BY_ID.values()
            ]
        )


def benchmark(reports: dict) -> None:
    st.title("A controlled test of detection")
    context(
        reports,
        {"evaluation": "Detection evaluation", "experiment": "Corruption pilot"},
    )
    evaluation = reports["evaluation"]
    if available(evaluation, "Detection evaluation"):
        data = evaluation.data
        metrics = data["metrics"]
        st.caption(BENCHMARK_NOTE)
        cards(
            [
                (key.title(), percentage(metrics[key]))
                for key in ("precision", "recall", "f1")
            ]
        )
        st.subheader("Finding coverage by rule")
        st.caption(
            "Expected obligations, matched findings (TP) and missed obligations "
            "(FN), on the predefined synthetic benchmark only."
        )
        coverage = rule_coverage(data["per_rule"])
        st.vega_lite_chart(
            pd.DataFrame(coverage),
            {
                "height": 350,
                "transform": [
                    {
                        "fold": ["Expected", "Matched", "Missed"],
                        "as": ["Series", "Findings"],
                    }
                ],
                "mark": {"type": "bar", "cornerRadiusEnd": 2},
                "encoding": {
                    "y": {"field": "Rule", "type": "nominal", "title": None},
                    "yOffset": {
                        "field": "Series",
                        "sort": ["Expected", "Matched", "Missed"],
                    },
                    "x": {
                        "field": "Findings",
                        "type": "quantitative",
                        "axis": {"tickMinStep": 1, "tickCount": 5},
                        "scale": {"zero": True},
                    },
                    "color": {
                        "field": "Series",
                        "type": "nominal",
                        "scale": {
                            "domain": ["Expected", "Matched", "Missed"],
                            "range": ["#94a3b8", "#0d9488", "#d97706"],
                        },
                        "legend": {"orient": "top", "title": None},
                    },
                    "tooltip": [
                        {"field": "Rule"},
                        {"field": "Expected"},
                        {"field": "Matched"},
                        {"field": "Missed"},
                    ],
                },
            },
            width="stretch",
        )
        no_cases = [row["Rule"] for row in coverage if row["Expected"] == 0]
        if no_cases:
            st.caption(
                f"No expected finding obligations: {', '.join(no_cases)}. "
                "This does not establish positive-case coverage; "
                "undefined scores stay N/A."
            )
        st.caption(
            "Zero missed findings have no visible orange bar; exact zeros remain "
            "in the table. Expected = TP + FN; matched = TP; missed = FN."
        )
        table(
            [
                {
                    **row,
                    "Unmatched (FP)": original["FP"],
                    "Precision": original["Precision"],
                    "Recall": original["Recall"],
                    "F1": original["F1"],
                }
                for row, original in zip(coverage, data["per_rule"], strict=True)
            ]
        )
        st.write(
            f"Finding matches: {metrics['tp']} TP · {metrics['fp']} FP · "
            f"{metrics['fn']} FN. "
            f"Mutation events detected: {data['detected_events']} / {data['events']}."
        )
    experiment = reports["experiment"]
    if available(experiment, "Corruption pilot"):
        data = experiment.data
        with st.expander("Pilot setup & mutation breakdown"):
            cards(
                [
                    ("Selected orders", f"{data['selected_orders']:,}"),
                    ("Mutation events", str(data["events"])),
                    ("Pilot findings before", str(data["before"]["finding_count"])),
                    ("Pilot findings after", str(data["after"]["finding_count"])),
                ]
            )
            horizontal_counts(data["mutations"], "Mutation type", "Events")
            st.caption(
                "Before/after uses the isolated pilot, not the original-data report. "
                "One duplicate mutation flags both members, so events and findings "
                "have different denominators. Benchmark coverage is limited."
            )


def interpretation(explanation: dict) -> None:
    st.write(explanation["evidence_based_explanation"])
    for title, key in (
        ("Root-cause hypotheses · unconfirmed", "possible_root_causes"),
        ("Potential business impact · unmeasured", "potential_business_impact"),
        ("Recommended investigations", "recommended_investigation_steps"),
    ):
        st.markdown(f"**{title}**")
        for item in explanation[key]:
            st.write(f"• {item['text'] if isinstance(item, dict) else item}")


def explanations(reports: dict) -> None:
    st.title("Evidence and AI interpretation")
    st.write("Recorded selections from the same reviewed catalog.")
    context(
        reports, {"offline": "Offline explanations", "live": "Live OpenAI explanations"}
    )
    offline, live = reports["offline"], reports["live"]
    offline_ok = available(offline, "Offline explanations")
    live_ok = available(live, "Live OpenAI explanations")
    if not offline_ok and not live_ok:
        return
    source = offline.data if offline_ok else live.data
    if offline_ok and live_ok and source["explanations"]:
        matched = sum(
            left == right
            for left, right in zip(
                offline.data["explanations"], live.data["explanations"], strict=True
            )
        )
        if matched == len(source["explanations"]):
            st.info(
                "The live model selected the same reviewed interpretations as the "
                f"offline provider in all {matched} groups. No additional analytical "
                "benefit was demonstrated by this test."
            )
        else:
            st.info(
                f"{matched} of {len(source['explanations'])} groups have identical "
                "selections. Differences do not establish analytical benefit "
                "or causal truth."
            )
    elif not (offline_ok and live_ok):
        st.caption("Both saved providers are required to assess selection differences.")
    st.caption(
        "Deterministic evidence is unchanged. Hypotheses are unconfirmed; impacts "
        "are potential and unmeasured. AI does not validate data or establish causes."
    )
    if live_ok:
        data = live.data
        cards(
            [
                ("Live requests (historical)", str(data["requests"])),
                ("Input tokens", f"{sum(x['Input tokens'] for x in data['usage']):,}"),
                (
                    "Output tokens",
                    f"{sum(x['Output tokens'] for x in data['usage']):,}",
                ),
                ("Estimated live cost", f"USD {data['estimated_cost']:.6f}"),
            ]
        )
        st.caption(
            "Historical cost uses conservative recorded rates, not a billing "
            "record. Offline selection has no API cost."
        )
    with st.expander("Model, token usage & source provenance"):
        if live_ok:
            table(live.data["usage"])
        st.caption(f"Validation snapshot (UTC): {source['source_timestamp']}")
        st.write("Quality report SHA-256")
        st.code(source["source_hash"], language=None, wrap_lines=True)
        st.caption(
            "Hashes identify saved report bytes, not causal truth or fresh validation."
        )
    layout = st.radio(
        "Explanation layout",
        ("Stacked", "Side by side"),
        horizontal=True,
        help="Stacked is easier to read on narrower screens.",
    )
    for index, item in enumerate(source["explanations"]):
        with st.expander(
            f"{item['group_id']} · {item['issue_summary']}", expanded=index == 0
        ):
            containers = (
                st.columns(2)
                if layout == "Side by side"
                else [st.container(), st.container()]
            )
            for container, report, ready, label in zip(
                containers,
                (offline, live),
                (offline_ok, live_ok),
                ("Offline · reviewed catalog", "Live · model-selected catalog"),
                strict=True,
            ):
                with container:
                    st.subheader(label)
                    if ready:
                        interpretation(report.data["explanations"][index])
                    else:
                        st.caption("Report unavailable.")
    with st.expander("Confidence limitations"):
        for note in (
            source["explanations"][0]["confidence_limitations"]
            if source["explanations"]
            else []
        ):
            st.write(f"• {note}")


def main() -> None:
    st.set_page_config(
        page_title="DataGuard AI | Portfolio", page_icon="🛡️", layout="wide"
    )
    mode = st.sidebar.selectbox("Mode", ("Olist portfolio", "Analyze Your CSV"))
    if mode == "Analyze Your CSV":
        from dataguard_ai.dashboard.upload import upload_view

        upload_view()
        return
    directory = Path(os.environ.get("DATAGUARD_REPORT_DIR", "reports/generated"))
    reports = load_dashboard(directory)
    with st.sidebar:
        st.title("DataGuard AI")
        st.caption("DATA QUALITY · PORTFOLIO")
        view = st.radio("Explore the project", VIEWS)
        st.divider()
        st.markdown("**Read-only report viewer**")
        st.caption("Explore saved evidence and its limitations.")
        if st.button("Reload saved reports"):
            st.rerun()
    st.caption("DATAGUARD AI / PORTFOLIO")
    {
        VIEWS[0]: overview,
        VIEWS[1]: findings,
        VIEWS[2]: benchmark,
        VIEWS[3]: explanations,
    }[view](reports)


if __name__ == "__main__":
    main()
