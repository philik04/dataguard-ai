# Development learning log

Historical entries record what existed at each milestone; early limitations and
test counts describe those checkpoints, not the current implementation. See
[the architecture](ARCHITECTURE.md) and [verification record](VERIFICATION.md) for
the current system and latest executed checks.

# Phase 0 learning log

## What I built

A Python repository with an installable `dataguard_ai` package, a `dataguard-ai`
command, one CLI smoke test, Ruff configuration, and project documentation.
Six empty packages show the planned responsibilities without implementing them.

## How it works

`pyproject.toml` defines package metadata, the Python requirement, the build
backend, the console command, development dependencies, and tool settings.
Setuptools discovers packages under `src/`. uv creates `.venv`, installs the
project in editable mode, and records resolved dependency versions in `uv.lock`.
Editable installation means source edits are available without rebuilding for
each change. Commit the lockfile to share the resolved dependency set.

The command declaration maps `dataguard-ai` to `dataguard_ai.cli:main`.
The installer creates an executable wrapper that calls `main()`. That function
returns `None` and prints a single line. Its return annotation documents intent;
Python does not enforce type annotations at runtime.

pytest runs the installed command in a subprocess and checks successful exit,
exact output, and empty stderr. This verifies the entry-point wiring as well as
the visible behavior. Ruff checks common errors, import ordering, and selected
Python best practices; its formatter checks consistent layout.

## Why these choices

- The `src/` layout keeps application imports dependent on installation, helping
  reveal packaging problems that imports from the repository root can hide.
- uv manages the environment and lockfile in one workflow. Setuptools provides
  a conventional build backend; these tools have different responsibilities.
- There are no runtime dependencies because printing a line needs only Python.
  pytest and Ruff are development tools, not application requirements.
- Small packages communicate boundaries without classes, frameworks, or service
  setup. A package's `__init__.py` currently contains only a descriptive docstring.
- Configuration examples contain placeholders only. Git ignores local secrets,
  environments, generated files, database files, and raw data.

## Alternatives

- A flat package layout is simpler initially but can hide installation mistakes.
- `venv` and pip could manage dependencies, but would need a separate workflow
  for capturing and reproducing the resolved dependency set.
- Hatchling could build this package too; setuptools is sufficient here.
- The standard-library `unittest` could replace pytest. pytest offers concise
  assertions and a straightforward path to later data-quality tests.
- A unit test calling `main()` directly would be shorter, but would not catch a
  broken console-script mapping. The subprocess smoke test checks that mapping.

## What I can explain in an interview

- Distribution names may contain hyphens (`dataguard-ai`); Python imports use
  the underscore name (`dataguard_ai`). The CLI name is configured independently.
- The build backend builds the package; uv manages dependencies and execution.
  The lockfile pins resolutions, while `pyproject.toml` declares allowed ranges.
- A smoke test verifies a small essential path. Linting finds static problems;
  formatting standardizes layout. Neither proves business logic is correct.
- `.gitignore` prevents ordinary addition of matching untracked files; it does
  not remove already tracked secrets or replace careful credential handling.
- Deterministic detection, isolated corruption, evaluation, and AI explanation
  are separate planned responsibilities. None is implemented in Phase 0.

# Phase 1A — Dataset Exploration

## What I built

A read-only Pandas CSV profiler in `ingestion/profiler.py`, reached through
`uv run dataguard-ai profile`. It profiles the nine existing Olist CSVs and
writes matching JSON and Markdown summaries in `reports/generated/`.
The original Phase 0 greeting still works. Pandas is the only new direct runtime
dependency. No database, corruption, AI, or dashboard functionality was added.

## How it works

1. `profile_dataset()` checks that all nine expected filenames exist. It loads
   one table at a time and validates the columns required by the proposed keys
   and relationships. Extra files are ignored.
2. `read_csv()` reads the header first, then passes a dtype mapping to Pandas.
   IDs and postal prefixes become strings, preserving values such as `001`.
   `keep_default_na=False, na_values=[""]` counts only empty fields as missing;
   literal `NA` and whitespace remain values. `utf-8-sig` handles the translation
   file's byte-order mark without changing the file.
3. `profile_table()` uses `len(frame)` and the columns to measure table size.
   `isna().sum()` counts missing cells. Dividing by row count and multiplying by
   100 produces percentages. `duplicated().sum()` counts repeated complete rows
   beyond their first occurrence, comparing parsed values across every column.
4. `profile_key()` selects the proposed key columns, drops incomplete rows, and
   counts distinct tuples with `drop_duplicates()`. A key is a candidate only
   when the nonempty table has no missing or repeated key tuples.
5. `infer_semantic_type()` suggests meaning from names and storage types. A
   timestamp can have observed dtype `object` while its semantic guess is
   datetime. This is not date parsing or proof that every value is a valid date.
6. Only columns needed for relationships are retained after each full profile.
   `check_relationship()` excludes null child keys, then uses `isin()` to test
   membership in parent keys. The `~` operator reverses the Boolean mask so that
   unmatched references can be counted without creating a potentially huge join.
7. `write_reports()` serializes the same result dictionary to JSON and renders
   Markdown using `render_markdown()`. It rejects output inside the raw input
   directory. No raw row values are exported. Repeat runs overwrite reports;
   errors leave older reports in place, so the process exit status matters.

## Why these choices

- Explicit candidate keys and relationships keep the domain assumptions visible.
  They are proposals, not declared database constraints or production rules.
- Identifier strings represent labels, not arithmetic quantities. Converting a
  postal prefix to an integer would lose leading zeros and damage matching.
- Missing fields, repeated keys, duplicate rows, and unmatched references answer
  different questions. Optional review text and unfinished order timestamps need
  context before being treated as problems.
- Repeated child order IDs are expected when an order has multiple items,
  payments, or reviews. A repeated parent key is different: it makes a proposed
  many-to-one relationship ambiguous.
- Postal prefixes in geolocation are intentionally not unique. Those checks
  measure coverage rather than enforce a unique parent constraint. Joining the
  raw lookup directly could inflate row counts and financial totals.
- One table at a time avoids retaining every CSV and all review text together.
  Exact duplicate/key counts still require a full table in memory. This is a
  practical educational tradeoff for the current files, not a streaming engine.
- Synthetic CSVs are generated under `tmp_path`; tests need no private/local data
  and exercise leading zeros, nulls, duplicates, composite keys, orphan references,
  parent ambiguity, lookup coverage, empty tables, errors, and report generation.

## Alternatives

- `chunksize` could reduce table memory, but exact global duplicate/composite-key
  counts would need state across chunks or a disk-based approach. Per-chunk
  uniqueness alone would miss duplicates in different chunks.
- A Pandas merge with `validate="many_to_one"` could check cardinality, but
  membership checks are sufficient here and avoid row multiplication.
- Automatic exhaustive key discovery could find accidental unique columns and
  becomes expensive across many column combinations. Listed proposals are easier
  to explain and challenge.
- Automatic date parsing or broader NA markers could help later, but would change
  interpretation. This phase explicitly documents a conservative missing policy
  and keeps semantic guesses separate from storage dtypes.

## What I can explain in an interview

- A table's grain is what one row represents. An item row differs from an order
  row, which is why `order_id + order_item_id` is unique while `order_id` repeats.
- A candidate primary key needs both uniqueness and completeness. Every passing
  proposal in this snapshot is 100% unique and non-missing, but future guarantees
  require business knowledge and eventually constraints.
- Olist has 99,441 orders, 112,650 item rows, and 1,000,163 geolocation rows.
  Only geolocation has complete-row duplicates: 261,831 beyond the first.
- `customer_unique_id` has 96,096 distinct values in 99,441 records, and
  `review_id` repeats 814 times beyond the first. Neither is a safe standalone
  primary key for its source table. `review_id + order_id` is unique here.
- All checked core ID references match. Thirteen products reference two categories
  absent from translation; 610 missing categories are a separate observation.
  Missing postal-prefix coverage affects 278 customer rows and seven seller rows.
- There are 775 orders without items, one without payments, and 768 without
  reviews. A parent without children is not an orphan child reference; its
  significance depends on lifecycle and business rules.
- Tests verify small known examples; profiling describes the actual snapshot.
  Neither proves that all real-world quality issues have been found.

# Phase 1B — PostgreSQL foundation and ingestion

## What I built

A local PostgreSQL Compose service with persistent storage and a readiness health
check; explicit SQL for nine raw tables; an `ingest` CLI using Psycopg COPY;
transactional replace loading and CSV/database count verification; an ingestion
JSON report; unit tests and opt-in real-database integration tests. The original
greeting and Phase 1A profiler are preserved. Shared filenames moved to `tables.py`
so ingestion does not need to import Pandas. No future-phase functionality exists.

## How it works

- Compose describes the database container; Python does not start it. The named
  volume keeps database files when a container is stopped or recreated. The
  `pg_isready` check measures server readiness, not application correctness.
- `DatabaseConfig.from_environment()` requires explicit connection settings.
  The password is excluded from its representation. Compose reads `.env`; Python
  reads environment variables, so uv's `--env-file .env` bridges the two tools.
- `inspect_csv()` reads CSV records to count them independently of the database.
  `csv.reader` handles quoted commas and embedded newlines, unlike counting lines.
- `ensure_schema()` executes packaged SQL and checks table columns against source
  headers. The schema retains all 52 original column names and allows NULLs.
- `load_tables()` enters one explicit transaction, truncates all nine tables,
  and calls `copy_csv()` once per table. The driver streams records over COPY;
  `write_row()` is not an individual SQL INSERT or per-record commit.
- Each source count must match both the streamed count and `SELECT COUNT(*)`.
  An exception causes rollback; success exits the transaction with one commit.
  Even first-time schema creation is inside that same transaction.
- The CLI writes `ingestion.json` only after commit. File writing is a separate
  operation; if it fails, the CLI reports that the database already committed.
  Old reports can survive a failed run, so exit status and report times matter.

## Why these choices

- Raw TEXT storage preserves input representations. Typed timestamp/numeric fields
  would reject malformed values or normalize formatting before quality checks.
  This consciously prioritizes a faithful landing layer over immediate analytics.
- IDs and postal prefixes are labels. Even numeric-looking IDs must preserve
  leading zeros. Empty fields become NULL, but literal `NULL`, `NA`, `\N` and
  whitespace remain text. The raw layer is decoded CSV data, not a byte archive.
- Timestamp text carries no timezone guarantee. A future validated layer must
  decide local time interpretation; it must not silently label timestamps UTC.
  Future money calculations should use decimal NUMERIC, with scale decided from
  requirements. Raw TEXT avoids both floating-point error and premature rounding.
- No business constraints are imposed on raw records. Missing identifiers,
  duplicate reviews and unmatched categories are evidence for later checks.
- Replace loading is understandable and repeatable for a static portfolio dataset.
  It retains original duplicates but avoids doubling row counts on a rerun.
  It replaces the prior raw snapshot, not other schemas or arbitrary tables.
- COPY avoids one SQL statement/network exchange per row. Parsing one CSV record
  at a time also avoids creating a full Pandas DataFrame during ingestion.
- Integration tests are separate because mocks cannot prove PostgreSQL COPY or
  transactional TRUNCATE behavior. Tests only reset an explicitly selected
  database whose name ends in `_test`; default pytest does not connect to a DB.

## Alternatives

- Typed raw tables would support direct calculations, but would reject invalid
  numeric/date strings. A later validated layer can add types and constraints
  while retaining this raw evidence. That layer is not implemented here.
- Appending every run would require batch/version management to distinguish
  duplicate imports from source duplicates. This phase uses replace loading.
- Upserts require a trustworthy unique key, which some source tables lack.
- A transaction per table would reduce lock duration, but could leave a mixture
  of new and old source tables after a later failure. One transaction avoids that.
- Streaming preformatted blocks could reduce Python overhead, but explicit CSV
  parsing and `write_row()` keep the missing-field policy and row counting clear.
- Docker initialization SQL runs only on a fresh volume. Executing the schema
  from Python makes it available for every ingest and includes DDL in rollback.

## What I can explain in an interview

- A schema is a namespace; a table holds rows. `raw.orders` identifies an orders
  table in the raw namespace, separate from potential later analytical layers.
- BEGIN/COMMIT/ROLLBACK group all nine replacements into one database change.
  PostgreSQL TRUNCATE can roll back; its locks may block concurrent readers.
- Idempotency means the same source produces the same stored values/counts on
  repeat runs. Report timestamps can change; physical row order is unspecified.
- COPY FROM STDIN sends data through the client connection; the server needs no
  access to local source paths. SQL identifiers are quoted with `sql.Identifier`.
- Counts verify completeness, not every value. Synthetic integration tests also
  inspect leading zeros, nulls, duplicate rows, multiline text and invalid values.
- A database transaction cannot also atomically commit a local JSON report. A
  report-write failure after commit must not be described as a database rollback.
- A readiness probe, unit test, database integration test, and full-data import
  provide different evidence. Report only the operations actually executed.

## Verification performed on 2026-10-19

After explicit approval, Compose downloaded PostgreSQL 17 and started a healthy
PostgreSQL 17.11 service. Port 5432 was already allocated, so only the ignored
local `.env` was changed to use host port 55432. The existing service was left
alone. Local credentials were generated in `.env` with owner-only permissions,
never printed or included in a report. The persistent Docker volume was retained.

`uv run pytest` passed 22 tests and deliberately skipped the five integration
tests. After creating `dataguard_ai_test`, the explicit database test command
passed all five integration tests. Those tests exercised real COPY, repeat
loading, retained duplicates/invalid values, and rollback after later failures.

`uv run --env-file .env dataguard-ai ingest` then committed all nine real source
tables, totaling 1,550,922 rows. All independent CSV, streamed COPY and database
row counts matched. A separate read-only SQL check matched every column's NULL
count to the Phase 1A profile, verified 52 nullable TEXT columns and no business
constraints, and confirmed 261,831 repeated geolocation rows were retained.
The committed counts are recorded in `reports/generated/ingestion.json`.

Before/after SHA-256 hashes matched for all nine original CSVs. Ruff lint and
formatting checks and the package build passed. The database is available on
`127.0.0.1:55432`; no commit or push was performed. Later phases remain unstarted.

# Phase 2A — Deterministic Data Quality Engine

## What I built

Eight explicit rules in `quality/rules.py`, a read-only SQL engine, and JSON/Markdown
reporting behind `dataguard-ai validate`. Existing greeting, profiler and ingestion
remain intact. No dependencies, database objects or future-phase features were added.
The registry exposes rule IDs, descriptions, severity and applicable tables.

## How it works

`validate_database()` reuses connection configuration. `validate_connection()`
starts one REPEATABLE READ, READ ONLY transaction with query and lock timeouts.
SQL SELECT DISTINCT builds parent-key sets; GROUP BY/HAVING finds duplicated
complete candidate keys. Server-side cursors stream only rule-relevant fields.
`check_row()` is pure Python: the same record and lookup inputs produce the same
findings without I/O. `write_quality_reports()` writes complete JSON and a bounded
Markdown summary after all rules finish; zero-count rules remain visible.

Missing identifiers include NULL, empty and whitespace-only strings. Monetary
syntax is checked before guarded Decimal conversion; it avoids binary float
rounding and rejects non-finite values. Timestamp syntax is checked before guarded
datetime parsing, which catches impossible calendar values. Raw TEXT is never
cast to numeric/timestamp in SQL or changed in storage. Naive timestamps stay
naive. Comparable datetime pairs are checked for reversed order; mixed timezone
awareness warns rather than inventing a timezone.

Each finding records rule ID, table, severity, classification and evidence. The
row identifier combines the logical key and snapshot CTID. CTID distinguishes
identical duplicates or rows without IDs but is not stable after table rewrites.
Counts represent rule/row/field matches, not distinct bad records. Duplicate-key
findings include all rows in the group, unlike Phase 1A's beyond-first metric.

## Why these choices

- Candidate keys respect grain: order_id repeats legitimately in items, payments
  and reviews. Their composite keys are the contracts. Neither customer_unique_id
  nor review_id alone is assumed unique.
- Explicit functions and dictionaries expose assumptions without framework magic.
  Pure functions make synthetic edge-case tests small and easy to inspect.
- Parent set membership avoids join multiplication. Missing child IDs are checked
  once as missing IDs, not also mislabeled unmatched references.
- Optional review text is not selected. Undelivered/canceled orders can lack event
  timestamps; these are observations. A delivered order lacking a delivery date
  receives a warning because missing export evidence is not proof of no delivery.
- Errors confirm contract violations, not business root causes. Missing expected
  context and mixed timezone comparisons warn. Estimates and unsupported lifecycle
  assumptions are not used to infer chronological errors.
- Read-only snapshot isolation keeps all checks consistent without raw writes.
  Query timeouts bound database work. Geolocation's million-row table is outside
  these rules because no trustworthy primary-key contract was proposed for it.

## Alternatives

- SQL casting might be faster but can abort on malformed TEXT. Guarded Python
  parsing keeps bad inputs inspectable and avoids dependence on SQL evaluation
  order. Python scans add overhead and parent sets/findings still need memory.
- Per-row reference queries would cause excessive round trips. A join could work,
  but would need cardinality handling. Small parent sets are clear for this scale.
- A validation framework could supply generic checks, but would obscure basic
  mechanics during this learning phase. No framework is needed for eight rules.
- Silently normalizing IDs or repairing dates could hide evidence. Validation
  reports problems without deciding how to repair the source.

## What I can explain in an interview

- Table grain determines a key; duplicate order IDs do not imply duplicate items.
- Storage type, parseability, range validity and business meaning are separate.
- REPEATABLE READ gives a consistent snapshot; READ ONLY prevents persistent
  writes. Neither removes the need to manage query cost and memory.
- Optional fields, missing references, invalid strings and reversed valid events
  are different conditions. Good rules avoid cascading claims from one bad value.
- Evidence must identify both the contract and the affected record. CTID is a
  snapshot locator rather than a durable identifier.

The real run scanned 550,759 rows across eight applicable tables and produced
197 findings: 189 DQ007 chronology violations (166 carrier-before-purchase and
23 customer-delivery-before-carrier), plus eight DQ008 delivered-date warnings.
DQ001–DQ006 each found zero violations. Missing timestamp observations are approval
160, carrier 1,783 and customer delivery 2,965; observations may overlap warnings.
Zero findings do not prove that the dataset is entirely clean.

Unit/regression tests and three dedicated-database quality integration tests were
run. The latter use session TEMP tables only; existing ingestion integration tests
were left skipped because their fixtures reset raw. Raw records were not modified.

Interview practice:

1. Why is order_id insufficient as the primary key of order_items?
2. How do Decimal and guarded datetime parsing protect validation of raw TEXT?
3. Why use a read-only repeatable-read transaction for a multi-query report?
4. How do you distinguish a missing foreign key from a missing optional event?
5. Why is CTID useful here, and why should it not become a permanent identifier?

# Phase 2B — Controlled Corruption Pilot

## What I built

A reproducible 1,000-order pilot in `experiment_pilot`, seven explicit mutation
types, stable experiment row IDs, a complete mutation manifest, and before/after
validation comparisons. CLI: `dataguard-ai experiment --orders 1000 --seed 42
--rate 0.07`. Raw remains untouched. There is no full-data corruption, repair,
LLM, dashboard or precision/recall calculation.

`planning.py` contains pure config/planning/comparison functions; `storage.py`
contains guarded SQL; `experiment.py` manages the transaction; `reporting.py`
exports evidence. No new dependency or framework was needed.

## How it works

1. Rank unique nonblank order IDs using md5(order_id + seed), with explicit C
   collation and a tie-breaker. Copy 1,000 orders, all matching child records,
   their referenced parents, and the small category lookup. EXISTS preserves
   multiplicity; a many-row join could accidentally multiply records.
2. Assign `_experiment_row_id` using row_number over a stable sort of every source
   field. It remains fixed when business fields change. Duplicate inserts receive
   their own derived locator. CTID is not used as experiment identity.
3. Run unchanged quality checks on the unmodified pilot. Plan mutations only on
   rows without baseline findings. Each original row is targeted at most once.
4. Calculate floor(orders × rate) events. At 0.07, this is 70 events split evenly
   across seven types, not seven percent of all copied rows. The local Random
   instance and sorted candidates make the plan reproducible for the same inputs.
5. Apply parameterized UPDATEs using the stable locator and an exact preimage
   check with IS NOT DISTINCT FROM. Duplicate events INSERT a copy under a new
   locator. Every event is saved to the database manifest in the same transaction.
6. Compare before/after findings by stable row ID, rule, severity and evidence.
   Separate pre-existing, new and no-longer-reported matches. Full-dataset counts
   are context; subtracting the full 197 from a small pilot would be incorrect.
7. Commit copy/mutations/manifest/run record together. Export files afterward.
   Failure rolls back creation or restores the previous pilot. Filesystem writes
   are a separate boundary; the persisted run supports export recovery.

## Why these choices

- Mutable business keys cannot locate a row after corruption. A separate stable
  experiment key lets us trace even a NULL identifier or duplicate business row.
- Parent closure keeps legitimate relationships intact except for explicit
  broken-reference events. The sentinel is verified absent before mutation.
- Preserving existing findings avoids mistaking source issues for injected ones.
  Clean, distinct targets reduce masking/cascades without changing quality rules.
- The raw and pilot snapshots share a REPEATABLE READ transaction. Experiment
  pre/post checks issue SELECTs inside that write transaction; the public validator
  continues to run read-only. This keeps experiment creation atomic without
  relaxing any rule contract. Source schema guards reject raw write targets.
- An ownership marker prevents replacement of unrelated schemas. A transaction
  advisory lock serializes this pilot's reruns; no CASCADE is used by the writer.
- The small copied dataset and key/finding sets fit memory. Geolocation is not
  needed by these rules, so copying its million rows would add unnecessary cost.

## Alternatives

- Per-experiment physical schemas or append-only batches could preserve history,
  but introduce lifecycle management. This phase has one replaceable owned pilot.
- CTID would be shorter to implement but changes after updates/rewrites, making
  before/after evidence unreliable. Stable local IDs avoid that dependency.
- Separate commits for copy, mutation and manifest would leave partial states.
  One transaction preserves an auditable relationship between changes and evidence.
- Fully random raw-row mutation would complicate parent closure, eligibility and
  pre-existing findings. This pilot isolates seven understandable error patterns.

## What I can explain in an interview

- Sampling and relational closure differ: the selected orders determine which
  child and parent records must accompany them. Sampling rows independently could
  manufacture false orphan references.
- Reproducibility requires fixed input, stable ordering, seed, algorithm version
  and stable locators, not just a seed. Completion timestamps naturally differ.
- Seven mutation events need not mean seven findings. Duplicating a complete key
  flags both original and inserted rows. This is why accuracy metrics require a
  validated matching/granularity methodology first.
- All database changes can commit atomically, while PostgreSQL and local JSON
  files cannot share that transaction. Export failures must preserve this distinction.

The real experiment was `pilot_076502c3a9e05335875e`: 6,688 copied records,
70 mutations (ten per type), and 6,698 records afterward. The selected pilot had
zero pre-existing findings. Afterward DQ001=10, DQ002=20, DQ003=10, DQ004=10,
DQ005=10, DQ006=10, DQ007=10, DQ008=0: 80 new matches, no warnings or disappearing
findings. The original raw baseline stayed at 189 chronological violations and
eight warnings; none happened to fall in this selected pilot.

Two real runs matched selection, manifest, source/corrupted content SHA-256 and
stable findings. A separate read-only validation matched all 80 findings. All
70 committed mutations matched both database/file manifests. All nine raw
counts/content guards matched in the transaction and fresh audit (1,550,922
rows); all source CSV SHA-256 hashes matched. SQL row checksums are non-cryptographic
guards, not archival proof, and in-snapshot checks do not observe others' commits.

Tests: 74 unit/regression cases passed; four experiment and three quality DB
tests passed in `_test`. Tests cover rollback of a late failed rerun, no partial
first schema, refusing unowned schemas, stable IDs and manifest/report fidelity.
Existing ingestion DB tests stayed skipped because their fixtures reset raw.

Plausible incorrect prices, swaps to valid parent IDs, consistent date shifts and
some optional-field changes can evade current checks. This synthetic pilot is
deliberately simple and does not establish real-world detection accuracy.

# Phase 3 — Detection Evaluation Framework

## What I built

An explicit matching policy in `docs/EVALUATION.md`, specified before metric
implementation; independent finding obligations in `evaluation/policy.py`;
baseline-aware, one-to-one matching and metrics in `metrics.py`; read-only
database orchestration in `runner.py`; JSON/Markdown reports in `reporting.py`;
and `dataguard-ai evaluate`. Unit tests cover imperfect results and negative
controls, and database tests exercise the existing detector and saved manifests.
No dependencies or changes to the quality rules or simulator were needed.

## How it works

1. Read the committed run and ordered mutation manifest in a READ ONLY,
   REPEATABLE READ transaction. Verify identities, manifest agreement, event
   budget/seed and the experiment's content SHA-256. Refuse stale evidence.
2. Run the existing quality engine on that same snapshot. Compare predictions
   to the stored run for provenance, but score fresh predictions even if the
   detector's output has changed. No perfect result is assumed.
3. Build expected obligations from independent, explicit mutation contracts.
   One field mutation expects one finding. One duplicate insertion expects two:
   one on the original stable row ID and one on the inserted stable row ID.
4. Exclude exact baseline finding occurrences using a Counter. Keep counts of
   retained/disappeared baseline findings; do not subtract the full raw baseline
   from a smaller pilot. Changed baseline evidence remains visible for review.
5. Match table, stable row ID, rule, severity and relevant evidence. Consume each
   expectation once. Matched new alarms are TP; unmatched new alarms are
   benchmark FP; unmatched expected findings are FN. Save every match link and
   unmatched item so the summary is auditable.
6. Compute precision TP/(TP+FP), recall TP/(TP+FN), and F1
   2TP/(2TP+FP+FN). Undefined denominators return null/N/A. Pool finding counts
   for overall metrics, and calculate event detection separately.

An event is detected when at least one obligation matches; fully detected means
all its obligations match. A partially reported duplicate can therefore count
as a detected event while lowering finding recall. Distinct affected rows are
another count, not interchangeable with mutations or rule findings.

## Why these choices

- The evaluation unit determines the denominator. The verified pilot's 70
  events imply 80 finding obligations, because ten duplicate insertions each
  flag two rows. Dividing 80 findings by 70 mutations would produce nonsense.
- Labels come from the manifest and independently specified contracts, not from
  detector outputs. Generating expected labels from observed alarms would hide
  missed detections. The policy still trusts the recorded ground truth and its
  mutation semantics; it cannot prove that all real-world defects are labeled.
- Stable row IDs survive missing business IDs and distinguish duplicate rows.
  Matching a row alone is too weak: a wrong field, value, rule or warning must
  not satisfy an intended error obligation. Chronology requires the intended
  event pair and changed endpoint; the manifest lacks the unchanged endpoint.
- Counter preserves multiplicity. A set would collapse repeated alarms and
  overstate precision. Consuming obligations prevents duplicate output from
  earning multiple TPs against one label.
- An unmatched alarm is FP under this benchmark, but could be an unlabeled real
  defect or collateral effect. The report preserves evidence instead of treating
  its label as a universal statement about correctness.
- A clean negative control has no positives or predictions, so all three ratios
  are undefined. There is no defined universe of true negatives, and therefore
  no accuracy or specificity score. N/A is more honest than inventing 100%.
- A plausible incorrect price can evade syntax/range rules. An evaluator-only
  sensitivity control includes that event in event detection, explicitly outside
  the finding contract. It creates no finding-level FN. A separate missed-alarm
  unit test verifies that genuinely expected but absent findings do become FN.
- Rejecting overlapping mutation rows keeps attribution explicit for this pilot.
  A larger interacting-corruption benchmark would need a revised policy.

## Alternatives

- Event-only recall would hide incomplete duplicate reporting. Finding-only
  recall would hide out-of-contract semantic changes. Both denominators matter.
- A row/rule join without evidence checks could credit an unrelated field alarm.
  Raw counts alone cannot attribute detections to the injected change.
- Reusing saved predictions without rerunning the detector would evaluate an
  old result rather than current code. Recreating the experiment would risk
  changing the evidence; this command only reads the existing committed pilot.
- Scikit-learn could calculate ratios, but three explicit formulas and ordinary
  dictionaries are easier to explain and avoid adding a dependency. A general
  matching framework is unnecessary for seven disjoint mutation contracts.

## What I can explain in an interview

- Precision asks which new alarms match labels; recall asks which expected
  finding obligations were recovered. F1 balances these at the same unit.
- Ground truth, predictions and matching policy are distinct inputs. Synthetic
  performance is conditional on label coverage, mutation types and selected data.
- One-to-one matching, exact baseline exclusion and stable row identity prevent
  inflated counts. A fixed seed alone does not establish fair evaluation.
- PostgreSQL snapshot isolation makes the metadata, digest and current findings
  consistent; READ ONLY protects persistent tables. Hash checks detect stale
  data, while tests establish how the evaluator interprets it.
- Defined denominators and explicit undefined ratios matter more than a headline
  percentage. A clean control cannot demonstrate positive-class recall.

## Verification performed on 2026-10-19

`uv run pytest`: **93 passed, 16 skipped** (opt-in database tests).

```sh
DATAGUARD_TEST_DB=dataguard_ai_test uv run --env-file .env pytest --run-db-tests tests/integration/test_evaluation_database.py tests/integration/test_quality_database.py -m integration
```

**Seven passed:** four evaluation and three existing quality integration tests.
They use a disposable evaluation schema or TEMP tables in the dedicated `_test`
database. Old ingestion/experiment database suites were not rerun. Evaluation
tests verify clean results, real-engine evasion/duplicates, no source/metadata
writes, and rejection of stale content or conflicting manifests.

`uv run ruff check .` and `uv run ruff format --check .`: **passed**.
`uv run dataguard-ai`: the original Phase 0 greeting remains unchanged.

`uv run --env-file .env dataguard-ai evaluate`: **80 TP, 0 FP, 0 FN** on the
preserved `pilot_076502c3a9e05335875e`. Precision=80/80, recall=80/80,
F1=160/160. DQ001=10 TP, DQ002=20 TP, DQ003–DQ007=10 TP each; DQ008=0 with
undefined ratios. All 70/70 events were detected and fully matched. The pilot
baseline had zero findings; the full-raw baseline of 197 is context only.

The synthetic evasion integration test detected **2/3 events**, while matching
**3/3 finding obligations**: invalid money contributed one and duplication two;
the plausible incorrect price contributed no contracted finding. Unit tests also
exercise actual FN, surplus FP, wrong evidence and partial duplicate recovery.
These results validate policy mechanics, not general real-world performance.

Before/after checks matched all nine raw counts/content guards (1,550,922 rows),
the experiment's SHA-256, all nine CSV hashes and the three existing Phase 2B
report hashes. Non-cryptographic SQL guards are not archival proof. No original
raw/experiment records, quality rules, credentials or existing reports changed.
Only evaluation reports were generated. No commit or push was performed.

Interview practice:

1. Why do 70 mutation events produce 80 expected finding obligations?
2. How does one-to-one evidence matching prevent inflated precision and recall?
3. How do baseline findings and unlabeled genuine issues affect FP interpretation?
4. Why can a clean control have undefined metrics, and a plausible-price test
   have perfect finding recall but only 2/3 event detection?
5. What do READ ONLY, REPEATABLE READ and a content digest each guarantee?

# Phase 4A — AI Explanation Foundation

## What I built

A public `explain_findings(findings, provider)` interface, aggregate evidence
grouping, a reviewed explanation catalog for all eight rule families, a zero-cost
offline provider, a separate disabled OpenAI request-preparation adapter, strict
choice validation, typed structured explanations and JSON/Markdown reports.
CLI: `uv run dataguard-ai explain --provider offline`. No new dependencies,
database operations, paid API calls or automatic corrections were introduced.

## How it works

1. Read the existing completed `quality.json`. Check its shape, completion time
   and agreement between summary counts and actual findings. Keep its SHA-256
   and original validation time in explanation provenance. This is a saved
   snapshot, not a new database scan.
2. Validate rule/table/field/severity patterns against existing contracts. Group
   by rule, logical table, severity, relevant column/pair and known reason.
   Different individual timestamps or identifiers do not split a pattern.
3. Count finding objects and distinct rows locally. Use snapshot CTIDs for raw
   findings and stable experiment IDs for experiments. Preserve repeated finding
   counts; unknown locators produce null row counts. Group row counts can overlap.
4. Pass a frozen `EvidenceGroup` to the provider. Only aggregate counts and
   allowlisted metadata cross that boundary; no identifiers, raw values, actual
   timestamps, comments or arbitrary report metadata are included.
5. The provider returns lists of reviewed hypothesis/impact/investigation IDs.
   Validate exact keys, list types, bounds, known IDs and uniqueness locally.
   Reject unsupported claims instead of asking a regex to recognize every
   possible hallucination in arbitrary prose.
6. Python creates the final `Explanation`: issue summary, evidence-based
   description, hypothesis-labeled possible causes, potential business impact,
   investigation steps and fixed confidence limits. Evidence and interpretations
   occupy separate report arrays linked by group ID; `field_origins` records the
   distinction. Report writes never change the source quality report.

The provider interface uses a small Python `Protocol`: any implementation with
the expected `name` and `explain(group)` method can be used without framework
inheritance. Dataclasses make the group and output fields explicit. Ordinary
dictionaries, sorted keys, `len()` and `set()` handle grouping and counts.

## Why these choices

- The deterministic engine decides what violates a contract. An explanation
  provider cannot change counts, severity or evidence. Grouping 197 findings
  into three patterns avoids repeating almost identical interpretation work.
- Offline text is reviewed template code, not an LLM result. It makes the
  interface, privacy boundary and output policy testable at zero API cost.
- A JSON schema can constrain shape without proving truth. Phase 4A therefore
  permits only reviewed option IDs, with no provider-authored numbers or free
  text. Counts/descriptions come from local evidence, and interpretation is
  clearly conditional. This is intentionally limited generation.
- Hypotheses are not confirmed causes. A potential impact on duration analysis
  is not proof of an operational failure or financial loss. No arbitrary
  confidence percentage is assigned to either statement.
- Optional missing events remain observations unless the engine emitted a
  finding. A delivered order missing a timestamp remains a warning: the export
  can omit an event that actually happened.
- Allowlisting known names is safer than trying to redact all sensitive values
  in raw records. Aggregate business information can still be sensitive, so a
  future live provider needs explicit approval and payload review.
- The OpenAI adapter prepares a Responses request and strict option-ID schema
  following official OpenAI Docs. It has no HTTP transport, SDK or key loading;
  execution and the CLI's OpenAI option fail closed. A key alone enables nothing.
  No live model compatibility has been established.

## Alternatives

- Sending each raw finding would duplicate context and expose identifiers or
  values unnecessarily. Sending only a total per rule would hide distinct
  chronological pairs and warning reasons. Pattern-level groups preserve that
  useful distinction without carrying records.
- Free-form LLM prose would be more flexible but harder to validate for
  unsupported counts, losses or confirmed causes. It needs a later grounding
  and review policy rather than a claim that JSON validity prevents hallucination.
- A validation framework or Pydantic could describe more general schemas. Three
  bounded lists of known IDs need only a small explicit validator in this phase.
- Connecting to the database from explanation would mix fresh validation with
  interpretation. Reading an existing report keeps the two operations separate
  and makes offline reproduction possible when PostgreSQL is stopped.

## What I can explain in an interview

- A provider interface separates application policy from an external service.
  Offline implementations support deterministic tests and a later live adapter.
- Data minimization means constructing an allowlisted aggregate payload, not
  sending records and hoping the prompt ignores private details.
- Structured output validity, evidence grounding and business truth are three
  different concerns. This implementation controls available claims instead of
  claiming that schema validation proves truth.
- Finding counts and distinct affected-row counts are different denominators.
  Missing row locators and overlapping groups must stay explicit.
- Source hashes and snapshot timestamps provide traceability; they neither
  refresh evidence nor prove the original detector found every real issue.

## Verification performed on 2026-10-21

`uv run pytest tests/test_explanations.py`: **37 passed**. Cases cover all rule
families and warning contexts, grouped versus distinct-row counts, missing
locators, stable grouping, privacy sentinels, malformed/extra/unknown choices,
unsupported counts/losses/confirmed causes, empty controls, source preservation,
JSON/Markdown consistency and disabled OpenAI execution even with a placeholder
key present. Request preparation is tested without making an API call.

`uv run pytest`: **130 passed, 16 database tests skipped**.
`uv run ruff check .` and `uv run ruff format --check .`: **passed**.
The original Phase 0 greeting remains available. Existing database suites were
not rerun: explanation has no SQL or database connection path.

`uv run dataguard-ai explain --provider offline` processed the preserved
2026-10-19 original-data validation snapshot: **197 findings, 189 rule violations,
eight warnings, three groups**. DQ007 delivery-before-carrier has 23 findings,
DQ007 carrier-before-purchase has 166, and DQ008 missing delivery timestamps on
delivered orders has eight. Each group's distinct-row count matches its finding
count here; this does not establish a global unique affected-order count.

Actual generated chronology hypothesis: “A source event timestamp may have been
captured or mapped incorrectly.” Actual potential impact: “Reversed timestamps
could distort fulfillment-duration or service-level analysis.” These are reviewed
interpretations, not measured facts. The timestamp pair, rule, severity and counts
are deterministic evidence. For missing delivery timestamps, the explanation
states that the warning does not prove delivery failed.

All ten previous report hashes matched after explanation. Raw and experiment
records were never accessed or changed. No paid API call, repair, dashboard,
commit or push occurred. This foundation does not establish live LLM quality or
general real-world detection accuracy.

Interview practice:

1. Why separate deterministic evidence from generated interpretation?
2. How does grouping reduce request volume while preserving meaningful patterns?
3. Why is JSON schema validity insufficient to prove an explanation is factual?
4. Which data is removed before the provider boundary, and what privacy risks remain?
5. How does an offline provider prepare the architecture for an approved live one?

# Phase 4B — OpenAI Explanation Provider (2026-10-23)

Implemented an optional official-SDK Responses adapter while retaining offline
as the default. This phase is complete with mocked verification only; a real
API call still requires user approval and explicit `--allow-paid-api`.

The existing provider protocol was intentionally preserved: `explain(group)`
returns reviewed hypothesis/impact/investigation IDs. The OpenAI response adds
fixed summary, evidence, evidence description and confidence-limit fields.
The strict schema restricts choices, but local validation is still authoritative:
reject duplicate JSON keys, unknown fields/IDs, malformed lists and any changed
facts. Python creates the final prose from reviewed text. No unrestricted LLM
prose is trusted or persisted. This trades expressive output for a small, auditable
claim surface. It cannot establish whether the selected hypothesis is true.

The lifecycle is input validation -> allowlisted grouping -> whole-report budget
preflight -> lazy SDK client -> one structured request per group -> local response
validation -> report assembly. Checking the saved summary before dispatch avoids
paying for explanations of an inconsistent input. Neither raw findings nor
identifiers cross the boundary. Aggregates and reviewed option text do cross it;
aggregate privacy remains a real consideration.

Learned why the SDK retry default must be overridden explicitly (`max_retries=0`)
and why a timeout does not prove a request was free. The adapter reserves request
count and conservative estimated cost before dispatch and keeps that reservation
after failure. No retry or fallback happens silently. A requested offline fallback
restarts the whole explanation, marks the output and retains attempted usage.

Input token estimation uses UTF-8 bytes plus framing allowance rather than adding
a tokenizer dependency. Output tokens have an explicit API limit. The local cost
budget uses configurable conservative rate assumptions, not billing prices or
an account-wide cap. A pinned model snapshot makes model provenance explicit;
changing models requires checking compatibility and adequate rate ceilings.

Verification on 2026-10-23:

- `uv run pytest`: **161 passed, 16 database integration tests skipped**.
- `uv run ruff check .`: **passed**.
- `uv run ruff format --check .`: **57 files already formatted**.
- `uv run dataguard-ai explain --provider offline`: **197 findings, three groups**.
- `uv run python scripts/demo_openai_explanations.py`: **197 findings, three groups,
  three mock requests** using the real SDK with an in-memory HTTP transport.
  Reports under `reports/generated/openai_mock/` explicitly label synthetic usage.

The 31 new SDK-boundary tests exercise success, malformed/duplicate JSON, changed
facts, unsupported claims, HTTP failure, timeout, refusal, incomplete output,
request/input/cost limits, invalid configuration, early source validation and
explicit fallback. They use dummy credentials and cannot reach an API endpoint.
No live account compatibility or live explanation quality is claimed.

No paid API calls, database mutations, automatic corrections, dashboard work,
commit or push occurred. Phase 4B stops here. Full architecture and five technical
interview questions are in [OpenAI provider notes](OPENAI_PROVIDER.md).

# Phase 5A — Streamlit Portfolio Dashboard (2026-10-23)

Built a local read-only dashboard in `dashboard/app.py` backed by pure report
parsing and projection functions in `dashboard/reports.py`. Streamlit is the only
new direct dependency. Overview, Findings, Corruption & evaluation, and AI
explanations present the existing saved evidence. There are no buttons that
execute validation, connect to PostgreSQL, call OpenAI or modify records.

The central design lesson is to remove private fields before calling any UI
function, rather than hiding columns in a raw dataframe. The server reads the
original report, validates structural fields using existing grouping/catalog
contracts, and constructs new public dictionaries. Business identifier values,
CTIDs, corruption values and arbitrary report text never reach the browser.
Display finding numbers are local positions, not record identifiers.

Streamlit reruns the script after widget changes. A sidebar radio selects a view;
metric cards and charts show report-derived values, and multiselect/text-input
widgets filter a safe findings projection. Search is literal and case-insensitive.
For these small reports, rereading on rerun is simpler than cache invalidation.
The reload button rereads files only, never regenerates evidence.

The dashboard distinguishes original-data findings (189 violations / eight
warnings) from the clean pilot baseline (0) and corrupted pilot findings (80).
There are 70 mutation events; an event can yield multiple findings. Finding-level
precision, recall and F1 are 100% **on the predefined synthetic benchmark only**.
Undefined denominators remain N/A, especially DQ008 without benchmark positives.
Metric values and per-rule totals are reconciled against their count denominators.

The saved controlled live test used three requests, model
`gpt-4o-mini-2024-07-18`, 2,359 input tokens and 606 output tokens; its recorded
conservative estimated cost is USD 0.004783. All three groups' selected reviewed
interpretations match offline selections. The dashboard rechecks source hashes,
evidence and catalog text before showing that comparison. Matching plausible
hypotheses does not establish the actual cause. No new paid request was made.

Missing/malformed reports yield unavailable sections rather than fabricated zeros.
Reports older than seven days are explicitly marked as old snapshots. Mismatched
quality/explanation hashes or pilot/evaluation provenance prevent misleading
comparisons. The app cannot tell whether the database changed after report export.

Verification on 2026-10-23:

- `uv run pytest`: **188 passed, 16 opt-in database tests skipped**.
- 27 new dashboard tests include parser/privacy checks and Streamlit AppTest
  interactions for all views, filters, reload and missing-file states.
- `uv run ruff check .` and `uv run ruff format --check .`: **passed**.
- Launched Streamlit on loopback port 8505 and checked all four views in a browser.
  Adjusted long-number metric cards to remain legible in the narrow desktop panel.
- AppTest blocks socket/DB/API actions and verifies fixture report hashes remain
  unchanged. The dashboard uses no report writer or validation runner.

No database mutations, automatic correction, paid API calls, commit or push.
Stopped after Phase 5A for review. See [dashboard architecture](DASHBOARD.md) for
the data flow, important Streamlit functions, run commands and limitations.

# Phase 5B — Portfolio publication preparation (2026-10-24)

Prepared the existing project for employer review without adding a new product
feature, changing rule contracts, calling a paid API or mutating source data.
The repository still has no commits. The work remains a local proposal for review.

The largest audit issue was that private generated reports could enter Git even
though the UI did not expose their sensitive fields. Presentation privacy and
repository privacy are different boundaries. Added complete ignore rules for
private reports/Streamlit secrets and created a small reproducible report exporter.
Its six public projections preserve measured counts and live selections/usage,
replace private locators with generated display surrogates, discard source values,
and distinguish the public quality-file hash from original API-input provenance.
Original reports and CSVs remain byte-for-byte unchanged. New export destinations
must not already exist, so preparing a projection cannot overwrite private inputs.

The second safety issue was relying on an `_test` suffix before fixture schema
resets. A session-level read-only preflight now rejects the configured source DB,
checks the actual database identity and refuses existing user schemas, relations
or routines. The dedicated test DB was verified empty before the fixtures ran.
All 16 integration tests passed and left it empty. Canonical sorted-row SHA-256
fingerprints of all 19 source raw/experiment tables matched before and after;
all nine CSV hashes and 19 original generated report/assets hashes also matched.
No source records were modified; fixture DDL/DML stayed in the disposable DB.

Verification: **202 normal tests passed, 16 opt-in integration tests skipped**;
separate DB run **16 passed, 202 deselected**, covering **218 tests across the two
runs**. Ruff lint and formatting passed. Offline lock check passed. A temporary
copy containing only publication candidate files passed the same 202 normal tests
without `.env`, raw CSVs or private reports, using the installed dependency runtime.
This is stronger than an in-place demo, but is not a fresh dependency install or
multi-OS/Python test matrix. Four public dashboard views and the eight-result
DQ008 search were checked; screenshots include explicit benchmark limitations.

Rewrote the README around outcomes, quickstart, technologies and limits. Added the
Mermaid architecture diagram, two-minute demo and proposed commit/publication plan.
Reconciled stale documentation about the previously approved live test. The model
still interprets existing evidence by selecting reviewed options; deterministic
rules alone perform detection. The 100% result still means only the predefined
synthetic finding benchmark, with known coverage/evasion limits and N/A for
untested DQ008 positive cases. No financial business impact was measured.

Interview talking points:

1. Explain why raw TEXT preservation plus transactional COPY retains inspectable
   anomalies, and why row-count equality alone is not a value-integrity proof.
2. Explain repeatable-read, read-only snapshots and why disposable-fixture identity
   checks are stronger than trusting a database-name suffix.
3. Derive the 80 expected findings from 70 events and discuss baseline-aware
   one-to-one matching, unsupported semantic errors and absence of a real holdout.
4. Distinguish structured-output shape checks, exact evidence invariants and the
   unresolved causal truth of a selected hypothesis.
5. Explain why column hiding is not privacy, why publication artifacts need their
   own projection, and how public/source hashes support honest provenance.

Remaining owner decisions: code license, dataset/aggregate distribution review,
final staged diff, commit plan and separate permission to push/publish. No license
was guessed and no Git changes were staged. The heuristic/exact-secret scan has
no current findings; it does not replace manual review. See
[Phase 5B audit](PHASE5B_AUDIT.md) and [verification record](VERIFICATION.md).


# Final portfolio presentation polish (2026-10-24)

Polished presentation without changing processing, validation, corruption,
evaluation, AI providers, report parsers or stored evidence. Shared snapshot and
privacy context is concise and appears once per view; exceptional notices remain
visible with source labels. Timestamps and detailed disclosures are retained in
Sources & report times. Shared AI confidence limitations are no longer repeated
for each group.

The imported-record chart now uses a labeled logarithmic dot plot by default.
This makes 71 category-translation rows and 3,095 seller rows visible alongside
1,000,163 geolocation rows. A linear option and exact-count table remain available.
Logarithmic distance represents ratios, and zeros are explicitly omitted only
from that plot, never replaced or removed from the numeric table.

The three identical benchmark bars were replaced by per-rule expected, matched
and missed finding counts. Expected = TP + FN, matched = TP, missed = FN simply
re-expresses the already validated denominators; the evaluation method is unchanged.
The chart highlights DQ002's 20 obligations and DQ008's absence of expected cases.
Undefined scores remain N/A and zero missed findings remain explicit in the table.

The AI view states that the live model selected the same reviewed interpretations
as the offline provider in all three groups, so this test demonstrated no additional
analytical benefit. Provider engineering and guardrails remain worthwhile learning
outcomes, but identical selections are not evidence of better causal analysis.
Interpretations default to stacked layout with an optional side-by-side display;
wrapped evidence text and a shared confidence section are easier to read on mobile.

Verification: **210 normal tests passed, 16 DB integration tests skipped**; Ruff
lint/format passed. Eight new presentation tests preserve disclosure exceptions,
zero handling, numeric counts, no-benefit wording and filters. Four views were
checked at 1280, 662 and 390 px, including mobile DQ008 search (eight matches),
source-details access and stacked AI text. Five screenshots were refreshed.
Before/after SHA-256 checks protect 64 report/data/core-code files. Publication
assets were visually reviewed and scanned for private values/credentials. No paid
API call, DB operation, commit, push or public publication occurred. Owner license,
distribution and final staged-file/publication review remain outstanding.

## Phase 6A — generic CSV analysis (2026-10-24)

The Olist profiler encodes dataset-specific key candidates and relationships. I
kept it and the eight SQL quality contracts intact instead of pretending they
would generalize to unknown CSVs. The new standard-library `generic/analysis.py`
works with arbitrary text columns, a bounded `Dataset` and explicit `Rules`.
Dashboard navigation branches into `dashboard/upload.py` before loading reports.
This preserves the four Olist views and makes generic analysis usable without a
database, reports or credentials. No dependency was added.

The important design distinction is **observation versus constraint**. A complete
unique column is a candidate key in this file, not a declared business key; a
numeric-looking string may still be an identifier. Profiling never changes cells
or automatically enables validation. Required, unique, finite-decimal,
nonnegative and date/time rules are chosen by the user. Decimal parsing avoids
binary float conversion, and optional checks skip blanks unless Required is
selected. Date parsing uses an explicit Python format, including its limitations
around padding, locale and default components. Configuration is checked before
scanning, including repeated/unsupported directives.

`st.file_uploader` delivers bytes to the server, not a purely browser-local
sandbox. The UI says this explicitly and gates the first-20-record preview behind
a checkbox. No upload goes to PostgreSQL, an LLM or persistent application files.
Streamlit session state is temporary memory, not secure erasure or a multi-tenant
privacy guarantee. New source bytes or parser options invalidate old results,
constraints and preview consent. Downloaded JSON omits raw cells and filenames;
headers, positions and aggregates still need review before sharing.

The parser rejects rather than truncates files over byte, row, column, cell,
cell-length or estimated-memory limits. Limits are 5 MiB / 50,000 rows / 100
columns / 250,000 cells / 64 MiB estimated working memory. Python/framework
allocation and concurrent sessions mean this is not a hard RSS guarantee.
Findings are capped at 1,000 details while total counts remain complete. That
separates bounded presentation from the completeness of the accepted-file scan.

Verification: **257 tests passed, 16 database integration tests skipped**. Forty
pure tests and seven UI tests cover unrelated retail, sensor and classroom
schemas, encodings, missing/duplicate values, invalid/negative numbers, malformed
dates, parser limits, privacy-safe errors, preview consent, session replacement
and value-free JSON. UI tests block database/API connections. Ruff lint and
formatting checks pass. A real local browser upload produced seven retail-rule
failures; its downloaded JSON matched the totals without source values. A new
screenshot uses synthetic input only. Existing Olist dashboard/publication tests
pass; all four Olist view functions are byte-for-byte unchanged, and SHA-256
checks found no change to 65 protected CSV/report/core files. The publication
audit found zero findings among 91 candidate files (heuristic, not a guarantee).
No database operation, paid request, correction, commit, push or publication ran.

Interview talking points: why CSV cells remain text; how profiling differs from
validation; why Decimal and explicit date formats are used; how complete failure
counts coexist with sampled details; why loopback/session memory do not imply
safe public hosting; and why Olist's 100% synthetic benchmark says nothing about
accuracy on arbitrary uploads. Phase 6A stops here for owner review.


## Licensing and reproducibility review — 2026-10-24

Verified the publisher's visible Kaggle License section directly: Olist's dataset
is CC BY-NC-SA 4.0. The original software license is separate from dataset-derived
rights. `NOTICE.md` identifies the six projected JSONs and five Olist screenshots,
attributes the source and records transformations. The CSV screenshot uses
synthetic input. Raw CSVs, archives and credentials stay excluded.

Verification recorded at this checkpoint: 257 normal tests passed (16 integration
tests skipped), then all 16 integration tests passed in the verified empty
separate test database. Nineteen protected raw/experiment table fingerprints
matched before/after, and the test database was empty afterward. All nine CSVs
and report JSONs were unchanged. Ruff passed (72 formatted files); the lockfile
checked offline (57 packages). A private-data/credential-free copy passed 61
focused tests covering generic uploads and the projected Olist dashboard. The
96-file candidate at that checkpoint passed the heuristic publication scan. These
are executed historical checks, not new test runs during documentation cleanup.

During subsequent presentation review, the author confirmed **Philipp Kulbatskiy**
as the public copyright holder and project author. Release coordination notes and
staging instructions were kept outside the public documentation. Technical design,
learning decisions, tests and verification evidence remain available. See
[VERIFICATION.md](VERIFICATION.md) for the public verification record.

The central boundaries remain unchanged: deterministic rules establish declared
structural evidence, AI chooses reviewed interpretations without demonstrated
additional analytical benefit in the historical test, and generic CSV analysis
has configurable constraints/resource limits with no claimed general accuracy.


## Final asset boundary — 2026-10-24

Reviewed each of six Olist-derived projections and five screenshots against the
recorded CC BY-NC-SA 4.0 terms. Privacy/provenance passed, but suitability of the
intended employment portfolio under NonCommercial could not be established.
Those eleven files are now ignored and withheld from staging, with their local
bytes preserved. Attribution, license links and the individual decisions remain
in NOTICE.md and DATASET_LICENSE.md. The public demo uses unrelated synthetic CSV
input; measured Olist results are not replaced by invented fixture metrics.

The report-view test now exercises an independently generated synthetic projection
and retains its original real-snapshot assertions as a local-only case. The actual
83-file staged export passed 257 tests with 17 explicit skips (16 database tests,
one excluded local snapshot). No database tests or paid requests ran in this step.
The staged README's screenshot, links and architecture rendered locally. This
reinforces that privacy review, redistribution suitability and application
correctness are separate review boundaries.
