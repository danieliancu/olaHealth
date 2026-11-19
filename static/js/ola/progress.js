// Progress: 7- or 30-day trends for energy and macros, water and activity minutes, plus an
// optional weight trend. A day without diary entries is "No entries", never zero.
// Bars are drawn with plain elements (heights through the CSSOM, allowed by the CSP).

import { announce } from "../app.js";
import { h, icon } from "./dom.js";
import { load, update, newId, localDate, shiftDate, dateFrom, dayTotals, RANGES } from "./store.js";

const root = document.querySelector("[data-progress]");
let range = 7;
const fmt = (n) => (n >= 100 ? Math.round(n).toString() : (Math.round(n * 10) / 10).toString());

function days() {
  const today = localDate();
  return [...Array(range)].map((_, i) => shiftDate(today, i - range + 1));
}
function dayLabel(iso) {
  const d = dateFrom(iso);
  return range === 7 ? d.toLocaleDateString(undefined, { weekday: "short" }) : String(d.getDate());
}

/** One small bar chart. values: number | null per day (null = "No entries"). */
function chart({ title, ic, unit, values, note }) {
  const known = values.filter((v) => v !== null);
  const max = Math.max(1, ...known);
  const avg = known.length ? known.reduce((a, v) => a + v, 0) / known.length : null;
  const list = days();
  const bars = values.map((v, i) => {
    const bar = h("span", { class: `bar${v === null ? " is-empty" : ""}` });
    if (v !== null) bar.style.height = `${Math.max(3, Math.round((v / max) * 100))}%`;
    const text = v === null ? "No entries" : `${fmt(v)} ${unit}`;
    return h(
      "li",
      { class: "bar-col", title: `${dateFrom(list[i]).toLocaleDateString()}: ${text}` },
      h("span", { class: "sr-only", text: `${dateFrom(list[i]).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}: ${text}` }),
      h("span", { class: "bar-track", "aria-hidden": "true" }, bar),
      h("span", { class: "bar-label", "aria-hidden": "true", text: range === 7 || i % 5 === 0 || i === values.length - 1 ? dayLabel(list[i]) : "" })
    );
  });
  return h(
    "section",
    { class: "card chart-card" },
    h("div", { class: "t-card-head" }, h("h2", {}, icon(ic, 20), ` ${title}`), h("span", { class: "chart-avg", text: avg === null ? "No entries" : `Average ${fmt(avg)} ${unit}` })),
    h("ol", { class: `bars r-${range}` }, bars),
    note ? h("p", { class: "caption", text: note }) : null
  );
}

function render(state) {
  const list = days();
  const diary = list.map((d) => dayTotals(state, d));
  const logged = diary.filter((d) => d.entries.length).length;
  root.querySelector("[data-consistency]").textContent = logged
    ? `You logged food on ${logged} of the last ${range} days.`
    : `No food logged in the last ${range} days yet. A scan a day is a good start.`;
  const macro = (k) => diary.map((d) => (d.entries.length ? d.totals[k].value : null));
  const partial = (k) => diary.some((d) => d.entries.length && d.totals[k].partial);
  const note = (k) => (partial(k) ? "Some days are partial: foods with unknown values are not counted." : "");
  const charts = [];
  if (state.settings.showEnergy) charts.push(chart({ title: "Energy", ic: "flame", unit: "kcal", values: macro("energy-kcal"), note: note("energy-kcal") }));
  charts.push(
    chart({ title: "Protein", ic: "egg", unit: "g", values: macro("proteins"), note: note("proteins") }),
    chart({ title: "Carbs", ic: "wheat", unit: "g", values: macro("carbohydrates"), note: note("carbohydrates") }),
    chart({ title: "Fat", ic: "droplet", unit: "g", values: macro("fat"), note: note("fat") }),
    chart({
      title: "Water",
      ic: "glass-water",
      unit: "ml",
      values: list.map((d) => {
        const w = state.water.filter((x) => x.date === d);
        return w.length ? w.reduce((a, x) => a + x.ml, 0) : null;
      }),
    }),
    chart({
      title: "Activity",
      ic: "activity",
      unit: "min",
      values: list.map((d) => {
        const a = state.activity.filter((x) => x.date === d);
        return a.length ? a.reduce((s, x) => s + x.minutes, 0) : null;
      }),
    })
  );
  root.querySelector("[data-charts]").replaceChildren(...charts);

  const card = root.querySelector("[data-weight-card]");
  card.hidden = !state.settings.trackWeight;
  if (state.settings.trackWeight) {
    const points = list.map((d) => {
      const w = state.weights.filter((x) => x.date === d).at(-1);
      return w ? w.kg : null;
    });
    const box = root.querySelector("[data-weight-chart]");
    if (!points.some((v) => v !== null)) box.replaceChildren(h("p", { class: "caption", text: "No weight logged in this period." }));
    else {
      const known = points.filter((v) => v !== null);
      box.replaceChildren(
        h("p", { class: "weight-range", text: `${fmt(known[0])} kg → ${fmt(known.at(-1))} kg (${known.length} entr${known.length === 1 ? "y" : "ies"})` }),
        h("ol", { class: "weight-list" }, list.map((d, i) => (points[i] === null ? null : h("li", {}, h("span", { text: dateFrom(d).toLocaleDateString(undefined, { day: "numeric", month: "short" }) }), h("strong", { text: `${fmt(points[i])} kg` })))).filter(Boolean))
      );
    }
  }
}

if (root) {
  root.querySelectorAll("[data-range]").forEach((b) =>
    b.addEventListener("click", () => {
      range = Number(b.dataset.range);
      root.querySelectorAll("[data-range]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      render(load());
    })
  );
  root.querySelector("[data-weight-form]").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = e.target.elements.kg;
    const kg = Number(String(input.value).replace(",", "."));
    const [lo, hi] = RANGES.weight;
    const valid = input.value && kg >= lo && kg <= hi;
    root.querySelector("[data-weight-error]").hidden = Boolean(valid);
    input.setAttribute("aria-invalid", String(!valid));
    if (!valid) return input.focus();
    const today = localDate();
    update((s) => {
      s.weights = s.weights.filter((w) => w.date !== today);
      s.weights.push({ id: newId(), date: today, kg });
    });
    e.target.reset();
    announce("Weight saved for today.");
  });
  render(load());
  window.addEventListener("ola:change", (e) => render(e.detail));
}
