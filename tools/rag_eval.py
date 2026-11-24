"""Acceptance evaluation of Ask Foodo with retrieval (products and Magazine), through the chat.

Every scenario goes through the real endpoint (``POST /ask-foodo/chat/send/``) with a logged-in
Django test client, on the database the app uses. The expected result of each scenario is
computed **independently** of the code under test: raw SQL on the catalogue (current revision,
as sold, declared, the same per-100 basis) and plain substring checks in the article text.
``myola.services.rag`` and ``core.passages`` are never called here.

    # inside the web container (local database), one mode or all
    python manage.py shell < tools/rag_eval.py                       # RAG_EVAL_MODE=noai
    RAG_EVAL_MODE=sim python manage.py shell < tools/rag_eval.py      # simulated provider
    RAG_EVAL_MODE=real python manage.py shell < tools/rag_eval.py     # the configured provider

Modes: ``noai`` (no provider, no embeddings: what users get when AI is off), ``sim`` (a
simulated provider that translates by echoing and answers Magazine questions with a quote of
the passage it was given), ``real`` (the provider configured in Staff → Settings). The test
account is ``VISUAL_CHECK_EMAIL``; a second account (``rag-eval-other``) is created for the
isolation check and deleted at the end. Only chat rows of these accounts are written.
Output: one line per scenario (PASS/FAIL, the failing check), then a summary.
"""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client

MODE = os.environ.get("RAG_EVAL_MODE", "noai")
MARKERS = re.compile(r"\[(?:P|A)\d+\]|\bmatches\b|scanned_product|\bproblem\b|answered_separately")
PIZZA_AH = "f64ca62e-51f9-46ec-ad35-9a602de06107"  # Amori Di pizza shoarma (Albert Heijn)
PIZZA_UK = "0d9e7e76-3c42-41a4-b70c-e268cda17220"  # Spicy pepper-no-ni pizza (UK, salt 2 g)
PIZZA_CA = "22e311d0-d26e-4081-80ea-3af5e09331a6"  # Casa di Mama (CA, no per-100 g salt)
PIZZA_DE = "02d6d196-1793-499e-9482-c778b71c152e"  # Pizza big City Salami (DE)
YOG_DE = "070eca88-6e78-4cb3-ac02-1b6953924207"  # Landliebe (DE, sugars 13 g)
YOG_RO = "16f7d5b4-3bbc-4a67-8095-a89307dfe8e6"  # covalact (RO, sugars 2.2 g)
YOG_US = "300bf6aa-843b-45ec-80e7-dc1af1e2c4e1"  # Brown Cow (US, no sugars per 100 g)
CER_EU = "123fdc2a-2a29-43c6-8a4b-d6dc3607fa07"  # cereal cookie (BG/HR/DE/RO, fibre 5.5 g)
CER_US = "088ebd97-d628-4857-8bd3-4532d9eef8cb"  # Classic Granola (US, fibre 10 g)
CER_NO = "2f54104f-19c2-440b-b562-cc97b6c9d854"  # Cheerios (US, no fibre per 100 g)
SALT_0 = "489020ec-5c22-411a-b08b-36cfae49bb44"  # 100% peanut butter crunchy (salt 0 g)
NO_SALT = "fb9b3c2c-7718-40db-9f63-7b25df48e24a"  # Buffalo Mozzarella (no salt fact)


# ---------- Independent references (SQL) ----------
def sql(query, params=()):
    with connection.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def value(pid, nutrient):
    rows = sql(
        """select f.value, f.basis, f.modifier from catalog_product p
           join catalog_nutrientfact f on f.revision_id = p.current_revision_id
           where p.id = %s and f.nutrient = %s and f.preparation = 'as_sold'
             and f.status = 'declared' and f.value is not null
             and f.basis in ('per_100g', 'per_100ml') order by f.basis""",
        [pid, nutrient],
    )
    return rows[0] if rows else None


def better(pid, category, nutrient, lower=True, markets=None):
    """Ids of products of ``category`` sold in the product's markets (or ``markets``) whose
    declared value on the same basis meets the criterion, best first (top 3)."""
    ref = value(pid, nutrient)
    if ref is None:
        return None
    countries = markets or markets_of(pid)
    op, order = ("<", "asc") if lower else (">", "desc")
    rows = sql(
        f"""select p.id from catalog_product p
            join catalog_nutrientfact f on f.revision_id = p.current_revision_id
            where p.listable and p.id <> %s and %s = any(p.categories)
              and p.countries && %s::varchar[] and f.nutrient = %s and f.basis = %s
              and f.preparation = 'as_sold' and f.status = 'declared'
              and f.modifier in ('', '~') and f.value {op} %s
            order by f.value {order}, p.name limit 3""",  # fixed operators only
        [pid, category, list(countries), nutrient, ref[1], ref[0]],
    )
    return [str(r[0]) for r in rows]


def markets_of(pid):
    """The market applied by the specification: the user's (the UK) when the product is sold
    there, else every market where it is sold."""
    countries = sql("select countries from catalog_product where id=%s", [pid])[0][0]
    return ["en:united-kingdom"] if "en:united-kingdom" in countries else list(countries)


def foodo_better(pid, category):
    rows = sql(
        """with ref as (select p.countries, r.foodo_score as s from catalog_product p
             join catalog_productrevision r on r.id = p.current_revision_id where p.id = %s)
           select p.id from catalog_product p
           join catalog_productrevision r on r.id = p.current_revision_id, ref
           where p.listable and p.id <> %s and %s = any(p.categories)
             and p.countries && %s::varchar[] and r.foodo_score->>'status' = 'ok'
             and r.foodo_score->>'version' = ref.s->>'version'
             and r.foodo_score->>'comparable_key' = ref.s->>'comparable_key'
             and (r.foodo_score->'foodo'->>'value')::int > (ref.s->'foodo'->>'value')::int
           order by (r.foodo_score->'foodo'->>'value')::int desc, p.name limit 3""",
        [pid, pid, category, markets_of(pid)],
    )
    return [str(r[0]) for r in rows]


def article_has(slug, words):
    body = sql("select body from core_learnarticle where slug=%s and published", [slug])
    return bool(body) and all(w.lower() in body[0][0].lower() for w in words)


def num(v):
    return format(Decimal(v).normalize(), "f")


# ---------- Modes ----------
def install_mode():
    from django.conf import settings

    from ai import embeddings, service
    from ai.providers.base import Reply

    # The chat's abuse limit (20 a minute) would stop the evaluation itself.
    settings.RATE_LIMITS = {**settings.RATE_LIMITS, "chat": (10_000, 60)}

    if MODE != "noai":
        # The per-user daily message count would stop a long evaluation (the monthly budget
        # still applies).
        service.reserve_message = lambda user, limit: True
    if MODE == "noai":
        service._ready = lambda user: None
        embeddings.allowed = lambda: False
        embeddings.embed_query = lambda text, user=None: None  # no cached vectors either
        from core import passages

        passages.bump_generation()  # no search result cached by an earlier run either
    elif MODE == "sim":

        class Sim:
            name, model = "fake", "fake-1"

            def reply(self, system, messages, max_tokens, *, short_thinking=False):
                content = messages[-1]["content"]
                if system.startswith("You translate"):
                    text = content
                elif "FACTS:" not in content:  # a general question: no material at all
                    text = "General answer (simulated)."
                else:  # quote the first passage it was given, cited
                    facts = json.loads(content.split("FACTS:\n", 1)[1].split("\n\nQUESTION:")[0])
                    mag = facts.get("magazine") or []
                    text = (
                        f"{mag[0]['passage'][:200]} [{mag[0]['id']}]"
                        if mag
                        else "The material does not cover this."
                    )
                return Reply(text=text, input_tokens=10, output_tokens=10, model=self.model)

        service.get_provider = lambda name, model="": Sim()


# ---------- Running a scenario ----------
User = get_user_model()
me = User.objects.get(email=os.environ["VISUAL_CHECK_EMAIL"])
client = Client(HTTP_HOST="localhost")
client.force_login(me)
results = []


def ask(context, question, who=client):
    r = who.post("/ask-foodo/chat/send/", {"context": context, "text": question},
                 HTTP_HX_REQUEST="true")  # fmt: skip
    from myola.models import ChatMessage

    user = me if who is client else other
    msg = ChatMessage.objects.filter(user=user, context=context, role="ola").order_by("-pk")[0]
    return r.status_code, msg


def product_url(pid):
    return f"/p/{pid}/"


def check(name, context, question, *, products=None, values=(), articles=(), absent=(),
          none_of=(), says=(), expect_links=True, who=None):  # fmt: skip
    """``products``: expected product ids linked (order kept; None = not checked);
    ``values``: numbers that must appear; ``articles``: slugs that must be linked;
    ``absent``: slugs that must not be linked; ``says``: any of these words must appear."""
    problems = []
    try:
        status, msg = ask(context, question, who or client)
    except Exception as exc:  # reported as a failure of the scenario
        results.append((name, False, f"error: {type(exc).__name__}: {exc}"[:160], ""))
        return None
    text = msg.text
    flat = text.replace(",", ".")
    if status != 200:
        problems.append(f"HTTP {status}")
    if MARKERS.search(text):
        problems.append(f"internal marker {MARKERS.search(text).group(0)!r}")
    lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) > 30]
    if len(lines) != len(set(lines)):
        problems.append("repeated explanation")
    urls = [s.get("url") for s in msg.sources]
    if products is not None:
        want = [product_url(p) for p in products]
        got = [u for u in urls if u.startswith("/p/")]
        if got != want:
            problems.append(f"product links {len(got)} != expected {len(want)}")
    for slug in articles:
        if f"/learn/{slug}/" not in urls:
            problems.append(f"missing article link {slug}")
    for slug in absent:
        if f"/learn/{slug}/" in urls:
            problems.append(f"unexpected article link {slug}")
    for v in values:
        if v not in flat:
            problems.append(f"value {v} not shown")
    for w in none_of:
        if w.lower() in text.lower():
            problems.append(f"should not say {w!r}")
    if says and not any(w.lower() in text.lower() for w in says):
        problems.append(f"expected one of {says}")
    for u in urls:
        if client.get(u).status_code != 200:
            problems.append(f"link {u} broken")
    results.append((name, not problems, "; ".join(problems), text.replace("\n", " / ")[:220]))
    return msg


def run():
    global other
    install_mode()
    other = User.objects.create_user("rag-eval-other", password="rag-eval-other-password-1")
    other_client = Client(HTTP_HOST="localhost")
    other_client.force_login(other)
    try:
        scenarios(other_client)
    finally:
        other.delete()
    report()


def scenarios(other_client):
    # 1. A value of the open product: existing, zero, unknown (RO, EN, no diacritics, typo).
    v = value(PIZZA_AH, "salt")
    check("value existing (RO)", f"product:{PIZZA_AH}", "Câtă sare are?",
          values=[f"{num(v[0])} g"], products=[PIZZA_AH])  # fmt: skip
    check("value existing (RO, no diacritics, caps)", f"product:{PIZZA_AH}", "cata sarE are?",
          values=[f"{num(v[0])} g"], products=[PIZZA_AH])  # fmt: skip
    check("value existing (EN)", f"product:{PIZZA_AH}", "How much salt is in it?",
          values=[f"{num(v[0])} g"], products=[PIZZA_AH])  # fmt: skip
    check("value zero (EN)", f"product:{SALT_0}", "How much salt does it have?",
          values=["0 g"], products=[SALT_0])  # fmt: skip
    assert value(NO_SALT, "salt") is None
    check("value unknown (RO)", f"product:{NO_SALT}", "Câtă sare are?", products=[NO_SALT],
          says=["not", "nu ", "no "])  # fmt: skip

    # 2. Alternatives by a criterion: alternative / none meets it / data missing.
    q_salt = "ce alta pizza are mai putina sarE?"
    exp = better(PIZZA_UK, "en:pizzas", "salt")
    check("salt↓ pizza UK: alternative", f"product:{PIZZA_UK}", q_salt,
          products=[PIZZA_UK, *exp], values=[f"{num(value(p, 'salt')[0])} g" for p in exp])  # fmt: skip
    exp_de = better(PIZZA_DE, "en:pizzas", "salt")
    check("salt↓ pizza DE: alternative", f"product:{PIZZA_DE}", q_salt,
          products=[PIZZA_DE, *exp_de])  # fmt: skip
    assert better(PIZZA_AH, "en:pizzas", "salt") == []
    check("salt↓ pizza BE/NL: none meets it (bug report)", f"product:{PIZZA_AH}", q_salt,
          products=[PIZZA_AH], says=["Belgi", "Netherlands", "Olanda", "Țările de Jos"])  # fmt: skip
    check("salt↓ pizza CA: data missing", f"product:{PIZZA_CA}", q_salt, products=[PIZZA_CA],
          says=["not", "nu ", "no "])  # fmt: skip
    q_sug = "Ce alt iaurt are mai puțin zahăr?"
    exp = better(YOG_DE, "en:yogurts", "sugars")
    check("sugar↓ yogurt DE: alternative", f"product:{YOG_DE}", q_sug,
          products=[YOG_DE, *exp], values=[f"{num(value(p, 'sugars')[0])} g" for p in exp])  # fmt: skip
    assert better(YOG_RO, "en:yogurts", "sugars") == []
    check("sugar↓ yogurt RO: none meets it", f"product:{YOG_RO}", q_sug, products=[YOG_RO],
          says=["Rom"])  # fmt: skip
    check("sugar↓ yogurt US: data missing", f"product:{YOG_US}", q_sug, products=[YOG_US],
          says=["not", "nu ", "no "])  # fmt: skip
    q_fib = "Which other cereals have more fibre?"
    exp = better(CER_EU, "en:breakfast-cereals", "fiber", lower=False)
    check("fibre↑ cereals EU: alternative (EN)", f"product:{CER_EU}", q_fib,
          products=[CER_EU, *exp], values=[f"{num(value(p, 'fiber')[0])} g" for p in exp])  # fmt: skip
    assert better(CER_US, "en:breakfast-cereals", "fiber", lower=False) == []
    check("fibre↑ cereals US: none meets it", f"product:{CER_US}", q_fib, products=[CER_US])
    check("fibre↑ cereals US: data missing", f"product:{CER_NO}", q_fib, products=[CER_NO],
          says=["not", "no "])  # fmt: skip
    exp = foodo_better(YOG_DE, "en:yogurts")
    check("Foodo Score↑ yogurt DE: alternative", f"product:{YOG_DE}",
          "Ce alt iaurt are un Foodo Score mai mare?", products=[YOG_DE, *exp])  # fmt: skip

    # 3. Editorial questions (other words than the article) and one the Magazine does not cover.
    assert article_has("festive-packs-per-100g", ["per portion", "per 100 g"])
    check("Magazine: other words (RO)", "personal",
          "De ce porția scrisă pe fața pachetului mă poate păcăli?",
          articles=["festive-packs-per-100g"])  # fmt: skip
    assert article_has("hidden-sugar", ["glucose syrup", "dextrose"])
    check("Magazine: other words (EN)", "personal", "what are the sneaky names for sugar?",
          articles=["hidden-sugar"])  # fmt: skip
    check("Magazine: not covered", "personal", "Câte vitamine are spanacul gătit?",
          absent=[s for (s,) in sql("select slug from core_learnarticle")])  # fmt: skip

    # 4. Mixed: numbers from the product records + an explanation from the Magazine.
    exp = better(YOG_DE, "en:yogurts", "sugars")
    check("mixed: data + passage", f"product:{YOG_DE}",
          "Recomandă un iaurt cu mai puțin zahăr și explică cum găsesc zahărul în ingrediente",
          products=[YOG_DE, *exp], values=[f"{num(value(exp[0], 'sugars')[0])} g"],
          articles=["hidden-sugar"])  # fmt: skip
    check("mixed: only data (Magazine silent)", f"product:{CER_EU}",
          "Which other cereals have more fibre and why does fibre help my gut bacteria?",
          products=[CER_EU, *better(CER_EU, "en:breakfast-cereals", "fiber", lower=False)])  # fmt: skip
    check("mixed: only passage (no product meets it)", f"product:{YOG_RO}",
          "Ce alt iaurt are mai puțin zahăr și cum recunosc zahărul ascuns?",
          products=[YOG_RO], articles=["hidden-sugar"])  # fmt: skip

    # 5. Follow-up in the same conversation.
    ctx = f"product:{PIZZA_UK}"
    first = check("follow-up 1: compare", ctx, "Ce pizza din UK are mai puțină sare?",
                  products=[PIZZA_UK, *better(PIZZA_UK, "en:pizzas", "salt")])  # fmt: skip
    two = [PIZZA_UK, *better(PIZZA_UK, "en:pizzas", "salt")][:2]
    if first is not None:
        check("follow-up 2: 'Dar la 200 g?'", ctx, "Dar la 200 g?", products=two,
              values=[f"{num(value(p, 'salt')[0] * 2)} g" for p in two])  # fmt: skip
    # A fresh account: nothing asked before in this chat, so the amount refers to nothing.
    check("follow-up ambiguous (no comparison before)", f"product:{SALT_0}", "Dar la 200 g?",
          says=["compar"], none_of=["605 kcal"], expect_links=False, who=other_client)  # fmt: skip

    # 5b. A catalogue recommendation that refers to the conversation ("these fats").
    fatty = [str(r[0]) for r in sql(
        """select p.id from catalog_product p join catalog_nutrientfact f
             on f.revision_id = p.current_revision_id and f.nutrient = 'fat'
            and f.basis = 'per_100g' and f.preparation = 'as_sold' and f.status = 'declared'
           where p.listable and 'en:fishes' = any(p.categories)
             and 'en:fatty-fishes' = any(p.categories)
             and 'en:united-kingdom' = any(p.countries) and f.value is not null
           order by f.value desc, p.name limit 3""")]  # fmt: skip
    assert not sql("""select 1 from catalog_product p join catalog_productrevision r
                      on r.id = p.current_revision_id where 'en:fishes' = any(p.categories)
                      and r.off_payload->'nutriments' ? 'omega-3-fat_100g' limit 1""")
    ask("personal", "Ce sunt grăsimile omega-3?")
    check("recommend: 'aceste grăsimi' after omega-3", "personal",
          "Ce pește îmi recomanzi care să aibă aceste grăsimi?", products=fatty,
          says=["omega-3"], values=[f"{num(value(p, 'fat')[0])} g" for p in fatty])  # fmt: skip

    # 6. Scanned product without a product context; isolation between two accounts.
    check("scanned product but no product open", "personal",
          "Ce pizza are mai puțină sare decât produsul scanat?",
          says=["Open", "Deschide", "deschide", "open "],
          absent=[s for (s,) in sql("select slug from core_learnarticle")])  # fmt: skip
    code, _ = ask(f"product:{PIZZA_UK}", "Ce pizza din UK are mai puțină sare?", other_client)
    page = client.get("/ask-foodo/chat/", {"context": f"product:{PIZZA_UK}"}).content.decode()
    leaked = "rag-eval-other" in page
    from myola.models import ChatMessage

    mine = ChatMessage.objects.filter(user=me, context=f"product:{PIZZA_UK}").count()
    theirs = ChatMessage.objects.filter(user=other, context=f"product:{PIZZA_UK}").count()
    results.append(("isolation: two accounts", code == 200 and not leaked and theirs == 2,
                    f"mine={mine} theirs={theirs}", ""))  # fmt: skip


def report():
    passed = sum(1 for _, ok, _, _ in results if ok)
    print(f"\nRAG evaluation, mode={MODE}: {passed}/{len(results)} passed\n")
    for name, ok, why, text in results:
        print(f"{'PASS' if ok else 'FAIL'} | {name} | {why}")
        if text:
            print(f"       {text}")


run()
