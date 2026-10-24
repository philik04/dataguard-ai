"""Upload-mode interaction tests use synthetic bytes, never real user files."""

import io
import json
import socket

import psycopg
import pytest
import streamlit as st
from openai.resources.responses import Responses
from streamlit.testing.v1 import AppTest
from test_dashboard import APP


@pytest.fixture
def app(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Upload analysis must not access a database or API")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(Responses, "create", forbidden)
    monkeypatch.setenv("DATAGUARD_REPORT_DIR", str(tmp_path / "absent"))
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    app.sidebar.selectbox[0].set_value("Analyze Your CSV").run()
    assert not app.exception
    return app


def upload(monkeypatch, app, content):
    monkeypatch.setattr(
        st, "file_uploader", lambda *args, **kwargs: io.BytesIO(content)
    )
    app.run()
    return app


def submit(app):
    next(b for b in app.button if b.label == "Analyze CSV").click().run()
    assert not app.exception


def test_missing_upload_needs_no_reports_or_external_services(app):
    assert any("Upload a CSV to begin" in x.value for x in app.info)
    assert len(app.warning) == 1
    assert "remote deployment receives your file" in app.warning[0].value


def test_uploaded_data_is_previewed_only_on_request_and_report_excludes_values(
    app, monkeypatch
):
    upload(monkeypatch, app, b"label,amount,date\nPRIVATE_SAMPLE,wrong,2026-02-30\n")
    assert not app.dataframe
    app.checkbox[0].check().run()
    assert app.dataframe[0].value.iloc[0]["label"] == "PRIVATE_SAMPLE"
    next(w for w in app.multiselect if w.label == "Numeric columns").set_value(
        ["amount"]
    )
    next(w for w in app.multiselect if w.label == "Date/time columns").set_value(
        ["date"]
    )
    submit(app)
    result = app.session_state["generic_result"]
    assert result["finding_count"] == 2
    assert "PRIVATE_SAMPLE" not in json.dumps(result)
    assert "wrong" not in json.dumps(result)
    assert len(app.get("download_button")) == 1


def test_replacement_clears_previous_configuration_and_results(app, monkeypatch):
    upload(monkeypatch, app, b"first\nx\n")
    app.checkbox[0].check().run()
    app.multiselect[0].set_value(["first"])
    submit(app)
    assert app.session_state["generic_result"]["profile"]["row_count"] == 1
    upload(monkeypatch, app, b"different\ny\nz\n")
    assert not app.exception and app.multiselect[0].value == []
    assert app.checkbox[0].value is False
    submit(app)
    assert app.session_state["generic_result"]["profile"]["row_count"] == 2
    assert app.session_state["generic_result"]["configured_rules"]["required"] == []
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: None)
    app.run()
    assert "generic_result" not in app.session_state


def test_malformed_upload_has_no_stale_result_or_raw_error(app, monkeypatch):
    upload(monkeypatch, app, b"a\n1\n")
    submit(app)
    upload(monkeypatch, app, b'a,b\n"PRIVATE_UNCLOSED')
    assert not app.exception and app.error
    assert "PRIVATE_UNCLOSED" not in app.error[0].value
    assert "generic_result" not in app.session_state


@pytest.mark.parametrize(
    ("content", "numeric", "date", "pattern", "expected", "duplicates"),
    [
        (
            b"product,amount,listed\nA,bad,2026-02-30\nB,,2026-01-01\n",
            "amount",
            "listed",
            "%Y-%m-%d",
            2,
            0,
        ),
        (
            b"sensor,station,captured\n3.1,alpha,2026-10-24\n"
            b"3.1,alpha,2026-10-24\nNaN,beta,2026-02-31\n",
            "sensor",
            "captured",
            "%Y-%m-%d",
            2,
            1,
        ),
        (
            b"Student label,Score %,Class time\nAda,90,08:30\n"
            b"Bea,oops,25:00\nCy,-1,09:00\n",
            "Score %",
            "Class time",
            "%H:%M",
            2,
            0,
        ),
    ],
)
def test_three_upload_structures_through_dashboard(
    app, monkeypatch, content, numeric, date, pattern, expected, duplicates
):
    upload(monkeypatch, app, content)
    next(w for w in app.multiselect if w.label == "Numeric columns").set_value(
        [numeric]
    )
    next(w for w in app.multiselect if w.label == "Date/time columns").set_value([date])
    app.text_input[0].set_value(pattern)
    submit(app)
    result = app.session_state["generic_result"]
    assert result["finding_count"] == expected
    assert result["profile"]["duplicate_rows_beyond_first"] == duplicates
