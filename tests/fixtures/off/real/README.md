# Real Open Food Facts products (Foodo Score examples)

These are **real products**, downloaded from the Open Food Facts product API v2 with
`fetch_real.py` (date in each file's `_fixture.fetched_at`). The files are trimmed to the fields
AskFoodo reads: no images, no contributor or user data. The data may have changed on OFF since
the download; the tests check our rules against these files, not against today's OFF.
Each product keeps OFF's per-version Nutri-Score grade, points and calculation-category flags
(`nutriscore.<version>.data.is_*`), which the Foodo Score needs.

**License:** Open Food Facts contributors, [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Product pages: `https://world.openfoodfacts.org/product/<code>`.

| Code | Product (kind) | Why it is here |
|---|---|---|
| 3033490004743 | plain skyr | A in 2021 and 2023 |
| 5010029000016 | breakfast cereal | A, NOVA 3 |
| 5000157024671 | baked beans | A, NOVA 4: processing does not change the score |
| 20994945 | cottage cheese | B in 2023, A in 2021 (versions differ) |
| 8002470031739 | frying oil (fats rules) | C in 2023, D in 2021; no ingredient list → additives unknown |
| 3175680011480 | bread | C in 2023, B in 2021; 4 additives listed |
| 5449000131805 | sugar-free cola (beverage rules) | C in 2023, B in 2021; 6 additives listed |
| 7622201693916 | soft cheese (cheese rules) | D, NOVA 4 |
| 3329770063341 | drinkable yogurt with sugar | D in 2023, E in 2021 |
| 5449000054227 | cola (beverage rules) | E, NOVA 4 |
| 3017624010701 | hazelnut spread | E; NOVA group missing |
| 3068320114453 | sparkling mineral water | A under the 2023 beverage rules |
| 75041670 | beer | Nutri-Score not applicable (alcohol) |
| 10614594 | Emmentaler | Nutri-Score unknown, no ingredient list, NOVA missing |
| 0051500241639 | crunchy peanut butter (fats, oils, nuts and seeds) | D, 12 points |
| 8710532469535 | 100% peanut butter (fats, oils, nuts and seeds) | A, −9 points |

No red-meat product (2023 `is_red_meat_product`) could be fetched while OFF search was
unavailable; that category and every edge of the formula are covered by synthetic cases in
`tests/test_foodo_formula.py`.

Cases that real data cannot give on demand (a grade printed on the package that disagrees, a
local correction, a missing algorithm version, invalid values) are **synthetic** and built in
`tests/test_score_v3.py`; they are marked there as such and are not validation on real products.
