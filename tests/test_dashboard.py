"""Pure report parsing and read-only Streamlit interaction contracts."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from test_explanations import PRIVATE, RAW_VALUE, finding, saved_report

from dataguard_ai.ai.explanations import explain_saved_report
from dataguard_ai.ai.providers import OfflineProvider
from dataguard_ai.dashboard.app import VIEWS
from dataguard_ai.dashboard.reports import (
    BENCHMARK_NOTE,
    LIVE_DIRECTORY,
    filter_findings,
    load_dashboard,
    parse_evaluation,
    parse_ingestion,
    parse_quality,
    percentage,
    read_report,
)
from dataguard_ai.ingestion.tables import TABLE_FILES
from dataguard_ai.quality.rules import RULE_BY_ID

APP = Path(__file__).parents[1] / "src/dataguard_ai/dashboard/app.py"
TIMESTAMP = "2026-10-19T12:00:00+00:00"


def save(directory, name, content):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content))
    return path


def metric_values(tp, fp=0, fn=0):
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }


def evaluation():
    return {
        "completed_at_utc": TIMESTAMP,
        "finding_level": metric_values(1)
        | {"expected_obligations": 1, "new_predictions": 1},
        "provenance": {
            "content_digest_verified": True,
            "corrupted_content_sha256": "a" * 64,
        },
        "mutation_event_level": {"total": 1, "detected": 1},
        "per_rule": [
            {"rule_id": key, **metric_values(int(key == "DQ007"))} for key in RULE_BY_ID
        ],
    }


@pytest.fixture
def reports(tmp_path):
    quality = saved_report([finding(), finding(locator="(0,2)")])
    quality_path = save(tmp_path, "quality.json", quality)
    ingestion = {
        "status": "committed",
        "completed_at_utc": TIMESTAMP,
        "database": PRIVATE,
        "tables": {
            key: {"csv_rows": 1, "copied_rows": 1, "database_rows": 1}
            for key in TABLE_FILES
        },
    }
    save(tmp_path, "ingestion.json", ingestion)
    offline = explain_saved_report(quality_path, OfflineProvider())
    save(tmp_path, "explanations.json", offline)
    live = deepcopy(offline)
    live["provider"] = "openai"
    live["api_metadata"] = {
        "requested_model": "gpt-4o-mini-2024-07-18",
        "requests_attempted": 1,
        "usage": [
            {
                "group_id": "group_001",
                "model": "gpt-4o-mini-2024-07-18",
                "input_tokens": 20,
                "output_tokens": 10,
                "estimated_cost_usd": 0.00006,
            }
        ],
        "key": PRIVATE,
        "limits": {"input_usd_per_million": 1, "output_usd_per_million": 4},
    }
    save(tmp_path, f"{LIVE_DIRECTORY}/explanations_openai.json", live)
    save(tmp_path, "evaluation.json", evaluation())
    experiment = {
        "status": "committed",
        "completed_at_utc": TIMESTAMP,
        "mutation_counts": {"reversed_chronology": 1},
        "config": {"mutation_budget": 1, "orders": 1},
        "pilot_baseline": saved_report([]),
        "pilot_after": saved_report([finding()]),
        "manifest": [{"original_value": RAW_VALUE, "row_locator": PRIVATE}],
        "selected_order_ids": [PRIVATE],
        "corrupted_content_sha256": "a" * 64,
    }
    save(tmp_path, "experiment.json", experiment)
    return tmp_path


def test_valid_reports_project_only_safe_fields(reports):
    loaded = load_dashboard(reports)
    assert all(report.data is not None for report in loaded.values())
    public = json.dumps({key: report.data for key, report in loaded.items()})
    assert PRIVATE not in public and RAW_VALUE not in public
    assert "row_identifier" not in public and "original_value" not in public
    assert loaded["ingestion"].data["total"] == 9
    assert len(loaded["quality"].data["rows"]) == 2
    assert loaded["live"].data["estimated_cost"] == 0.00006
    assert loaded["offline"].data["explanations"] == loaded["live"].data["explanations"]


@pytest.mark.parametrize(
    "value,expected", [(None, "N/A"), (0, "0.0%"), (1, "100.0%"), (0.125, "12.5%")]
)
def test_metric_presentation(value, expected):
    assert percentage(value) == expected


@pytest.mark.parametrize("value", [-1, 1.1, float("nan"), float("inf"), True, "1"])
def test_bad_metric_does_not_become_success(value):
    with pytest.raises((ValueError, TypeError)):
        percentage(value)


def test_undefined_per_rule_metric_and_consistency():
    data = parse_evaluation(evaluation())
    assert data["per_rule"][0]["Precision"] == "N/A"
    assert "not measure general real-world accuracy" in BENCHMARK_NOTE
    raw = evaluation()
    raw["finding_level"]["precision"] = 0.5
    with pytest.raises(ValueError):
        parse_evaluation(raw)


def test_zero_findings_is_distinct_from_missing(tmp_path):
    path = save(tmp_path, "quality.json", saved_report([]))
    parsed = read_report(path, parse_quality)
    assert parsed.data["summary"]["finding_count"] == 0
    missing = read_report(tmp_path / "missing.json", parse_quality)
    assert missing.data is None and "missing" in missing.error


@pytest.mark.parametrize("content", ["{", "[]", '{"private":"secret"}', "null"])
def test_malformed_reports_fail_without_echoing_input(tmp_path, content):
    path = tmp_path / "quality.json"
    path.write_text(content)
    report = read_report(path, parse_quality)
    assert report.data is None and "invalid" in report.error
    assert "secret" not in report.error


def test_stale_future_and_timezone_missing(reports):
    path = reports / "quality.json"
    stale = read_report(path, parse_quality, now=datetime(2026, 11, 1, tzinfo=UTC))
    assert "seven days" in stale.notices[0] and stale.data is not None
    future = read_report(path, parse_quality, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert "future" in future.notices[0]
    raw = json.loads(path.read_text())
    raw["completed_at_utc"] = "2026-10-19T12:00:00"
    save(reports, "quality.json", raw)
    assert read_report(path, parse_quality).data is None


def test_ai_hash_mismatch_and_unreviewed_claims_are_not_displayed(reports):
    path = reports / LIVE_DIRECTORY / "explanations_openai.json"
    original = json.loads(path.read_text())
    for changed in ("hash", "claim", "count", "model"):
        raw = deepcopy(original)
        if changed == "hash":
            raw["source"]["input_sha256"] = "wrong"
        elif changed == "claim":
            raw["explanations"][0]["possible_root_causes"] = [
                {"status": "hypothesis", "text": "Financial loss is $1000"}
            ]
        elif changed == "count":
            raw["evidence_groups"][0]["finding_count"] = 999
        else:
            raw["api_metadata"]["usage"][0]["model"] = PRIVATE
        save(reports, f"{LIVE_DIRECTORY}/explanations_openai.json", raw)
        loaded = load_dashboard(reports)
        assert loaded["live"].data is None
        assert loaded["offline"].data is not None


def test_filters_are_literal_and_empty_selection_is_empty(reports):
    rows = load_dashboard(reports)["quality"].data["rows"]
    kwargs = {"rules": ["DQ007"], "severities": ["error"], "tables": ["orders"]}
    assert len(filter_findings(rows, **kwargs, query="CARRIER")) == 2
    assert filter_findings(rows, **kwargs, query="[.*") == []
    assert filter_findings(rows, **(kwargs | {"rules": []}), query="") == []


def test_summary_and_ingestion_counts_are_checked(reports):
    raw = json.loads((reports / "quality.json").read_text())
    raw["summary"]["finding_count"] = 10
    with pytest.raises(ValueError):
        parse_quality(raw)
    raw = json.loads((reports / "ingestion.json").read_text())
    raw["tables"]["orders"]["database_rows"] = 2
    assert not next(
        row for row in parse_ingestion(raw)["rows"] if row["Table"] == "orders"
    )["Counts match"]


def test_all_views_and_search_are_read_only(reports, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Dashboard attempted a network/API/database action")

    import socket

    import psycopg
    from openai.resources.responses import Responses

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(Responses, "create", forbidden)
    monkeypatch.setenv("DATAGUARD_REPORT_DIR", str(reports))
    before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest() for p in reports.rglob("*.json")
    }
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    assert not app.exception
    assert app.metric[0].value == "9"
    for view in VIEWS[1:]:
        app.sidebar.radio[0].set_value(view).run()
        assert not app.exception
        serialized = str(app)
        assert PRIVATE not in serialized and RAW_VALUE not in serialized
    app.sidebar.radio[0].set_value("Findings").run()
    app.text_input[0].set_value("does-not-exist").run()
    assert any("No findings match" in message.value for message in app.info)
    app.text_input[0].set_value("carrier").run()
    assert (
        len(
            next(
                frame.value
                for frame in app.dataframe
                if "Finding" in frame.value.columns
            )
        )
        == 2
    )
    assert all(
        hashlib.sha256(p.read_bytes()).hexdigest() == digest
        for p, digest in before.items()
    )


def test_all_views_with_missing_files(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAGUARD_REPORT_DIR", str(tmp_path))
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    assert app.metric[0].value == "N/A"
    for view in VIEWS:
        app.sidebar.radio[0].set_value(view).run()
        assert not app.exception
        assert any("unavailable" in item.value.lower() for item in app.info)


def test_evaluation_from_different_pilot_is_unavailable(reports):
    raw = json.loads((reports / "evaluation.json").read_text())
    raw["provenance"]["corrupted_content_sha256"] = "b" * 64
    save(reports, "evaluation.json", raw)
    loaded = load_dashboard(reports)
    assert loaded["evaluation"].data is None
    assert "does not match" in loaded["evaluation"].error
    assert loaded["experiment"].data is not None


def test_no_pilot_discloses_unchecked_provenance(reports):
    (reports / "experiment.json").unlink()
    loaded = load_dashboard(reports)
    assert loaded["evaluation"].data is not None
    assert any("cannot be checked" in x for x in loaded["evaluation"].notices)


def test_reported_ai_cost_must_reconcile_to_usage(reports):
    path = reports / LIVE_DIRECTORY / "explanations_openai.json"
    raw = json.loads(path.read_text())
    raw["api_metadata"]["usage"][0]["estimated_cost_usd"] = 0
    save(reports, f"{LIVE_DIRECTORY}/explanations_openai.json", raw)
    assert load_dashboard(reports)["live"].data is None


def test_reload_reads_changed_report_without_stale_cache(reports, monkeypatch):
    monkeypatch.setenv("DATAGUARD_REPORT_DIR", str(reports))
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    assert app.metric[0].value == "9"
    raw = json.loads((reports / "ingestion.json").read_text())
    for item in raw["tables"].values():
        item.update(csv_rows=2, copied_rows=2, database_rows=2)
    save(reports, "ingestion.json", raw)
    app.sidebar.button[0].click().run()
    assert not app.exception
    assert app.metric[0].value == "18"
