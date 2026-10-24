"""Read-only Olist profiling with exact counts on one full table at a time."""

import json
from pathlib import Path
from typing import Any

import pandas as pd

from dataguard_ai.ingestion.tables import TABLE_FILES

# Domain-informed proposals, not a search of every possible column combination.
KEY_CANDIDATES = {
    "customers": [["customer_id"], ["customer_unique_id"]],
    "geolocation": [["geolocation_zip_code_prefix"]],
    "order_items": [["order_id"], ["order_id", "order_item_id"]],
    "order_payments": [["order_id"], ["order_id", "payment_sequential"]],
    "order_reviews": [["review_id"], ["order_id"], ["review_id", "order_id"]],
    "orders": [["order_id"]],
    "products": [["product_id"]],
    "sellers": [["seller_id"]],
    "category_translation": [["product_category_name"]],
}

# Child table/column, parent table/column, expected unique parent key.
# False means coverage only: geolocation is not a unique postal-prefix dimension.
RELATIONSHIPS = [
    ("orders", "customer_id", "customers", "customer_id", True),
    ("order_items", "order_id", "orders", "order_id", True),
    ("order_items", "product_id", "products", "product_id", True),
    ("order_items", "seller_id", "sellers", "seller_id", True),
    ("order_payments", "order_id", "orders", "order_id", True),
    ("order_reviews", "order_id", "orders", "order_id", True),
    (
        "products",
        "product_category_name",
        "category_translation",
        "product_category_name",
        True,
    ),
    (
        "customers",
        "customer_zip_code_prefix",
        "geolocation",
        "geolocation_zip_code_prefix",
        False,
    ),
    (
        "sellers",
        "seller_zip_code_prefix",
        "geolocation",
        "geolocation_zip_code_prefix",
        False,
    ),
]


def is_identifier(column: str) -> bool:
    """Protect IDs and postal prefixes from numeric conversion."""
    return (
        column.endswith("_id")
        or column.endswith("_zip_code_prefix")
        or column == "payment_sequential"
    )


def read_csv(path: Path) -> pd.DataFrame:
    """Preserve IDs verbatim; only empty fields are interpreted as missing."""
    columns = pd.read_csv(path, nrows=0, encoding="utf-8-sig").columns
    identifier_types = {name: "string" for name in columns if is_identifier(name)}
    return pd.read_csv(
        path,
        dtype=identifier_types,
        encoding="utf-8-sig",
        keep_default_na=False,
        na_values=[""],
        low_memory=False,
    )


def infer_semantic_type(column: str, values: pd.Series) -> str:
    """Suggest meaning from names and storage; do not validate or convert values."""
    if column.endswith("_zip_code_prefix"):
        return "postal prefix"
    if is_identifier(column):
        return "identifier"
    if column.endswith(("_timestamp", "_date", "_at")):
        return "datetime (name-based guess; unvalidated)"
    if values.notna().sum() == 0:
        return "unknown (no non-missing values)"
    if pd.api.types.is_numeric_dtype(values):
        return "number (measurement, count, code or score; review meaning)"
    return "text (may include categories)"


def profile_key(frame: pd.DataFrame, columns: list[str]) -> dict[str, Any]:
    """Measure a proposed key on complete rows, including composite keys."""
    keys = frame[columns]
    complete = keys.dropna()
    distinct = len(complete.drop_duplicates())
    duplicate_rows = len(complete) - distinct
    missing_rows = len(keys) - len(complete)
    return {
        "columns": columns,
        "missing_rows": missing_rows,
        "distinct_complete_keys": distinct,
        "duplicate_complete_rows": duplicate_rows,
        "uniqueness_percent_non_missing": (
            round(100 * distinct / len(complete), 4) if len(complete) else None
        ),
        "is_candidate_primary_key": (
            len(frame) > 0 and missing_rows == 0 and duplicate_rows == 0
        ),
    }


def profile_table(frame: pd.DataFrame, candidates: list[list[str]]) -> dict[str, Any]:
    """Count missing cells, duplicate parsed rows and proposed-key uniqueness."""
    row_count = len(frame)
    columns = []
    for name in frame.columns:
        values = frame[name]
        missing = int(values.isna().sum())
        columns.append(
            {
                "name": name,
                "observed_dtype": str(values.dtype),
                "dtype_origin": "explicit string"
                if is_identifier(name)
                else "pandas inferred",
                "inferred_semantic_type": infer_semantic_type(name, values),
                "missing_count": missing,
                "missing_percent": round(100 * missing / row_count, 4)
                if row_count
                else 0.0,
            }
        )
    return {
        "row_count": row_count,
        "column_count": len(frame.columns),
        "columns": columns,
        "duplicate_rows": int(frame.duplicated().sum()),
        "candidate_keys": [profile_key(frame, key) for key in candidates],
    }


def check_relationship(
    child: pd.Series, parent: pd.Series, *, parent_unique: bool = True
) -> dict[str, Any]:
    """Check membership without a join that could multiply rows."""
    child_values = child.dropna()
    parent_values = parent.dropna()
    parent_keys = parent_values.drop_duplicates()
    unmatched = child_values[~child_values.isin(parent_keys)]
    parent_duplicates = int(parent_values.duplicated().sum())
    if parent_unique:
        status = (
            "violation_of_proposed_relationship"
            if len(unmatched) or parent_duplicates or parent.isna().any()
            else "no_observed_violation"
        )
    else:
        status = "coverage_gap" if len(unmatched) else "full_coverage"
    return {
        "expected_relationship": (
            "many-to-one (repeated child keys allowed)"
            if parent_unique
            else "postal-prefix coverage (repeated lookup keys expected)"
        ),
        "parent_unique_required": parent_unique,
        "child_missing_rows": int(child.isna().sum()),
        "child_non_missing_rows": len(child_values),
        "child_repeated_key_rows": int(child_values.duplicated().sum()),
        "parent_missing_rows": int(parent.isna().sum()),
        "parent_duplicate_key_rows": parent_duplicates,
        "unmatched_child_rows": len(unmatched),
        "unmatched_distinct_child_keys": int(unmatched.nunique()),
        "unmatched_percent_non_missing": (
            round(100 * len(unmatched) / len(child_values), 4)
            if len(child_values)
            else None
        ),
        "parent_keys_without_children": int((~parent_keys.isin(child_values)).sum()),
        "status": status,
    }


def profile_dataset(input_dir: Path) -> dict[str, Any]:
    """Profile the nine named Olist files, retaining only relationship columns."""
    missing_files = [
        name for name in TABLE_FILES.values() if not (input_dir / name).is_file()
    ]
    if missing_files:
        raise ValueError(
            f"Missing CSV files in {input_dir}: {', '.join(missing_files)}"
        )

    tables = {}
    references = {}
    for table, filename in TABLE_FILES.items():
        frame = read_csv(input_dir / filename)
        reference_columns = set()
        for child, child_column, parent, parent_column, _ in RELATIONSHIPS:
            if child == table:
                reference_columns.add(child_column)
            if parent == table:
                reference_columns.add(parent_column)
        required = reference_columns | {
            column for key in KEY_CANDIDATES[table] for column in key
        }
        missing_columns = required - set(frame.columns)
        if missing_columns:
            raise ValueError(f"{filename}: missing columns {sorted(missing_columns)}")
        tables[table] = {
            "file": filename,
            **profile_table(frame, KEY_CANDIDATES[table]),
        }
        references[table] = frame[sorted(reference_columns)].copy()
        del frame  # Release text/measurement columns before loading the next table.

    relationships = []
    for child, child_column, parent, parent_column, unique in RELATIONSHIPS:
        relationships.append(
            {
                "child": f"{child}.{child_column}",
                "parent": f"{parent}.{parent_column}",
                **check_relationship(
                    references[child][child_column],
                    references[parent][parent_column],
                    parent_unique=unique,
                ),
            }
        )
    return {
        "phase": "1A — Dataset Exploration",
        "input_directory": str(input_dir),
        "pandas_version": pd.__version__,
        "methodology": [
            "Only empty fields are missing; literal NA/NULL and whitespace remain.",
            "IDs/postal prefixes are strings; Pandas infers other storage dtypes.",
            "Semantic types are guesses from names/storage, not validated meanings.",
            "Duplicate counts exclude the first occurrence and compare parsed values.",
            "Key uniqueness uses complete rows; a candidate requires no missing rows.",
            "Keys and relationships are domain-informed proposals, not constraints.",
            "Unmatched non-null references violate proposed parent relationships.",
            "Missing child keys and parents without children are observations.",
            "Repeated child keys are expected; postal-prefix checks measure coverage.",
            "Exact counts; one full table plus reference columns held in memory.",
        ],
        "tables": tables,
        "relationships": relationships,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render the same counts as JSON without an additional report dependency."""
    lines = ["# DataGuard AI — Phase 1A dataset profile", "", "## Method", ""]
    lines += [f"- {note}" for note in report["methodology"]]
    lines += [
        "",
        f"Pandas: {report['pandas_version']}",
        "",
        "## Table overview",
        "",
        "| Table | Rows | Columns | Duplicate rows beyond first |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, table in report["tables"].items():
        lines.append(
            f"| {name} | {table['row_count']:,} | {table['column_count']} "
            f"| {table['duplicate_rows']:,} |"
        )
    for name, table in report["tables"].items():
        lines += [
            "",
            f"## {name}",
            "",
            f"Source: `{table['file']}`",
            "",
            "| Column | Observed dtype | Origin | Semantic guess "
            "| Missing | Missing % |",
            "| --- | --- | --- | --- | ---: | ---: |",
        ]
        for column in table["columns"]:
            lines.append(
                f"| {column['name']} | {column['observed_dtype']} "
                f"| {column['dtype_origin']} "
                f"| {column['inferred_semantic_type']} | {column['missing_count']:,} "
                f"| {column['missing_percent']:.4f}% |"
            )
        lines += [
            "",
            "### Proposed keys",
            "",
            "| Columns | Distinct complete keys | Missing rows "
            "| Duplicate complete rows "
            "| Uniqueness % (non-missing) | Candidate PK in this data? |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for key in table["candidate_keys"]:
            lines.append(
                f"| {', '.join(key['columns'])} | {key['distinct_complete_keys']:,} "
                f"| {key['missing_rows']:,} | {key['duplicate_complete_rows']:,} "
                f"| {key['uniqueness_percent_non_missing']} "
                f"| {key['is_candidate_primary_key']} |"
            )
    lines += ["", "## Relationships", ""]
    for relation in report["relationships"]:
        lines += [
            f"### {relation['child']} → {relation['parent']}",
            "",
            f"- Expected: {relation['expected_relationship']}.",
            f"- Status: **{relation['status']}**.",
            f"- Non-missing child rows: {relation['child_non_missing_rows']:,}; "
            f"missing child rows: {relation['child_missing_rows']:,}.",
            "- Repeated child keys beyond first: "
            f"{relation['child_repeated_key_rows']:,}.",
            "- Parent duplicate keys beyond first: "
            f"{relation['parent_duplicate_key_rows']:,}; "
            f"missing parent rows: {relation['parent_missing_rows']:,}.",
            f"- Unmatched child rows: {relation['unmatched_child_rows']:,}; "
            f"distinct unmatched keys: {relation['unmatched_distinct_child_keys']:,}; "
            f"unmatched % of non-missing: {relation['unmatched_percent_non_missing']}.",
            f"- Distinct parent keys without children: "
            f"{relation['parent_keys_without_children']:,} (observation only).",
            "",
        ]
    lines += [
        "## Interpretation and limitations",
        "",
        "Missing fields may be optional or depend on order status; inspect context.",
        "Repeated customer_unique_id values may represent repeat customers.",
        "Items, payments and reviews may repeat order_id; this alone is not an error.",
        "Multiple geolocation records per prefix can multiply rows in direct joins.",
        "Unmatched categories show translation gaps under a proposed relationship.",
        "No values are cleaned, dates validated, or business rules enforced.",
        "Snapshot uniqueness does not prove future or business uniqueness.",
        "Empty tables have 0% missing cells but no validated candidate key.",
        "This is not streaming: one full table plus reference columns must fit in RAM.",
        "",
    ]
    return "\n".join(lines)


def write_reports(report: dict[str, Any], output_dir: Path) -> None:
    """Write summary metadata only; never copy raw rows into the reports."""
    source_dir = Path(report["input_directory"]).resolve()
    if output_dir.resolve().is_relative_to(source_dir):
        raise ValueError("Output directory must be outside the raw input directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "profile.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "profile.md").write_text(render_markdown(report), encoding="utf-8")
