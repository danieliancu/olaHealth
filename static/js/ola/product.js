// Product page, personal layer: restriction states for the chosen preferences, lists
// (Buy often / Avoid), scan history, quick notes, the preference picker and Ask Ola.
// Everything shown comes from the page data (#ola-data) and this device's preferences.

import { announce } from "../app.js";
import { blocking, confirmed, statusText, DIET_LABELS, GOAL_LABELS } from "./assess.js";
import { load, update, toggleList, recordScan, inList, newId, storageOk } from "./store.js";
import { bindPicker } from "./prefs.js";
import { h } from "./dom.js";
import "./chat.js";

const dataEl = document.getElementById("ola-data");
const page = document.querySelector("[data-product]");
const payload = dataEl ? JSON.parse(dataEl.textContent) : null;

function entry() {
  const p = payload.product;
  return { id: p.id, name: p.name, brand: p.brand, quantity: p.quantity, image: p.image, url: p.url };
}

// ---------- Scan history: only the scan / code entry flow, never a reload ----------
function recordScanIfNeeded() {
  const code = new URLSearchParams(location.search).get("scanned");
  if (!code || !payload) return;
  const nav = performance.getEntriesByType ? performance.getEntriesByType("navigation")[0] : null;
  if (nav && nav.type !== "navigate") return; // reload or back/forward: not a new scan
  const key = `ola:scan:${payload.product.id}:${code}`;
  try {
    if (window.sessionStorage.getItem(key)) return;
    window.sessionStorage.setItem(key, "1");
  } catch (e) {
    /* sessionStorage unavailable: the navigation check above still applies */
  }
  recordScan(entry(), code);
}

// ---------- For you: goal values, then restriction states ----------
const GOAL_NUTRIENT = { lower_sugar: ["sugars", "Sugars"], more_protein: ["proteins", "Protein"] };

function renderRestrictions(state) {
  const box = document.querySelector("[data-ola-restrictions]");
  if (!box) return;
  const prefs = state.prefs;
  const out = [];
  const goals = prefs.goals.map((g) => {
    const [key, label] = GOAL_NUTRIENT[g];
    const v = payload.facts.values[key];
    const text = v ? `${label} ${v.display} ${payload.facts.basis_label}` : `${label}: not recorded`;
    return h("li", { class: "goal-value" }, h("span", { class: "goal-label", text: GOAL_LABELS[g] }), h("strong", { text }));
  });
  if (goals.length) out.push(h("ul", { class: "goal-list" }, goals));
  if (prefs.diets.length || prefs.allergens.length) {
    const items = [...blocking(payload.restrictions, prefs), ...confirmed(payload.restrictions, prefs)];
    out.push(
      h("ul", { class: "restriction-list" }, items.map((b) => h("li", { class: `rs rs-${b.status}` }, h("strong", { text: `${b.label}: ${statusText(b.status)}. ` }), b.reason))),
      h("p", { class: "caption", text: "Never a guarantee: the physical label is the reference, especially for allergies." })
    );
  }
  box.replaceChildren(...out);
}

// ---------- Active preferences summary ----------
function renderPrefsSummary(state) {
  const box = document.querySelector("[data-ola-prefs]");
  if (!box) return;
  const p = state.prefs;
  const names = [
    ...p.goals.map((g) => GOAL_LABELS[g]),
    ...p.diets.map((d) => DIET_LABELS[d]),
    ...(p.allergens.length ? [`${p.allergens.length} allerg${p.allergens.length === 1 ? "y" : "ies"}`] : []),
  ];
  box.textContent = names.length
    ? `Personalised for: ${names.join(", ")}.`
    : state.onboarded
      ? "Not personalised."
      : "Add your diet, allergies or goals to see what matters first.";
  const edit = document.querySelector("[data-prefs-edit]");
  if (edit) edit.textContent = names.length ? "Change" : "Personalise";
}

// ---------- Lists ----------
function syncLists(state) {
  document.querySelectorAll("[data-list-toggle]").forEach((btn) => {
    const list = btn.dataset.listToggle;
    const on = inList(state, list, payload.product.id);
    btn.setAttribute("aria-pressed", String(on));
    const label = btn.querySelector("span");
    if (label) label.textContent = list === "avoid" ? (on ? "Avoid ✓" : "Avoid") : on ? "Buy often ✓" : "Buy often";
  });
}

function bindLists() {
  document.querySelectorAll("[data-list-toggle]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const list = btn.dataset.listToggle;
      const { saved, added } = toggleList(list, entry());
      const name = list === "avoid" ? "Avoid" : "Buy often";
      if (!saved) announce("This browser does not allow saving; the change is kept only until you leave the page.");
      else announce(added ? `Added to ${name}.${list === "avoid" ? " Removed from Buy often if it was there." : ""} Press again to undo.` : `Removed from ${name}.`);
    })
  );
}

// ---------- Quick note ----------
function bindNotes() {
  const dialog = document.querySelector("[data-note-dialog]");
  if (!dialog) return;
  document.querySelectorAll("[data-note-open]").forEach((btn) =>
    btn.addEventListener("click", () => {
      dialog.querySelector("form").reset();
      dialog.showModal ? dialog.showModal() : dialog.setAttribute("open", "");
      dialog.querySelector("textarea").focus();
    })
  );
  dialog.querySelector("[data-note-cancel]").addEventListener("click", () => dialog.close());
  dialog.querySelector("form").addEventListener("submit", (e) => {
    e.preventDefault();
    const form = e.target;
    const text = form.elements.text.value.trim();
    if (!text) {
      form.elements.text.focus();
      return;
    }
    const now = Date.now();
    update((s) => s.notes.unshift({ id: newId(), text, kind: form.elements.kind.value, product: entry(), createdAt: now, updatedAt: now }));
    dialog.close();
    announce(storageOk() ? "Note saved in My Ola → Notes." : "This browser does not allow saving notes.");
  });
}

// ---------- Onboarding (optional) and preference editing ----------
function bindOnboarding(state) {
  const intro = document.querySelector("[data-onboarding]");
  const panel = document.querySelector("[data-prefs-panel]");
  const edit = document.querySelector("[data-prefs-edit]");
  const skip = document.querySelector("[data-onboarding-skip]");
  if (!panel) return;
  bindPicker(panel.querySelector("[data-pref-picker]"));
  const setOpen = (open) => {
    panel.hidden = !open;
    edit?.setAttribute("aria-expanded", String(open));
  };
  const firstTime = () => !load().onboarded;
  panel.querySelectorAll("[data-onboarding-done]").forEach((b) =>
    b.addEventListener("click", () => {
      const first = intro && !intro.hidden;
      if (intro) intro.hidden = true;
      if (skip) skip.hidden = true;
      update((s) => (s.onboarded = true));
      setOpen(false);
      edit?.focus();
      announce(first ? "Saved on this device. You can change your preferences any time in My Ola." : "Preferences updated.");
    })
  );
  edit?.addEventListener("click", () => {
    const open = panel.hidden;
    setOpen(open);
    if (intro) intro.hidden = !(open && firstTime());
    if (skip) skip.hidden = !(open && firstTime());
    if (open) panel.querySelector("button")?.focus();
  });
}

function bindMoreMenu() {
  const toggle = document.querySelector("[data-more-toggle]");
  const menu = document.querySelector("[data-more-menu]");
  if (!toggle || !menu) return;
  const set = (open) => {
    menu.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
  };
  toggle.addEventListener("click", () => {
    set(menu.hidden);
    if (!menu.hidden) menu.querySelector("button")?.focus();
  });
  menu.addEventListener("click", (e) => {
    if (e.target.closest("[data-note-open]")) set(false);
  });
  document.addEventListener("click", (e) => {
    if (!menu.hidden && !e.target.closest(".more")) set(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !menu.hidden) {
      set(false);
      toggle.focus();
    }
  });
}

function renderAll(state) {
  renderRestrictions(state);
  renderPrefsSummary(state);
  syncLists(state);
}

if (payload && page) {
  recordScanIfNeeded();
  const state = load();
  bindLists();
  bindMoreMenu();
  bindNotes();
  bindOnboarding(state);
  renderAll(state);
  window.addEventListener("ola:change", (e) => renderAll(e.detail));
  // Coming back from the comparison (bfcache): show the current preferences.
  window.addEventListener("pageshow", (e) => e.persisted && renderAll(load()));
}
