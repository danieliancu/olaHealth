// My Track page: Overview, Notes, Activity and Preferences (all on this device).

import { announce, productLists } from "../app.js";
import { confirmDialog } from "../dialog.js";
import { ALLERGEN_LABELS, DIET_LABELS } from "./assess.js";
import { h, fmtDate, icon } from "./dom.js";
import { load, newId, reset, save, storageOk, update } from "./store.js";
import "./prefs.js";
import { bindProfile } from "./profile.js";
import "./chat.js";

const root = document.querySelector("[data-myola-tabs]");

// ---------- Tabs (hash keeps the tab on reload and Back) ----------
const TABS = ["overview", "profile", "notes", "saved", "account"];
function selectTab(name, focus = false) {
  // "#targets" opens Profile at the daily targets form.
  const toTargets = name === "targets";
  if (toTargets) name = "profile";
  if (!TABS.includes(name)) name = "overview";
  if (toTargets) {
    window.setTimeout(() => {
      const form = document.querySelector("[data-targets-form]");
      form?.scrollIntoView({ block: "start" });
      form?.querySelector("input:not([hidden])")?.focus({ preventScroll: true });
    }, 60);
  }
  root.querySelectorAll("[role=tab]").forEach((t) => {
    const on = t.dataset.tab === name;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    if (on && focus) t.focus();
  });
}
function bindTabs() {
  const tabs = [...root.querySelectorAll("[role=tab]")];
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => {
      history.replaceState(null, "", `#${tab.dataset.tab}`);
      selectTab(tab.dataset.tab);
    });
    tab.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      history.replaceState(null, "", `#${next.dataset.tab}`);
      selectTab(next.dataset.tab, true);
    });
  });
  document.querySelectorAll("[data-goto]").forEach((b) =>
    b.addEventListener("click", () => {
      history.replaceState(null, "", `#${b.dataset.goto}`);
      selectTab(b.dataset.goto, true);
    })
  );
  selectTab(location.hash.slice(1));
  window.addEventListener("hashchange", () => selectTab(location.hash.slice(1)));
}

// ---------- Product rows ----------
function item(entry, { onRemove, removeLabel, meta } = {}) {
  const tpl = document.querySelector("[data-item-template]");
  const li = tpl.content.firstElementChild.cloneNode(true);
  li.querySelector("[data-img]").src = entry.image || document.body.dataset.placeholder;
  const link = li.querySelector("[data-link]");
  link.href = entry.url || `/p/${entry.id}/`;
  link.textContent = entry.name || "Unnamed product";
  li.querySelector("[data-meta]").textContent = meta || [entry.brand, entry.quantity].filter(Boolean).join(" · ");
  const btn = li.querySelector("[data-remove]");
  if (onRemove) {
    btn.setAttribute("aria-label", removeLabel);
    btn.addEventListener("click", onRemove);
  } else btn.remove();
  return li;
}

const GOAL_TEXT = {
  lose_weight: "Losing weight",
  muscle_and_fat: "Gaining muscle and losing fat",
  muscle_first: "Gaining muscle, losing fat is secondary",
  eat_healthier: "Eating healthier without losing weight",
};
function prefsChips(state) {
  const prefs = state.prefs;
  const chips = [
    ...(state.profile.goal ? [GOAL_TEXT[state.profile.goal]] : []),
    ...prefs.diets.map((d) => DIET_LABELS[d]),
    ...prefs.allergens.map((a) => `Allergy: ${ALLERGEN_LABELS[a]}`),
  ];
  return chips.length
    ? h("ul", { class: "chips" }, chips.map((c) => h("li", { text: c })))
    : h("p", { class: "caption", text: "Nothing set yet. Add your goal, diet and allergies in Profile." });
}

// ---------- Overview ----------
function renderOverview(state) {
  document.querySelector("[data-ov-prefs]").replaceChildren(prefsChips(state));
  const scans = state.scans.slice(0, 3);
  document.querySelector("[data-ov-scans]").replaceChildren(
    ...scans.map((s) => item(s, { meta: `Scanned ${fmtDate(s.at)}` }))
  );
  // Observations only from recorded actions: a scan is a scan, not a purchase.
  const weekAgo = Date.now() - 7 * 864e5;
  const recent = state.scans.filter((s) => s.at >= weekAgo).length;
  document.querySelector("[data-ov-observation]").textContent = state.scans.length
    ? `You scanned ${recent} product${recent === 1 ? "" : "s"} in the last 7 days (${state.scans.length} recorded in total).`
    : "No scans recorded on this device yet.";
  const favs = productLists.read("favorites").length;
  const rows = [
    ["Favourites", favs, "saved"],
    ["Buy often", state.lists.buyOften.length, "saved"],
    ["Avoid", state.lists.avoid.length, "saved"],
    ["Notes", state.notes.length, "notes"],
    ["Memories", state.memories.filter((m) => m.active && m.confirmedAt).length, "profile"],
  ];
  document.querySelector("[data-ov-lists]").replaceChildren(
    ...rows.map(([label, n, tab]) => {
      const b = h("button", { type: "button", class: "list-count" }, h("span", { text: label }), h("strong", { text: String(n) }));
      b.addEventListener("click", () => {
        history.replaceState(null, "", `#${tab}`);
        selectTab(tab, true);
      });
      return h("li", {}, b);
    })
  );
}

// ---------- Activity ----------
function renderActivity(state) {
  const lists = {
    scans: state.scans.map((s) => ({ entry: s, meta: `Scanned ${fmtDate(s.at)}${s.code ? ` · ${s.code}` : ""}`, key: s.scanId })),
    favorites: productLists.read("favorites").map((e) => ({ entry: e, key: e.id })),
    buyOften: state.lists.buyOften.map((e) => ({ entry: e, key: e.id })),
    avoid: state.lists.avoid.map((e) => ({ entry: e, key: e.id })),
  };
  const names = { scans: "scan history", favorites: "favourites", buyOften: "Buy often", avoid: "Avoid" };
  for (const [kind, rows] of Object.entries(lists)) {
    const list = document.querySelector(`[data-activity="${kind}"]`);
    list.replaceChildren(
      ...rows.map(({ entry, meta, key }) =>
        item(entry, {
          meta,
          removeLabel: `Remove ${entry.name || "product"} from ${names[kind]}`,
          onRemove: () => {
            if (kind === "favorites") {
              productLists.remove("favorites", entry.id);
              productLists.refreshCounts();
              renderAll(load());
            } else if (kind === "scans") update((s) => (s.scans = s.scans.filter((x) => x.scanId !== key)));
            else update((s) => (s.lists[kind] = s.lists[kind].filter((x) => x.id !== entry.id)));
            announce(`Removed from ${names[kind]}.`);
          },
        })
      )
    );
    document.querySelector(`[data-activity-empty="${kind}"]`).hidden = rows.length > 0;
  }
}

// ---------- Notes ----------
const KIND_LABELS = { general: "Note", taste: "Taste", prefer: "Why I prefer it", avoid: "Why I avoid it" };
function knownProducts(state) {
  const seen = new Map();
  [...productLists.read("favorites"), ...state.lists.buyOften, ...state.lists.avoid, ...state.scans].forEach((e) => {
    if (!seen.has(e.id)) seen.set(e.id, e);
  });
  return [...seen.values()];
}
function renderNotes(state) {
  const form = document.querySelector("[data-note-form]");
  const select = form.querySelector("[data-note-products]");
  const current = select.value;
  const products = knownProducts(state);
  select.replaceChildren(h("option", { value: "", text: "No product" }), ...products.map((p) => h("option", { value: p.id, text: p.name || "Unnamed product" })));
  if (products.some((p) => p.id === current)) select.value = current;

  const q = document.querySelector("[data-note-search]").value.trim().toLowerCase();
  const notes = state.notes.filter((n) => !q || n.text.toLowerCase().includes(q) || (n.product && n.product.name.toLowerCase().includes(q)));
  const list = document.querySelector("[data-notes-list]");
  list.replaceChildren(
    ...notes.map((n) => {
      const edit = h("button", { type: "button", class: "note-action", "aria-label": "Edit note", title: "Edit" }, icon("pencil", 18));
      const del = h("button", { type: "button", class: "note-action is-danger", "aria-label": "Delete note", title: "Delete" }, icon("trash-2", 18));
      edit.addEventListener("click", () => startEdit(n));
      del.addEventListener("click", async () => {
        const ok = await confirmDialog({ title: "Delete this note?", message: "It is removed from this device.", confirmLabel: "Delete", tone: "danger" });
        if (!ok) return;
        update((s) => (s.notes = s.notes.filter((x) => x.id !== n.id)));
        announce("Note deleted.");
      });
      return h(
        "li",
        { class: `note-item kind-${n.kind}` },
        h(
          "div",
          { class: "note-top" },
          h("span", { class: "note-kind", text: KIND_LABELS[n.kind] }),
          h("span", { class: "note-date", text: fmtDate(n.updatedAt) }),
          h("span", { class: "note-actions" }, edit, del)
        ),
        h("p", { class: "note-text", text: n.text }),
        n.product
          ? h("a", { class: "note-product", href: n.product.url }, icon("shopping-basket", 16), h("span", { text: n.product.name || "Unnamed product" }), icon("arrow-right", 14))
          : null
      );
    })
  );
  const empty = document.querySelector("[data-notes-empty]");
  empty.hidden = notes.length > 0;
  empty.textContent = q ? "No note matches your search." : "No notes yet. Add one here or from a product page.";
}
function startEdit(n) {
  const form = document.querySelector("[data-note-form]");
  form.elements.id.value = n.id;
  form.elements.text.value = n.text;
  form.elements.kind.value = n.kind;
  form.elements.product.value = n.product ? n.product.id : "";
  form.querySelector("[data-note-form-title]").textContent = "Edit note";
  form.querySelector("[data-note-reset]").hidden = false;
  form.elements.text.focus();
}
function resetNoteForm() {
  const form = document.querySelector("[data-note-form]");
  form.reset();
  form.elements.id.value = "";
  form.querySelector("[data-note-form-title]").textContent = "New note";
  form.querySelector("[data-note-reset]").hidden = true;
}
function bindNotes() {
  const form = document.querySelector("[data-note-form]");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = form.elements.text.value.trim();
    if (!text) return;
    const id = form.elements.id.value;
    const product = knownProducts(load()).find((p) => p.id === form.elements.product.value) || null;
    const kind = form.elements.kind.value;
    update((s) => {
      const now = Date.now();
      const existing = s.notes.find((x) => x.id === id);
      if (existing) Object.assign(existing, { text, kind, product, updatedAt: now });
      else s.notes.unshift({ id: newId(), text, kind, product, createdAt: now, updatedAt: now });
    });
    announce(id ? "Note updated." : "Note saved.");
    resetNoteForm();
  });
  form.querySelector("[data-note-reset]").addEventListener("click", resetNoteForm);
  document.querySelector("[data-note-search]").addEventListener("input", () => renderNotes(load()));
}

// ---------- Memories ----------
function suggestions(state) {
  const out = [];
  if (state.profile.goal) out.push([`goal:${state.profile.goal}`, `My goal: ${GOAL_TEXT[state.profile.goal].toLowerCase()}.`]);
  state.prefs.diets.forEach((d) => out.push([`diet:${d}`, `I choose ${DIET_LABELS[d].toLowerCase()} products.`]));
  state.lists.avoid.slice(0, 3).forEach((x) => out.push([`avoid:${x.id}`, `I avoid “${x.name}”.`]));
  const known = new Set(state.memories.map((m) => m.suggestionKey).filter(Boolean));
  return out.filter(([key]) => !known.has(key));
}
function renderMemories(state) {
  const list = document.querySelector("[data-memories]");
  list.replaceChildren(
    ...state.memories.map((m) => {
      const text = h("p", { class: "memory-text", text: m.text });
      const status = h("p", { class: "caption", text: `${m.source === "suggestion" ? "From a suggestion" : "Added by you"} · ${m.confirmedAt ? `confirmed ${fmtDate(m.confirmedAt)}` : "not confirmed"}${m.active ? "" : " · paused"}` });
      // Same quiet icon buttons as the notes: pause / use again, edit, delete.
      const active = h("button", { type: "button", class: "note-action", "aria-label": m.active ? "Pause this memory" : "Use this memory again", title: m.active ? "Pause" : "Use again" }, icon(m.active ? "pause" : "play", 18));
      const edit = h("button", { type: "button", class: "note-action", "aria-label": "Edit memory", title: "Edit" }, icon("pencil", 18));
      const del = h("button", { type: "button", class: "note-action is-danger", "aria-label": "Delete memory", title: "Delete" }, icon("trash-2", 18));
      active.addEventListener("click", () => {
        update((s) => {
          const x = s.memories.find((y) => y.id === m.id);
          if (x) x.active = !x.active;
        });
        announce(m.active ? "Memory paused: not used in replies." : "Memory used again.");
      });
      edit.addEventListener("click", () => {
        const input = h("input", { value: m.text, maxlength: 300, "aria-label": "Edit memory" });
        const saveBtn = h("button", { type: "submit", class: "btn btn-primary btn-small" }, "Save");
        const form = h("form", { class: "row memory-add", "data-no-busy": true }, input, saveBtn);
        form.addEventListener("submit", (e) => {
          e.preventDefault();
          const value = input.value.trim();
          if (!value) return;
          update((s) => {
            const x = s.memories.find((y) => y.id === m.id);
            if (x) Object.assign(x, { text: value, confirmedAt: Date.now() });
          });
          announce("Memory updated.");
        });
        text.replaceWith(form);
        input.focus();
      });
      del.addEventListener("click", () => {
        update((s) => (s.memories = s.memories.filter((y) => y.id !== m.id)));
        announce("Memory deleted. It is no longer used in replies.");
      });
      return h("li", { class: `memory${m.active ? "" : " is-paused"}` }, h("div", { class: "memory-top" }, text, h("span", { class: "note-actions" }, active, edit, del)), status);
    })
  );
  document.querySelector("[data-memories-empty]").hidden = state.memories.length > 0;
  const sugg = suggestions(state);
  document.querySelector("[data-suggestions-wrap]").hidden = sugg.length === 0;
  document.querySelector("[data-suggestions]").replaceChildren(
    ...sugg.map(([key, text]) => {
      const ok = h("button", { type: "button", class: "btn btn-outline btn-small" }, "Confirm");
      ok.addEventListener("click", () => {
        update((s) => s.memories.unshift({ id: newId(), text, source: "suggestion", suggestionKey: key, active: true, confirmedAt: Date.now(), createdAt: Date.now() }));
        announce("Memory confirmed.");
      });
      return h("li", { class: "memory is-suggestion" }, h("p", { class: "memory-text", text }), h("p", { class: "caption", text: "Suggestion – not used until you confirm it." }), ok);
    })
  );
  document.querySelector("[data-use-notes]").checked = state.settings.useNotes;
}
function bindMemories() {
  const form = document.querySelector("[data-memory-form]");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = form.elements.text.value.trim();
    if (!text) return;
    update((s) => s.memories.unshift({ id: newId(), text, source: "manual", active: true, confirmedAt: Date.now(), createdAt: Date.now() }));
    form.reset();
    announce("Foodo will remember this.");
  });
  document.querySelector("[data-use-notes]").addEventListener("change", (e) => {
    update((s) => (s.settings.useNotes = e.target.checked));
    announce(e.target.checked ? "Relevant notes will be used in replies." : "Notes are no longer used in replies.");
  });
  document.querySelector("[data-clear-chats]").addEventListener("click", async () => {
    const ok = await confirmDialog({ title: "Delete chat history?", message: "All conversations with Foodo on this device are deleted.", confirmLabel: "Delete", tone: "danger" });
    if (!ok) return;
    update((s) => (s.chats = {}));
    announce("Chat history deleted.");
  });
  document.querySelector("[data-clear-all]").addEventListener("click", async () => {
    const ok = await confirmDialog({
      title: "Delete all your data?",
      message: "Profile, diary, water, activity, weight, lists, notes, memories and chats are removed from this browser. This cannot be undone.",
      confirmLabel: "Delete everything",
      tone: "danger",
    });
    if (!ok) return;
    try {
      ["olahealth:myola", "olahealth:favorites", "olahealth:history", "olahealth:compare", "olahealth:hidden"].forEach((k) => window.localStorage.removeItem(k));
    } catch (e) {
      /* storage unavailable: nothing was kept */
    }
    reset();
    productLists.refreshCounts();
    announce("All your data was deleted from this browser.");
  });
  document.querySelector("[data-clear-scans]").addEventListener("click", async () => {
    const ok = await confirmDialog({ title: "Clear scan history?", message: "Scans are removed from this device.", confirmLabel: "Clear", tone: "danger" });
    if (!ok) return;
    update((s) => (s.scans = []));
    announce("Scan history cleared.");
  });
}

function renderAll(state) {
  renderOverview(state);
  renderActivity(state);
  renderNotes(state);
  renderMemories(state);
  document.querySelector("[data-storage-error]").hidden = storageOk() && productLists.storageOk();
}

if (root) {
  bindTabs();
  bindNotes();
  bindMemories();
  bindProfile();
  const state = load();
  save(state); // normalises old or partial data once
  renderAll(state);
  window.addEventListener("ola:change", (e) => renderAll(e.detail));
}
