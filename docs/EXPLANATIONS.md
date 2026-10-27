# Explanation design — Phase 4A foundation and Phase 4B provider

Phase 4B has mocked automated tests and one separately approved three-request
live test. Offline remains the default. See
[OpenAI provider lifecycle and safeguards](OPENAI_PROVIDER.md) for current
SDK integration, configuration, budget policy and failure behavior. Sections
explicitly describing Phase 4A below record the foundation at that phase;
request execution is now available only with explicit paid-request opt-in.

This layer explains existing deterministic findings. It does not discover issues,
change validation severity, infer confirmed causes, repair data or evaluate
real-world accuracy. The offline provider is reviewed template code, not an LLM.

## Boundary and interface

`explain_findings(findings, provider)` accepts structured quality findings.
Grouping extracts only allowlisted rule/table/column names, known evidence
patterns, severity and locally computed counts. A provider receives one aggregate
`EvidenceGroup` per pattern, never row identifiers, raw values or review text.

The provider's `explain(group)` returns three lists of reviewed option IDs:
hypotheses, potential impacts and investigation steps. A strict schema rejects
missing/extra fields, unknown IDs and duplicates. Python constructs the final
explanation: issue summary, evidence-based explanation, hypothesis-labeled causes,
conditional business impacts, investigation steps and fixed confidence limits.
Counts are always computed in Python and stored in a separate evidence section.

This intentionally constrains generation: a model cannot insert an affected-row
count, monetary loss or a confirmed root cause through an arbitrary prose field.
Schema-valid free text alone would not guarantee factual accuracy. Supporting
open-ended generated prose requires a later, separately reviewed grounding policy.

## Grouping and counting policy

Group by rule, table, severity and evidence pattern: the relevant column,
composite-key columns or chronological pair plus known reason. Ignore individual
values so hundreds of examples of the same condition share one explanation.
Keep chronology errors separate from mixed-timezone warnings and missing-data
warnings. Optional-field observations are not silently turned into findings.

Finding count is the number of supplied finding objects, preserving repeated
findings. Distinct affected rows are counted within each group using the engine's
snapshot CTID or stable experiment row ID. If any locator is unavailable, the
group's affected-row count is null rather than guessed from business keys.
Raw CTIDs identify only that saved snapshot. Group row counts may overlap and
must not be summed into a dataset-wide unique affected-row count.

## Privacy and provider preparation

The input report stays local and unchanged. Original evidence remains in
`quality.json`; generated reports contain aggregate evidence and interpretations
in separate arrays linked by group ID. No business identifiers, row locators,
actual timestamps, monetary strings, comments or arbitrary metadata cross the
provider boundary. Known names are allowlisted rather than merely regex-redacted.
The output links to the local input by SHA-256, without embedding source records.

`OpenAIProvider` is a separate request-preparation adapter. It builds a Responses
API request using a caller-supplied model and a strict JSON schema of option IDs.
`explain()` fails closed in Phase 4A. The CLI cannot enable live calls; no SDK,
HTTP transport, API-key loading or paid request is implemented. Request-shape
tests are local only and do not establish live API/model compatibility.

Request format follows the official OpenAI Docs
[Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs),
checked on 2026-10-21. A later approved integration must select an available model,
handle refusals/incomplete responses, validate choices locally, and review cost,
privacy and aggregate-data sensitivity. Removing identifiers does not make all
aggregate business data automatically public.

## Limitations

Hypotheses are possibilities, not probabilities or confirmed causes. Potential
impact is not evidence of a realized loss. The offline catalog has limited
coverage and cannot know the actual source process. The layer trusts the
detector's findings; grouping checks known structural contracts, not raw truth.
Saved findings describe their recorded snapshot, not a fresh database scan.
Unknown rule patterns fail explicitly so new engine behavior needs review.
No database connection is needed for explanation, and no SQL is executed.

## Code walkthrough

- `grouping.py`: the frozen `EvidenceGroup` dataclass defines exactly what may
  cross the provider boundary. `evidence_pattern()` checks rule/table/field and
  severity contracts against the existing registry. `group_findings()` uses a
  dictionary to collect matching patterns, then counts findings with `len()` and
  distinct local locators with `set()`. It never copies those locators into a
  group. Missing locators produce null affected-row counts, not key-based guesses.
  Sorted pattern keys make output order repeatable; group IDs belong to that
  report and are not permanent identifiers across changing input reports.
- `catalog.py`: a small `Catalog` dataclass holds reviewed text for every rule,
  with a separate mixed-timezone warning catalog. `describe_evidence()` inserts
  only validated field names into descriptions. It does not parse or echo original
  values or independently rerun validation. Hypotheses remain unconfirmed.
- `providers.py`: a `Protocol` describes the required `name` and `explain()`
  method without a framework or inheritance requirement. `OfflineProvider`
  deterministically selects the reviewed options. `selection_schema()` describes
  the provider response as an exact object of bounded lists of known IDs.
  `validate_choices()` implements that small schema locally and additionally
  rejects duplicate choices. This is not a general JSON Schema validator.
- `openai_provider.py`: request preparation is a pure dictionary-building
  function, with no model default or network effects. `store=False` is a prepared
  request option, not a claim that future API use has zero retention. Execution
  raises a clear error even when an API key exists in the environment.
- `explanations.py`: the `Explanation` dataclass defines six requested content
  fields plus the evidence group reference. `explain_findings()` validates provider
  choices and assembles text from the catalog. `explain_saved_report()` checks a
  completed report and reconciles its summary with its actual findings. It retains
  only a content hash and normalized completion timestamp as source provenance.
- `reporting.py`: JSON preserves structured data; Markdown renders a deterministic
  evidence table followed by labeled interpretations. Output cannot overwrite
  the input report or be placed under `data/raw`. Each file uses temporary-file
  replacement; the pair is not atomic. A failed run may leave an older report.

`evidence_groups` holds measured aggregates; `explanations` holds reviewed text.
`field_origins` explains which fields are deterministic descriptions versus
selected interpretations. There is no numeric confidence score because these
rules/catalog choices do not provide calibrated probabilities.

## Actual original-data run on 2026-10-21

```sh
uv run dataguard-ai explain --provider offline
```

Result: **197 findings explained in three groups**. Input validation completed
on 2026-10-19; explanation did not refresh the database snapshot. JSON and Markdown
are `reports/generated/explanations.json` and `explanations.md`.

| Group | Deterministic evidence | Findings | Within-group rows |
| --- | --- | ---: | ---: |
| group_001 | DQ007 delivery before carrier handoff | 23 | 23 |
| group_002 | DQ007 carrier handoff before purchase | 166 | 166 |
| group_003 | DQ008 delivered orders missing delivery timestamp | 8 | 8 |

The first two groups share reviewed hypotheses about timestamp capture/mapping
and source clock/timezone conventions. The generated potential impact is distorted
fulfillment-duration/service-level analysis. Recommended steps are comparing the
flagged pair with source logs locally and reviewing mappings/clocks/timezones.
These are suggestions, not established business causes or measured effects.

The warning group says the missing timestamp does not prove delivery failed.
Possible export gaps or differently timed extracts are hypotheses. The suggested
investigation checks field availability and lifecycle/extraction context.
No affected counts originate from provider prose; no monetary loss is asserted.

Verification commands:

```sh
uv run pytest tests/test_explanations.py
# 37 passed
uv run pytest
# 130 passed, 16 database tests skipped
uv run ruff check .
uv run ruff format --check .
# Both passed
uv run dataguard-ai
# Original Phase 0 greeting preserved
```

Tests use synthetic data only, with no paid API calls or database connections.
They include false/unsupported claim fields, a confirmed-cause string substituted
for a choice ID, duplicate/missing choices, unknown patterns, absent locators,
timezone warnings, empty findings, source preservation, report consistency and
private sentinel values absent from requests/reports. Existing database integration
tests were not rerun because this layer performs no database work. All ten prior
report hashes matched after explanation; the original evidence was unchanged.
