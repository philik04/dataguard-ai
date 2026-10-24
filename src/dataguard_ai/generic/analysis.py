"""Bounded, in-memory CSV analysis. No database, filesystem or network access."""

import csv
import io
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


class AnalysisError(ValueError):
    """Safe user-facing input or configuration error; never includes cell values."""


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 5 * 1024 * 1024
    max_rows: int = 50_000
    max_columns: int = 100
    max_cells: int = 250_000
    max_memory_bytes: int = 64 * 1024 * 1024
    max_cell_chars: int = 16_000
    max_findings: int = 1_000


DEFAULT_LIMITS = Limits()
ENCODINGS = ("utf-8-sig", "utf-16", "cp1252")
DELIMITERS = {"Comma": ",", "Semicolon": ";", "Tab": "\t", "Pipe": "|"}
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


@dataclass(frozen=True)
class Dataset:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    encoding: str
    delimiter: str
    estimated_working_bytes: int


@dataclass(frozen=True)
class Rules:
    required: tuple[str, ...] = ()
    unique: tuple[str, ...] = ()
    numeric: tuple[str, ...] = ()
    nonnegative: tuple[str, ...] = ()
    dates: dict[str, str] = field(default_factory=dict)


def read_upload(upload: Any, limits: Limits = DEFAULT_LIMITS) -> bytes:
    """Bound reads even when an upload's declared size is incorrect."""
    if getattr(upload, "size", 0) > limits.max_bytes:
        raise AnalysisError("CSV exceeds the 5 MiB upload limit.")
    upload.seek(0)
    content = upload.read(limits.max_bytes + 1)
    if len(content) > limits.max_bytes:
        raise AnalysisError("CSV exceeds the upload byte limit.")
    return content


def parse_csv(
    content: bytes,
    *,
    encoding: str = "utf-8-sig",
    delimiter: str = ",",
    limits: Limits = DEFAULT_LIMITS,
) -> Dataset:
    """Strict decoding and rectangular CSV parsing; reject rather than truncate."""
    if not content or len(content) > limits.max_bytes:
        raise AnalysisError("CSV is empty or exceeds the upload byte limit.")
    if encoding not in ENCODINGS or delimiter not in DELIMITERS.values():
        raise AnalysisError("Unsupported encoding or delimiter.")
    try:
        text = content.decode(encoding, errors="strict")
    except UnicodeError:
        raise AnalysisError(
            "Cannot decode CSV. Select its encoding explicitly."
        ) from None
    if any(ord(char) < 32 and char not in "\r\n\t" for char in text):
        raise AnalysisError("Binary data or unsupported control characters in CSV.")
    # Reserve headroom for profiling counters, tuples, preview and report objects.
    memory = len(content) + sys.getsizeof(text) * 2
    if memory > limits.max_memory_bytes:
        raise AnalysisError("CSV exceeds the estimated working-memory budget.")
    rows = []
    try:
        reader = csv.reader(
            io.StringIO(text, newline=""), delimiter=delimiter, strict=True
        )
        header = next(reader, None)
        if not header or len(header) > limits.max_columns:
            raise AnalysisError("CSV needs a header with at most 100 columns.")
        if any(not name.strip() or len(name) > 256 for name in header):
            raise AnalysisError(
                "Column names must be nonblank and at most 256 characters."
            )
        if len(set(header)) != len(header):
            raise AnalysisError("Duplicate column names are not supported.")
        memory += sum(sys.getsizeof(name) for name in header) * 4
        for row in reader:
            if len(row) != len(header):
                raise AnalysisError(
                    "CSV row width differs from header; check delimiter."
                )
            if (
                len(rows) >= limits.max_rows
                or (len(rows) + 1) * len(header) > limits.max_cells
            ):
                raise AnalysisError(
                    "CSV exceeds the row or cell limit; no partial analysis."
                )
            if any(len(value) > limits.max_cell_chars for value in row):
                raise AnalysisError(
                    "CSV contains a cell longer than the supported limit."
                )
            memory += 4 * (sys.getsizeof(row) + sum(sys.getsizeof(v) for v in row))
            if memory > limits.max_memory_bytes:
                raise AnalysisError("CSV exceeds the estimated working-memory budget.")
            rows.append(tuple(row))
    except csv.Error:
        raise AnalysisError("Malformed CSV quoting or oversized CSV field.") from None
    if memory > limits.max_memory_bytes:
        raise AnalysisError("CSV exceeds the estimated working-memory budget.")
    return Dataset(tuple(header), tuple(rows), encoding, delimiter, memory)


def decimal_value(value: str) -> Decimal | None:
    """Finite decimal syntax, including scientific notation; no locale guessing."""
    value = value.strip()
    if not NUMBER.fullmatch(value):
        return None
    try:
        number = Decimal(value)
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def profile(dataset: Dataset) -> dict[str, Any]:
    columns = []
    candidates = []
    for index, name in enumerate(dataset.columns):
        present = [row[index].strip() for row in dataset.rows if row[index].strip()]
        distinct = len(set(present))
        missing = len(dataset.rows) - len(present)
        candidate = bool(present) and missing == 0 and distinct == len(present)
        if candidate:
            candidates.append(name)
        inferred = "empty" if not present else "text"
        if present and all(decimal_value(value) is not None for value in present):
            inferred = "decimal-like text"
        elif present:
            try:
                for value in present:
                    datetime.fromisoformat(value)
                inferred = "ISO date/time-like text"
            except ValueError:
                pass
        columns.append(
            {
                "column": name,
                "inferred_type": inferred,
                "missing": missing,
                "distinct_nonmissing": distinct,
                "candidate_unique": candidate,
            }
        )
    return {
        "row_count": len(dataset.rows),
        "column_count": len(dataset.columns),
        "duplicate_rows_beyond_first": len(dataset.rows) - len(set(dataset.rows)),
        "candidate_unique_columns": candidates,
        "columns": columns,
    }


def validate_configuration(dataset: Dataset, rules: Rules) -> None:
    selected = set(
        (
            *rules.required,
            *rules.unique,
            *rules.numeric,
            *rules.nonnegative,
            *rules.dates,
        )
    )
    if not selected.issubset(dataset.columns):
        raise AnalysisError("A configured column is absent from this CSV.")
    for pattern in rules.dates.values():
        if not pattern or len(pattern) > 128:
            raise AnalysisError("Date/time formats must contain 1–128 characters.")
        if not re.search(r"%[aAbBcdHIjmMpSUwWxXyYfzZGVu]", pattern):
            raise AnalysisError("Date/time format needs a date or time directive.")
        try:
            sample = datetime(2004, 1, 2, 13, 14, 15, tzinfo=UTC)
            datetime.strptime(sample.strftime(pattern), pattern)
        except (ValueError, TypeError, re.error):
            raise AnalysisError("Unsupported date/time format configuration.") from None


def analyze(
    dataset: Dataset, rules: Rules, limits: Limits = DEFAULT_LIMITS
) -> dict[str, Any]:
    """Count every failure, retain a bounded sample without source cell values."""
    validate_configuration(dataset, rules)
    findings = []
    counts: Counter = Counter()

    def add(rule: str, row: int, column: str, message: str) -> None:
        counts[(rule, column)] += 1
        if len(findings) < limits.max_findings:
            findings.append(
                {
                    "rule_id": rule,
                    "row_number": row,
                    "column": column,
                    "severity": "error",
                    "message": message,
                }
            )

    for index, column in enumerate(dataset.columns):
        values = [row[index].strip() for row in dataset.rows]
        duplicates = (
            Counter(value for value in values if value)
            if column in rules.unique
            else {}
        )
        for row_number, value in enumerate(values, start=1):
            if not value:
                if column in rules.required:
                    add("CSV001", row_number, column, "Required value is missing.")
                continue
            if column in rules.unique and duplicates[value] > 1:
                add("CSV002", row_number, column, "Nonmissing value is repeated.")
            if column in rules.numeric or column in rules.nonnegative:
                number = decimal_value(value)
                if number is None:
                    add("CSV003", row_number, column, "Value is not a finite decimal.")
                elif column in rules.nonnegative and number < 0:
                    add("CSV004", row_number, column, "Numeric value is negative.")
            if column in rules.dates:
                try:
                    datetime.strptime(value, rules.dates[column])
                except ValueError:
                    add(
                        "CSV005",
                        row_number,
                        column,
                        "Value does not parse with the configured date/time format.",
                    )
    total = sum(counts.values())
    return {
        "schema_version": "generic-csv-1",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {"encoding": dataset.encoding, "delimiter": dataset.delimiter},
        "profile": profile(dataset),
        "configured_rules": {
            "required": list(rules.required),
            "unique": list(rules.unique),
            "numeric": list(rules.numeric),
            "nonnegative": list(rules.nonnegative),
            "dates": dict(rules.dates),
        },
        "finding_count": total,
        "findings": findings,
        "findings_truncated": total > len(findings),
        "counts": [
            {"rule_id": rule, "column": column, "count": count}
            for (rule, column), count in sorted(counts.items())
        ],
        "limits": {
            "max_bytes": limits.max_bytes,
            "max_rows": limits.max_rows,
            "max_columns": limits.max_columns,
            "max_cells": limits.max_cells,
            "max_memory_bytes": limits.max_memory_bytes,
            "max_cell_chars": limits.max_cell_chars,
            "max_findings": limits.max_findings,
        },
        "estimated_working_bytes": dataset.estimated_working_bytes,
        "limitations": [
            "Only configured structural checks; semantic and business errors "
            "may remain.",
            "Inferred types and candidate keys are suggestions, not "
            "constraints or proof.",
            "Missing means empty or whitespace-only; literal NA/null remain text.",
            "Checks trim outer whitespace; duplicate-row profiling compares "
            "original cells.",
            "Row numbers count data records, not physical lines; raw cell "
            "values are omitted.",
            "No database access, corrections or external API calls.",
        ],
    }
