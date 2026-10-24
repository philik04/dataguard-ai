# Phase 1B database design and operations

## Scope

One local PostgreSQL 17 container, a persistent Docker named volume, and nine
raw landing tables. Python streams the local CSVs to PostgreSQL; the raw directory
is not mounted in the container. No cleaning, corruption, LLM, dashboard, or cloud
service is implemented. The existing greeting and profiler remain available.

## Tables and SQL types

The packaged source of truth is
`src/dataguard_ai/ingestion/raw_schema.sql`. It creates `raw` and these tables:

| Table | Source file | Columns |
| --- | --- | ---: |
| raw.customers | olist_customers_dataset.csv | 5 |
| raw.geolocation | olist_geolocation_dataset.csv | 5 |
| raw.order_items | olist_order_items_dataset.csv | 7 |
| raw.order_payments | olist_order_payments_dataset.csv | 5 |
| raw.order_reviews | olist_order_reviews_dataset.csv | 7 |
| raw.orders | olist_orders_dataset.csv | 8 |
| raw.products | olist_products_dataset.csv | 9 |
| raw.sellers | olist_sellers_dataset.csv | 4 |
| raw.category_translation | product_category_name_translation.csv | 2 |

All 52 source columns retain their names and order, including `lenght` spellings.
All are nullable `TEXT`. A raw landing layer records what arrived, including
values that cannot yet be trusted. This is a deliberate SQL type decision:

| Field meaning | Raw storage and assumption | Possible analytical type after validation, not implemented |
| --- | --- | --- |
| IDs and postal prefixes | TEXT; preserve leading zeros, case and spelling | TEXT |
| Timestamps | TEXT; retain supplied text without assuming a timezone or validating dates | TIMESTAMP WITHOUT TIME ZONE if the source truly represents local wall-clock time; timezone interpretation needs a separate decision |
| Price, payment and freight values | TEXT; preserve formatting such as `10.050`, avoid binary floating-point conversion and scale rounding | NUMERIC with precision/scale chosen after validation |
| Counts, scores, dimensions | TEXT; no integer parsing, range checks or rounding | INTEGER/NUMERIC after validation of meaning and range |
| Latitude/longitude | TEXT; retain original decimal representation | NUMERIC or DOUBLE PRECISION after deciding precision needs |
| Missing fields | Empty CSV fields, quoted or unquoted, become SQL NULL | Meaning depends on column and business context |

Using typed timestamp/numeric columns here could reject malformed values before
quality checks can inspect them. The tradeoff is that raw numeric/date columns
cannot yet be used directly for arithmetic or date operations. Raw storage is
different from both Pandas inferred dtypes and inferred semantic meaning.

No primary keys, foreign keys, unique constraints, NOT NULL constraints, length
limits, defaults, or business checks are added. Duplicates, missing IDs, unmatched
references, invalid dates, and non-numeric amounts can therefore be retained.
Invalid CSV structure, invalid UTF-8 and PostgreSQL-incompatible NUL bytes still
cause a failed import; accepting problematic values does not mean ignoring file
format or transport errors. This is not a byte-for-byte CSV archive: CSV quoting
is decoded and empty fields become NULL. Original files remain unchanged.

## Configuration and Docker

Copy `.env.example` to `.env` locally and replace the user/password placeholders.
Never commit `.env`. Compose reads it automatically; the Python application reads
environment variables only. uv can load the same file without another dependency:

```sh
uv run --env-file .env dataguard-ai ingest
```

If variables are already exported, use `uv run dataguard-ai ingest` directly.
`POSTGRES_DB`, `POSTGRES_USER` and `POSTGRES_PASSWORD` are required. Host defaults
to `127.0.0.1` and port to `5432`. Connection attempts have a five-second timeout.

After approval, start the service from the repository root:

```sh
docker compose up -d --wait
docker compose ps
```

`compose.yaml` binds the host port only to `127.0.0.1`, uses the official
`postgres:17` image, and stores data in the `postgres_data` named volume
(Compose prefixes its actual name with the project name). The health check uses
`pg_isready`; it indicates server readiness, not successful application auth or
the existence of the raw schema. The image-created user is a local development
administrator. No production role management is configured.

The image initializes the database/user/password only on an empty data volume.
Editing `.env` later does not change credentials already stored in PostgreSQL.
The major-version image tag receives patch updates; it is not an immutable digest.

```sh
docker compose stop
docker compose down
```

`stop` stops the service; `down` also removes the container/network. Both retain
the named volume. Adding `-v` to `down` deletes the stored databases; this project
does not run that operation automatically. Nothing starts Docker from Python.

## Import flow and transactions

1. Preflight all nine expected files using Python's standard-library `csv.reader`.
   Count CSV records, validate field counts, and handle quoted commas/newlines and
   an optional UTF-8 BOM. Extra files are ignored. Only one record is held at a time.
2. Open one database connection with `autocommit=True`, then enter one explicit
   `connection.transaction()` block. Autocommit outside that block prevents an
   accidental implicit outer transaction; it does not commit each COPY row.
3. Run the schema SQL inside the transaction. Check that source headers exactly
   match each table's column names/order and that existing columns are nullable
   TEXT. `IF NOT EXISTS` does not migrate incompatible tables; mismatches abort.
4. Truncate all nine named tables in one SQL command, without CASCADE or dropping
   the schema. This replaces the previous raw snapshot and acquires locks.
   `SET LOCAL lock_timeout = '10s'` prevents indefinite lock waits.
5. Use one `COPY raw.table (columns...) FROM STDIN` stream per table.
   `copy.write_row()` sends field data through that bulk protocol, not individual
   SQL INSERT statements. Empty fields map to Python `None`, then SQL NULL.
   Other decoded text is unchanged, including literal `NULL`, `NA`, `\N`,
   whitespace, leading zeros, Unicode, commas and embedded newlines.
6. Compare the independent preflight CSV count, the count streamed to COPY, and
   `SELECT COUNT(*)` for each table. Any mismatch raises an error inside the block.
7. Commit once when all nine tables pass. Exceptions roll back schema creation,
   truncation and copied data together. There are no per-table commits.
8. Write `reports/generated/ingestion.json` after the commit, using a temporary
   file and rename to avoid publishing a half-written JSON file.

Important SQL shapes (Python safely quotes table/column identifiers):

```sql
BEGIN;
CREATE SCHEMA IF NOT EXISTS raw;
CREATE TABLE IF NOT EXISTS raw.customers (
    customer_id TEXT,
    customer_unique_id TEXT,
    customer_zip_code_prefix TEXT,
    customer_city TEXT,
    customer_state TEXT
);
-- Other CREATE TABLE statements are in raw_schema.sql.
TRUNCATE TABLE raw.customers, raw.geolocation, raw.order_items,
    raw.order_payments, raw.order_reviews, raw.orders, raw.products,
    raw.sellers, raw.category_translation;
COPY raw.customers (customer_id, customer_unique_id,
    customer_zip_code_prefix, customer_city, customer_state) FROM STDIN;
SELECT COUNT(*) FROM raw.customers;
-- Remaining COPY/count checks, then COMMIT; on failure, ROLLBACK instead.
COMMIT;
```

`COPY FROM STDIN` sends data through the client connection, so the server does
not need filesystem access to the CSVs. The driver handles escaping for NULL,
tabs, backslashes and newlines. Do not add `FORMAT CSV` when using `write_row()`.

## Repeatability and reports

The idempotency strategy is **replace all nine tables**, not append or deduplicate.
Running the same inputs twice produces the same stored row values/counts and
retains source duplicates. PostgreSQL tables do not guarantee physical row order.
There is no incremental loading, source history, or migration framework.

The ingestion report records the database name (no password/DSN), UTC start/end
times, strategy, source filenames, and all three row counts for every table.
It says `committed` only after the transaction exits successfully. An earlier
report is left untouched if an import fails; check command exit status and report
times. Reports contain no raw rows or database error contexts.

Database commits and filesystem writes cannot form one atomic transaction here.
If report writing fails after commit, the CLI explicitly says the database import
committed and exits nonzero. A lost connection during commit can leave the client
uncertain about the outcome; it does not prove a rollback. The CLI says an import
was not confirmed rather than claiming success. Repeating the replace import is
the recovery path. Count equality is a useful completeness check, not proof that
every value is identical. Do not edit inputs while ingestion is running.

## Tests

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The normal suite runs the greeting, profiler and ingestion unit tests and skips
database tests without connecting. Ingestion units check configuration, secret
representation, BOM/multiline CSV parsing, empty files, malformed inputs, COPY
serialization, full-dataset requirements and report-writing behavior.

Integration tests have the `integration` marker and require `--run-db-tests`,
connection settings, and `DATAGUARD_TEST_DB` ending in `_test`. They reset the
entire `raw` schema in that dedicated database before and after each test, using
synthetic CSVs only. They never create or choose the test database implicitly.
After approval, create it once using the running container's local credentials:

```sh
docker compose exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" dataguard_ai_test'
DATAGUARD_TEST_DB=dataguard_ai_test uv run --env-file .env pytest --run-db-tests -m integration
```

Those tests exercise actual PostgreSQL COPY, duplicate and raw-text preservation,
repeat imports, rollback after a later table fails, row-count mismatch rollback
including first-time DDL, schema mismatch handling, and CLI/report integration.
Use a dedicated disposable test database: the `_test` suffix is a guard, not proof
that its contents are expendable. Python does not drop a database or volume.

## Phase 5B test safety preflight

Integration tests now reject a target equal to configured `POSTGRES_DB`, even if
that source name ends in `_test`. Before any fixture executes DDL/DML, a session
preflight connects to the explicitly selected test database, verifies its identity
and refuses pre-existing user schemas, relations or routines. A suffix alone is
not proof of disposability. Use a separate empty database; do not delete someone
else's objects to satisfy the guard. The tests still create/drop only their known
synthetic fixtures and temporary tables after this check.

On 2026-10-24 the existing dedicated test database was inspected read-only and
found empty. All 16 integration tests passed; afterward it was empty again. Sorted
canonical-row SHA-256 fingerprints for all 19 protected `raw`/`experiment_pilot`
tables matched before/after, as did SHA-256 for all nine original CSVs. No source
records were modified. See [Phase 5B audit](PHASE5B_AUDIT.md).

## Verified local run

The owner approved database startup, integration tests and the full import on
2026-10-19. PostgreSQL 17.11 started healthy on `127.0.0.1:55432`; the default
host port 5432 was already allocated, so only the ignored local `.env` was changed.
No existing service was stopped. Both the main database `dataguard_ai` and the
separate `dataguard_ai_test` database reside in the persistent named volume.

All five real-database integration tests passed, in addition to 22 unit/regression
tests. The full ingest committed 1,550,922 rows across nine tables. All three row
counts agreed per table. An independent read-only SQL check matched each column's
NULL count to Phase 1A, verified all 52 nullable TEXT columns and zero business
constraints, and counted the expected 261,831 geolocation row repetitions.
SHA-256 hashes confirmed that all original CSV bytes remained unchanged.
The local ingestion report records the import; its Olist-derived projection is
withheld from this release (see [the asset review](DATASET_LICENSE.md)).

## References

- [Psycopg COPY and write_row](https://www.psycopg.org/psycopg3/docs/basic/copy.html)
- [Psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)
- [PostgreSQL transactional TRUNCATE and locking](https://www.postgresql.org/docs/17/sql-truncate.html)
- [Official PostgreSQL container configuration](https://hub.docker.com/_/postgres)
