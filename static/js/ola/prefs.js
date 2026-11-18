// Preference picker: icon toggles with visible labels (aria-pressed), saved on this device.
// Every change is applied at once on the page.

import { announce } from "../app.js";
import { load, update } from "./store.js";

function sync(root, state) {
  root.querySelectorAll("[data-pref]").forEach((btn) => {
    const on = state.prefs[btn.dataset.pref].includes(btn.dataset.value);
    btn.setAttribute("aria-pressed", String(on));
  });
  const n = state.prefs.allergens.length;
  root.querySelectorAll("[data-allergy-count]").forEach((el) => {
    el.textContent = n ? String(n) : "";
    el.hidden = n === 0;
  });
  root.querySelector("[data-allergy-toggle]")?.classList.toggle("has-selection", n > 0);
}

export function bindPicker(root) {
  if (!root || root.dataset.bound) return;
  root.dataset.bound = "1";
  sync(root, load());
  root.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-pref]");
    if (btn) {
      const kind = btn.dataset.pref;
      const value = btn.dataset.value;
      let on = false;
      update((s) => {
        const list = s.prefs[kind];
        on = !list.includes(value);
        s.prefs[kind] = on ? [...list, value] : list.filter((v) => v !== value);
      });
      const label = btn.querySelector("span")?.textContent || value;
      announce(`${label}: ${on ? "on" : "off"}.`);
      return;
    }
    const toggle = e.target.closest("[data-allergy-toggle]");
    if (toggle) {
      const grid = document.getElementById(toggle.getAttribute("aria-controls"));
      const open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      if (grid) grid.hidden = !open;
      if (open) grid?.querySelector("button")?.focus();
    }
  });
  window.addEventListener("ola:change", (e) => sync(root, e.detail));
  // Open the allergen list when allergies are already chosen.
  const state = load();
  const toggle = root.querySelector("[data-allergy-toggle]");
  if (toggle && state.prefs.allergens.length) {
    toggle.setAttribute("aria-expanded", "true");
    const grid = document.getElementById(toggle.getAttribute("aria-controls"));
    if (grid) grid.hidden = false;
  }
}

document.querySelectorAll("[data-pref-picker]").forEach(bindPicker);
