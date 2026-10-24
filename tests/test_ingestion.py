"""Unit tests: configuration, CSV parsing and COPY serialization without a DB."""

import csv
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dataguard_ai.ingestion.database import DatabaseConfig
from dataguard_ai.ingestion.loader import (
    copy_csv,
    inspect_csv,
    load_tables,
    write_ingestion_report,
)
from dataguard_ai.ingestion.tables import TABLE_FILES


def test_configuration_requires_explicit_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="Missing environment settings"):
        DatabaseConfig.from_environment()


def test_configuration_does_not_expose_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "POSTGRES_DB": "unit_test",
        "POSTGRES_USER": "example",
        "POSTGRES_PASSWORD": "synthetic-secret",
        "POSTGRES_PORT": "55432",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    config = DatabaseConfig.from_environment()
    assert config.dbname == "unit_test"
    assert config.port == 55432
    assert "synthetic-secret" not in repr(config)
    monkeypatch.setenv("POSTGRES_PORT", "70000")
    with pytest.raises(ValueError, match="POSTGRES_PORT must be"):
        DatabaseConfig.from_environment()


def test_csv_counts_records_not_lines(ingestion_dir: Path) -> None:
    for filename in TABLE_FILES.values():
        info = inspect_csv(ingestion_dir / filename)
        assert info.row_count == 2
        assert not info.columns[0].startswith("\ufeff")


@pytest.mark.parametrize("content", ["", "id,id\na,b\n", ",id\na,b\n"])
def test_csv_rejects_missing_or_ambiguous_headers(tmp_path: Path, content: str) -> None:
    path = tmp_path / "invalid.csv"
    path.write_text(content)
    with pytest.raises(ValueError):
        inspect_csv(path)


def test_csv_rejects_malformed_rows_and_supports_empty_tables(tmp_path: Path) -> None:
    path = tmp_path / "source.csv"
    path.write_text("id,note\n")
    assert inspect_csv(path).row_count == 0
    path.write_text("id,note\n\n001,hello\n")
    assert inspect_csv(path).row_count == 1
    path.write_text("id,note\n001,hello,extra\n")
    with pytest.raises(ValueError, match="wrong field count"):
        inspect_csv(path)
    path.write_text('id,note\n001,"unterminated\n')
    with pytest.raises(csv.Error):
        inspect_csv(path)


def test_copy_stream_preserves_strings_and_maps_only_empty_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.csv"
    path.write_text('id,note\n001,""\n002,NULL\n003,\\N\n004, \n')
    cursor = MagicMock()
    count = copy_csv(cursor, "example", inspect_csv(path))
    assert count == 4
    statement = cursor.copy.call_args.args[0].as_string()
    assert statement == 'COPY "raw"."example" ("id", "note") FROM STDIN'
    stream = cursor.copy.return_value.__enter__.return_value
    assert [call.args[0] for call in stream.write_row.call_args_list] == [
        ["001", None],
        ["002", "NULL"],
        ["003", "\\N"],
        ["004", " "],
    ]
    cursor.execute.assert_not_called()  # No individual INSERTs.


def test_changed_headers_fail_before_copy(tmp_path: Path) -> None:
    path = tmp_path / "source.csv"
    path.write_text("id\n001\n")
    info = inspect_csv(path)
    path.write_text("different_header\n001\n")
    cursor = MagicMock()
    with pytest.raises(ValueError, match="header changed"):
        copy_csv(cursor, "example", info)
    cursor.copy.assert_not_called()


def test_partial_table_imports_and_implicit_transactions_are_rejected() -> None:
    connection = MagicMock()
    connection.autocommit = True
    with pytest.raises(ValueError, match="all nine"):
        load_tables(connection, {})
    connection.autocommit = False
    with pytest.raises(ValueError, match="explicit transaction"):
        load_tables(connection, {})
    connection.transaction.assert_not_called()


def test_ingestion_report_is_separate_from_raw_inputs(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    report = {"status": "committed", "input_directory": str(raw), "tables": {}}
    with pytest.raises(ValueError, match="outside the raw input"):
        write_ingestion_report(report, raw / "reports")
    output = write_ingestion_report(report, tmp_path / "reports")
    assert json.loads(output.read_text()) == report
    assert not output.with_name("ingestion.json.tmp").exists()


def test_cli_distinguishes_report_failure_after_database_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataguard_ai.cli import main
    from dataguard_ai.ingestion import loader

    monkeypatch.setattr("sys.argv", ["dataguard-ai", "ingest"])
    monkeypatch.setattr(
        DatabaseConfig,
        "from_environment",
        lambda: DatabaseConfig("test", "test", "test"),
    )
    monkeypatch.setattr(loader, "ingest_dataset", lambda *_: {"status": "committed"})

    def fail_report(*_args: object) -> None:
        raise OSError("synthetic file failure")

    monkeypatch.setattr(loader, "write_ingestion_report", fail_report)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    assert "Database import committed" in capsys.readouterr().err
