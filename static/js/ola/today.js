// Today: day picker, daily summary, food diary, water and activity (all on this device).
// Totals are recomputed from the saved entries on every change. Unknown values stay unknown:
// a total that misses some values says it is partial.

import { announce } from "../app.js";
import { h, icon } from "./dom.js";
import { load, update, newId, storageOk, localDate, shiftDate, dateFrom, dayTotals, entryValues } from "./store.js";

const root = document.querySelector("[data-today]");
const MEALS = [
  ["breakfast", "Breakfast", "coffee"],
  ["lunch", "Lunch", "sandwich"],
  ["dinner", "Dinner", "soup"],
  ["snacks", "Snacks", "cookie"],
];
const MACROS = [
  ["energy-kcal", "Energy", "kcal"],
  ["proteins", "Protein", "g"],
  ["carbohydrates", "Carbs", "g"],
  ["fat", "Fat", "g"],
];
const ACTIVITY_LABELS = { walking: "Walking", running: "Running", gym: "Gym", cycling: "Cycling", swimming: "Swimming", other: "Other" };
const fmt = (n) => (n >= 100 ? Math.round(n).toString() : (Math.round(n * 10) / 10).toString());

let day = localDate();
const wide = window.matchMedia("(min-width: 1024px)");

// ---------- Undo toast ----------
let toastTimer = null;
function toast(message, undo) {
  let el = document.querySelector("[data-toast]");
  if (!el) {
    el = h("div", { class: "toast", "data-toast": true, role: "status" });
    document.body.append(el);
  }
  const children = [h("span", { text: message })];
  if (undo) {
    const b = h("button", { type: "button", class: "toast-undo" }, "Undo");
    b.addEventListener("click", () => {
      undo();
      el.hidden = true;
      announce("Undone.");
    });
    children.push(b);
  }
  el.replaceChildren(...children);
  el.hidden = false;
  announce(message);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 6000);
}

// ---------- Day picker ----------
function setDay(iso) {
  day = iso > localDate() ? localDate() : iso;
  const url = new URL(location.href);
  if (day === localDate()) url.searchParams.delete("date");
  else url.searchParams.set("date", day);
  history.replaceState(null, "", url);
  render(load());
}

function renderDays() {
  const today = localDate();
  const d = dateFrom(day);
  // Desktop shows two weeks (the previous and the selected one) so the days do not spread out.
  const span = wide.matches ? 14 : 7;
  const monday = shiftDate(day, -((d.getDay() + 6) % 7) - (span - 7));
  const days = [...Array(span)].map((_, i) => shiftDate(monday, i));
  root.querySelector("[data-days]").classList.toggle("two-weeks", span === 14);
  const state = load();
  root.querySelector("[data-days]").replaceChildren(
    ...days.map((iso) => {
      const date = dateFrom(iso);
      const logged = state.diary.some((e) => e.date === iso);
      const b = h(
        "button",
        {
          type: "button",
          class: `day${iso === day ? " is-selected" : ""}${iso === today ? " is-today" : ""}`,
          "aria-pressed": String(iso === day),
          "aria-label": `${date.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}${logged ? ", food logged" : ""}`,
          disabled: iso > today,
        },
        h("span", { class: "day-name", text: date.toLocaleDateString(undefined, { weekday: "narrow" }) }),
        h("span", { class: "day-num", text: String(date.getDate()) }),
        h("span", { class: `day-dot${logged ? " is-on" : ""}`, "aria-hidden": "true" })
      );
      b.addEventListener("click", () => setDay(iso));
      return h("li", {}, b);
    })
  );
  const date = dateFrom(day);
  root.querySelector("[data-day-title]").textContent =
    day === today ? "Today" : day === shiftDate(today, -1) ? "Yesterday" : date.toLocaleDateString(undefined, { weekday: "long" });
  root.querySelector("[data-day-sub]").textContent = date.toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" });
  root.querySelector("[data-day-next]").disabled = shiftDate(monday, span) > today;
}

// ---------- Daily summary ----------
// Targets switch: on when targets exist and are shown; with none set it opens the targets form.
function renderTargetsSwitch(state) {
  const sw = root.querySelector("[data-targets-switch]");
  const has = Object.values(state.settings.targets).some((v) => v);
  const on = has && state.settings.showTargets;
  sw.setAttribute("aria-checked", String(on));
  sw.title = has ? (on ? "Hide progress towards your targets" : "Show progress towards your targets") : "Set daily targets";
  root.querySelector("[data-targets-edit]").hidden = !on;
}

function renderSummary(state) {
  renderTargetsSwitch(state);
  const box = root.querySelector("[data-summary]");
  const { entries, totals } = dayTotals(state, day);
  const macros = MACROS.filter(([k]) => k !== "energy-kcal" || state.settings.showEnergy);
  const cells = macros.map(([k, label, unit]) => {
    const t = totals[k];
    const target = state.settings.showTargets ? state.settings.targets[k] : null;
    const children = [
      h("span", { class: "sum-label", text: label }),
      h("strong", { class: "sum-value" }, entries.length ? fmt(t.value) : "–", h("small", { text: ` ${unit}` })),
    ];
    if (target && entries.length) {
      const pct = Math.min(100, Math.round((t.value / target) * 100));
      const fill = h("span", { class: "meter-fill" });
      fill.style.width = `${pct}%`; // CSSOM: allowed by the CSP (no inline style attribute)
      children.push(
        h("span", { class: "meter", role: "img", "aria-label": `${fmt(t.value)} of ${fmt(target)} ${unit}` }, fill),
        h("span", { class: "sum-target", text: `of ${fmt(target)} ${unit}` })
      );
    } else if (target) {
      children.push(h("span", { class: "sum-target", text: `target ${fmt(target)} ${unit}` }));
    }
    return h("div", { class: "sum-cell" }, children);
  });
  const anyPartial = entries.length && macros.some(([k]) => totals[k].partial);
  const note = entries.length
      ? anyPartial
        ? h("p", { class: "caption", text: "Some foods have unknown values, which are not counted." })
        : null
      : h("p", { class: "caption", text: "No entries yet for this day." });
  box.replaceChildren(...[h("div", { class: "sum-grid" }, cells), note].filter(Boolean));
}

// ---------- Food diary ----------
// Diary mode: the scanner and the catalogue go straight to "how much?" for this meal and day.
const diaryUrl = (path, meal) => `${path}?${new URLSearchParams({ add: meal || "auto", date: day })}`;
function scanLink(meal, label) {
  return h("a", { class: label ? "btn btn-primary" : "btn btn-outline btn-small meal-scan", href: diaryUrl("/scan/", meal) }, icon("scan-barcode", 18), h("span", { text: label || "Scan" }));
}
function mealScan(meal, label) {
  const tool = (path, ic, text) => h("a", { class: "meal-add", href: diaryUrl(path, meal), "aria-label": `${text} for ${label}`, title: text }, icon(ic, 20));
  return h("span", { class: "meal-tools" }, tool("/scan/", "scan-barcode", "Scan a barcode"), tool("/products/", "search", "Search products"));
}

function renderDiary(state) {
  const box = root.querySelector("[data-diary]");
  const entries = state.diary.filter((e) => e.date === day);
  if (!entries.length) {
    box.replaceChildren(
      h(
        "div",
        { class: "diary-empty" },
        icon("utensils", 28),
        h("p", { text: "Nothing logged for this day yet." }),
        h("p", { class: "caption", text: "Scan a barcode, check the label, then add how much you had." }),
        scanLink("", "Scan your first food")
      ),
      h("div", { class: "meal-links" }, MEALS.map(([key, label]) => scanLink(key, `${label}`)))
    );
    box.querySelectorAll(".meal-links .btn").forEach((b) => (b.className = "btn btn-outline btn-small meal-scan"));
    return;
  }
  box.replaceChildren(
    ...MEALS.map(([key, label, ic]) => {
      const list = entries.filter((e) => e.meal === key);
      return h(
        "section",
        { class: "meal" },
        h("div", { class: "meal-head" }, h("span", { class: "meal-icon" }, icon(ic, 20)), h("h3", { text: label }), mealScan(key, label)),
        list.length
          ? h(
              "ul",
              { class: "entry-list" },
              list.map((e) => {
                const b = h(
                  "button",
                  { type: "button", class: "entry", "aria-label": `${e.name}: details` },
                  h("span", { class: "entry-name", text: e.name }),
                  icon("chevron-right", 18)
                );
                b.addEventListener("click", () => editEntry(e.id));
                return h("li", {}, b);
              })
            )
          : null
      );
    })
  );
}

// ---------- Edit / move / delete an entry ----------
const dialog = document.querySelector("[data-entry-dialog]");
function editEntry(id) {
  const e = load().diary.find((x) => x.id === id);
  if (!e || !dialog) return;
  const form = dialog.querySelector("form");
  form.dataset.id = id;
  form.elements.amount.value = String(e.amount);
  form.elements.meal.value = e.meal;
  dialog.querySelector("[data-entry-name]").textContent = e.name;
  dialog.querySelector("[data-entry-unit]").textContent = e.unit;
  const link = dialog.querySelector("[data-entry-product]");
  link.hidden = !e.product;
  if (e.product) link.href = e.product.url;
  dialog.querySelector("[data-entry-meta]").textContent = e.portion;
  preview(e);
  dialog.showModal ? dialog.showModal() : dialog.setAttribute("open", "");
  form.elements.amount.focus();
}
function preview(e) {
  const amount = Number(dialog.querySelector("form").elements.amount.value);
  const box = dialog.querySelector("[data-entry-preview]");
  if (!(amount > 0)) return box.replaceChildren(h("p", { class: "caption", text: "Enter an amount." }));
  const vals = entryValues({ ...e, amount });
  if (MACROS.every(([k]) => vals[k] === null)) {
    return box.replaceChildren(h("p", { class: "macro-note", text: e.basisKnown ? "No nutrition values are known for this product." : "Values unknown: the source does not say if they are per 100 g or per 100 ml." }));
  }
  box.replaceChildren(
    h(
      "div",
      { class: "macro-row" },
      MACROS.map(([k, label, unit]) => h("div", { class: "macro" }, h("span", { class: "macro-label", text: label }), h("strong", { text: vals[k] === null ? "–" : `${fmt(vals[k])} ${unit}`, title: vals[k] === null ? "unknown" : "" }), vals[k] === null ? h("span", { class: "sr-only", text: "unknown" }) : null))
    ),
    MACROS.some(([k]) => vals[k] === null) ? h("p", { class: "caption", text: "– means unknown." }) : null
  );
}
if (dialog) {
  const form = dialog.querySelector("form");
  form.elements.amount.addEventListener("input", () => {
    const e = load().diary.find((x) => x.id === form.dataset.id);
    if (e) preview(e);
  });
  dialog.querySelector("[data-entry-cancel]").addEventListener("click", () => dialog.close());
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const amount = Number(form.elements.amount.value);
    if (!(amount > 0 && amount <= 10000)) {
      form.elements.amount.setAttribute("aria-invalid", "true");
      return form.elements.amount.focus();
    }
    form.elements.amount.removeAttribute("aria-invalid");
    update((s) => {
      const e = s.diary.find((x) => x.id === form.dataset.id);
      if (e) Object.assign(e, { amount, meal: form.elements.meal.value, portion: e.amount === amount ? e.portion : "" });
    });
    dialog.close();
    announce("Entry updated.");
  });
  dialog.querySelector("[data-entry-delete]").addEventListener("click", () => {
    const id = form.dataset.id;
    const removed = load().diary.find((x) => x.id === id);
    update((s) => (s.diary = s.diary.filter((x) => x.id !== id)));
    dialog.close();
    toast(`${removed ? removed.name : "Entry"} deleted.`, () => update((s) => removed && s.diary.push(removed)));
  });
}

// ---------- Water ----------
function addWater(ml) {
  const item = { id: newId(), date: day, ml, at: Date.now() };
  update((s) => s.water.push(item));
  toast(`${ml} ml of water added.`, () => update((s) => (s.water = s.water.filter((w) => w.id !== item.id))));
}
function renderWater(state) {
  const list = state.water.filter((w) => w.date === day);
  const total = list.reduce((a, w) => a + w.ml, 0);
  root.querySelector("[data-water-total]").textContent = total >= 1000 ? `${fmt(total / 1000)} l` : `${total} ml`;
  root.querySelector("[data-water-list]").replaceChildren(
    ...list.map((w) => {
      const x = h("button", { type: "button", class: "log-remove", "aria-label": `Remove ${w.ml} ml` }, icon("x", 16));
      x.addEventListener("click", () => {
        update((s) => (s.water = s.water.filter((y) => y.id !== w.id)));
        toast(`${w.ml} ml removed.`, () => update((s) => s.water.push(w)));
      });
      return h("li", {}, h("span", { text: `${w.ml} ml` }), h("span", { class: "caption", text: new Date(w.at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) }), x);
    })
  );
}

// ---------- Activity ----------
function renderActivity(state) {
  const list = state.activity.filter((a) => a.date === day);
  const total = list.reduce((a, x) => a + x.minutes, 0);
  root.querySelector("[data-activity-total]").textContent = `${fmt(total)} min`;
  root.querySelector("[data-activity-list]").replaceChildren(
    ...list.map((a) => {
      const x = h("button", { type: "button", class: "log-remove", "aria-label": `Remove ${ACTIVITY_LABELS[a.type]} ${a.minutes} minutes` }, icon("x", 16));
      x.addEventListener("click", () => {
        update((s) => (s.activity = s.activity.filter((y) => y.id !== a.id)));
        toast(`${ACTIVITY_LABELS[a.type]} removed.`, () => update((s) => s.activity.push(a)));
      });
      return h("li", {}, h("span", { text: ACTIVITY_LABELS[a.type] }), h("span", { class: "caption", text: `${fmt(a.minutes)} min` }), x);
    })
  );
}

function render(state) {
  renderDays();
  root.querySelector("[data-scan-link]").href = diaryUrl("/scan/", "");
  renderSummary(state);
  renderDiary(state);
  renderWater(state);
  renderActivity(state);
  root.querySelector("[data-setup]").hidden = state.onboarded;
  root.querySelector("[data-storage-error]").hidden = storageOk();
}

if (root) {
  const fromUrl = new URLSearchParams(location.search).get("date");
  if (fromUrl && /^\d{4}-\d{2}-\d{2}$/.test(fromUrl)) day = fromUrl > localDate() ? localDate() : fromUrl;
  const step = () => (wide.matches ? 14 : 7);
  root.querySelector("[data-targets-switch]").addEventListener("click", () => {
    const st = load();
    if (!Object.values(st.settings.targets).some((v) => v)) {
      window.location.assign("/my-ola/#targets");
      return;
    }
    update((s) => (s.settings.showTargets = !s.settings.showTargets));
    announce(load().settings.showTargets ? "Targets on." : "Targets off. They stay saved.");
  });
  root.querySelector("[data-day-prev]").addEventListener("click", () => setDay(shiftDate(day, -step())));
  root.querySelector("[data-day-next]").addEventListener("click", () => setDay(shiftDate(day, step())));
  wide.addEventListener("change", () => render(load()));
  root.querySelectorAll("[data-water-add]").forEach((b) => b.addEventListener("click", () => addWater(Number(b.dataset.waterAdd))));
  root.querySelector("[data-water-form]").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = e.target.elements.ml;
    const ml = Number(input.value);
    if (!(ml > 0 && ml <= 5000)) {
      input.setAttribute("aria-invalid", "true");
      return input.focus();
    }
    input.removeAttribute("aria-invalid");
    addWater(Math.round(ml));
    e.target.reset();
  });
  root.querySelector("[data-activity-form]").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    const minutes = Number(f.elements.minutes.value);
    if (!(minutes > 0 && minutes <= 1440)) {
      f.elements.minutes.setAttribute("aria-invalid", "true");
      return f.elements.minutes.focus();
    }
    f.elements.minutes.removeAttribute("aria-invalid");
    const item = { id: newId(), date: day, type: f.elements.type.value, minutes, at: Date.now() };
    update((s) => s.activity.push(item));
    f.elements.minutes.value = "";
    toast(`${ACTIVITY_LABELS[item.type]}, ${fmt(minutes)} min added.`, () => update((s) => (s.activity = s.activity.filter((a) => a.id !== item.id))));
  });
  render(load());
  // Back from the "how much?" step.
  const added = new URLSearchParams(location.search).get("added");
  if (added) {
    const url = new URL(location.href);
    url.searchParams.delete("added");
    history.replaceState(null, "", url);
    const names = { breakfast: "Breakfast", lunch: "Lunch", dinner: "Dinner", snacks: "Snacks" };
    if (names[added]) toast(`Added to ${names[added]}.`);
  }
  window.addEventListener("ola:change", (e) => render(e.detail));
  window.addEventListener("pageshow", (e) => e.persisted && render(load()));
}
