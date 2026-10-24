# Phase 2B — Controlled corruption pilot

## Scope and commands

The simulator owns `experiment_pilot`, never `raw`. It copies at most 1,000
orders and their related records, records every controlled mutation, runs the
unchanged quality rules before/after, and exports evidence. No automatic repair,
full-dataset corruption, accuracy metrics, LLM or dashboard is implemented.

```sh
uv run --env-file .env dataguard-ai experiment --orders 1000 --seed 42 --rate 0.07
# Repeat the same command to verify reproducibility against the stored run.
```

With POSTGRES_* already exported, omit `--env-file .env`. There is no Docker
startup or credential creation in this command. `--output-dir` defaults to
`reports/generated`, outside `data/raw`. It writes `experiment.md`,
`mutation_manifest.json` and full `experiment.json`. Existing Phase 2A
`quality.json`/`quality.md` remain the original raw validation baseline.

The **event budget** is floor(selected order count × rate). Rate 0.07 gives
70 events across 1,000 selected orders: ten for each of seven mutation types.
It is not the fraction of all copied rows, distinct affected orders, or cells.
Types receive events round-robin; a non-divisible budget differs by at most one
event per type. The rate must be finite, above zero and at most one; the budget
must cover all seven types. Order count is capped at 1,000, even via the Python API.

## Architecture and relational closure

- `planning.py`: config validation, pure eligibility/planning, stable finding
  comparison and rerun checks. Randomness uses a local `random.Random(seed)`.
- `storage.py`: schema guards, copy/update/insert SQL and data fingerprints.
- `experiment.py`: one atomic workflow and persisted run metadata.
- `reporting.py`: complete JSON artifacts and readable summaries.

Orders are selected by `ORDER BY md5(order_id || seed), order_id`, using C
collation. This deterministic hash ranking avoids connection-specific random
state. Only unique nonblank order IDs qualify. This is a reproducible pilot,
not a claim of representative statistical sampling; excluding bad order keys
is also a sampling limitation. The selected order IDs are saved in the report.

Related records are copied in this order:

1. The selected orders and all matching items, payments and reviews.
2. Customers referenced by those orders.
3. Products and sellers referenced by their items.
4. All 71 category translations, a small lookup.

`EXISTS` preserves source multiplicity without multiplying rows as a join might.
No child rows are required to exist; a canceled order without items is still
an eligible source order. Existing orphan references remain source evidence;
the copy does not invent parents. Geolocation is deliberately omitted because
it is not a unique parent in the current rule model and is not needed by these
checks. This pilot therefore has eight source-shaped tables, not nine full copies.

## Stable experiment-local IDs

Each copied table retains its source columns plus `_experiment_row_id TEXT
PRIMARY KEY`. It is the only primary key imposed by the experiment; business
keys remain unconstrained so deliberate duplicates and missing IDs can exist.

IDs are assigned with `row_number()` ordered by all source fields using C
collation and NULLS FIRST, then formatted as `order_items:00000001`, etc.
They do not use CTID or mutable business IDs. Exact identical source rows are
indistinguishable but receive separate ordinal IDs; their ID-to-value mapping
is still reproducible. Inserted duplicates receive a new ID derived from the
source locator and event number, such as `...:duplicate:0002`.

An ID remains fixed when order_item_id is nulled or another value changes.
It is stable for the unchanged pilot source/config/code, not a permanent ID
across source revisions. Source/corrupted pilot SHA-256 digests include all
columns and local IDs in a canonical order. Experiment identity is derived
from algorithm version `pilot-v1`, configuration and the copied source digest.
Planner/selection changes require revisiting the algorithm version.

## Mutations and manifest

Targets must have no pilot-baseline findings, including warnings. Each original
row is used at most once across all types. Insufficient eligible targets fail
the run instead of silently lowering the requested event count.

| Mutation | Table / field | Change | Expected rule |
| --- | --- | --- | --- |
| missing_identifier | order_items.order_item_id | Nonblank ID → NULL | DQ001 |
| duplicate_key | order_items, whole row | Insert a copy with a new local ID | DQ002 |
| invalid_money | order_items.price | Valid decimal → not-a-number | DQ003 |
| negative_money | order_items.freight_value | Valid nonnegative amount → -1.00 | DQ004 |
| broken_reference | order_items.product_id | Existing reference → verified-absent sentinel | DQ005 |
| invalid_timestamp | order_items.shipping_limit_date | Valid timestamp → not-a-timestamp | DQ006 |
| reversed_chronology | orders.order_delivered_carrier_date | Valid carrier timestamp → purchase minus one day | DQ007 |

The reverse-event target requires valid purchase/carrier inputs and no existing
row findings. Setting carrier before purchase guarantees the intended reversal
without forcing missing events to exist on an unfinished order. These choices
reduce interactions; they do not simulate every real error mechanism.

Each manifest event includes experiment ID, mutation ID, target table, stable
row locator, source row locator, field, original value, mutated value, mutation
type, seed and expected rule ID. A duplicate is an INSERT, so `field=__row__`,
original value is null (no row existed under its new locator), and mutated value
contains the full inserted business record. Its source locator identifies the
copied original. All other events record exact old/new field values.

## Transactions and important SQL

One REPEATABLE READ transaction contains source guards, selection, copying,
baseline checks, planning, mutations, after checks, database manifest and run
record. A transaction-scoped advisory lock serializes reruns of this namespace.
Query and lock timeouts prevent indefinite waits. Reruns replace only known
tables in an ownership-marked experiment schema using TRUNCATE without CASCADE.
Unmarked schemas are refused. No source table is ever a write target.

Representative SQL shapes:

```sql
CREATE TABLE experiment_pilot.order_items (
    LIKE raw.order_items,
    _experiment_row_id TEXT PRIMARY KEY
);

-- Actual source columns are explicitly listed by the code.
INSERT INTO experiment_pilot.order_items (...)
SELECT ..., 'order_items:' || lpad(row_number() OVER (
    ORDER BY ... COLLATE "C" NULLS FIRST)::text, 8, '0')
FROM raw.order_items s
WHERE order_id = ANY (...selected order IDs...);

UPDATE experiment_pilot.order_items
SET price = 'not-a-number'
WHERE _experiment_row_id = 'order_items:00000001'
  AND price IS NOT DISTINCT FROM '...recorded original...';
```

These are illustrative shapes; the implementation uses `sql.Identifier` for
names and bound parameters for values. Each UPDATE must affect exactly one row.
`IS NOT DISTINCT FROM` compares even nullable preimages safely. Duplicate INSERT
copies original fields while supplying a different control ID. No unsafe casts
of numeric/date source fields or individual raw updates are involved.

`experiment_pilot._mutations` stores structured JSONB events; `_run` stores the
complete run document. Both commit with the mutations. A failed first run leaves
no partial schema; a failed rerun restores the previous experiment. Files are
exported after commit. A file failure is reported as committed-but-export-failed;
the stored `_run` is available for recovery. Files are individually replaced,
not atomically committed together or with PostgreSQL. There is one current pilot,
not an experiment-history system. Replacing a pilot is not source-data repair.

## Validation and comparison

The rule registry and `check_row()` are unchanged. The engine now separates
`validate_snapshot()` (SELECT-only checks in an active transaction) from
`validate_connection()` (the public read-only transaction wrapper).
Experiment pre/post checks use the first inside the atomic write transaction;
their `transaction_read_only` flag is honestly false. Only the experiment writer
issues writes. Normal `validate` and post-commit verification still use the
read-only wrapper. Experiment findings use `_experiment_row_id`, not CTID.

The full raw baseline is context. The correct comparison is the **same copied
pilot before versus after mutation**. Findings are compared by rule, qualified
table, stable local ID, severity and evidence. Reports separate retained existing,
new and no-longer-reported findings; disappearing findings could mean masking.
New findings are associated with event target/source locators. One duplicate
event creates two findings, so event counts and finding counts cannot be equated.
No precision, recall or other detection-accuracy claim is made.

## Verified pilot and integrity

Experiment `pilot_076502c3a9e05335875e` selected 1,000 orders with seed 42 and
rate 0.07. Before mutation: customers 1,000; orders 1,000; items 1,137;
payments 1,038; reviews 998; products 930; sellers 514; categories 71.
There were 6,688 copied records, becoming 6,698 after ten duplicate inserts.

All seven types have exactly ten events. This selected pilot had zero baseline
findings. After mutation: DQ001 10, DQ002 20, DQ003 10, DQ004 10, DQ005 10,
DQ006 10, DQ007 10 and DQ008 0: 80 new rule matches, no warnings, retained
pre-existing matches or disappearing matches. Full raw validation remains
189 chronology violations and eight warnings. Zero pilot-baseline findings do
not imply the full source is clean; the test fixture also exercises a pilot
with a retained pre-existing issue.

Two real runs matched selected orders, mutation manifest, source/corrupted SHA-256
digests and stable-row findings. The second report records those rerun checks.
A separate public read-only validation matched all 80 after findings. Every
manifest value/insert was checked against the committed tables and database
manifest. All nine raw counts and content checksums matched before/after and a
fresh read-only audit: 1,550,922 original rows. All nine CSV SHA-256 hashes matched.

Source SQL guards use row counts plus an order-independent sum of 64-bit row
hashes. They are efficient non-cryptographic checks, not archival proof. The
in-transaction checks catch accidental source changes by this transaction; the
snapshot does not expose concurrent commits by other clients. The fresh audit
checks the committed source afterward. No raw write is issued by this program.

## Tests and limitations

```sh
uv run pytest
DATAGUARD_TEST_DB=dataguard_ai_test uv run --env-file .env pytest --run-db-tests tests/integration/test_experiment_database.py tests/integration/test_quality_database.py -m integration
uv run ruff check .
uv run ruff format --check .
```

Tests cover budgets/caps, deterministic plans, clean-target exclusion, source
preservation, protected schemas, stable findings, relational closure, actual
manifest/report output, reproducible reruns and rollback after late failure.
Database tests use TEMP source tables and one exact owned experiment schema
inside the dedicated `_test` database. Cleanup touches only those test objects.
Old ingestion DB tests are left skipped because their fixtures reset raw.

These easy-to-detect, isolated mutations are not representative of all errors.
Plausible wrong prices, swapped references to existing parents, coordinated date
shifts that preserve ordering and many optional-field changes can evade current
rules. Missing events may intentionally warn or pass. Parent-key duplicates can
affect more records than one mutation; future experiments need a validated
matching/evaluation methodology before claiming accuracy. Only the small pilot
and finding/key sets are retained in Python memory; full-data corruption is blocked.
