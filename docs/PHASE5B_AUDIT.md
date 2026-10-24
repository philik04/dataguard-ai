# Phase 5B quality and publication audit

Audit date: **2026-10-24** (Europe/Paris). Scope: portfolio quality, safety,
reproducibility, documentation and presentation. No validation rules were changed,
no major feature added, no paid API invoked and no automatic corrections made.
No staging, commit, push or public publication was performed.

## Exact verification results

| Check | Observed result |
| --- | --- |
| Normal suite: `.venv/bin/pytest -q` | **202 passed, 16 integration tests skipped** |
| Dedicated DB suite: `pytest --run-db-tests -m integration -q` | **16 passed, 202 deselected** |
| Combined coverage of the two runs | **218 tests passed across separate invocations**, with every integration test exercised |
| Public-candidate-only temporary copy | **202 passed, 16 opt-in DB tests skipped**, no `.env`, raw CSVs or private reports |
| Ruff lint | **Passed** |
| Ruff formatting | **Passed** |
| `uv lock --check --offline` | **Passed**, 57 packages resolved from the current lock |
| Dashboard | All four public-snapshot views render; `DQ008` search returns **8 / 197**, empty/incompatible filters return zero |
| Source CSV integrity | **9 / 9 SHA-256 matches**, 126,186,995 total original bytes |
| Protected DB integrity | **19 / 19 sorted canonical-content SHA-256 matches**: nine raw + ten experiment tables |
| Original generated reports/assets | **19 / 19 SHA-256 matches** |
| Disposable DB cleanup | No user tables left in the dedicated test DB |
| Publication scan | No findings in the current tracked + non-ignored untracked candidate set; see scan command for current file count |

The installed environment uses Python 3.12.8. The public-candidate copy reused its
installed dependencies with source imports pointed at that copy. This verifies
that private inputs are unnecessary for the tests/demo; it is not a clean network
install, OS matrix or runtime compatibility certification for every Python 3.11+
version. The two DB-suite runs both passed; the table records the final run.

Detailed checkpoint files and command logs are local under ignored
`reports/private/phase5b/`. Only counts and hashes, not record contents, were saved
for integrity comparison. Credential values were neither printed nor copied into
publication artifacts. Database checks used repeatable-read, read-only transactions
and streamed sorted canonical JSON row bytes into SHA-256. The snapshots prove
content equality at their checkpoints, not continuous absence of outside writes.

## Issues found and fixed

1. **Private generated reports were eligible for Git.** Original quality and
   experiment/evaluation artifacts contain identifiers, record-level evidence and
   mutation values. Ignore rules now exclude the full generated/private directories,
   and Streamlit secret configuration. Originals remain untouched.
2. **A fresh public copy could not reproduce the dashboard without private reports.**
   Added a small explicit export utility and six reviewed public projections.
   All measured groups/counts/metrics, reviewed choices and historical usage are
   preserved; raw values and IDs are omitted. Public source hashes are distinguished
   from the original input hash. Existing destinations cannot be overwritten.
3. **The DB test suffix guard was insufficient by itself.** Added a session preflight
   refusing the configured source DB and existing user objects before fixture DDL.
   Unit tests exercise protected targets and occupied-schema/relation/routine cases.
4. **Benchmark limits could scroll out of a metric screenshot.** The predefined
   synthetic scope now appears beside the metric cards as well as above the view.
5. **Documentation had stale live-test status and too much phase-by-phase prose on
   the landing page.** Replaced the README with employer-oriented results, quickstart,
   architecture, screenshots, code map and limitations; reconciled AI/database/dashboard
   documentation and added demo/publication/audit guides.

The Phase 5B test additions are 14 cases for public export consistency, privacy,
no-overwrite behavior, missing sources, unknown row counts, safe DB target selection,
occupied-DB preflight and all four shipped-snapshot views. Existing detector/rule
contracts and original reports were not changed to make tests pass.

## Dashboard and report review

The browser used the public projection on loopback port 8506. Normal tests also
exercise both missing/invalid reports and safe projections with network/database/API
calls blocked. Headline metrics match the existing reports: 1,550,922 imported rows,
189 violations/eight warnings, 70 mutations, 80 TP, and historical live usage of
three requests / 2,359 input tokens / 606 output tokens / USD 0.004783 estimated.

Source report timestamps are preserved: original validation 2026-10-19, explanations
2026-10-23. They are historical evidence, not a refreshed database scan. The existing
seven-day stale policy and future/naive-timestamp rejection are tested. Hash-mismatched
explanations and cross-pilot evaluation provenance are rejected. Undefined metric
denominators remain N/A; no source values, business IDs or mutation values are
passed to Streamlit dataframes or screenshots.

## Remaining limitations and readiness

The code, measured artifacts and local demo are **technically ready for owner
publication review**. Public release is not yet approved: code license selection,
dataset distribution/attribution review and final staged-file review are outstanding.
The current Git repository has no commits, tracked files or configured remote;
there is no hidden existing public history to amend. No history rewrite was attempted.

The secret/personal-data scan is heuristic and exact-match-assisted, not a formal
data-loss-prevention guarantee. Public aggregate evidence still requires judgment.
The dashboard has no authentication or deployment hardening; source reports may
become old; file-pair exports are not transactional. AI selection cannot establish
causal truth, calibrated confidence or measured loss. Configured API budgets are
local estimates, not account billing limits. No new live compatibility or paid
quality test was performed during this phase.

The benchmark is deliberately narrow: seeded known corruption families, clean
eligible targets and explicit one-to-one matching. DQ008 has no positive benchmark
case. Plausible semantic errors and interacting/novel corruption can evade rules.
100% synthetic precision/recall must never be presented as general real-world
accuracy. See [evaluation interpretation](EVALUATION.md#publication-interpretation).

See [the verification record](VERIFICATION.md) for later executed checks.
