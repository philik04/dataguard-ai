# Verification record

Verification date: **2026-10-24**, after generic CSV analysis was implemented.
This record preserves executed results and their limits. The earlier authorship cleanup did not change application/test code. The final
asset checkpoint below adds synthetic report-view coverage while retaining the
local-only real-snapshot assertions. Earlier results remain distinct historical records.

## Final staged-only checkpoint — 2026-10-24

The final candidate contains **83 staged files**. Eleven Olist-derived assets
(six projected JSON reports and five screenshots) are withheld; the synthetic CSV
screenshot, application and synthetic fixtures remain included. See the individual
[asset decisions](DATASET_LICENSE.md). Excluded local files remain unchanged.

A copy exported from Git's index, with no private inputs or database/API environment
variables, passed **257 tests, 17 skipped in 4.02 seconds**. Skips are the 16 opted-out
database integration tests and the local-only Olist snapshot check. All four report
views are also exercised using an independent synthetic projection. The original
real-snapshot assertions remain intact for local copies where those files exist.
This parameterized test adds synthetic coverage; no detection/evaluation method
or application behavior changed. The focused local publication suite passed all
15 cases with the local snapshots available.

The actual staged blobs passed the secret/private-path checks and relative-link
checks; `git diff --cached --check` passed. The README was rendered from the staged
bytes using GitHub-flavored Markdown with its included synthetic screenshot and
Mermaid architecture diagram. This local preview is not a GitHub-hosted rendering.
Database integration tests were not rerun for the asset/documentation boundary;
the earlier protected-database results below remain historical evidence.

## Executed checks

| Check | Recorded result |
| --- | --- |
| Complete normal suite, `.venv/bin/pytest -q` | **257 passed, 16 opt-in integration tests skipped**, 4.33 seconds |
| Dedicated DB suite, `pytest --run-db-tests -m integration -q` | **16 passed, 257 deselected**, 3.89 seconds |
| Combined normal + integration cases | **273 passed across separate runs** |
| Copy without private inputs/credentials | **61 focused generic CSV/dashboard/publication tests passed**, 2.62 seconds |
| Ruff lint, `.venv/bin/ruff check .` | **Passed** |
| Ruff formatting, `.venv/bin/ruff format --check .` | **Passed; 72 files already formatted** |
| `uv lock --check --offline` | **Passed; 57 packages resolved**, no downloads or dependency changes |
| Source CSV integrity | **9 / 9 SHA-256 matches** |
| Protected PostgreSQL content | **19 / 19 sorted canonical-content SHA-256 matches**, raw and experiment tables |
| Disposable fixture database | Empty of user schemas, relations and routines before and after integration tests |
| Reports and screenshots | Original and projected report JSONs unchanged; all six screenshots visually reviewed |

The 61 focused cases are a subset of the normal suite, not additional independent
coverage. The private-data-free copy used installed dependencies; it did not
perform a fresh network installation. The environment was Python 3.12.8, not an
OS/Python-version compatibility matrix. The 96-file publication candidate at that
checkpoint passed the heuristic secret/private-path scan; that count describes
the earlier candidate, before release-only notes were excluded.

## What is covered

Generic tests use unrelated retail, sensor and classroom CSV schemas. They cover
missing/duplicate cells, invalid/negative/nonfinite decimals, malformed date/time
values, supported encodings, multiline CSV records, resource limits, safe errors,
preview consent, file replacement and value-free JSON reports.

Streamlit tests exercise all four Olist views, filters, metric scope, timestamps,
provenance and missing reports while blocking database/API/network operations.
The projections tested at that historical checkpoint required no raw CSVs or API
credentials. They are now excluded from the release; synthetic projections cover
the public application tests. The
OpenAI provider tests use mocked transport, never paid API requests.

Integration tests use only a separate disposable `_test` database. Read-only
preflight verifies identity, difference from the configured source database and
absence of user objects. Tests create/change/remove synthetic fixtures there.
Protected-source fingerprinting uses repeatable-read, read-only transactions and
streamed sorted canonical JSON row text. Matching before/after hashes establish
content equality at those checkpoints, not continuous absence of external writes.
Detailed checkpoints/logs remain local and are excluded from publication.

## Reproduce

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python scripts/audit_publication.py
```

No database/API credentials or private data are needed for normal checks. See
[DATABASE.md](DATABASE.md) for explicitly opted-in disposable integration tests,
[GENERIC_CSV.md](GENERIC_CSV.md) for upload boundaries, and
[PHASE5B_AUDIT.md](PHASE5B_AUDIT.md) for the earlier audit's distinct results.

## Limits of the evidence

The synthetic corruption benchmark measures known predefined finding obligations,
not general real-world accuracy. DQ008 has no positive benchmark case. Historical
live AI selections matched the offline provider and demonstrated no additional
analytical benefit; hypotheses are unconfirmed and financial impact is unmeasured.

Secret scanning is heuristic and supplemented by manual artifact review. No
vulnerability scan, production penetration test or hardened remote upload hosting
certification was performed. Privacy-reviewed Olist artifacts still have separate
CC BY-NC-SA licensing and intended-use considerations; see [NOTICE.md](../NOTICE.md).
