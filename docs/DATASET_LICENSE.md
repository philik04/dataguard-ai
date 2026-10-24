# Dataset licensing and reproduction

Reviewed 2026-10-24. The authoritative source is the **Olist publisher's Kaggle
listing**, not another project's README:

- [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
- [CC BY-NC-SA 4.0 deed](https://creativecommons.org/licenses/by-nc-sa/4.0/)
- [CC BY-NC-SA 4.0 legal code](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.en)

The publisher page was rendered in a browser and its License link verified as
CC BY-NC-SA 4.0. No source dataset was downloaded during this audit. A page label
alone does not resolve the legality of every intended use; the owner's proposed
public employment portfolio still needs confirmation of noncommercial suitability
or appropriate permission. The repository is not an endorsement by Olist/Kaggle.

The project separates original software (proposed MIT) from Olist-derived artifacts
(excluded from this release pending intended-use clearance). [NOTICE.md](../NOTICE.md) identifies
the exact six projected JSON files and five screenshots, attributes Olist and
records transformations. The original CSVs and private record-level reports stay
excluded. A projected file can pass privacy checks and still need license review.
The generic CSV screenshot uses unrelated synthetic input and has no Olist data.

## Download for your own local reproduction

1. Visit the publisher link and review its current license, terms and dataset
   version. Sign in to Kaggle yourself if required; keep any API token private.
2. Use the publisher's Download control and extract the nine CSV files locally.
   Place the CSVs directly in the ignored `data/raw/` directory, not a nested
   archive folder. Never commit the archive or credentials.
3. Confirm these exact filenames (the code's source of truth is
   `src/dataguard_ai/ingestion/tables.py`):

```text
olist_customers_dataset.csv
olist_geolocation_dataset.csv
olist_order_items_dataset.csv
olist_order_payments_dataset.csv
olist_order_reviews_dataset.csv
olist_orders_dataset.csv
olist_products_dataset.csv
olist_sellers_dataset.csv
product_category_name_translation.csv
```

4. Follow [DATABASE.md](DATABASE.md) for optional local PostgreSQL setup. Ingestion
   intentionally replaces its nine target raw tables; this release preparation
   does not run ingestion or alter the protected dataset. Corruptions belong only
   to explicitly owned disposable experiment copies.

Downloads and regenerated outputs can differ from the historical snapshot if the
publisher version, environment or configuration differs. Original source files
are not needed for normal tests, the generic analyzer or the included synthetic
CSV demo. No API credentials or new paid requests are required for the demo.

## Individual asset review and release decisions

Reviewed 2026-10-24. Each projected JSON passed the existing schema/provenance
parser. Each screenshot was visually reviewed. No raw source values, original
identifiers, credentials or private paths were found. Retained Olist attribution,
source/license links and transformation descriptions appear in `NOTICE.md`.

CC BY-NC-SA 4.0 sections 2(a), 3(a), 3(b) and 4 distinguish noncommercial sharing,
attribution, adapted-material licensing and database rights. Attribution and
ShareAlike notices can be preserved, but this review cannot establish that the
intended employment portfolio satisfies NonCommercial or an applicable exception.
No additional permission was supplied. Therefore **each of these 11 assets is
excluded**, without deleting or modifying its local bytes:

| Asset | Content individually reviewed | Decision |
| --- | --- | --- |
| `reports/portfolio/ingestion.json` | Measured table row counts, status, timestamp and projection notice | Exclude: intended-use suitability unresolved |
| `reports/portfolio/quality.json` | Findings with display surrogates, rule/severity/evidence structure and summary | Exclude: intended-use suitability unresolved |
| `reports/portfolio/experiment.json` | Baseline/after findings, mutation/configuration counts and provenance | Exclude: intended-use suitability unresolved |
| `reports/portfolio/evaluation.json` | Finding-level/per-rule metrics, event totals and provenance | Exclude: intended-use suitability unresolved |
| `reports/portfolio/explanations.json` | Reviewed offline interpretations, aggregate groups and source hashes | Exclude: intended-use suitability unresolved |
| `reports/portfolio/openai_live_test_2026-10-23/explanations_openai.json` | Live selections, aggregate evidence, model metadata and historical usage/cost | Exclude: mixed artifact still contains Olist-derived evidence; usage metadata alone does not clear the whole file |
| `docs/assets/dashboard-overview.png` | Measured import/validation totals and table-count visualization | Exclude: visual rendering of Olist-derived results |
| `docs/assets/dashboard-ingestion.png` | Per-table source/import/database counts and logarithmic chart | Exclude: visual rendering of Olist-derived results |
| `docs/assets/dashboard-findings.png` | Eight warnings selected from the measured report; display positions, no original locators | Exclude: visual rendering of Olist-derived results |
| `docs/assets/dashboard-benchmark.png` | Per-rule expected/matched/missed obligations and benchmark scores | Exclude: experiment used an Olist-derived copy |
| `docs/assets/dashboard-explanations.png` | Reviewed live/offline comparison and historical usage/cost | Exclude: mixed visual artifact contains Olist-derived interpretation |

`docs/assets/dashboard-csv.png` is **included**: its retail input is entirely
synthetic and unrelated to Olist. It has no embedded text/EXIF metadata or private
values. Original code, synthetic tests, architecture and honest historical
verification/benchmark limitations are retained; exclusions are not replaced by
fabricated Olist metrics. This decision does not assert that facts themselves are
copyrightable or determine the legal status of every possible portfolio use.

## Reproduction without the excluded assets

Start the application and select **Analyze Your CSV**; use the synthetic input
in [DEMO.md](DEMO.md). The Olist views remain implemented and show unavailable
sections without local reports. Tests generate independent synthetic report
projections to exercise those views. A separate real-snapshot check is explicitly
skipped when local Olist files are absent. No database/API credentials or paid
requests are needed for the public demonstration.

Any later proposal to add Olist-derived assets requires a fresh rights/intended-use
review, privacy review and explicit staging approval. Do not force-add the ignored
files merely because the original dataset is publicly downloadable.
