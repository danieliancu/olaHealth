// Search-as-you-type for the catalogue (ARIA combobox + listbox).
// Queries our own catalogue only (/api/v1/suggest/), debounced; never Open Food Facts.

import { hiddenIds } from "./app.js";

const MIN_CHARS = 2;
const DEBOUNCE_MS = 180;

document.querySelectorAll("[data-suggest]").forEach(setup);

function setup(box) {
  const input = box.querySelector("input[role=combobox]");
  const list = box.querySelector("[role=listbox]");
  const status = box.querySelector("[data-suggest-status]");
  const url = box.dataset.suggestUrl;
  const placeholder = document.body.dataset.placeholder || "";
  let timer = null;
  let controller = null;
  let active = -1;
  let lastQuery = "";

  const options = () => [...list.querySelectorAll("[role=option]")];

  function open(isOpen) {
    list.hidden = !isOpen;
    input.setAttribute("aria-expanded", String(isOpen));
    if (!isOpen) setActive(-1);
  }

  function setActive(index) {
    const items = options();
    active = items.length ? (index + items.length) % items.length : -1;
    items.forEach((el, i) => el.setAttribute("aria-selected", String(i === active)));
    if (active >= 0) {
      input.setAttribute("aria-activedescendant", items[active].id);
      items[active].scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
    if (index === -1) active = -1;
  }

  function option(id, html) {
    const li = document.createElement("li");
    li.id = id;
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", "false");
    li.className = "suggest-item";
    li.append(...html);
    return li;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function render(query, results) {
    list.replaceChildren();
    results.forEach((p, i) => {
      const img = el("img", "suggest-thumb");
      img.src = p.image_url || placeholder;
      img.alt = "";
      img.width = 40;
      img.height = 40;
      const text = el("span", "suggest-text");
      text.append(el("span", "suggest-name", p.name || "Unnamed product"),
                  el("span", "suggest-meta", [p.brand, p.quantity].filter(Boolean).join(" · ")));
      const parts = [img, text];
      if (p.nutriscore) {
        const g = el("span", `grade grade-sm grade-${p.nutriscore.grade}`, p.nutriscore.grade.toUpperCase());
        g.setAttribute("aria-label", `Nutri-Score ${p.nutriscore.grade.toUpperCase()}`);
        parts.push(g);
      }
      const li = option(`suggest-${i}`, parts);
      li.dataset.href = p.url;
      list.append(li);
    });
    if (!results.length) {
      const li = option("suggest-none", [el("span", "suggest-empty", `No products match “${query}”`)]);
      li.setAttribute("aria-disabled", "true");
      list.append(li);
    } else {
      const all = option("suggest-all", [el("span", "suggest-all", `See all results for “${query}”`)]);
      all.dataset.submit = "1";
      list.append(all);
    }
    status.textContent = results.length
      ? `${results.length} suggestion${results.length === 1 ? "" : "s"}`
      : "No suggestions";
    open(true);
  }

  async function fetchSuggestions() {
    const query = input.value.trim();
    if (query === lastQuery && !list.hidden) return;
    lastQuery = query;
    if (query.length < MIN_CHARS) {
      open(false);
      return;
    }
    controller?.abort();
    controller = new AbortController();
    box.classList.add("is-loading");
    try {
      const res = await fetch(`${url}?${new URLSearchParams({ q: query })}`, {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!res.ok) return;
      const data = await res.json();
      const hidden = hiddenIds();
      const results = (data.results || []).filter((p) => !hidden.has(p.id));
      if (input.value.trim() === query) render(query, results);
    } catch (err) {
      // aborted or offline: keep the normal search form working
    } finally {
      box.classList.remove("is-loading");
    }
  }

  function choose(li) {
    if (!li || li.getAttribute("aria-disabled") === "true") return;
    if (li.dataset.href) window.location.href = li.dataset.href;
    else if (li.dataset.submit) input.form.requestSubmit();
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(fetchSuggestions, DEBOUNCE_MS);
  });
  input.addEventListener("focus", () => {
    if (input.value.trim().length >= MIN_CHARS && list.children.length) open(true);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (list.hidden) fetchSuggestions();
      else setActive(active + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (!list.hidden) setActive(active - 1);
    } else if (event.key === "Enter" && !list.hidden && active >= 0) {
      event.preventDefault();
      choose(options()[active]);
    } else if (event.key === "Escape" && !list.hidden) {
      event.preventDefault();
      open(false);
    }
  });
  // mousedown (not click) so the choice happens before the input loses focus
  list.addEventListener("mousedown", (event) => {
    const li = event.target.closest("[role=option]");
    if (li) {
      event.preventDefault();
      choose(li);
    }
  });
  document.addEventListener("click", (event) => {
    if (!box.contains(event.target)) open(false);
  });
}
