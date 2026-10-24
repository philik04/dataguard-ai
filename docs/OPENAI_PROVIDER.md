# Phase 4B — OpenAI Explanation Provider

Implemented 2026-10-23. Automated tests use mocked responses only. One separately
approved controlled live test completed on 2026-10-23: three successful requests,
three validated evidence groups, 2,359 input tokens and 606 output tokens. The
recorded conservative estimated cost was USD 0.004783, and all selections matched
offline output. No new paid call was made during Phase 5B. Offline remains the
default; future real requests require separate authorization and explicit opt-in.
The local portfolio copy is a privacy projection and is excluded from this release;
see [verification record](VERIFICATION.md).

## Architecture and lifecycle

1. CLI reads a saved completed quality report. Python validates known findings,
   timestamp and summary consistency **before** a provider can send anything.
2. `group_findings` reduces findings to frozen `EvidenceGroup` objects. The
   `ExplanationProvider` protocol still requires only `name` and `explain(group)`;
   both providers return the same three lists of reviewed choice IDs. The OpenAI
   adapter additionally exposes whole-report `preflight()` and `metadata()`.
3. Preflight requires `allow_paid=True` (CLI `--allow-paid-api`), checks input
   estimates and reserves capacity for all groups against request and cost limits.
   `prepare_request()` itself is pure and can be inspected without a credential.
4. The official `OpenAI` client is constructed lazily, reading `OPENAI_API_KEY`
   from the environment. It uses the official HTTPS endpoint explicitly, a
   configured timeout and `max_retries=0`. Environment base-URL overrides cannot
   redirect credentials. The CLI does not automatically load `.env`.
5. One Responses API request per evidence group sends a strict JSON schema,
   aggregate evidence and reviewed option text. `store=False` avoids storing a
   response as retrievable application state; it is not a zero-retention promise.
   No tools or database access are provided to the model.
6. A completed response with usage is required. JSON parsing rejects duplicate
   keys. Local validation compares fixed fields exactly, rejects unknown/extra
   fields, and validates choice IDs, list sizes and duplicates. Refusal,
   truncation, malformed JSON or changed facts fail the run. Use a pinned snapshot:
   returned model must match the configured model, so a floating alias resolving
   to a different snapshot is rejected.
7. Only validated IDs reach the assembler. Python reconstructs the final six
   explanation fields from reviewed text, labels hypotheses and potential impacts,
   and writes deterministic evidence separately. Raw model output is never saved.

The response envelope contains `summary`, `evidence_based_explanation`,
`evidence`, `confidence_limitations`, `hypothesis_ids`, `impact_ids` and
`investigation_ids`. The last three become the report's root-cause hypotheses,
potential business impacts and recommended investigations. Schema enums constrain
scalar facts and choice IDs; bounded arrays constrain content. Local equality
also checks exact array order/content and integer types. Counts, rule IDs,
severity, table, columns and parent references cannot be rewritten by the model.

This deliberately extends Phase 4A's constrained selection approach. The model
chooses which reviewed interpretations are relevant; it does not author novel
explanations. The summary and evidence description are fixed templates. This is
a design tradeoff: stronger control and easier auditing, less expressive output.

## Exact outbound data

Only group ID, allowlisted rule ID, unqualified table name, severity, known pattern,
column names, known parent table/column, finding count and within-group distinct
row count (or null). The request also includes reviewed templates, hypotheses,
potential impacts, investigations, fixed limitations and their response schema.
A name such as `customer_id` is a column name, never a customer's ID value.

No raw rows, customer/order ID values, CTIDs, original timestamps, monetary values,
comments, unrestricted metadata, entire validation report, database schema name,
credentials, source path or source hash is sent. The API key is sent only as SDK
HTTPS authentication, not prompt content. No report or application exception
includes it. Aggregate business evidence can still be sensitive.

## Tokens and budget

Defaults: pinned `gpt-4o-mini-2024-07-18`, 30-second SDK timeout, 1200 output tokens,
16000 estimated input tokens, three requests and USD 0.10 per provider instance.
All are CLI configurable (see `uv run dataguard-ai explain --help`). SDK timeout
covers HTTP operations; it is not a guaranteed total wall-clock deadline.

Before each request, estimate input as serialized request UTF-8 bytes plus 4096
framing tokens. This intentionally overestimates ordinary text tokenization and
includes the response schema, but is not the server's exact tokenizer. Reject
requests above the input ceiling. The API enforces `max_output_tokens`; the input
ceiling is a local conservative estimate, not a server input-limit parameter.

Reserve `(estimated_input * input_rate + max_output * output_rate) / 1_000_000`.
The default rates (USD 1 input and USD 4 output per million) are conservative
configuration assumptions, **not published current prices**. Verify adequate rate
ceilings before approval, especially when changing models. Reservations are never
refunded within an instance, even after timeout or invalid output: a request may
already have been charged. Whole-report preflight prevents partial spending on a
report known to exceed capacity. Runs are sequential; instances are not intended
for concurrent use. Limits reset with a new process/instance.

Actual returned input/output tokens and an estimated cost using those rates are
recorded per group, alongside configured limits, model and attempted requests.
These estimates do not constitute billing records. An application budget cannot
guarantee an account-wide cap, exact token accounting or protection against stale
rates; configure account-side controls separately if needed. No tokenizer or
budget service dependency was added.

## Failures and explicit fallback

No SDK automatic retries, application retries or model repair requests occur.
Failures produce a sanitized error, suppress SDK exception context and stop
without a new completed report. Existing reports may remain; inspect exit status
and timestamps. After ambiguous failure, a manual rerun could spend again.

Only `--fallback-offline` explicitly requests fallback. It regenerates the whole
report offline, records failure/fallback and attempted API metadata, warns that
charges may exist, and writes `explanations_openai_fallback.json/.md`. Without that
flag, failures are errors. Missing paid opt-in always fails, even with fallback.
There are no partial successful explanation reports on API failure; successful
prior-group usage is retained in the provider object, and in a requested fallback
report. JSON/Markdown pair writes are not transactional.

## Claim validation limits

Unsupported confirmed-cause statements, measured financial losses and unprovided
facts cannot enter via arbitrary prose: only reviewed IDs are accepted, and fixed
fields must match. All selected causes remain hypotheses, all impacts potential.
The model can still choose an irrelevant plausible hypothesis. The catalog can
contain mistakes, omit real causes or become outdated. Automated validation does
not establish causal truth, completeness, business relevance or calibrated
confidence. Expanding to free text would require a new grounding policy; regex
claim detection alone would not make arbitrary prose trustworthy.

The evidence is a deterministic description of supplied detector findings, not a
fresh database scan or independent proof of the underlying real-world event.
`field_origins`, evidence groups and hypothesis/potential labels expose that
separation. Group row counts may overlap; never sum them into unique orders.

## Safe demonstration and verification

```sh
uv run python scripts/demo_openai_explanations.py
uv run dataguard-ai explain --provider offline
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The demo uses the real SDK with `httpx.MockTransport`, a dummy credential and
synthetic usage. No socket transport exists in its client. It selects the first
reviewed option per category for the original 197 findings / three groups and
writes `reports/generated/openai_mock/explanations_openai.json/.md`. Its metadata
explicitly marks all responses/usage as mocked. Tests intercept the SDK HTTP
boundary for success, HTTP errors, timeout, refusal, truncation, malformed JSON,
duplicate keys, changed evidence, unsupported claims, budgets and fallback.
Mock success verifies our integration contract, not live account/model access or
live quality. The default provider and existing report names remain offline.

## Official documentation consulted

- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs):
  Responses `text.format`, strict schemas and refusal/incomplete handling.
- [GPT-4o Mini](https://developers.openai.com/api/docs/models/gpt-4o-mini):
  low-cost model family, pinned snapshot, Responses and Structured Outputs support.

## Five technical interview questions

1. Why use a provider protocol, and how does a mocked HTTP transport test more
   than a mocked `explain()` method?
2. Why does strict JSON schema validation not prove a root-cause hypothesis true?
3. How does allowlisted aggregation protect privacy more reliably than redacting
   identifiers from unrestricted report text?
4. Why reserve the maximum estimated cost before dispatch and keep the reservation
   after timeout, and what limits does this budget have?
5. How do deterministic evidence, reviewed templates and model-selected hypotheses
   differ, and how does the report make their origins auditable?
