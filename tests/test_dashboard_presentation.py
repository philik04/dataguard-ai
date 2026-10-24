"""Presentation contracts: no new metrics, lost disclosures or report writes."""

import json
from copy import deepcopy

from streamlit.testing.v1 import AppTest
from test_dashboard import APP
from test_dashboard import reports as reports

from dataguard_ai.dashboard.presentation import (
    ingestion_rows,
    report_context,
    rule_coverage,
)
from dataguard_ai.dashboard.reports import LIVE_DIRECTORY, Report

PROJECTION = (
    "Privacy-projected portfolio snapshot; display surrogates replace private "
    "row locators. Historical metrics, not a fresh scan."
)
STALE = "Snapshot is over seven days old; it is not live data."


def test_notice_consolidation_preserves_each_source_and_exception():
    source = [
        (
            "Offline",
            Report(
                data={},
                timestamp="2026-10-23T09:17:00+00:00",
                notices=[PROJECTION, STALE],
            ),
        ),
        (
            "Live",
            Report(
                data={},
                timestamp="2026-10-23T09:39:00+00:00",
                notices=[PROJECTION, STALE, "Future timestamp; check provenance."],
            ),
        ),
    ]
    before = deepcopy(source)
    summary, alerts, times = report_context(source)
    assert summary.count("privacy-projected") == 1
    assert "Identifiers and source values are omitted" in summary
    assert "No API calls or database writes" in summary
    assert len(alerts) == 2
    assert "Offline, Live" in alerts[0]
    assert "Future timestamp" in alerts[1] and "Live" in alerts[1]
    assert times[0]["Completed (UTC)"] == "2026-10-23 09:17"
    assert source == before


def test_missing_reports_keep_unavailable_status_without_projection_claim():
    summary, alerts, sources = report_context([("Validation", Report(error="missing"))])
    assert "privacy-projected" not in summary
    assert not alerts
    assert sources[0]["Status"] == sources[0]["Completed (UTC)"] == "Unavailable"


def test_log_plot_keeps_counts_and_excludes_only_unrepresentable_zeros():
    rows = [
        {"Table": "orders", "Database rows": 100, "private": "never copy"},
        {"Table": "customers", "Database rows": 0},
        {"Table": "sellers", "Database rows": 1},
    ]
    before = deepcopy(rows)
    assert ingestion_rows(rows, logarithmic=True) == [
        {"Table": "orders", "Rows": 100},
        {"Table": "sellers", "Rows": 1},
    ]
    assert ingestion_rows(rows, logarithmic=False)[1] == {
        "Table": "customers",
        "Rows": 0,
    }
    assert rows == before


def test_rule_coverage_preserves_tp_fn_obligations_not_events_or_false_positives():
    rows = [
        {"Rule": "DQ001", "TP": 2, "FN": 1, "FP": 5},
        {"Rule": "DQ008", "TP": 0, "FN": 0, "FP": 0},
    ]
    before = deepcopy(rows)
    assert rule_coverage(rows) == [
        {"Rule": "DQ001", "Expected": 3, "Matched": 2, "Missed": 1},
        {"Rule": "DQ008", "Expected": 0, "Matched": 0, "Missed": 0},
    ]
    assert rows == before


def test_same_ai_selections_disclose_no_demonstrated_benefit_and_default_to_stacked(
    reports,
    monkeypatch,
):
    monkeypatch.setenv("DATAGUARD_REPORT_DIR", str(reports))
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    app.sidebar.radio[0].set_value("AI explanations").run()
    assert not app.exception
    assert any(
        "No additional analytical benefit was demonstrated" in item.value
        for item in app.info
    )
    layout = next(
        widget for widget in app.radio if widget.label == "Explanation layout"
    )
    assert layout.value == "Stacked"
    layout.set_value("Side by side").run()
    assert not app.exception
    assert any(metric.value == "USD 0.000060" for metric in app.metric)


def test_different_reviewed_selection_does_not_claim_matching_or_better_analysis(
    reports,
    monkeypatch,
):
    path = reports / LIVE_DIRECTORY / "explanations_openai.json"
    raw = json.loads(path.read_text())
    raw["explanations"][0]["possible_root_causes"] = raw["explanations"][0][
        "possible_root_causes"
    ][:1]
    path.write_text(json.dumps(raw))
    monkeypatch.setenv("DATAGUARD_REPORT_DIR", str(reports))
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    app.sidebar.radio[0].set_value("AI explanations").run()
    assert not app.exception
    assert any(
        "0 of 1 groups have identical selections" in item.value for item in app.info
    )
    assert not any(
        "No additional analytical benefit was demonstrated" in item.value
        for item in app.info
    )


def test_overview_with_zero_counts_has_no_fabricated_log_values(reports, monkeypatch):
    path = reports / "ingestion.json"
    raw = json.loads(path.read_text())
    for row in raw["tables"].values():
        row.update(csv_rows=0, copied_rows=0, database_rows=0)
    path.write_text(json.dumps(raw))
    monkeypatch.setenv("DATAGUARD_REPORT_DIR", str(reports))
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    assert not app.exception and app.metric[0].value == "0"
    assert any("No positive row counts" in item.value for item in app.info)
    scale = next(widget for widget in app.radio if widget.label == "Row-count scale")
    scale.set_value("Linear").run()
    assert not app.exception and app.metric[0].value == "0"


def test_findings_dimension_changes_preserve_filter_results(reports, monkeypatch):
    monkeypatch.setenv("DATAGUARD_REPORT_DIR", str(reports))
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    app.sidebar.radio[0].set_value("Findings").run()
    for dimension in ("Rule", "Severity", "Table"):
        next(w for w in app.selectbox if w.label == "Compare findings by").set_value(
            dimension
        ).run()
        assert not app.exception
        frame = next(x.value for x in app.dataframe if "Finding" in x.value.columns)
        assert len(frame) == 2
    assert app.metric[0].value == "2"
