// Ask Foodo (MVP): deterministic answers built only from the facts already on the page
// (the product's values, its Nutri-Score, processing level and restriction states) and from this device's My Track data
// (lists, scans, confirmed memories, and notes only when the user allows it).
// It never invents values, ingredients, effects, availability or compatibility, and gives no
// medical advice. In V2, Python provides the facts and an AI only phrases the answer.

import { statusText, DIET_LABELS, GOAL_LABELS, ALLERGEN_LABELS } from "./assess.js";
import { load, update } from "./store.js";

const SUGGESTIONS = {
  product: [
    ["values", "What are the key values?"],
    ["indicators", "What do Nutri-Score and processing mean?"],
    ["restrictions", "Does it fit my restrictions?"],
    ["remember", "What do you remember?"],
  ],
  personal: [
    ["remember", "What do you remember?"],
    ["avoid", "What did I mark as Avoid?"],
    ["scans", "What did I scan recently?"],
    ["prefs", "What are my preferences?"],
  ],
};
const LIMIT_TEXT =
  "I can't answer that yet: I only use the facts shown in the app and what you saved on this device. Try: ";

function intentOf(text) {
  const t = text.toLowerCase();
  if (/(nutri|nova|score|grade)/.test(t)) return "indicators";
  if (/(sugar|salt|fat|protein|fibre|fiber|calor|energy|value)/.test(t)) return "values";
  if (/(remember|memor)/.test(t)) return "remember";
  if (/avoid/.test(t)) return "avoid";
  if (/scan/.test(t)) return "scans";
  if (/(allerg|vegan|vegetarian|gluten|lactose|pork|restriction|fit)/.test(t)) return "restrictions";
  if (/(preference|goal)/.test(t)) return "prefs";
  return null;
}

const fmtDate = (ms) => new Date(ms).toLocaleDateString(undefined, { day: "numeric", month: "short" });
const quote = (s) => `“${s}”`;

function relevantNotes(state, ctx, question) {
  if (!state.settings.useNotes) return [];
  const words = question.toLowerCase().split(/\W+/).filter((w) => w.length >= 4);
  return state.notes
    .filter((n) => {
      if (ctx.product && n.product && n.product.id === ctx.product.product.id) return true;
      const text = n.text.toLowerCase();
      return words.some((w) => text.includes(w));
    })
    .slice(0, 2);
}

function prefsSummary(prefs) {
  const parts = [
    ...prefs.goals.map((g) => GOAL_LABELS[g]),
    ...prefs.diets.map((d) => DIET_LABELS[d]),
    ...prefs.allergens.map((a) => `allergy: ${ALLERGEN_LABELS[a]}`),
  ];
  return parts.length ? parts.join(", ") : "";
}

export function answer(intent, ctx, question = "") {
  const state = load();
  const prefs = state.prefs;
  const p = ctx.product; // page payload or null
  const lines = [];

  if (intent === "indicators") {
    if (!p) {
      lines.push("Open a product and ask there: I explain that product's indicators.");
    } else {
      lines.push(
        p.nutriscore
          ? `Nutri-Score ${p.nutriscore.toUpperCase()}, as published by Open Food Facts (A is the most favourable, E the least), computed per 100 g or 100 ml.`
          : "The source gives no Nutri-Score for this product."
      );
      lines.push(p.nova ? `Processing: ${["", "unprocessed or minimally processed", "processed culinary ingredient", "processed food", "ultra-processed"][p.nova]}, judged from the ingredient list. It says nothing about sugar, fat or salt.` : "The source does not say how processed it is.");
    }
  } else if (intent === "values") {
    if (!p) {
      lines.push("Open a product and ask there: I read that product's label values.");
    } else {
      const v = p.facts.values;
      const names = [["energy-kcal", "Energy"], ["sugars", "Sugars"], ["saturated-fat", "Saturates"], ["salt", "Salt"], ["proteins", "Protein"], ["fiber", "Fibre"]];
      const known = names.filter(([k]) => v[k]).map(([k, label]) => `${label} ${v[k].display}`);
      const unknown = names.filter(([k]) => !v[k]).map(([, label]) => label.toLowerCase());
      if (!known.length) lines.push("No values per 100 g or 100 ml are recorded for this product.");
      else lines.push(`${p.facts.basis_label[0].toUpperCase()}${p.facts.basis_label.slice(1)}: ${known.join("; ")}.`);
      if (known.length && unknown.length) lines.push(`Not recorded: ${unknown.join(", ")}.`);
    }
  } else if (intent === "restrictions") {
    if (!p) {
      lines.push("Open a product and ask there: I check its recorded allergens and diet information.");
    } else if (!prefs.diets.length && !prefs.allergens.length) {
      lines.push("You haven't chosen any diet or allergy. You can set them in My Track → Profile.");
    } else {
      for (const d of prefs.diets) {
        const s = p.restrictions.diets[d];
        lines.push(`${DIET_LABELS[d]}: ${statusText(s.status)}. ${s.reason}`);
      }
      for (const a of prefs.allergens) {
        const s = p.restrictions.allergens[a];
        lines.push(`${ALLERGEN_LABELS[a]}: ${statusText(s.status)}. ${s.reason}`);
      }
      lines.push("This is not a guarantee: always check the physical label.");
    }
  } else if (intent === "remember") {
    const mem = state.memories.filter((m) => m.active && m.confirmedAt);
    if (mem.length) lines.push(`You asked me to remember: ${mem.map((m) => quote(m.text)).join("; ")}.`);
    else lines.push("I don't remember anything yet. You can add or confirm memories in My Track → Profile → Foodo remembers.");
    const summary = prefsSummary(prefs);
    if (summary) lines.push(`Your preferences on this device: ${summary}.`);
  } else if (intent === "avoid") {
    const items = state.lists.avoid;
    lines.push(items.length ? `You marked as Avoid: ${items.map((x) => x.name || "Unnamed product").join(", ")}.` : "You haven't marked any product as Avoid.");
  } else if (intent === "scans") {
    const items = state.scans.slice(0, 5);
    lines.push(items.length ? `Your recent scans on this device: ${items.map((x) => `${x.name || "Unnamed product"} (${fmtDate(x.at)})`).join(", ")}. A scan is not a purchase.` : "No scans recorded on this device yet.");
  } else if (intent === "prefs") {
    const summary = prefsSummary(prefs);
    lines.push(summary ? `Your preferences on this device: ${summary}.` : "You haven't set any preferences yet.");
  } else {
    const options = SUGGESTIONS[p ? "product" : "personal"].map(([, label]) => quote(label)).join(", ");
    lines.push(LIMIT_TEXT + options + ".");
    return lines.join("\n");
  }

  const notes = relevantNotes(state, ctx, question);
  if (notes.length) lines.push(`From your notes: ${notes.map((n) => quote(n.text)).join("; ")}.`);
  return lines.join("\n");
}

// ---------- Dialog ----------
const dialog = document.querySelector("[data-chat]");
let current = null; // { key, product }

function render() {
  if (!dialog || !current) return;
  const log = dialog.querySelector("[data-chat-log]");
  const msgs = load().chats[current.key] || [];
  log.replaceChildren(
    ...msgs.map((m) => {
      const li = document.createElement("li");
      li.className = `chat-msg chat-${m.role}`;
      const who = document.createElement("span");
      who.className = "sr-only";
      who.textContent = m.role === "user" ? "You: " : "Foodo: ";
      const text = document.createElement("p");
      text.textContent = m.text; // plain text only
      li.append(who, text);
      return li;
    })
  );
  const empty = dialog.querySelector("[data-chat-empty]");
  if (empty) empty.hidden = msgs.length > 0;
  log.scrollTop = log.scrollHeight; // scroll the messages only, never the page
}

function ask(text, intent) {
  const reply = answer(intent || intentOf(text), current, text);
  update((s) => {
    const list = s.chats[current.key] || [];
    list.push({ role: "user", text, at: Date.now() }, { role: "ola", text: reply, at: Date.now() });
    s.chats[current.key] = list;
  });
  render();
  const live = dialog.querySelector("[data-chat-live]");
  if (live) {
    live.textContent = "";
    window.setTimeout(() => (live.textContent = reply), 50);
  }
}

export function openChat(ctx) {
  if (!dialog) return;
  current = ctx;
  dialog.querySelector("[data-chat-context]").textContent = ctx.product
    ? `About: ${ctx.product.product.name || "this product"}`
    : "About your preferences, lists, scans, notes and memories";
  const box = dialog.querySelector("[data-chat-suggest]");
  box.replaceChildren(
    ...SUGGESTIONS[ctx.product ? "product" : "personal"].map(([intent, label]) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip-btn";
      b.textContent = label;
      b.addEventListener("click", () => ask(label, intent));
      return b;
    })
  );
  render();
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
  fitToViewport();
  // On touch screens the keyboard opens only when the user taps the field.
  if (window.matchMedia("(pointer: fine)").matches) dialog.querySelector("[data-chat-input]")?.focus();
  else dialog.querySelector("[data-chat-close]")?.focus({ preventScroll: true });
}

// Phones: the panel fills exactly the visible area above the on-screen keyboard, so its top
// (title and Close) never moves out of view. Sizes go through the CSSOM (allowed by the CSP).
function fitToViewport() {
  if (!dialog || !dialog.open) return;
  const vv = window.visualViewport;
  const height = vv ? vv.height : window.innerHeight;
  const top = vv ? vv.offsetTop : 0;
  dialog.style.setProperty("--chat-h", `${Math.round(height)}px`);
  dialog.style.setProperty("--chat-top", `${Math.round(top)}px`);
  const log = dialog.querySelector("[data-chat-log]");
  if (log) log.scrollTop = log.scrollHeight;
}
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", fitToViewport);
  window.visualViewport.addEventListener("scroll", fitToViewport);
}
window.addEventListener("resize", fitToViewport);

if (dialog) {
  dialog.querySelector("[data-chat-form]").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = dialog.querySelector("[data-chat-input]");
    const text = input.value.trim().slice(0, 300);
    if (!text) return;
    input.value = "";
    ask(text);
  });
  dialog.querySelector("[data-chat-close]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
  });
  window.addEventListener("ola:change", render);
}

export function chatKey(productPayload) {
  return productPayload ? `product:${productPayload.product.id}` : "personal";
}

document.querySelectorAll("[data-chat-open]").forEach((btn) =>
  btn.addEventListener("click", () => {
    if (btn.dataset.chatOpen === "personal") openChat({ key: "personal", product: null });
    else {
      const payload = JSON.parse(document.getElementById("ola-data").textContent);
      openChat({ key: chatKey(payload), product: payload });
    }
  })
);

