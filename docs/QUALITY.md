# Phase 2A — Deterministic Data Quality Engine

## Architecture and execution

Phase 2B keeps the rule contracts below unchanged. `validate_connection()` still
provides the read-only wrapper; its SELECT-only body is now `validate_snapshot()`
for use inside an already active transaction. Owned `experiment_<name>` schemas
are supported and use `_experiment_row_id` instead of snapshot CTID for findings.
See [Experiment transaction boundaries](EXPERIMENTS.md) for the atomic write
workflow and separate public read-only verification.

`quality/rules.py` contains explicit keys, references, the eight-rule registry,
safe parsers and pure `check_row()` logic. `engine.py` reads a consistent SQL
snapshot and calls those checks. `reporting.py` produces JSON and Markdown.
Existing connection configuration is reused; there are no new dependencies.

```sh
uv run --env-file .env dataguard-ai validate
# With POSTGRES_* variables already exported:
uv run dataguard-ai validate
```

Reports are `reports/generated/quality.json` (all evidence) and `quality.md`
(exact counts and up to three examples per rule). `--output-dir` is supported
outside `data/raw`. Successful validation exits zero even with findings. Execution
failures exit nonzero and can leave older reports in place. Each file is replaced
through a temporary file, but replacing the pair is not an atomic operation.

## Rule contracts and severity

An error confirms a violation of a declared rule, not its business cause.
A warning needs context; observations are aggregate context rather than errors.

| ID | Default severity | Contract |
| --- | --- | --- |
| DQ001 | error | Required key/identifier is NULL, empty or whitespace-only. Includes customer_unique_id, core reference IDs, composite sequence IDs and the translation key. Postal prefixes are outside this contract. |
| DQ002 | error | Every row in a repeated complete candidate-key group is affected. Incomplete keys are handled by DQ001 instead. |
| DQ003 | error | Non-missing price, freight_value or payment_value is not a finite decimal under the declared syntax. |
| DQ004 | error | Parsed amount is below zero. Zero is allowed; no arbitrary maximum is imposed. |
| DQ005 | error | Non-missing core reference has no exactly matching parent. Blank references are DQ001 instead, avoiding duplicate claims. |
| DQ006 | error | Non-missing event text fails the declared ISO datetime syntax or calendar/time validation. |
| DQ007 | error | A later event precedes an earlier event. If one has a timezone offset and the other has none, warn instead because comparison needs context. |
| DQ008 | warning | Missing item/payment amount, purchase timestamp, or customer-delivery timestamp on an order marked exactly delivered. |

The registry records ID, description, default severity and applicable tables.
DQ007 has the only contextual severity override. Zero-count rules remain in reports.

### Keys and relationships

| Table | Candidate key |
| --- | --- |
| customers | customer_id |
| orders | order_id |
| order_items | order_id + order_item_id |
| order_payments | order_id + payment_sequential |
| order_reviews | review_id + order_id |
| products | product_id |
| sellers | seller_id |
| category_translation | product_category_name |

`customer_unique_id` and `review_id` alone are intentionally not unique contracts.
Repeating a child order ID is legitimate. Geolocation has no candidate key and is
not scanned here; its repetitions and postal coverage remain Phase 1A observations.

Core references: orders → customers; items → orders/products/sellers;
payments → orders; reviews → orders. Parent sets use SELECT DISTINCT and exact
string membership. This avoids joins that multiply child rows when parents repeat.
Parent duplication is still checked by DQ002. IDs are not trimmed or converted.

Chronology pairs require approval, carrier and customer-delivery events at or
after purchase, customer delivery at or after carrier handoff, and review answer
at or after creation. Equality is allowed. Missing/invalid inputs skip chronology.
Estimated delivery is not an actual event, so early delivery versus an estimate
does not fail. Approval-before-carrier ordering is not assumed without stronger
knowledge of the source process.

## Important SQL and safe conversion

```sql
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '3s';

SELECT DISTINCT customer_id FROM raw.customers;

SELECT order_id, order_item_id, COUNT(*)
FROM raw.order_items
GROUP BY order_id, order_item_id
HAVING COUNT(*) > 1;

SELECT ctid::text AS _row_locator, order_id, order_item_id,
       product_id, seller_id, price, freight_value, shipping_limit_date
FROM raw.order_items;
```

One explicit read-only transaction gives all SELECTs the same snapshot and blocks
writes to persistent tables. Validation issues no DDL, INSERT, UPDATE, DELETE,
TRUNCATE, schema reset or index creation. Timeouts bound each query/lock wait,
not total Python runtime. Psycopg `sql.Identifier` quotes identifiers safely.

Server-side cursors stream source rows, using batches of 2,000 for table reads.
Only rule-relevant fields are selected. Parent sets, duplicate groups and findings
still need memory. No numeric/date raw TEXT is cast in SQL; the system CTID-to-text
cast is safe metadata formatting, not conversion of untrusted source text.

- Missing detection uses `None` and `str.strip()`, including tabs/newlines and
  Unicode whitespace. Literal `NULL` and `NA` remain nonblank strings.
- Money accepts optional sign, digits/decimal point and optional exponent.
  Currency symbols, separators and underscores are outside the contract.
  Guarded `Decimal` conversion rejects exceptions/non-finite values and avoids
  binary float rounding. Outer whitespace is accepted for parsing only.
- Timestamps require a four-digit-year ISO date plus HH:MM:SS, space or T
  separator, optional 1–6 fractional digits, and optionally Z or ±HH:MM.
  Guarded `datetime.fromisoformat()` validates calendar/time values. Date-only
  and locale-specific strings are outside this contract. Offset-free values
  stay naive; no UTC assumption is invented. Aware pairs compare instants,
  naive pairs compare wall-clock values, and mixed pairs warn. Alternate
  legitimate source formats would require review of the contract.

## Findings and acceptable missing values

Each finding has rule ID, logical raw table, candidate key plus `snapshot_ctid`,
severity, classification and evidence: offending field/value, duplicate-group
size, missing parent or chronological pair. CTID distinguishes duplicate rows
or rows without an ID inside this snapshot. It can change after updates/rewrites
and must never become a permanent business identifier.

A row can have several findings. Counts are rule/row/field matches, not unique
bad records. DQ002 counts all rows in a duplicate group; Phase 1A's duplicate-row
count instead excludes the first occurrence. These metrics answer different questions.

Optional review comments are never selected. Unfinished/canceled orders may
legitimately lack approval or delivery events. Missing event counts are aggregate
observations. A delivered order without a delivery timestamp warns: the event
may have occurred but be absent from the export. These observations overlap
warnings and must not be added to them. No mandatory child rows, arbitrary ranges,
ID lengths, payment totals, category or postal integrity constraints are invented.

## Tests and actual results

```sh
uv run pytest
DATAGUARD_TEST_DB=dataguard_ai_test uv run --env-file .env pytest --run-db-tests tests/integration/test_quality_database.py -m integration
uv run ruff check .
uv run ruff format --check .
uv run --env-file .env dataguard-ai validate
```

Units cover valid/invalid records, whitespace IDs, composite keys, decimal edge
cases, calendar dates, chronology, lifecycle and timezone context. Three new
integration tests use only small session TEMP tables in the dedicated `_test`
database, verifying real SQL, findings and unchanged sources. Closing the session
discards only TEMP tables. Existing ingestion DB tests are not rerun during this
phase because their fixtures reset raw; they remain skipped by default.

The supplied snapshot scanned 550,759 rows across eight applicable tables.
DQ001–DQ006 each found zero violations. DQ007 found 189 chronology violations:
166 carrier dates before purchase and 23 customer deliveries before carrier
handoff. No delivery-before-purchase case was found. DQ008 warned on eight orders
marked delivered without a customer-delivery timestamp. Missing-event observations
were approval 160, carrier 1,783, and customer delivery 2,965. These results do not
establish root causes; a zero count means only these rules found no violation.
