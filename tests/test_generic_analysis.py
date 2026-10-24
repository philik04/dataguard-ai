"""Unrelated synthetic schemas exercise the isolated generic CSV contract."""

import io
import json
from dataclasses import replace

import pytest

from dataguard_ai.generic.analysis import (
    DEFAULT_LIMITS,
    AnalysisError,
    Rules,
    analyze,
    decimal_value,
    parse_csv,
    read_upload,
)


@pytest.mark.parametrize(
    ("csv", "rules", "expected", "duplicates"),
    [
        (
            "sku,price,stocked\nA,12.5,2026-01-01\nA,-2,2026-02-30\n"
            "B,bad,broken\n, ,2026-01-04\n",
            Rules(
                required=("sku", "price"),
                unique=("sku",),
                nonnegative=("price",),
                dates={"stocked": "%Y-%m-%d"},
            ),
            {"CSV001": 2, "CSV002": 2, "CSV003": 1, "CSV004": 1, "CSV005": 2},
            0,
        ),
        (
            "sensor reading;station;captured\n3.1;alpha;24/10/2026\n"
            "3.1;alpha;24/10/2026\nNaN;beta;31/02/2026\n",
            Rules(numeric=("sensor reading",), dates={"captured": "%d/%m/%Y"}),
            {"CSV003": 1, "CSV005": 1},
            1,
        ),
        (
            "Student label,Score %,Class time\nAda,90,08:30\n"
            "Bea,oops,25:00\nCy,-1,09:00\n",
            Rules(
                required=("Student label",),
                unique=("Student label",),
                nonnegative=("Score %",),
                dates={"Class time": "%H:%M"},
            ),
            {"CSV003": 1, "CSV004": 1, "CSV005": 1},
            0,
        ),
    ],
)
def test_three_unrelated_schemas(csv, rules, expected, duplicates):
    dataset = parse_csv(csv.encode(), delimiter=";" if ";" in csv else ",")
    result = analyze(dataset, rules)
    totals = {}
    for count in result["counts"]:
        totals[count["rule_id"]] = totals.get(count["rule_id"], 0) + count["count"]
    assert totals == expected
    assert result["finding_count"] == sum(expected.values())
    assert result["profile"]["duplicate_rows_beyond_first"] == duplicates
    assert all("value" not in item for item in result["findings"])
    assert dataset.rows[0][0] in csv


def test_profile_preserves_text_and_only_suggests_complete_unique_columns():
    data = parse_csv(
        b"code,amount,when,empty,note\n001,1.25,2026-01-01,,NA\n"
        b"002,2,2026-01-02, ,null\n"
    )
    result = analyze(data, Rules())
    assert data.rows[0][0] == "001"
    assert result["finding_count"] == 0
    columns = {c["column"]: c for c in result["profile"]["columns"]}
    assert columns["empty"]["missing"] == 2
    assert columns["note"]["missing"] == 0
    assert columns["amount"]["inferred_type"] == "decimal-like text"
    assert columns["when"]["inferred_type"] == "ISO date/time-like text"
    assert "empty" not in result["profile"]["candidate_unique_columns"]


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "cp1252"])
def test_explicit_supported_encodings(encoding):
    dataset = parse_csv("café,note\nélève,ok\n".encode(encoding), encoding=encoding)
    assert dataset.columns[0] == "café"
    assert dataset.rows == (("élève", "ok"),)


@pytest.mark.parametrize(
    "content",
    [b"", b"a,a\n1,2", b",b\n1,2", b"a,b\n1", b'a\n"unclosed', b"a\n\x00", b"\xff"],
)
def test_invalid_csv_has_safe_error(content):
    with pytest.raises(AnalysisError) as error:
        parse_csv(content)
    assert "unclosed" not in str(error.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"max_bytes": 3},
        {"max_rows": 1},
        {"max_columns": 1},
        {"max_cells": 3},
        {"max_memory_bytes": 50},
        {"max_cell_chars": 1},
    ],
)
def test_limits_reject_without_partial_reports(changes):
    with pytest.raises(AnalysisError):
        parse_csv(b"a,b\n11,22\n33,44\n", limits=replace(DEFAULT_LIMITS, **changes))


def test_bounded_read_does_not_trust_size_metadata():
    upload = io.BytesIO(b"123456")
    upload.size = 1
    with pytest.raises(AnalysisError):
        read_upload(upload, replace(DEFAULT_LIMITS, max_bytes=5))
    assert read_upload(io.BytesIO(b"a\n1")) == b"a\n1"


@pytest.mark.parametrize(
    "value", ["NaN", "Inf", "1,000", "1_000", "one", "1e999999999999999999999999"]
)
def test_decimal_rejects_nonfinite_and_ambiguous_values(value):
    assert decimal_value(value) is None


@pytest.mark.parametrize("value", ["0", "-0.1", "+.5", "1e3", " 1.25 "])
def test_decimal_accepts_finite_decimal_syntax(value):
    assert decimal_value(value) is not None


def test_report_omits_cell_values_and_counts_all_failures_beyond_sample():
    dataset = parse_csv(b"item,cost\nPRIVATE_SAMPLE,wrong\nOTHER_SAMPLE,wrong\n")
    result = analyze(
        dataset, Rules(numeric=("cost",)), replace(DEFAULT_LIMITS, max_findings=1)
    )
    assert result["finding_count"] == 2 and len(result["findings"]) == 1
    assert result["findings_truncated"] is True
    serialized = json.dumps(result)
    assert all(
        value not in serialized for value in ("PRIVATE_SAMPLE", "OTHER_SAMPLE", "wrong")
    )


def test_empty_dataset_and_quoted_multiline_records():
    result = analyze(parse_csv(b"name,score\n"), Rules())
    assert result["profile"]["row_count"] == 0
    assert result["profile"]["candidate_unique_columns"] == []
    dataset = parse_csv(b'name,score\n"two\nlines",bad\n')
    assert analyze(dataset, Rules(numeric=("score",)))["findings"][0]["row_number"] == 1


@pytest.mark.parametrize(
    "rules",
    [Rules(required=("absent",)), Rules(dates={"a": "%Q"}), Rules(dates={"a": ""})],
)
def test_bad_configuration_rejected(rules):
    with pytest.raises(AnalysisError):
        analyze(parse_csv(b"a\n1"), rules)


def test_required_and_optional_semantics_and_duplicate_members():
    result = analyze(
        parse_csv(b"a,b\n ,\nx,-0\nx,-2\n"),
        Rules(required=("a",), unique=("a",), nonnegative=("b",)),
    )
    assert result["finding_count"] == 4
    assert [
        f["row_number"] for f in result["findings"] if f["rule_id"] == "CSV002"
    ] == [2, 3]


def test_timezone_aware_date_format_and_literal_only_config():
    dataset = parse_csv(b"recorded\n2026-10-24T10:20:30+0200\n")
    result = analyze(dataset, Rules(dates={"recorded": "%Y-%m-%dT%H:%M:%S%z"}))
    assert result["finding_count"] == 0
    with pytest.raises(AnalysisError):
        analyze(dataset, Rules(dates={"recorded": "literal"}))


def test_repeated_date_directive_is_a_safe_configuration_error():
    with pytest.raises(AnalysisError):
        analyze(parse_csv(b"a\n2026-2026"), Rules(dates={"a": "%Y-%Y"}))
