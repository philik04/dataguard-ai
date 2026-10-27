# Phase 5A — Streamlit portfolio dashboard

The **Olist portfolio mode** is a presentation layer over existing generated reports. It does
not connect to PostgreSQL, execute the validation engine, call an explanation
provider, mutate data, or offer automatic correction. Only Streamlit was added as
a direct dependency; Pandas was already present. No new chart framework is needed.

## Run and navigate

From the repository root:

```sh
uv sync --locked
uv run streamlit run src/dataguard_ai/dashboard/app.py
```

The default address is `http://127.0.0.1:8501`. The verification server used port
8505 (`--server.port 8505`). The sidebar selects Overview, Findings, Corruption &
evaluation, or AI explanations. At narrow widths, expand the sidebar with the
upper-left chevron. Metric cards use two columns for long counts and costs so
these remain readable in the desktop app's side panel; Streamlit stacks columns
at narrower widths. Explanation layout defaults to Stacked, with a Side by side option.

For a different saved-report directory, set `DATAGUARD_REPORT_DIR` in the server's
process environment. The Olist mode has no report file picker or path input. Default
resolution is relative to the working directory, so start from the project root.
`.env` is not loaded. `.streamlit/config.toml` provides a light blue/slate theme,
disables Streamlit usage telemetry, and binds to `127.0.0.1`. This is local-only
verification, not a deployment or an authentication/access-control system.

## Data flow and files

```text
Saved JSON on disk
       |
       v
reports.py: shape checks, reconciliation, privacy projection, provenance
       |
       v
Report(data=validated public fields, timestamp, notices, error)
       |
       v
app.py: navigation -> filters -> metrics / charts / tables / reviewed text
```

| File relative to report directory | View / purpose |
| --- | --- |
| `ingestion.json` | Nine allowlisted table counts; sum of imported rows |
| `quality.json` | Original findings, structural fields, deterministic descriptions |
| `experiment.json` | Allowlisted mutation counts and isolated pilot before/after |
| `evaluation.json` | Finding-level metrics, per-rule denominators, event detection |
| `explanations.json` | Saved offline catalog selections |
| `openai_live_test_2026-10-23/explanations_openai.json` | Saved live selections, model and usage |

The live directory is deliberately fixed to the known controlled test. The viewer
does not discover arbitrary model reports, substitute mock responses or choose
another snapshot silently. Supporting another approved run is a small explicit
configuration/code change. This prevents accidental presentation of mock usage
as live usage. Missing reports do not trigger regeneration.

`load_dashboard()` loads sections independently. A missing or malformed report
makes that section unavailable; working sections still render. Empty valid
findings mean zero, while a missing report means N/A. Parser errors shown in the
UI are fixed messages, not raw report values or exception bodies.

Every report needs a timezone-aware completion timestamp. Snapshot times are
listed in **Sources & report times** in UTC, with a warning after seven days or for timestamps over five minutes
in the future. Seven days is a UI freshness policy, not an assertion that findings
expire. Old snapshots remain viewable; the dashboard cannot determine whether the
underlying database changed since export. Each widget rerun rereads reports.
There is intentionally no caching for these small saved snapshots, so the Reload
button cannot leave a stale parsed report in a cache. Report files written while
viewing may temporarily be inconsistent; refresh again after generation finishes.

Explanation report evidence and SHA-256 must match the current quality file;
mismatches make the explanation section unavailable. Each explanation's fixed
text and selected options are checked against the reviewed catalog. Model names
are restricted to the recorded GPT-4o Mini snapshot. Cost must reconcile with the
recorded token counts and configured rate assumptions. Only projected public
usage fields are displayed, never arbitrary API metadata or raw model output.

Evaluation metric values must reconcile to TP/FP/FN and declared expected/new
finding counts. Per-rule totals must match overall totals. Its verified corrupted
content digest and mutation-event total must match the experiment report. If the
pilot report is absent, the evaluation can still be shown with an explicit warning
that cross-report provenance could not be checked. These checks establish report
consistency, not authenticity of an externally supplied malicious report.

## Olist privacy boundary

`reports.py` reads raw reports **on the server only**. Before passing data to any
Streamlit call, it creates new dictionaries containing selected safe fields.
Hiding a column in a dataframe would not be sufficient, since hidden data could
still be sent to the browser. The original raw dataframe is never constructed
for display. Raw reports are not exposed as JSON, downloadable files, links,
HTML, or debug output.

The findings table contains a generated display position, rule ID, unqualified
table, severity, column names, known pattern and reviewed description. Source
values, order/customer ID values, CTIDs, experiment row locators, timestamps in
source records, comments and arbitrary evidence fields are omitted. Names such
as `customer_id` can appear as a **column name**, never as a business ID value.
The rule registry and grouping/catalog helpers are reused without changing rules
or revalidating the database.

Corruption presentation excludes selected order IDs and manifest row locators,
original values and mutated values. Only aggregate mutation counts and pilot
finding totals are shown. Ingestion excludes database connection details and
paths. Explanation prose must match reviewed catalog options, so unsupported
report text cannot be rendered as an asserted cause. Aggregated business counts
may still be sensitive; a local viewer is not permission to publish them.

## Metrics and honest interpretation

- Imported rows are summed across nine tables, not unique orders.
- Original validation has 189 chronology rule violations and eight warnings.
  A warning is not a confirmed violation; missing delivery time does not prove
  delivery failed. A finding count is not a count of unique affected orders.
- The isolated pilot starts with zero findings and ends with 80. It has 70
  mutation events. Duplicate-key injection can yield two findings, so event and
  finding counts are different denominators. The original 197 findings are not
  the pilot's baseline.
- Precision = TP / (TP + FP); recall = TP / (TP + FN);
  F1 = 2TP / (2TP + FP + FN). A zero denominator stays `None` internally and N/A
  in the UI, including DQ008's untested benchmark metrics. Valid zero is 0.0%.
  Cards retain these scores. The coverage chart shows expected (= TP + FN), matched
  (= TP) and missed (= FN) findings per rule, using the existing validated counts.
  The table also retains FP and undefined scores; this is display arithmetic,
  not a change in evaluation policy.
- Current 100% benchmark scores are limited to the predefined synthetic corruption
  scenarios and matching policy. They are not general real-world accuracy or
  evidence that all eight rules have positive benchmark coverage.
- Historical live usage: three requests, 2,359 input tokens, 606 output tokens;
  estimated USD 0.004783 using conservative recorded rates. This is not an invoice
  or a new dashboard cost. The saved live selections match the saved offline
  selections in all three groups. No additional analytical benefit was demonstrated
  by that test, and agreement does not establish causal truth.

## Streamlit implementation notes

`st.set_page_config()` sets the title, icon and wide layout. Streamlit reruns the
script when a widget changes; `st.sidebar` and `st.radio()` select which rendering
function executes. `st.columns()` and bordered `st.container()` arrange metric
cards. `st.metric()` formats numbers provided by the report projection.

`st.multiselect()` collects rule, severity and table filters. `st.text_input()`
collects a literal, case-insensitive search string; it is not executed as a regex
or SQL. Filtering is a pure Python function over the projected rows and affects
only the findings table, not snapshot totals. An empty selected filter yields
no matching rows. `st.dataframe()` provides sortable/searchable tables with safe
columns only. Streamlit table export features contain only that projection.

`st.vega_lite_chart()` renders horizontal count charts, the labeled log-scale
ingestion dot plot and grouped expected/matched/missed rule coverage. It uses
already validated counts and adds no plotting dependency. Overview provides a
linear scale alternative. Log plots omit zero counts explicitly without altering
the exact-count table. `st.selectbox()` chooses the finding chart dimension. `st.expander()` keeps detailed explanation comparisons and
rule descriptions accessible. `st.caption()`, `st.info()` and `st.warning()` label
provenance, unavailable sections and benchmark limits. These are presentation
functions; they never start background jobs. `st.button()` with `st.rerun()` reloads
saved files; it does not call the CLI or refresh database validation.

The app imports grouping/catalog/rule contracts and the report projection module;
it does not import a DB connector or the OpenAI adapter. The existing validation
engine and provider behavior remain unchanged.

## Phase 5A verification (historical)

`tests/test_dashboard.py` contains 27 synthetic tests for safe parsing, malformed
and missing files, counts, filters, timestamp policies, AI source mismatch, rejected
claims, model metadata, cost reconciliation, cross-pilot mismatch, undefined metrics,
reload behavior and all four Streamlit views. `AppTest` simulates widget reruns;
it tests real Streamlit presentation instead of replacing the UI with mocks.
The read-only interaction test blocks socket connections, database connections
and SDK request creation, and checks that report hashes remain unchanged.

The full suite passes: 188 tests passed and 16 explicitly opted-in database tests
were skipped. Ruff lint and formatting checks pass. The dashboard was launched
locally on port 8505 and all four main views checked in a browser. No report was
regenerated, API called, database changed, correction made, commit created or push
performed. A safe overview screenshot was saved locally. Current publication screenshots
are under `docs/assets/`; current verification is recorded in
[the Phase 5B audit](PHASE5B_AUDIT.md).

Official references: [Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest)
and [st.dataframe](https://docs.streamlit.io/develop/api-reference/data/st.dataframe).


## Phase 5B publication snapshot

Historical local projection usage (the JSON files are now excluded from the
public release pending intended-use clearance):

```sh
DATAGUARD_REPORT_DIR=reports/portfolio uv run streamlit run src/dataguard_ai/dashboard/app.py
```

Original `reports/generated/` files are private and ignored. The projection
preserves measured counts and recorded selections/usage, replaces private row
locators with generated display surrogates, removes source values and references
its own public quality hash. Every view labels the projection as historical.
The exporter refuses an existing destination and never writes to private inputs.
See `dashboard/publication.py`, the report-directory README and the root README.
The benchmark limitation is repeated next to the metric cards for screenshots.


## Final presentation polish (2026-10-24)

Only `dashboard/app.py` and the pure display helpers in `dashboard/presentation.py`
were changed in the application. Report parsers, source reports, public report
projections, data files and detection/experiment/evaluation/AI code remain unchanged.
Eight presentation tests cover grouped disclosures, exceptions and timestamps,
log zero handling, unchanged numeric coverage, honest AI comparison wording,
stacked/side-by-side rendering and unchanged filter results. Existing UI tests
locate the findings table by its column schema, since source metadata is now a
separate table above it; their privacy and result assertions remain in place.

Common historical/privacy notices are shown once per view. Report-specific stale,
future or provenance exceptions remain visible and are grouped with source labels.
Detailed UTC timestamps, projection-surrogate disclosure and remaining sensitivity
limits are retained in **Sources & report times**. AI model/usage/hash details and
shared confidence limitations have separate expanders. This reduces repeated
boxes without removing material disclosures.

Verified all four views at desktop (1280 px), side-panel (662 px) and mobile
(390 px) widths. There was no page-level horizontal overflow; wide dataframes
retain their normal internal scrolling. Mobile findings search still returns eight
DQ008 rows, and stacked AI text wraps. Five desktop screenshots were refreshed.
Normal suite: **210 passed, 16 opt-in DB tests skipped**. Ruff lint and formatting
passed. No API request or DB connection was performed. Existing reports and core
logic were protected by before/after SHA-256 checks. Publication blockers remain
code-license choice, dataset/aggregate distribution review and owner approval of
the staged file list and publication.


## Phase 6A — isolated generic upload mode

The sidebar now defaults to **Olist portfolio**, with **Analyze Your CSV** as a
second mode. The four Olist view functions, report parsing, projection and sources
are unchanged. The new mode branches before saved reports are loaded and delegates
to `dashboard/upload.py` and `generic/analysis.py`. It never invokes the SQL quality
engine or AI providers. Original values reach the browser only when an upload
preview is explicitly enabled, unlike the projected Olist report path.

See [Generic CSV analysis](GENERIC_CSV.md) for use, resource limits, privacy,
configuration semantics and the upload data-flow diagram. The shared server upload
cap is now 5 MB; no dependency was added. Publication safeguards still apply to the
Olist reports. Uploads/reports are not automatically added to publication assets.


## Final release asset boundary

The release includes the application and synthetic tests/CSV screenshot, but none
of the six Olist-derived report JSONs or five Olist screenshots. With a fresh
checkout, select **Analyze Your CSV** for the credential-free demo. Olist views
remain functional with locally generated reports, and show unavailable sections
when reports are absent. Tests export wholly synthetic projections to exercise
all four views; a separate real-snapshot check skips when local files are absent.
See [DATASET_LICENSE.md](DATASET_LICENSE.md) for the individual exclusion decisions.
