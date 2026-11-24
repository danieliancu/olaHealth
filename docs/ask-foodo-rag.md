# Ask Foodo with retrieval (products and Magazine)

Ask Foodo answers these questions in the existing chat (`/ask-foodo/chat/`):

* a **value of the open product**: "Câtă sare are?";
* **alternatives** by one criterion: "Ce altă pizza are mai puțină sare?";
* **recommendations** from the whole catalogue, also in a chat started from the diary:
  "Ce pește îmi recomanzi care să aibă aceste grăsimi?";
* **follow-ups** for another amount: "Dar la 200 g?";
* **explanations** from the Magazine, alone or after a comparison: "…și cum compar etichetele?".

The rule: **numbers come from the product records, explanations from Magazine passages, links
from what the app selected.** Answers with product numbers are written by code, directly in
English or Romanian. Other languages are translated like the rule answers. The AI provider
never writes numbers: it only phrases Magazine-only answers, from the passages it is given.

## Flow (`myola/services/chat.py`)

1. Product search (`find …`, barcodes).
2. `_data_answer`, from the data only, in this order:
   * **follow-up** (`rag.amount_asked` / `rag.followup_answer`): the last Foodo answer of the
     same chat keeps what it was built from (`ChatMessage.details`). The values per 100 g are
     multiplied in code, on the same unit only: g with per 100 g, ml with per 100 ml, never
     converted. Without a previous value, or with another unit, a short question back.
   * **recommendation** (`rag.wants_recommendation` / `rag.recommend`): "recomandă-mi…",
     "ce produs are…", "unde găsesc…" with a food ("pește" → `en:fishes`). The market is
     named in the question, else the user's.
     * "These fats" and other references are read from the user's earlier messages in the
       same chat.
     * The answer lists 1–3 real products with links, ranked by the value asked for.
     * Things the catalogue does not record (omega-3 for fish) are said to be missing. The
       answer then only lists what the data confirms: OFF's "Fatty fishes" category and the
       total fat per 100 g.
   * **value of the open product** (`rag.nutrient_asked` / `rag.value_answer`): declared, as
     sold, per 100 g or 100 ml. 0 is a value; a missing value is said to be missing.
3. Personal questions and allergy/diet checks keep their existing paths.
4. **Comparison** (`rag.compare_products` → `rag.direct_answer`).
   * **Criterion:** one nutrient and a direction, or the Foodo Score. A question that names
     a nutrient compares that nutrient.
   * **Market** (`rag.markets_for`): named in the question, else where the open product is
     sold (the user's market first when it is one of them).
   * **Category:** the open product's, or the one the question names.
   * **Candidates:** listable products, with current values (approved corrections included).
   * **Comparison rule:** the same nutrient, unit, per-100 basis and as-sold state, declared
     values only. "< x" / "> x" count only when they settle it.
   * **Exclusions:** why each candidate was left out (avoid, no_value, other_unit,
     label_bound, not_better, other_scale) is stored in `ChatMessage.details`, never shown.
   * **Answers:**
     * products that meet the criterion, with their values;
     * "no match in the verified catalogue" (compared n of N);
     * "not enough data" (no candidate has a comparable value);
     * "the label gives no value" (the open product's own value is missing).
   * **Explanations:** when the question also asks how or why, the best Magazine passage is
     quoted after the data. For a Romanian answer the passage is translated (translation
     only), else it stays in English, marked as such. If no passage fits, one line says the
     Magazine has nothing on it.
5. **Magazine-only questions:** `ai.service.ask(mode="rag")` gets the passages only. Without
   the AI, a quote of the passage is shown.
6. **Visible text:** citation ids (`[P1]`, `[A1]`) and field names never reach it
   (`rag.cited_sources`). Links are under the answer (`ChatMessage.sources`).

## Magazine passages (`core/passages.py`, `core.ArticlePassage`)

* Each published article is split into its title and summary, then its sections (~700
  characters each). Passages are versioned by a hash of title, summary and body.
* **Search by meaning:** OpenAI `text-embedding-3-large`, vectors stored in PostgreSQL, cosine
  computed in Python, threshold 0.26. On the Magazine, unrelated questions scored at most
  0.23 and answering passages 0.28 or more, also for Romanian questions. The small model
  confused them. Only vectors of the current model are used; `rebuild_passages` re-embeds.
* **Full-text fallback** (no embeddings): OR of the question's words, and a passage must hold
  at least two of them. It cannot match a Romanian question with an English article.
* **Edits and withdrawals:** an edit rebuilds the passages at once and the worker adds the
  vectors. Unpublishing or deleting removes them at once. Each search re-reads the article.
  The search cache is keyed by a generation bumped on every article change and by whether
  embeddings are available.

## Evaluation (`tools/rag_eval.py`)

28 scenarios through the real chat endpoint. Expected results come from SQL and the article
text, never from the code under test. The script runs in three modes:
`RAG_EVAL_MODE=noai|sim|real`.

```sh
docker exec -e RAG_EVAL_MODE=real olahealth-web-1 sh -c "python manage.py shell < tools/rag_eval.py"
```

Latest results (2026-11-24, `olahealth_pilot`):

* **real model:** 28/28;
* **simulated model:** 28/28;
* **no AI:** 25/28. The 3 failures are Romanian questions whose answer is in an English
  article; they need embeddings.

## Limits

* Without AI, non-English answers other than Romanian, and Magazine quotes, stay in English
  with a one-line note.
* Omega-3, vitamins and other values the import does not map are not compared; they are said
  to be missing.
* The pilot has few per-100 g values for some categories (UK pizzas: 2 of 6 with salt per
  100 g).
