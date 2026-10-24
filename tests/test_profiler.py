"""Synthetic CSV tests independent of the real, ignored Olist data."""

import json
import subprocess
import sysconfig
from pathlib import Path

import pandas as pd
import pytest

from dataguard_ai.ingestion.profiler import (
    TABLE_FILES,
    check_relationship,
    profile_dataset,
    profile_key,
    profile_table,
    read_csv,
    write_reports,
)


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    """Include leading zeros, literal NA, missing cells and a repeated row."""
    path = tmp_path / "sample.csv"
    path.write_text(
        "order_id,customer_zip_code_prefix,price,note,order_purchase_timestamp\n"
        "001,01234,10.5,NA,2020-01-01\n"
        "001,01234,10.5,NA,2020-01-01\n"
        "002,00001,,,not-a-date\n"
        ",00002,0, ,2020-01-03\n",
        encoding="utf-8",
    )
    return path


def test_csv_types_missing_values_and_duplicates(csv_path: Path) -> None:
    frame = read_csv(csv_path)
    assert frame["order_id"].iloc[0] == "001"
    assert frame["customer_zip_code_prefix"].iloc[0] == "01234"
    assert frame["note"].iloc[0] == "NA"
    assert frame["note"].iloc[3] == " "
    report = profile_table(frame, [["order_id"]])
    assert report["row_count"] == 4
    assert report["column_count"] == 5
    assert report["duplicate_rows"] == 1
    columns = {column["name"]: column for column in report["columns"]}
    assert columns["order_id"]["observed_dtype"] == "string"
    assert columns["order_id"]["dtype_origin"] == "explicit string"
    assert columns["price"]["observed_dtype"] == "float64"
    assert columns["price"]["missing_count"] == 1
    assert columns["price"]["missing_percent"] == 25.0
    assert columns["note"]["missing_count"] == 1
    timestamp = columns["order_purchase_timestamp"]
    assert timestamp["observed_dtype"] == "object"
    assert timestamp["inferred_semantic_type"] == (
        "datetime (name-based guess; unvalidated)"
    )
    key = report["candidate_keys"][0]
    assert key["missing_rows"] == 1
    assert key["distinct_complete_keys"] == 2
    assert key["duplicate_complete_rows"] == 1
    assert key["uniqueness_percent_non_missing"] == pytest.approx(66.6667)
    assert not key["is_candidate_primary_key"]


def test_composite_key_works_when_single_key_repeats(tmp_path: Path) -> None:
    path = tmp_path / "items.csv"
    path.write_text("order_id,order_item_id\n001,01\n001,02\n002,01\n")
    frame = read_csv(path)
    assert not profile_key(frame, ["order_id"])["is_candidate_primary_key"]
    composite = profile_key(frame, ["order_id", "order_item_id"])
    assert composite["is_candidate_primary_key"]
    assert composite["uniqueness_percent_non_missing"] == 100.0
    assert frame["order_item_id"].iloc[0] == "01"


def test_empty_csv_and_all_missing_column(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("order_id,value\n")
    empty = profile_table(read_csv(path), [["order_id"]])
    assert empty["row_count"] == 0
    assert empty["columns"][0]["missing_percent"] == 0.0
    assert empty["candidate_keys"][0]["uniqueness_percent_non_missing"] is None
    assert not empty["candidate_keys"][0]["is_candidate_primary_key"]
    path.write_text("order_id,value\n001,\n002,\n")
    report = profile_table(read_csv(path), [["order_id"]])
    assert report["columns"][1]["missing_percent"] == 100.0
    assert report["columns"][1]["inferred_semantic_type"].startswith("unknown")


def test_expected_child_repetition_and_missing_values_are_not_violations() -> None:
    child = pd.Series(["001", "001", None], dtype="string")
    parent = pd.Series(["001", "002"], dtype="string")
    result = check_relationship(child, parent)
    assert result["status"] == "no_observed_violation"
    assert result["child_repeated_key_rows"] == 1
    assert result["child_missing_rows"] == 1
    assert result["parent_keys_without_children"] == 1


def test_relationship_counts_orphans_and_ambiguous_parents() -> None:
    child = pd.Series(["001", "999", "999", None], dtype="string")
    parent = pd.Series(["001", "001"], dtype="string")
    result = check_relationship(child, parent)
    assert result["unmatched_child_rows"] == 2
    assert result["unmatched_distinct_child_keys"] == 1
    assert result["unmatched_percent_non_missing"] == pytest.approx(66.6667)
    assert result["parent_duplicate_key_rows"] == 1
    assert result["status"] == "violation_of_proposed_relationship"
    # Parent duplication alone also violates a proposed unique parent key.
    assert check_relationship(child.iloc[:1], parent)["status"].startswith("violation")
    null_parent = pd.Series(["001", None], dtype="string")
    assert check_relationship(child.iloc[:1], null_parent)["parent_missing_rows"] == 1
    assert check_relationship(child.iloc[:1], null_parent)["status"].startswith(
        "violation"
    )


def test_geolocation_is_a_coverage_check_not_a_unique_parent_constraint() -> None:
    child = pd.Series(["001", "999"], dtype="string")
    parent = pd.Series(["001", "001"], dtype="string")
    assert (
        check_relationship(child, parent, parent_unique=False)["status"]
        == "coverage_gap"
    )
    assert check_relationship(child.iloc[:1], parent, parent_unique=False)[
        "status"
    ] == ("full_coverage")


@pytest.fixture
def olist_dir(tmp_path: Path) -> Path:
    """A tiny linked dataset using the same nine filenames as the real source."""
    contents = {
        "customers": (
            "customer_id,customer_unique_id,customer_zip_code_prefix\n001,u1,01234\n"
        ),
        "geolocation": (
            "geolocation_zip_code_prefix,geolocation_lat\n01234,1.5\n01234,2.5\n"
        ),
        "order_items": (
            "order_id,order_item_id,product_id,seller_id\n01,1,p1,s1\n01,2,p1,s1\n"
        ),
        "order_payments": "order_id,payment_sequential\n01,1\n",
        "order_reviews": "review_id,order_id\nr1,01\n",
        "orders": "order_id,customer_id\n01,001\n",
        "products": "product_id,product_category_name\np1,books\n",
        "sellers": "seller_id,seller_zip_code_prefix\ns1,01234\n",
        "category_translation": (
            "product_category_name,product_category_name_english\nbooks,books\n"
        ),
    }
    raw = tmp_path / "raw"
    raw.mkdir()
    for table, content in contents.items():
        (raw / TABLE_FILES[table]).write_text(content, encoding="utf-8-sig")
    return raw


def test_cli_generates_consistent_reports_without_modifying_inputs(
    olist_dir: Path, tmp_path: Path
) -> None:
    original = {path.name: path.read_bytes() for path in olist_dir.iterdir()}
    output_dir = tmp_path / "reports"
    executable = Path(sysconfig.get_path("scripts")) / "dataguard-ai"
    result = subprocess.run(
        [
            str(executable),
            "profile",
            "--input-dir",
            str(olist_dir),
            "--output-dir",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Profiled 9 tables" in result.stdout
    assert result.stderr == ""
    report = json.loads((output_dir / "profile.json").read_text())
    assert len(report["tables"]) == 9
    assert report["tables"]["order_items"]["row_count"] == 2
    assert report["tables"]["order_items"]["candidate_keys"][1][
        "is_candidate_primary_key"
    ]
    assert len(report["relationships"]) == 9
    assert all(
        relation["unmatched_child_rows"] == 0 for relation in report["relationships"]
    )
    markdown = (output_dir / "profile.md").read_text()
    for table in report["tables"]:
        assert f"## {table}\n" in markdown
    assert "| order_items | 2 | 4 | 0 |" in markdown
    assert "repeated child keys allowed" in markdown
    assert {path.name: path.read_bytes() for path in olist_dir.iterdir()} == original


def test_missing_file_and_schema_errors_are_actionable(olist_dir: Path) -> None:
    orders = olist_dir / TABLE_FILES["orders"]
    orders.write_text("order_id\n01\n")
    with pytest.raises(ValueError, match="olist_orders_dataset.csv: missing columns"):
        profile_dataset(olist_dir)
    orders.unlink()
    with pytest.raises(ValueError, match="Missing CSV files"):
        profile_dataset(olist_dir)


def test_reports_cannot_be_written_under_raw_inputs(olist_dir: Path) -> None:
    report = profile_dataset(olist_dir)
    with pytest.raises(ValueError, match="outside the raw input directory"):
        write_reports(report, olist_dir / "reports")
