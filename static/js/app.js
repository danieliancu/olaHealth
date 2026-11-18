// Small progressive enhancements. Every page works without JavaScript except the
// camera scanner and the browser-local lists (favourites, history, compare selection).

import { confirmDialog } from "./dialog.js";

document.documentElement.classList.add("js");

const KEYS = {
  history: "olahealth:history",
  favorites: "olahealth:favorites",
  compare: "olahealth:compare",
  hidden: "olahealth:hidden", // products the user removed from their own list (this device only)
};
const LIMITS = { history: 50, favorites: 200, compare: 3, hidden: 1000 };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function announce(message) {
  const live = document.querySelector("[data-live]");
  if (!live) return;
  live.textContent = "";
  window.setTimeout(() => (live.textContent = message), 50);
}

// ---------- Storage (unavailable or corrupt storage is handled) ----------
let storageOk = true;
function read(kind) {
  try {
    const raw = window.localStorage.getItem(KEYS[kind]);
    if (!raw) return [];
    const data = JSON.parse(raw);
    if (!Array.isArray(data)) throw new Error("corrupt");
    // Keep only well-formed, minimal entries.
    return data
      // Entries from the removed demo (fictional products) are dropped.
      .filter((x) => x && typeof x === "object" && UUID.test(String(x.id)) && !String(x.url || "").startsWith("/demo/"))
      .map((x) => ({
        id: String(x.id),
        name: String(x.name || "").slice(0, 200),
        brand: String(x.brand || "").slice(0, 200),
        quantity: String(x.quantity || "").slice(0, 60),
        image: safeImage(x.image),
        url: safeUrl(x.url),
        at: Number(x.at) || 0,
      }));
  } catch (err) {
    if (err instanceof SyntaxError || err.message === "corrupt") {
      try { window.localStorage.removeItem(KEYS[kind]); } catch (e) { storageOk = false; }
      return [];
    }
    storageOk = false;
    return [];
  }
}
function write(kind, items) {
  try {
    window.localStorage.setItem(KEYS[kind], JSON.stringify(items.slice(0, LIMITS[kind])));
    return true;
  } catch (err) {
    storageOk = false;
    return false;
  }
}
function safeUrl(url) {
  url = String(url || "");
  return url.startsWith("/") && !url.startsWith("//") ? url.slice(0, 300) : "";
}
function safeImage(url) {
  url = String(url || "");
  return url.startsWith("/") || url.startsWith("https://") ? url.slice(0, 500) : "";
}
function entryFrom(el) {
  return {
    id: el.dataset.id,
    name: el.dataset.name || "",
    brand: el.dataset.brand || "",
    quantity: el.dataset.quantity || "",
    image: safeImage(el.dataset.image),
    url: safeUrl(el.dataset.url),
    at: Date.now(),
  };
}
const has = (kind, id) => read(kind).some((x) => x.id === id);
function add(kind, entry) {
  const items = read(kind).filter((x) => x.id !== entry.id);
  items.unshift(entry);
  return write(kind, items);
}
function remove(kind, id) {
  return write(kind, read(kind).filter((x) => x.id !== id));
}

// ---------- Product page: history + favourite ----------
const product = document.querySelector("[data-product]");
if (product && UUID.test(product.dataset.id)) {
  add("history", entryFrom(product));
}
// Save buttons: on the product page they use the page's product, on cards their own data.
document.querySelectorAll("[data-fav]").forEach((fav) => {
  const source = fav.dataset.id ? fav : fav.closest("[data-product]");
  if (!source || !UUID.test(source.dataset.id || "")) return;
  const id = source.dataset.id;
  const label = fav.querySelector("span");
  const sync = () => {
    const on = has("favorites", id);
    fav.setAttribute("aria-pressed", String(on));
    if (label) label.textContent = on ? "Favourite ✓" : "Favourite";
    else fav.title = on ? "In favourites" : "Favourite";
  };
  sync();
  fav.addEventListener("click", () => {
    const on = has("favorites", id);
    const ok = on ? remove("favorites", id) : add("favorites", entryFrom(source));
    if (!ok) return announce("This browser does not allow saving.");
    sync();
    updateSavedCount();
    announce(on ? "Removed from favourites." : "Added to favourites on this device.");
  });
});

// ---------- Card actions: a discreet arrow reveals Save / Compare / Remove ----------
document.querySelectorAll(".actions-toggle").forEach((toggle) => {
  const panel = document.getElementById(toggle.getAttribute("aria-controls"));
  if (!panel) return;
  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    panel.classList.toggle("is-open", open);
    if (open) panel.querySelector("button")?.focus({ preventScroll: true });
  });
});

// ---------- "Remove from my list" (this device only, never the database) ----------
export function hiddenIds() {
  return new Set(read("hidden").map((x) => x.id));
}
function applyHidden() {
  const hidden = hiddenIds();
  const cards = [...document.querySelectorAll(".product-card")];
  let count = 0;
  cards.forEach((card) => {
    const btn = card.querySelector("[data-hide-product]");
    const isHidden = !!btn && hidden.has(btn.dataset.id);
    card.closest("li").hidden = isHidden;
    if (isHidden) count += 1;
  });
  const counter = document.querySelector("[data-product-count]");
  if (counter && counter.dataset.total) {
    const visible = Number(counter.dataset.total) - count;
    counter.textContent = `${visible} product${visible === 1 ? "" : "s"}`;
  }
  const note = document.querySelector("[data-hidden-note]");
  if (note) {
    note.hidden = count === 0;
    note.querySelector("[data-hidden-text]").textContent =
      `${count} product${count === 1 ? "" : "s"} hidden on this device.`;
  }
}
document.querySelectorAll("[data-hide-product]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    const name = btn.dataset.name || "This product";
    const ok = await confirmDialog({
      title: "Remove from your list?",
      message: `“${name}” will be hidden on this device only. It stays in the catalogue for everyone else.`,
      confirmLabel: "Remove",
      tone: "danger",
    });
    if (!ok) return;
    if (!add("hidden", entryFrom(btn))) return announce("This browser does not allow saving.");
    applyHidden();
    announce(`${name} removed from your list.`);
  })
);
document.querySelectorAll("[data-unhide-all]").forEach((btn) =>
  btn.addEventListener("click", () => {
    write("hidden", []);
    applyHidden();
    announce("Hidden products are shown again.");
  })
);
applyHidden();

// ---------- Saved counter in the menu ----------
function updateSavedCount() {
  const n = read("favorites").length;
  document.querySelectorAll("[data-saved-count]").forEach((el) => {
    el.textContent = String(n);
    el.hidden = n === 0;
  });
}
updateSavedCount();

// ---------- Compare selection ----------
function updateCompareCount() {
  const n = read("compare").length;
  document.querySelectorAll("[data-compare-count]").forEach((el) => {
    el.textContent = String(n);
    el.hidden = n === 0;
  });
}
function compareUrl() {
  const ids = read("compare").map((x) => x.id);
  return `/compare/?ids=${ids.join(",")}`;
}
document.querySelectorAll("[data-compare-toggle]").forEach((btn) => {
  const id = btn.dataset.id;
  if (!UUID.test(id || "")) return;
  const label = btn.querySelector("span");
  const sync = () => {
    const on = has("compare", id);
    btn.setAttribute("aria-pressed", String(on));
    if (label) label.textContent = on ? "Added" : "Compare";
    else btn.title = on ? "Added to compare" : "Compare";
  };
  sync();
  btn.addEventListener("click", () => {
    if (has("compare", id)) {
      remove("compare", id);
      announce("Removed from comparison.");
    } else {
      const items = read("compare");
      if (items.length >= LIMITS.compare) {
        announce("You can compare at most 3 products. Remove one first.");
        return;
      }
      add("compare", entryFrom(btn));
      const n = items.length + 1;
      announce(n >= 2 ? `${n} products selected. Open Compare to see them.` : "Added. Choose one or two more products.");
    }
    sync();
    updateCompareCount();
  });
});
updateCompareCount();

// The server tells which of the requested products still exist; drop the others.
const shown = document.querySelector("[data-compare-shown]");
if (shown && shown.hasAttribute("data-compare-requested")) {
  const keep = new Set(shown.dataset.compareShown.split(",").filter(Boolean));
  const items = read("compare");
  if (items.some((x) => !keep.has(x.id))) {
    write("compare", items.filter((x) => keep.has(x.id)));
    updateCompareCount();
  }
}

// Compare page without ids: send the stored selection, or render the tray.
const comparePage = document.querySelector("[data-compare-empty]");
if (comparePage && !new URLSearchParams(location.search).get("ids")) {
  if (read("compare").length >= 2) location.replace(compareUrl());
}
document.querySelectorAll("[data-compare-tray]").forEach((tray) => {
  const items = read("compare");
  if (!items.length) return;
  const list = document.createElement("ul");
  list.className = "product-list";
  items.forEach((x) => list.append(row(x, "compare", () => location.reload())));
  tray.replaceChildren(list);
  if (items.length >= 2) {
    const go = document.createElement("a");
    go.className = "btn btn-primary btn-block";
    go.href = compareUrl();
    go.textContent = `Compare ${items.length} products`;
    tray.append(go);
  }
});
document.querySelectorAll("[data-compare-remove]").forEach((link) =>
  link.addEventListener("click", () => {
    remove("compare", link.dataset.compareRemove);
    updateCompareCount();
  })
);
document.querySelectorAll("[data-compare-clear]").forEach((btn) =>
  btn.addEventListener("click", () => {
    write("compare", []);
    location.href = "/compare/";
  })
);

// ---------- Lists (saved page, recent on home) ----------
function row(entry, kind, onRemove) {
  const tpl = document.querySelector("[data-row-template]");
  let li;
  if (tpl) {
    li = tpl.content.firstElementChild.cloneNode(true);
  } else {
    li = document.createElement("li");
    li.className = "product-card";
    li.innerHTML = '<img class="thumb" alt="" width="64" height="64" data-img><div class="grow"><a class="product-link" data-link></a><p class="meta" data-meta></p></div>';
  }
  const img = li.querySelector("[data-img]");
  if (entry.image) img.src = entry.image;
  else img.src = document.body.dataset.placeholder || "/static/img/placeholder.svg";
  const link = li.querySelector("[data-link]");
  link.href = entry.url || `/p/${entry.id}/`;
  link.textContent = entry.name || "Unnamed product";
  li.querySelector("[data-meta]").textContent = [entry.brand, entry.quantity].filter(Boolean).join(" · ");
  const btn = li.querySelector("[data-remove]");
  if (btn) {
    if (kind) {
      btn.setAttribute("aria-label", `Remove ${entry.name || "product"} from ${kind === "favorites" ? "saved products" : kind === "compare" ? "comparison" : "history"}`);
      btn.addEventListener("click", () => {
        remove(kind, entry.id);
        announce("Removed.");
        onRemove && onRemove();
      });
    } else {
      btn.remove();
    }
  }
  return li;
}

function renderList(kind, listSel, emptySel) {
  const list = document.querySelector(listSel);
  if (!list) return;
  const items = read(kind);
  list.replaceChildren(...items.map((x) => row(x, kind, () => renderSaved())));
  const empty = document.querySelector(emptySel);
  if (empty) empty.hidden = items.length > 0;
}
function renderSaved() {
  updateSavedCount();
  renderList("favorites", "[data-favorites]", "[data-favorites-empty]");
  renderList("history", "[data-history]", "[data-history-empty]");
  const err = document.querySelector("[data-storage-error]");
  if (err) err.hidden = storageOk;
}
renderSaved();

document.querySelectorAll("[data-clear]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    const what = btn.dataset.clear;
    const ok = await confirmDialog({
      title: what === "history" ? "Clear history?" : "Delete all saved products?",
      message: "This removes them from this device only.",
      confirmLabel: what === "history" ? "Clear" : "Delete",
      tone: "danger",
    });
    if (!ok) return;
    if (what === "history") write("history", []);
    else Object.keys(KEYS).forEach((k) => write(k, []));
    renderSaved();
    updateCompareCount();
    announce(what === "history" ? "History cleared." : "All saved data deleted from this device.");
  })
);

const recent = document.querySelector("[data-recent]");
if (recent) {
  const items = read("history").slice(0, 4);
  if (items.length) {
    recent.querySelector("[data-recent-list]").replaceChildren(...items.map((x) => row(x, null)));
    recent.hidden = false;
  }
}

// ---------- Tabs (Saved page) ----------
document.querySelectorAll("[data-tabs]").forEach((tablist) => {
  const tabs = [...tablist.querySelectorAll("[role=tab]")];
  const select = (tab) => {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    });
  };
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      select(next);
      next.focus();
    });
  });
});

// ---------- Forms ----------
// Filters apply on change (the Apply button stays for no-JS).
document.querySelectorAll("form[data-autosubmit] select, form[data-autosubmit] input[type=checkbox]").forEach((el) =>
  el.addEventListener("change", () => el.form.requestSubmit())
);
// Photo slots upload as soon as a file is chosen.
document.querySelectorAll("form[data-autoupload] input[type=file]").forEach((input) =>
  input.addEventListener("change", () => {
    if (input.files.length) {
      const slot = input.closest(".slot-add");
      if (slot) {
        slot.querySelector(".icon")?.replaceWith(spinner());
        slot.classList.add("is-uploading");
      }
      document.querySelectorAll("form[data-autoupload] input[type=file]").forEach((i) => (i.disabled = i !== input));
      announce("Uploading photo…");
      input.form.submit();
    }
  })
);

// ---------- OCR status polling ----------
const ocrBox = document.querySelector("[data-ocr-status]");
if (ocrBox) {
  const labels = { queued: "in progress…", running: "in progress…", done: "done", failed: "failed" };
  const started = Date.now();
  const poll = async () => {
    try {
      const res = await fetch(ocrBox.dataset.url, { headers: { Accept: "application/json" } });
      const data = await res.json();
      let pending = 0;
      let failedChanged = false;
      for (const run of data.runs) {
        const li = ocrBox.querySelector(`[data-run="${run.id}"] [data-run-status]`);
        if (li && li.dataset.state !== run.status) {
          if (run.status === "failed") failedChanged = true;
          li.dataset.state = run.status;
          li.textContent = labels[run.status];
          if (run.status === "queued" || run.status === "running") li.prepend(spinner(), " ");
        }
        if (run.status === "queued" || run.status === "running") pending += 1;
      }
      if (data.runs.some((r) => r.status === "done")) ocrBox.querySelector("[data-ocr-apply]").hidden = false;
      if (failedChanged) return location.reload(); // shows the retry button
      if (pending === 0) return announce("Text recognition finished. You can fill the form with the recognised values.");
    } catch (err) {
      return;
    }
    if (Date.now() - started < 120000) window.setTimeout(poll, 3000);
  };
  if (ocrBox.querySelector("[data-run-status]")) window.setTimeout(poll, 2000);
}

// ---------- Discreet activity indicators ----------
export function spinner() {
  const s = document.createElement("span");
  s.className = "spinner";
  s.setAttribute("aria-hidden", "true");
  return s;
}

// Forms: the submit button shows a spinner and cannot be pressed twice.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || form.hasAttribute("data-no-busy")) return;
  window.setTimeout(() => {
    if (event.defaultPrevented) return;
    const button = event.submitter || form.querySelector("button[type=submit], button:not([type])");
    if (!button || button.classList.contains("is-busy")) return;
    button.classList.add("is-busy");
    button.setAttribute("aria-disabled", "true");
    button.prepend(spinner());
    form.setAttribute("aria-busy", "true");
  }, 0);
});
// Coming back with the browser's Back button restores the page from cache: reset buttons.
window.addEventListener("pageshow", () => {
  document.querySelectorAll(".btn.is-busy").forEach((b) => {
    b.classList.remove("is-busy");
    b.removeAttribute("aria-disabled");
    b.querySelector(".spinner")?.remove();
  });
  document.querySelectorAll("form[aria-busy]").forEach((f) => f.removeAttribute("aria-busy"));
});

// Images: soft shimmer until each photo has loaded (OFF photos can be slow).
function watchImage(img) {
  if (img.complete && img.naturalWidth > 0) return;
  img.classList.add("is-loading");
  const done = () => img.classList.remove("is-loading");
  img.addEventListener("load", done, { once: true });
  img.addEventListener("error", done, { once: true });
}
document.querySelectorAll("img.product-photo, img.thumb, .gallery img, .compare-head img").forEach(watchImage);
new MutationObserver((records) => {
  for (const r of records) {
    r.addedNodes.forEach((n) => {
      if (n.nodeType !== 1) return;
      n.querySelectorAll?.("img.product-photo, img.thumb").forEach(watchImage);
      if (n.matches?.("img.product-photo, img.thumb")) watchImage(n);
    });
  }
}).observe(document.body, { childList: true, subtree: true });

// ---------- Shared with the My Ola modules (same storage, same validation) ----------
export const productLists = {
  read: (kind) => read(kind),
  has: (kind, id) => has(kind, id),
  add: (kind, entry) => add(kind, entry),
  remove: (kind, id) => remove(kind, id),
  entryFrom,
  safeUrl,
  safeImage,
  storageOk: () => storageOk,
  refreshCounts() {
    updateSavedCount();
    updateCompareCount();
  },
};
