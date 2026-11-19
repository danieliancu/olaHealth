// Onboarding: goal → about you → restrictions and allergies → health conditions → Today.
// Answers are saved on this device as they are given, so Back shows them again.
// Placeholders are examples only and are never saved.

import { announce } from "../app.js";
import { load, update, RANGES } from "./store.js";
import "./prefs.js";

const root = document.querySelector("[data-onboard]");
const STEPS = 4;
let step = 1;

function show(n, focus = true) {
  step = Math.min(Math.max(n, 1), STEPS);
  root.querySelectorAll("[data-step]").forEach((s) => (s.hidden = Number(s.dataset.step) !== step));
  const bar = root.querySelector("[data-ob-progress]");
  bar.setAttribute("aria-valuenow", String(step));
  bar.querySelectorAll("span").forEach((el, i) => el.classList.toggle("is-done", i < step));
  root.querySelector("[data-ob-count]").textContent = `Step ${step} of ${STEPS}`;
  root.querySelector("[data-ob-next-label]").textContent = step === STEPS ? "Finish" : "Continue";
  root.querySelector("[data-ob-back]").querySelector("span").textContent = step === 1 ? "Home" : "Back";
  history.replaceState(null, "", `#step-${step}`);
  fill(load());
  if (focus) root.querySelector(`[data-step="${step}"] h1`)?.focus();
  window.scrollTo(0, 0);
}

function fill(state) {
  const p = state.profile;
  root.querySelectorAll("input[name=goal]").forEach((i) => (i.checked = i.value === p.goal));
  root.querySelectorAll("input[name=gender]").forEach((i) => (i.checked = i.value === p.gender));
  for (const k of ["age", "height", "weight"]) {
    const input = root.querySelector(`input[name=${k}]`);
    if (document.activeElement !== input) input.value = p[k] === null ? "" : String(p[k]);
  }
  root.querySelectorAll("[data-condition]").forEach((b) => b.setAttribute("aria-pressed", String(p.conditions.includes(b.dataset.condition))));
}

function error(name, on) {
  const el = root.querySelector(`[data-error="${name}"]`);
  if (el) el.hidden = !on;
  root.querySelector(`input[name=${name}]`)?.setAttribute("aria-invalid", String(on));
}

// Step 2: every typed value is checked before it is saved; empty means "not given".
function saveAboutYou() {
  const values = {};
  let ok = true;
  for (const k of ["age", "height", "weight"]) {
    const raw = root.querySelector(`input[name=${k}]`).value.trim();
    if (!raw) {
      values[k] = null;
      error(k, false);
      continue;
    }
    const n = Number(raw.replace(",", "."));
    const [lo, hi] = RANGES[k];
    const valid = Number.isFinite(n) && n >= lo && n <= hi;
    error(k, !valid);
    if (!valid) ok = false;
    values[k] = valid ? n : null;
  }
  if (!ok) {
    root.querySelector('[aria-invalid="true"]')?.focus();
    return false;
  }
  update((s) => Object.assign(s.profile, values));
  return true;
}

function next() {
  if (step === 1 && !load().profile.goal) {
    error("goal", true);
    root.querySelector("input[name=goal]").focus();
    return;
  }
  if (step === 2 && !saveAboutYou()) return;
  if (step < STEPS) return show(step + 1);
  finish();
}

function finish() {
  update((s) => {
    s.onboarded = true;
    // A history of eating disorders: start with weight and calorie figures hidden; the diary,
    // water and activity stay. This can be changed later in My Track.
    if (s.profile.conditions.includes("eating_disorder")) {
      s.settings.showEnergy = false;
      s.settings.trackWeight = false;
    }
  });
  announce("Saved on this device.");
  location.href = "/today/";
}

if (root) {
  root.addEventListener("change", (e) => {
    const t = e.target;
    if (t.name === "goal") {
      update((s) => (s.profile.goal = t.value));
      error("goal", false);
    }
    if (t.name === "gender") update((s) => (s.profile.gender = t.value));
  });
  root.addEventListener("click", (e) => {
    const c = e.target.closest("[data-condition]");
    if (!c) return;
    const key = c.dataset.condition;
    update((s) => {
      let list = s.profile.conditions;
      const on = !list.includes(key);
      if (key === "none" || key === "prefer_not") list = on ? [key] : [];
      else list = on ? [...list.filter((x) => x !== "none" && x !== "prefer_not"), key] : list.filter((x) => x !== key);
      s.profile.conditions = list;
    });
    fill(load());
  });
  root.querySelector("[data-ob-next]").addEventListener("click", next);
  root.querySelector("[data-ob-back]").addEventListener("click", () => {
    if (step === 1) location.href = "/";
    else show(step - 1);
  });
  root.querySelector("[data-ob-skip]").addEventListener("click", () => {
    ["age", "height", "weight"].forEach((k) => error(k, false));
    show(3);
  });
  root.querySelectorAll("[data-step] h1").forEach((h) => (h.tabIndex = -1));
  const fromHash = Number((location.hash.match(/step-(\d)/) || [])[1]) || 1;
  show(fromHash, false);
}
