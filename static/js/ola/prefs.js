// Restrictions and allergies picker (onboarding step 3, the product page and My Track).
// It edits the profile; diets and allergens for the product checks are derived from it
// (store.js derivePrefs). "None" is exclusive. Free notes never create a compatibility claim.

import { announce } from "../app.js";
import { load, update } from "./store.js";

function sync(root, state) {
  const p = state.profile;
  root.querySelectorAll("[data-restriction]").forEach((b) => b.setAttribute("aria-pressed", String(p.restrictions.includes(b.dataset.restriction))));
  root.querySelectorAll("[data-subtype]").forEach((b) => b.setAttribute("aria-pressed", String(p[b.dataset.subtype].includes(b.dataset.value))));
  root.querySelectorAll("[data-sub]").forEach((box) => (box.hidden = !p.restrictions.includes(box.dataset.sub)));
  root.querySelectorAll("[data-note-field]").forEach((input) => {
    if (document.activeElement !== input) input.value = p[input.dataset.noteField] || "";
  });
}

export function bindPicker(root) {
  if (!root || root.dataset.bound) return;
  root.dataset.bound = "1";
  sync(root, load());
  root.addEventListener("click", (e) => {
    const r = e.target.closest("[data-restriction]");
    if (r) {
      const key = r.dataset.restriction;
      let on = false;
      update((s) => {
        let list = s.profile.restrictions;
        on = !list.includes(key);
        if (key === "none") list = on ? ["none"] : [];
        else list = on ? [...list.filter((x) => x !== "none"), key] : list.filter((x) => x !== key);
        s.profile.restrictions = list;
      });
      announce(`${r.querySelector("span").textContent}: ${on ? "selected" : "not selected"}.`);
      return;
    }
    const sub = e.target.closest("[data-subtype]");
    if (sub) {
      update((s) => {
        const list = s.profile[sub.dataset.subtype];
        s.profile[sub.dataset.subtype] = list.includes(sub.dataset.value) ? list.filter((x) => x !== sub.dataset.value) : [...list, sub.dataset.value];
      });
    }
  });
  root.querySelectorAll("[data-note-field]").forEach((input) =>
    input.addEventListener("change", () => update((s) => (s.profile[input.dataset.noteField] = input.value.trim())))
  );
  window.addEventListener("ola:change", (e) => sync(root, e.detail));
}

document.querySelectorAll("[data-restrictions-picker]").forEach(bindPicker);
