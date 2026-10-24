# Licensing and dataset attribution

## Original project work

`LICENSE` contains the MIT license for original DataGuard AI code, synthetic test fixtures,
original explanatory documentation and the synthetic CSV dashboard screenshot
(`docs/assets/dashboard-csv.png`). Copyright holder: **2026 Philipp Kulbatskiy**. Third-party packages and Streamlit UI elements retain their own
licenses. Dependencies are installed separately, not vendored here.

## Olist dataset

**Brazilian E-Commerce Public Dataset by Olist**, provided by **Olist** and hosted
at [the publisher's Kaggle page](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce).
On 2026-10-24, the publisher page's License section was directly inspected in a
browser and linked to **Creative Commons Attribution-NonCommercial-ShareAlike 4.0
International** ([license](https://creativecommons.org/licenses/by-nc-sa/4.0/),
[legal code](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.en)).
The web text extractor did not render that dynamic section; the verification is
from the visible publisher page, not a third-party GitHub copy or search snippet.
No dataset download or agreement acceptance occurred in this review.

The license requires appropriate credit, a license link and indication of changes;
licensed sharing/adaptation is limited to noncommercial purposes and adaptations
must use the same or a compatible license. Privacy and other rights can still
apply. The deed is a summary; the legal code controls. This project is independent
and is not endorsed by Olist or Kaggle. Public availability does not mean public
domain, and anonymization does not eliminate all privacy obligations.

Raw CSVs are **not included**. Obtain them directly from the publisher under its
current terms; do not upload Kaggle credentials, raw records or downloaded archives
to this repository. The MIT proposal does not relicense the Olist dataset.

## Derived reports and screenshots: separate from MIT

The following locally retained artifacts are **excluded from this release**.
Their noncommercial suitability for the intended employment portfolio has not
been established. Any later redistribution must separately address applicable
**CC BY-NC-SA 4.0** rights and preserve attribution and license links:

- `reports/portfolio/ingestion.json`
- `reports/portfolio/quality.json`
- `reports/portfolio/experiment.json`
- `reports/portfolio/evaluation.json`
- `reports/portfolio/explanations.json`
- `reports/portfolio/openai_live_test_2026-10-23/explanations_openai.json`
- `docs/assets/dashboard-overview.png`
- `docs/assets/dashboard-ingestion.png`
- `docs/assets/dashboard-findings.png`
- `docs/assets/dashboard-benchmark.png`
- `docs/assets/dashboard-explanations.png`

Changes made by DataGuard AI: structural profiling/validation, a seeded experiment
on an isolated copy, finding-level evaluation, reviewed interpretation selections,
and privacy projection removing original values, identifiers and locators while
preserving measured counts and historical usage. Screenshots show those projected
results through the original dashboard. They are not original Olist graphics.
Dates and hash distinctions remain documented in `reports/portfolio/README.md`.
Olist-derived portions of documentation retain this source attribution; the MIT
proposal for original prose does not override third-party rights. No ownership
claim is made over facts, the original dataset, or third-party UI components.

Individual privacy and provenance checks passed, but they do not establish
redistribution rights. These files remain unchanged locally and are explicitly
ignored by Git. The included CSV screenshot uses unrelated synthetic data; no
Olist report JSONs or screenshots are distributed. Original software and synthetic
fixtures remain available under MIT. The individual decisions are documented in
`docs/DATASET_LICENSE.md`; this is a conservative release exclusion, not a finding
that every aggregate/fact is legally restricted or that all portfolio use is prohibited.

See `docs/DATASET_LICENSE.md` and `docs/VERIFICATION.md` for licensing and
verification details. Original uploads, private reports and API credentials
remain excluded regardless of licensing decisions.
