# Architecture and decisions

The README contains the Mermaid system diagram. This guide explains the
boundaries behind it and maps them to code.

## Preserve evidence before interpretation

The raw PostgreSQL layer uses nullable TEXT columns and preserves formatting,
invalid strings, duplicate rows and missingness. This intentionally avoids losing
observed anomalies during ingestion. An empty field maps to SQL NULL; other strings
remain unchanged. Streaming COPY avoids per-row round trips. All nine tables are
loaded in one transaction so a late failure restores the previous complete state.
Counts reconcile completeness, not semantic correctness of every value.

`ingestion/loader.py` owns this explicitly mutating import. `quality/engine.py`
performs SELECT-based checks inside a read-only, repeatable-read snapshot, so a
multi-table validation run has a consistent view. Validation does not clean data.
The eight contracts live in `quality/rules.py` and were not changed in Phase 5B.

## Test detections against explicit ground truth

`corruption/planning.py` creates a seeded bounded mutation plan. The experiment
layer copies related records to an owned `experiment_<name>` schema and records
old/new values and stable local locators privately. Writes are forbidden to `raw`
by experiment schema checks; transactional replacement requires an ownership
marker. This protects source data while preserving intentionally invalid examples.

`evaluation/policy.py` creates expected finding obligations independently of
actual predictions. Matching includes table, stable locator, rule, severity and
relevant evidence. `evaluation/metrics.py` matches each obligation/prediction once
and excludes exact pre-existing baseline findings with multiplicity preserved.
Counts of events, rows and finding matches remain separate. The evaluator reads
the committed manifest and fresh predictions in a read-only snapshot, checking a
canonical content digest against the recorded experiment.

The generator and detector share domain assumptions, so this is a contract
benchmark, not an independent real-world ground truth study. A semantically wrong
but structurally valid value can remain undetected.

## Keep the model outside detection

`ai/grouping.py` creates frozen allowlisted aggregate evidence. A provider protocol
allows deterministic offline selection or optional OpenAI selection without
changing detector code. The live adapter sends no business identifier values,
raw records or database credentials. Structured Outputs constrain shape and option
IDs; application validation still rejects changed evidence and unsupported fields.
Python assembles final text from the reviewed catalog. The result remains
hypothesis-labeled and needs investigation.

Timeouts, explicit token limits, conservative request/cost reservations and zero
SDK retries bound a single adapter instance. They are not account-wide billing
controls. An ambiguous failure may have incurred a charge; no automatic repair
request is made. Offline fallback must be explicit. No live API invocation was
performed during Phase 5B.

## Separate private artifacts from public presentation

The raw CSVs, record-level reports and audit checkpoint files are ignored by Git.
`dashboard/publication.py` exports six minimal report projections using validated
counts and reviewed text. It replaces private row locators with generated display
surrogates, retaining grouping multiplicity but providing no crosswalk to source
identifiers. Raw timestamps, monetary strings, review text, selected business IDs
and mutation before/after values are omitted.

The original reports are immutable inputs. Public quality bytes receive their own
SHA-256. Public explanation copies reference that hash and retain the original
quality hash separately; this does not pretend the projected file was the live
API request input. Historical selections and token usage are retained exactly.
Every public file declares `portfolio-projection-v1`, and the dashboard labels it
as a historical privacy projection. These are measured snapshots, not synthetic
replacement metrics.

`dashboard/reports.py` parses and reconciles reports into new safe dictionaries.
In Olist mode, only these dictionaries reach `dashboard/app.py`, so hiding a sensitive dataframe
column is never the privacy mechanism. Filters are pure literal-text operations;
missing reports stay unavailable and undefined metrics stay N/A. The Streamlit
process has no database/API action path. A local viewer is not a publicly hardened
service or a guarantee that all aggregate information is non-sensitive.

## Verification boundaries

Unit tests use synthetic inputs and an in-memory SDK HTTP transport. No paid
request is part of any test. Streamlit AppTest exercises real widget reruns while
blocking sockets/database/SDK requests. Synthetic-projection tests check all four views without private data.
A local-only Olist snapshot check skips when the excluded real assets are absent.

Integration tests use only a dedicated empty `_test` database, with identity,
source-name and existing-object preflight checks. Their fixture DDL/DML is
intentionally destructive only to data created for that disposable session. No
production-like source schema is selected as a test target. Canonical sorted-row
SHA-256 fingerprints before/after the tests verify the protected raw and experiment
content at the two checkpoints. They do not exclude external changes between
checkpoints or provide continuous audit logging.

## Tradeoffs to discuss

The project favors small explicit modules over orchestration frameworks. Some
large-file profiling and parent-key sets still require memory proportional to
data size; the bounded pilot is not a full-scale simulator. Report file pairs are
not transactional with each other or with database commits. The dashboard's live
report directory/model are deliberately tied to the recorded controlled test;
new runs require explicit configuration/review rather than silent discovery.


## Phase 6A: independent generic CSV path

`dashboard/app.py` routes its new upload mode to `dashboard/upload.py` before
loading Olist reports. `generic/analysis.py` handles bounded CSV parsing, heuristic
profiling and five configurable structural rules using only standard-library code.
It has no database, AI, filesystem persistence or Olist-specific column mapping.
Streamlit retains uploads in session memory; only an explicitly requested preview
contains source values. Downloadable reports omit them. This path adds no generic
accuracy claim and never reuses the Olist benchmark as evidence of generalization.
See [GENERIC_CSV.md](GENERIC_CSV.md) for the separate flow and resource boundaries.
