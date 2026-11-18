# MVP data contracts (UI ↔ data)

Structures the MVP interface consumes. Product data is real: Open Food Facts plus approved local
corrections, read through the existing contracts (`ProductDetailSerializer`, `revision.data`,
`/api/v1/compare/`). The personal layer is computed by deterministic Python rules (`myola/`) and
stored in the browser (`static/js/ola/store.js`). No production endpoints or migrations exist yet
for anything below.

Conventions:

* Ids are stable: catalogue products use their UUID.
* Every number carries its unit and basis: `{value: "12.5", unit: "g", basis: "per_100g",
  preparation: "as_sold"}`. `value` is a decimal string. Bases are never converted (no g↔ml).
* Unknown is explicit (`status: "unknown"`, `"not_enough_data"`, `null` score) and is never 0.
* Texts are plain text. The UI renders them with `textContent` only.

## 1. Product payload on the product page (`#ola-data`, `myola.page.ola_payload`)

```json
{
  "product": {"id": "uuid", "name": "", "brand": "", "quantity": "", "image": "/…", "url": "/…"},
  "restrictions": Restrictions,
  "facts": {"basis": "per_100g" | "per_100ml" | null, "basis_label": "per 100 g", "values": {"sugars": {"value": "12.5", "unit": "g", "display": "12.5 g"}}},
  "nutriscore": "c" | "",
  "nova": 4 | null
}
```

Goals (`lower_sugar`, `more_protein`) only bring the matching value (sugars, protein, per 100 g or
100 ml) to the top of the “For you” card; no score is computed in the MVP.

## 2. Restrictions (`myola.restrictions.all_statuses`)

```json
{
  "diets": {"vegan": {"status": "compatible" | "incompatible" | "may_contain" | "unknown", "reason": "…"}},
  "allergens": {"en:milk": {"status": "contains" | "may_contain" | "not_declared" | "unknown", "reason": "…"}}
}
```

* **Diets**: `vegetarian`, `vegan`, `gluten_free`, `lactose_free`, `pork_free`.
* **Allergens**: the 14 UK/EU allergens as Open Food Facts tags (`myola.preferences.ALLERGENS`).
* **Sources**: allergen and trace sections (`listed` / `none_declared` / `unknown`), label tags and
  the source's ingredient analysis.
* **How the rules decide**:
  * evidence of incompatibility wins;
  * an empty field never confirms compatibility;
  * "Contains" and "May contain" stay separate;
  * lactose-free (a diet) is not the same as a milk allergy.
* **What counts as respected**: only `compatible` for diets and only `not_declared` for allergens.
* **Wording**: no state is called "safe"; the UI always points to the physical label.

## 3. V2: score and alternatives (not in the MVP)

* **Score**: a single Ola Score with reconstructible points, computed in Python; the production
  rule and weights are decided before it is published. Unknown values give `not_enough_data`.
* **Alternatives**: up to 3 similar products recorded as sold in the UK, each with a verifiable
  advantage and its trade-offs, filtered by strict restrictions and the Avoid list.

**Search contract.**

* **Who asks OFF, and what**: the Django backend sends a structured Open Food Facts search with
  country `en:united-kingdom` and the reference category (then its parent). It asks only for the
  fields needed here (code, name, brand, quantity, categories, countries, nutriments per 100,
  allergens, traces, labels, ingredients analysis, image).
* **Limits**: a short timeout, a cache per (category, country) and the shared OFF rate limit
  (`core.coordination`).
* **Local products**: approved local products are added. Contributions still under review are
  never used.
* **Python decides**: it normalises the results into `revision.data` and decides eligibility,
  score and order.
  OFF never decides allergen compatibility.
* **Privacy**: personal preferences are never sent to OFF.

## 4. Preferences, lists and activity (browser store `olahealth:myola`, version 1)

```json
{
  "v": 1,
  "prefs": {"goals": ["lower_sugar"], "diets": ["vegan"], "allergens": ["en:milk"]},
  "lists": {"buyOften": [Entry], "avoid": [Entry]},
  "scans": [Entry + {"scanId": "id", "code": "5941234567899"}],
  "notes": [Note], "memories": [Memory], "chats": {"personal": [Message], "product:<uuid>": [Message]},
  "settings": {"useNotes": false},
  "onboarded": false
}
```

* **Entry**: `{id, name, brand, quantity, image, url, at}`. Favourites keep their existing
  key `olahealth:favorites`, with the same entry shape plus an optional `url`.
* **Buy often / Avoid**: the two lists are exclusive. Avoid also removes the product from
  personalised alternatives.
* **Scans**: recorded only when a product page is opened from the scan or code-entry flow
  (`?scanned=`). Reloads, back/forward and plain visits are never recorded.
* **Recently viewed**: the older `olahealth:history` key stays as "Recently viewed" (page views,
  not scans).

## 5. Notes, memories and chat

* **Note**: `{id, text ≤ 2000, kind: general|taste|prefer|avoid, product: Entry|null, createdAt, updatedAt}`.
* **Memory**: `{id, text ≤ 300, source: manual|suggestion, suggestionKey, active, confirmedAt|null, createdAt}`.
  * Only memories that are `active` **and** confirmed are used in replies.
  * Suggestions come only from recorded actions or preferences, and stay unused until the
    user confirms them.
  * Deleting or pausing a memory excludes it from later replies.
* **Message**: `{role: user|ola, text, at}`, stored per context.
  * Contexts are `personal` and `product:<uuid>`.
  * Deleting the chat history removes all contexts.
* **Ask Ola (MVP)**:
  * Intents are deterministic: values, indicators, restrictions, remember, avoid, scans, prefs.
  * Answers are built only from the page payload and the store.
  * Notes are used only when `settings.useNotes` is on, and only the relevant ones (same product,
    or matching words).
  * Anything else gets a clear limit and a suggested question.
* **V2**: Python computes scores, filters and the facts; an AI model receives only that context
  and phrases the reply. Standard explanations, the product page, the score and the basic
  comparison keep working without an AI call.
* **Never stored as data**: diagnoses, symptoms or medication. The UI never asks for them, and
  nothing turns them into memories.

## 6. Comparison

The existing `/compare/` and `/api/v1/compare/` are unchanged. The pure part of the comparison is
available as `assessments.services.compare.build_comparison` (no database access).

## 7. What stays out of the catalogue

Data from My Ola stays on the device and never reaches the catalogue or the export.
