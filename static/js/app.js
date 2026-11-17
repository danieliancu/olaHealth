// Small progressive enhancements. Every page works without JavaScript except the
// camera scanner and the browser-local lists (favourites, history, compare selection).

document.documentElement.classList.add("js");

const KEYS = { history: "olahealth:history", favorites: "olahealth:favorites", compare: "olahealth:compare" };
const LIMITS = { history: 50, favorites: 200, compare: 3 };
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
      .filter((x) => x && typeof x === "object" && UUID.test(String(x.id)))
      .map((x) => ({
        id: String(x.id),
        name: String(x.name || "").slice(0, 200),
        brand: String(x.brand || "").slice(0, 200),
        quantity: String(x.quantity || "").slice(0, 60),
        image: safeImage(x.image),
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
  const fav = product.querySelector("[data-fav]");
  if (fav) {
    const sync = () => {
      const on = has("favorites", product.dataset.id);
      fav.setAttribute("aria-pressed", String(on));
      fav.querySelector("span").textContent = on ? "Saved" : "Save";
    };
    sync();
    fav.addEventListener("click", () => {
      const on = has("favorites", product.dataset.id);
      const ok = on ? remove("favorites", product.dataset.id) : add("favorites", entryFrom(product));
      if (!ok) return announce("This browser does not allow saving.");
      sync();
      announce(on ? "Removed from saved products." : "Saved on this device.");
    });
  }
}

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

// Compare page without ids: send the stored selection, or render the tray.
const comparePage = document.querySelector("[data-compare-empty]");
if (comparePage && !new URLSearchParams(location.search).get("ids")) {
  if (read("compare").length >= 2) location.replace(compareUrl());
}
document.querySelectorAll("[data-compare-tray]").forEach((tray) => {
  const items = read("compare");
  if (!items.length) {
    tray.textContent = "No products selected.";
    return;
  }
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
  link.href = `/p/${entry.id}/`;
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
  renderList("favorites", "[data-favorites]", "[data-favorites-empty]");
  renderList("history", "[data-history]", "[data-history-empty]");
  const err = document.querySelector("[data-storage-error]");
  if (err) err.hidden = storageOk;
}
renderSaved();

document.querySelectorAll("[data-clear]").forEach((btn) =>
  btn.addEventListener("click", () => {
    const what = btn.dataset.clear;
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
        if (li && li.textContent !== labels[run.status]) {
          if (run.status === "failed") failedChanged = true;
          li.textContent = labels[run.status];
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
