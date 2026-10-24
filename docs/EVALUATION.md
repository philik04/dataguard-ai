# Phase 3 — Evaluation policy v1

This methodology was specified before implementing metrics. It evaluates the
isolated synthetic pilot, not general real-world detection performance.

## Three different units

1. **Mutation event:** one manifest entry. A duplicate INSERT is one event.
2. **Affected row:** one qualified table and stable experiment-local row ID.
   A duplicate event affects the source row and the inserted row.
3. **Finding obligation:** one independently specified expected rule result on
   a row with relevant field/pair evidence. This is the precision/recall unit.

For the original pilot, 70 events imply 80 expected finding obligations: each
of ten duplicate events expects two results, while each other event expects one.
Expected labels are derived from the manifest and explicit contracts, never
from which predictions the detector happens to emit. The evaluator independently
defines supported mappings and rejects inconsistent declared rule IDs/fields.

## Matching policy

Each expected result requires exact qualified table, stable experiment row ID,
expected rule ID, error severity and the following evidence:

| Mutation | Required evidence |
| --- | --- |
| Missing identifier | DQ001, declared column and exact mutated value |
| Duplicate key | DQ002 on both original and inserted IDs; composite key column list, matching business key values from inserted record, group_size >= 2 |
| Invalid money | DQ003, declared column and exact mutated value |
| Negative money | DQ004, declared column and exact mutated value |
| Broken reference | DQ005, declared column, mutated value and expected parent reference |
| Invalid timestamp | DQ006, declared column and exact mutated value |
| Reversed chronology | DQ007, purchase as earlier column, carrier as later column, exact mutated later value, and reversed-order reason |

Chronology expectations do not reconstruct an unchanged purchase value from the
manifest: that field is not recorded there. They require the intended pair and
changed endpoint. The detector supplies the other endpoint from the verified
database snapshot. This is one limit of available ground truth.

One prediction can satisfy at most one obligation, and one obligation can accept
at most one prediction. Repeated identical predictions cannot inflate TP: the
first matching result is TP and surplus results are FP. Missing, wrong-field,
wrong-value, wrong-rule and warning-only predictions cannot satisfy error labels.
The pilot policy rejects overlapping mutation source/target rows and malformed
or unknown mutation contracts rather than choosing an arbitrary attribution.
Larger interacting duplicate groups require a later explicit policy revision.

## Baseline and false-positive interpretation

Use the same pilot's unmodified baseline, not the full raw baseline of 197.
Exclude exact baseline matches using a multiset signature of rule, table,
stable row ID, severity and evidence. Preserve multiplicity: a repeated new
prediction is not silently deduplicated. Retained and disappearing baseline
findings are reported separately, and disappearing baseline findings are not
injection FNs. An alarm with changed evidence is new, even on the same row;
the report flags changes to existing baseline conditions for review.

- **TP:** a new prediction matched one expected finding obligation.
- **FP:** a remaining new prediction matched no available expectation.
- **FN:** an expected finding obligation received no matching prediction.

FP means unmatched under this closed-world synthetic labeling policy. It could
also be an unlabeled genuine issue, a collateral effect or a changed baseline
condition. It is not proof of a false real-world alarm. Warnings are predictions
too; unexpected new warnings count as benchmark FP under this policy.

## Metrics and denominators

Precision = TP / (TP + FP).
Recall = TP / (TP + FN).
F1 = 2 TP / (2 TP + FP + FN).

Return JSON null / Markdown N/A for each zero denominator. For example, a clean
negative control with no labels and no alarms has TP=FP=FN=0 and all three
metrics undefined, not 100%. With expected results but no alarms, recall and
F1 are zero, while precision is undefined. Overall metrics pool finding counts
(micro aggregation), not averages of per-rule percentages. No TN population is
enumerated, so accuracy and specificity are not reported.

Mutation-event detection rate = events with at least one matched obligation /
all injected events. Report fully matched events separately. A duplicate event
with only one matching result is detected but only half of its finding
obligations were recovered. Counts of targeted rows, expected affected rows and
observed finding rows remain separate from event/finding metrics.

An evaluator-only `plausible_money_change` negative-sensitivity scenario has no
deterministic rule contract (`expected_rule_id` is null). It stays in the event
denominator, with no finding obligations and no finding-level FN. A valid-looking
wrong price can therefore reduce event detection while finding-level recall
remains high. These events are prominently counted as outside-contract, not
silently presented as covered. The Phase 2B simulator is not expanded by this test.

## Read-only provenance

Default evaluation reads the committed experiment's database `_run` and
`_mutations`, checks their consistency, verifies the current pilot content digest,
and reruns the quality engine in the same read-only repeatable-read snapshot.
A stale/mismatched data snapshot aborts metrics. Fresh predictions are used;
agreement with recorded predictions is reported, not assumed or forced.
No raw/experiment rows or metadata are written. Only evaluation files are saved.

## Validation cases required before publishing metrics

- Clean negative control: no expectations, no predictions, undefined ratios.
- Unexpected alarm on clean data: FP, with no invented ground-truth positives.
- Missing expected prediction: FN; wrong evidence: FP plus FN.
- Duplicate event: two TPs when complete; one TP/one FN when partial.
- Duplicate detector output: surplus FP, never extra TP.
- Exact baseline exclusion, disappeared baseline and changed baseline evidence.
- Overlapping/malformed manifests rejected rather than ambiguously scored.
- Real-engine sensitivity control with a valid-looking wrong price that evades
  the declared rules, separate from a detectable injected invalid value.

Passing these cases validates this explicit pilot matching policy; it does not
establish performance on arbitrary errors, interacting mutations or unseen data.

## Code and important SQL

- `policy.py`: `build_expectations()` turns each declared mutation into a small
  typed `ExpectedFinding`. Its `matches()` method checks row, rule, severity and
  the required evidence. These labels are independent of observed predictions.
  The manifest is trusted ground truth, not independently reconstructed business
  truth: valid-looking values cannot establish whether a real price is correct.
- `metrics.py`: `evaluate_findings()` first removes exact baseline occurrences
  using `Counter`, a dictionary of occurrence counts. A set would lose duplicate
  predictions. It indexes obligations by `(table, stable row ID, rule ID)`, then
  compares evidence and consumes each matched obligation once. Unmatched alarms
  become benchmark FP; unmatched obligations become FN. An explicit loop pools
  per-rule TP/FP/FN for overall metrics; event recovery uses separate counts.
- `runner.py`: `evaluate_connection()` validates committed metadata, verifies
  the current pilot digest and calls the existing `validate_snapshot()` inside
  one read-only transaction. `evaluate_database()` manages the connection.
- `reporting.py`: `percentage()` renders undefined ratios as N/A. JSON retains
  numeric ratios or null and every TP link, FP and FN. Files are replaced via
  temporary siblings individually; the pair is not one atomic database/file
  transaction. Failed runs can leave older reports, so completion time matters.

The evaluator starts this transaction before reading any experiment evidence:

```sql
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '3s';
SELECT experiment_id, details FROM experiment_pilot._run;
SELECT details FROM experiment_pilot._mutations ORDER BY mutation_index;
```

READ ONLY prohibits persistent table changes. REPEATABLE READ makes metadata,
digest and predictions see one consistent snapshot. Timeouts bound individual
statements and lock waits; they are not a total runtime deadline. Psycopg
`sql.Identifier` quotes schema/table names instead of interpolating unsafe SQL.

The existing digest reader streams `SELECT * FROM <table> ORDER BY
_experiment_row_id` through a named cursor, hashing canonical JSON including
stable IDs. It processes only the small pilot's eight relevant tables. The
existing validator uses SELECT DISTINCT for parent keys and GROUP BY/HAVING for
duplicate keys, then guarded Python value checks. No evaluation-specific SQL
casts or persistent database objects are introduced. Production evaluation never
starts Docker, reloads raw or rebuilds the experiment.

## Verified results on 2026-10-19

`uv run --env-file .env dataguard-ai evaluate` evaluated
`pilot_076502c3a9e05335875e`, seed 42, 1,000 selected orders. There were 70 events,
80 affected rows, 80 expected obligations and 80 new findings. DQ001 had 10 TP,
DQ002 20, and DQ003–DQ007 10 each. Each had zero FP/FN. DQ008 had no labels or
predictions, so its ratios are undefined. Overall TP=80, FP=0, FN=0:

- Precision = 80 / (80 + 0) = 1: all new alarms match labeled obligations.
- Recall = 80 / (80 + 0) = 1: all labeled obligations received matching alarms.
- F1 = 160 / (160 + 0 + 0) = 1: combines finding precision and recall.
- Event detection = 70 / 70 = 1; all 70 were also fully matched.

The pilot had zero baseline findings. Full raw still has 197 as recorded in the
original context (189 chronology errors and eight warnings); these do not belong
in the pilot's evaluation denominator. The fresh 80 predictions match the
recorded ones, and the current pilot digest matches its committed record.

For intuition, if only one member of a duplicate pair were reported, that event
would still be detected, but it would have one TP and one FN: recall 1/2 and
F1 2/3 for that pair. If the actual pilot had one extra unsupported alarm,
precision would be 80/81 (about 98.77%), with recall still 80/80. Neither
hypothetical is presented as an observed pilot outcome.

`uv run pytest` passed 93 tests and skipped 16 opt-in database tests. The explicit
command below passed seven real PostgreSQL tests (four evaluation, three quality):

```sh
DATAGUARD_TEST_DB=dataguard_ai_test uv run --env-file .env pytest --run-db-tests tests/integration/test_evaluation_database.py tests/integration/test_quality_database.py -m integration
uv run ruff check .
uv run ruff format --check .
```

Both Ruff checks passed. Old ingestion/experiment database tests were not rerun.
The evaluation fixture creates and drops its own schema in `_test`, refusing to
reuse an existing schema. The evaluation call itself leaves its tables and run
metadata unchanged. Tests also reject stale content and conflicting manifests.
The real-engine evasion control had three events: invalid money, a duplicate,
and a plausible price change. It returned three TP, zero FP/FN within the rule
contract, but detected only two of three events. Unit cases deliberately remove
expected predictions or supply wrong evidence, exercising FN and imperfect F1.

A separate before/after read-only audit matched all nine raw table counts and
content guards (1,550,922 rows), the experiment SHA-256, all nine CSV hashes and
the three existing Phase 2B report hashes. Raw SQL hash sums are non-cryptographic
content guards, not archival proof. The audit cannot rule out concurrent writes
that are later undone. No new dependencies, commits or pushes were made.

## Publication interpretation

The recorded original pilot has TP=80, FP=0, FN=0 under policy
`pilot-finding-v1`. Precision, recall and F1 are therefore 100% **for this predefined
synthetic benchmark**. Seventy events produce eighty expected findings because
ten duplicate-key events each require two matched findings; the other sixty
events each require one. All 70 events are detected here, which is a separate
measurement from finding-level recall.

This is a rule-contract benchmark, with seeded, baseline-clean targets and known
injections. It has no independently labeled real-world holdout, confidence
intervals, or evidence of broad coverage over unknown corruptions. DQ008 warnings
are not positively exercised by the seven mutation families, so its per-rule
metrics remain undefined. A real detector evasion integration test shows that a
plausible price change can escape the rules even when expected finding-level
metrics for supported obligations are perfect. Do not write “100% real-world
accuracy” in a CV or project description.

The proper portfolio statement is: “Achieved 100% precision and recall across
80 predefined synthetic finding obligations from 70 controlled mutation events;
documented unsupported corruption patterns and benchmark limitations.” This is
neither a root-cause diagnosis nor a measured reduction in business losses.
