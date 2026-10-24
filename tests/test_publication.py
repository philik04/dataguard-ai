"""Publication controls preserve measured results and exclude source values."""

import hashlib
import json
from pathlib import Path

import pytest
from conftest import require_disposable_database
from test_dashboard import reports as reports
from test_explanations import PRIVATE, RAW_VALUE, finding, saved_report

from dataguard_ai.dashboard.publication import export_reports, project_quality
from dataguard_ai.dashboard.reports import load_dashboard


def test_export_preserves_metrics_and_original_reports(reports: Path, tmp_path_factory):
    output = tmp_path_factory.mktemp("publication") / "public"
    original_hashes = {
        p: hashlib.sha256(p.read_bytes()).hexdigest() for p in reports.rglob("*.json")
    }
    original = load_dashboard(reports)
    export_reports(reports, output)
    public = load_dashboard(output)
    assert all(report.data is not None for report in public.values())
    assert public["quality"].data == original["quality"].data
    assert public["evaluation"].data == original["evaluation"].data
    assert public["experiment"].data == original["experiment"].data
    assert public["live"].data["usage"] == original["live"].data["usage"]
    assert all(
        hashlib.sha256(p.read_bytes()).hexdigest() == h
        for p, h in original_hashes.items()
    )
    content = "".join(p.read_text() for p in output.rglob("*.json"))
    for forbidden in (
        PRIVATE,
        RAW_VALUE,
        "original_value",
        "mutated_value",
        "selected_order_ids",
    ):
        assert forbidden not in content
    assert "display-row-" in content
    assert all("Privacy-projected" in report.notices[0] for report in public.values())
    assert public["quality"].digest != original["quality"].digest


def test_export_never_overwrites_private_or_existing_destinations(reports, tmp_path):
    for destination in (reports, reports / "nested", tmp_path):
        with pytest.raises(ValueError):
            export_reports(reports, destination)


def test_missing_source_aborts_export(tmp_path):
    with pytest.raises(ValueError, match="six source reports"):
        export_reports(tmp_path / "missing", tmp_path / "public")
    assert not (tmp_path / "public").exists()


def test_unknown_row_counts_remain_unknown():
    raw = saved_report([finding(locator=None), finding()])
    raw["findings"][0]["row_identifier"] = None
    projection = project_quality(raw)
    from dataguard_ai.ai.grouping import group_findings

    assert group_findings(projection["findings"])[0].affected_row_count is None
    assert PRIVATE not in json.dumps(projection)


@pytest.mark.parametrize(
    "name,protected",
    [
        ("dataguard_ai", "dataguard_ai"),
        ("source_test", "source_test"),
        ("", "source"),
        ("production", "source"),
    ],
)
def test_dedicated_database_guard_rejects_protected_or_ambiguous_targets(
    name, protected
):
    with pytest.raises(ValueError):
        require_disposable_database(name, protected)


def test_separate_disposable_database_is_allowed():
    require_disposable_database("disposable_test", "dataguard_ai")


@pytest.mark.parametrize("occupied", ["schemas", "objects", "routines", "empty"])
def test_integration_preflight_refuses_existing_objects(monkeypatch, occupied):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from conftest import integration_database_preflight

    from dataguard_ai.ingestion.database import DatabaseConfig

    monkeypatch.setenv("DATAGUARD_TEST_DB", "fixtures_test")
    monkeypatch.setattr(
        DatabaseConfig,
        "from_environment",
        lambda: DatabaseConfig("protected", "example", "synthetic"),
    )
    connection = MagicMock()
    connection.execute.side_effect = [
        MagicMock(fetchone=lambda: ("fixtures_test",)),
        MagicMock(fetchall=lambda: [("existing",)] if occupied == "schemas" else []),
        MagicMock(fetchall=lambda: [(1,)] if occupied == "objects" else []),
        MagicMock(fetchall=lambda: [(1,)] if occupied == "routines" else []),
    ]
    # SET TRANSACTION is the first execute, before the four SELECTs.
    connection.execute.side_effect = [MagicMock(), *connection.execute.side_effect]
    context = MagicMock()
    context.__enter__.return_value = connection
    monkeypatch.setattr(DatabaseConfig, "connect", lambda _: context)
    request = SimpleNamespace(
        config=SimpleNamespace(getoption=lambda _: True),
        session=SimpleNamespace(
            items=[SimpleNamespace(keywords={"integration": True})]
        ),
    )
    if occupied == "empty":
        integration_database_preflight.__wrapped__(request)
    else:
        with pytest.raises(pytest.fail.Exception, match="not empty"):
            integration_database_preflight.__wrapped__(request)
    assert all(
        call.args[0].startswith(("SELECT", "SET TRANSACTION READ ONLY"))
        for call in connection.execute.call_args_list
    )


@pytest.mark.parametrize("source", ["synthetic", "local_olist"])
def test_snapshot_renders_all_views_without_external_actions(
    monkeypatch, reports, tmp_path_factory, source
):
    import socket

    from streamlit.testing.v1 import AppTest

    if source == "synthetic":
        directory = tmp_path_factory.mktemp("synthetic_snapshot") / "projection"
        export_reports(reports, directory)
        expected = (9, 2, 1, 1, "DQ007", 2, "warning")
    else:
        directory = Path(__file__).parents[1] / "reports/portfolio"
        if not (directory / "quality.json").is_file():
            pytest.skip("Local Olist snapshot excluded from public release")
        expected = (1_550_922, 197, 70, 80, "DQ008", 8, "error")
    app_path = Path(__file__).parents[1] / "src/dataguard_ai/dashboard/app.py"
    monkeypatch.setenv("DATAGUARD_REPORT_DIR", str(directory))

    def forbidden(*args, **kwargs):
        pytest.fail("Portfolio snapshot attempted an external action")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    loaded = load_dashboard(directory)
    assert all(report.data is not None for report in loaded.values())
    assert loaded["ingestion"].data["total"] == expected[0]
    assert loaded["quality"].data["summary"]["finding_count"] == expected[1]
    assert loaded["experiment"].data["events"] == expected[2]
    assert loaded["evaluation"].data["metrics"]["tp"] == expected[3]
    app = AppTest.from_file(str(app_path), default_timeout=20).run()
    for view in ("Overview", "Findings", "Corruption & evaluation", "AI explanations"):
        app.sidebar.radio[0].set_value(view).run()
        assert not app.exception
    app.sidebar.radio[0].set_value("Findings").run()
    app.multiselect[0].set_value([expected[4]]).run()
    assert (
        len(
            next(
                frame.value
                for frame in app.dataframe
                if "Finding" in frame.value.columns
            )
        )
        == expected[5]
    )
    app.multiselect[1].set_value([expected[6]]).run()
    assert any("No findings match" in item.value for item in app.info)
