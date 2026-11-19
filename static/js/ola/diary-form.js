// "How much did you have?": meal, declared portions or an amount in the product's basis unit,
// a preview of the values, then save on confirm. Shared by the product page (dialog) and the
// short add step reached from the Food diary (/today/add/<id>/).

import { announce } from "../app.js";
import { h } from "./dom.js";
import { update, newId, storageOk, localDate, dateFrom } from "./store.js";

export const MEAL_LABELS = { breakfast: "Breakfast", lunch: "Lunch", dinner: "Dinner", snacks: "Snacks" };
const PREVIEW = [["energy-kcal", "Energy", "kcal"], ["proteins", "Protein", "g"], ["carbohydrates", "Carbs", "g"], ["fat", "Fat", "g"]];
const fmt = (n) => (n >= 10 ? Math.round(n).toString() : (Math.round(n * 10) / 10).toString());

export function defaultMeal() {
  const hour = new Date().getHours();
  return hour < 11 ? "breakfast" : hour < 15 ? "lunch" : hour < 21 ? "dinner" : "snacks";
}
export function dayLabel(iso) {
  return iso === localDate() ? "Today" : dateFrom(iso).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
}

/**
 * root: element holding the form; payload: #ola-data; onSaved(meal, date) after saving.
 * Returns { reset(meal, date) } to (re)start the form.
 */
export function setupDiaryForm(root, payload, onSaved) {
  const form = root.querySelector("form");
  const unit = payload.portions.unit; // "g" | "ml" | null (basis not stated by the source)
  const values = payload.facts.values;
  const amountInput = form.elements.amount;
  const unitSelect = form.elements.unit;
  const preview = root.querySelector("[data-diary-preview]");
  const options = root.querySelector("[data-diary-portions]");
  const p = payload.product;

  if (unit) {
    unitSelect.replaceChildren(h("option", { value: unit, text: unit }));
    unitSelect.disabled = true;
  }
  options.replaceChildren(
    ...payload.portions.options.map((o) => {
      const b = h("button", { type: "button", class: "chip-btn", "aria-pressed": "false", "data-amount": o.amount }, o.label);
      b.addEventListener("click", () => {
        amountInput.value = o.amount;
        options.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        renderPreview();
      });
      return b;
    })
  );
  options.hidden = payload.portions.options.length === 0;

  function renderPreview() {
    const amount = Number(amountInput.value);
    if (!amountInput.value || !(amount > 0)) {
      preview.replaceChildren(h("p", { class: "caption", text: "Enter how much you had to see the values." }));
      return;
    }
    if (!unit) {
      preview.replaceChildren(h("p", { class: "macro-note", text: "The source does not say if the values are per 100 g or per 100 ml, so they are not calculated. The entry is saved with unknown values." }));
      return;
    }
    const cells = PREVIEW.map(([k, label, u]) => {
      const v = values[k];
      const text = v ? `${fmt((Number(v.value) * amount) / 100)} ${u}` : "–";
      return h("div", { class: "macro" }, h("span", { class: "macro-label", text: label }), h("strong", { text }), v ? null : h("span", { class: "sr-only", text: "unknown" }));
    });
    const missing = PREVIEW.some(([k]) => !values[k]);
    preview.replaceChildren(h("div", { class: "macro-row" }, cells), h("p", { class: "caption", text: `For ${fmt(amount)} ${unit}, from the values per 100 ${unit}.${missing ? " – means unknown." : ""}` }));
  }
  amountInput.addEventListener("input", () => {
    options.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.amount === amountInput.value)));
    renderPreview();
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const amount = Number(amountInput.value);
    if (!(amount > 0 && amount <= 10000)) {
      amountInput.setAttribute("aria-invalid", "true");
      amountInput.focus();
      return;
    }
    amountInput.removeAttribute("aria-invalid");
    const per100 = {};
    for (const [k] of PREVIEW) per100[k] = values[k] ? Number(values[k].value) : null;
    const portion = options.querySelector("[aria-pressed=true]")?.textContent || "";
    const meal = form.elements.meal.value;
    const date = form.dataset.date || localDate();
    const product = { id: p.id, name: p.name, brand: p.brand, quantity: p.quantity, image: p.image, url: p.url };
    update((st) =>
      st.diary.push({ id: newId(), date, meal, product, name: p.name || "Unnamed product", amount, unit: unit || unitSelect.value, basisKnown: Boolean(unit), portion, per100, at: Date.now() })
    );
    announce(storageOk() ? `Added to ${MEAL_LABELS[meal]} in your diary.` : "This browser does not allow saving.");
    onSaved(meal, date);
  });

  return {
    reset(meal, date) {
      form.reset();
      form.elements.meal.value = MEAL_LABELS[meal] ? meal : defaultMeal();
      form.dataset.date = /^\d{4}-\d{2}-\d{2}$/.test(date || "") && date <= localDate() ? date : localDate();
      const day = root.querySelector("[data-diary-day]");
      if (day) day.textContent = dayLabel(form.dataset.date);
      if (unit) unitSelect.value = unit;
      options.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", "false"));
      renderPreview();
    },
  };
}
