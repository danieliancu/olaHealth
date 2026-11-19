// My Track → Profile: goal, about you, health conditions, daily targets and display settings.
// Everything is validated before it is saved, and stays in this browser.

import { announce } from "../app.js";
import { load, update, RANGES } from "./store.js";

const TARGETS = { "energy-kcal": 10000, proteins: 1000, carbohydrates: 2000, fat: 1000 };

function fill(state) {
  const form = document.querySelector("[data-profile-form]");
  if (!form) return;
  const p = state.profile;
  form.querySelectorAll("input[name=goal]").forEach((i) => (i.checked = i.value === p.goal));
  form.querySelectorAll("input[name=gender]").forEach((i) => (i.checked = i.value === p.gender));
  for (const k of ["age", "height", "weight"]) {
    const input = form.elements[k];
    if (document.activeElement !== input) input.value = p[k] === null ? "" : String(p[k]);
  }
  document.querySelectorAll("#panel-profile [data-condition]").forEach((b) => b.setAttribute("aria-pressed", String(p.conditions.includes(b.dataset.condition))));
  const t = document.querySelector("[data-targets-form]");
  for (const k of Object.keys(TARGETS)) {
    if (document.activeElement !== t.elements[k]) t.elements[k].value = state.settings.targets[k] ?? "";
  }
  t.querySelector("[data-target-energy]").hidden = !state.settings.showEnergy;
  document.querySelector("[data-show-energy]").checked = state.settings.showEnergy;
  document.querySelector("[data-track-weight]").checked = state.settings.trackWeight;
}

export function bindProfile() {
  const form = document.querySelector("[data-profile-form]");
  if (!form) return;
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const values = {};
    let ok = true;
    for (const k of ["age", "height", "weight"]) {
      const input = form.elements[k];
      const raw = input.value.trim();
      const n = Number(raw.replace(",", "."));
      const [lo, hi] = RANGES[k];
      const valid = !raw || (Number.isFinite(n) && n >= lo && n <= hi);
      input.setAttribute("aria-invalid", String(!valid));
      if (!valid) ok = false;
      values[k] = raw && valid ? n : null;
    }
    document.querySelector("[data-profile-error]").hidden = ok;
    if (!ok) return form.querySelector('[aria-invalid="true"]').focus();
    values.goal = form.querySelector("input[name=goal]:checked")?.value || null;
    values.gender = form.querySelector("input[name=gender]:checked")?.value || null;
    update((s) => Object.assign(s.profile, values));
    announce("Profile saved on this device.");
  });

  document.querySelectorAll("#panel-profile [data-condition]").forEach((b) =>
    b.addEventListener("click", () => {
      const key = b.dataset.condition;
      update((s) => {
        let list = s.profile.conditions;
        const on = !list.includes(key);
        if (key === "none" || key === "prefer_not") list = on ? [key] : [];
        else list = on ? [...list.filter((x) => x !== "none" && x !== "prefer_not"), key] : list.filter((x) => x !== key);
        s.profile.conditions = list;
      });
    })
  );
  document.querySelector("[data-clear-health]").addEventListener("click", () => {
    update((s) => {
      s.profile.conditions = [];
      s.profile.conditionsNote = "";
    });
    announce("Health information deleted from this device.");
  });

  const targets = document.querySelector("[data-targets-form]");
  targets.addEventListener("submit", (e) => {
    e.preventDefault();
    const next = {};
    let ok = true;
    for (const [k, max] of Object.entries(TARGETS)) {
      const input = targets.elements[k];
      const raw = input.value.trim();
      const n = Number(raw);
      const valid = !raw || (Number.isFinite(n) && n > 0 && n <= max);
      input.setAttribute("aria-invalid", String(!valid));
      if (!valid) ok = false;
      next[k] = raw && valid ? n : null;
    }
    if (!ok) return targets.querySelector('[aria-invalid="true"]').focus();
    update((s) => (s.settings.targets = next));
    announce("Targets saved.");
  });
  targets.querySelector("[data-targets-clear]").addEventListener("click", () => {
    update((s) => (s.settings.targets = {}));
    announce("Targets cleared. Today shows totals only.");
  });
  document.querySelector("[data-show-energy]").addEventListener("change", (e) => update((s) => (s.settings.showEnergy = e.target.checked)));
  document.querySelector("[data-track-weight]").addEventListener("change", (e) => update((s) => (s.settings.trackWeight = e.target.checked)));

  fill(load());
  window.addEventListener("ola:change", (e) => fill(e.detail));
}
