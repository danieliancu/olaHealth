// The short "how much?" step reached from the Food diary: no product page in between.
// A barcode scan is recorded in the scan history, then the entry is saved on confirm and the
// user goes back to Today for that day.

import { load, recordScan, localDate } from "./store.js";
import { blocking, statusText } from "./assess.js";
import { h } from "./dom.js";
import { setupDiaryForm, defaultMeal } from "./diary-form.js";

const root = document.querySelector("[data-add-food]");
const dataEl = document.getElementById("ola-data");

if (root && dataEl) {
  const payload = JSON.parse(dataEl.textContent);
  const p = payload.product;
  const date = root.dataset.date && root.dataset.date <= localDate() ? root.dataset.date : localDate();

  // Scanned from the diary: keep it in the scan history (once, never on reload).
  const code = root.dataset.scanned;
  const nav = performance.getEntriesByType ? performance.getEntriesByType("navigation")[0] : null;
  if (code && (!nav || nav.type === "navigate")) {
    recordScan({ id: p.id, name: p.name, brand: p.brand, quantity: p.quantity, image: p.image, url: p.url }, code);
  }

  // The user's strict restrictions that the source does not confirm, before adding.
  const blocked = blocking(payload.restrictions, load().prefs);
  if (blocked.length) {
    root.querySelector("[data-add-warnings]").replaceChildren(
      h("ul", { class: "restriction-list" }, blocked.map((b) => h("li", { class: `rs rs-${b.status}` }, h("strong", { text: `${b.label}: ${statusText(b.status)}. ` }), b.reason))),
      h("p", { class: "caption", text: "Check the physical label." })
    );
  }

  const form = setupDiaryForm(root.querySelector(".add-card"), payload, (meal, day) => {
    window.location.assign(`/today/?${new URLSearchParams({ date: day, added: meal })}`);
  });
  form.reset(root.dataset.meal || defaultMeal(), date);
  root.querySelector("input[name=amount]").focus({ preventScroll: true });
}
