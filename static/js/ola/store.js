// My Ola: one local store for preferences, lists, scans, notes, memories and chats.
// Everything stays on this device (no account, no sync). Unavailable or corrupt storage is
// handled: the page keeps working with an in-memory copy and says that nothing is kept.
// Favourites keep their existing key (olahealth:favorites, see app.js), unchanged.

export const KEY = "olahealth:myola";
export const GOALS = ["lower_sugar", "more_protein"];
export const DIETS = ["vegetarian", "vegan", "gluten_free", "lactose_free", "pork_free"];
export const ALLERGENS = [
  "en:gluten", "en:crustaceans", "en:eggs", "en:fish", "en:peanuts", "en:soybeans", "en:milk",
  "en:nuts", "en:celery", "en:mustard", "en:sesame-seeds", "en:sulphur-dioxide-and-sulphites",
  "en:lupin", "en:molluscs",
];
const LIMITS = { list: 300, scans: 100, notes: 300, memories: 100, messages: 60, chats: 30 };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const NOTE_KINDS = ["general", "taste", "prefer", "avoid"];

let fallback = null; // used when localStorage is not available
let ok = true;

export function storageOk() {
  return ok;
}

export function newId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

const str = (v, max) => String(v ?? "").slice(0, max);
const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
const pick = (values, allowed) => [...new Set((Array.isArray(values) ? values : []).filter((v) => allowed.includes(v)))];
const safeUrl = (u) => {
  u = String(u || "");
  return u.startsWith("/") && !u.startsWith("//") ? u.slice(0, 300) : "";
};
const safeImage = (u) => {
  u = String(u || "");
  return u.startsWith("/") || u.startsWith("https://") ? u.slice(0, 500) : "";
};

export function productEntry(x) {
  if (!x || !UUID.test(String(x.id))) return null;
  if (String(x.url || "").startsWith("/demo/")) return null; // removed demo products
  return {
    id: String(x.id),
    name: str(x.name, 200),
    brand: str(x.brand, 200),
    quantity: str(x.quantity, 60),
    image: safeImage(x.image),
    url: safeUrl(x.url) || `/p/${x.id}/`,
    at: num(x.at) || Date.now(),
  };
}

function entries(list, max) {
  const seen = new Set();
  const out = [];
  for (const x of Array.isArray(list) ? list : []) {
    const e = productEntry(x);
    if (e && !seen.has(e.id)) {
      seen.add(e.id);
      out.push(e);
    }
  }
  return out.slice(0, max);
}

function empty() {
  return {
    v: 1,
    prefs: { goals: [], diets: [], allergens: [] },
    lists: { buyOften: [], avoid: [] },
    scans: [],
    notes: [],
    memories: [],
    chats: {},
    settings: { useNotes: false },
    onboarded: false,
  };
}

function sanitize(raw) {
  const s = empty();
  if (!raw || typeof raw !== "object") return s;
  const p = raw.prefs || {};
  s.prefs = { goals: pick(p.goals, GOALS), diets: pick(p.diets, DIETS), allergens: pick(p.allergens, ALLERGENS) };
  const l = raw.lists || {};
  s.lists = { buyOften: entries(l.buyOften, LIMITS.list), avoid: entries(l.avoid, LIMITS.list) };
  // Scans: one entry per scan event (the same product can be scanned again later).
  s.scans = (Array.isArray(raw.scans) ? raw.scans : [])
    .map((x) => {
      const e = productEntry(x);
      return e ? { ...e, scanId: str(x.scanId || newId(), 64), code: str(x.code, 32) } : null;
    })
    .filter(Boolean)
    .slice(0, LIMITS.scans);
  s.notes = (Array.isArray(raw.notes) ? raw.notes : [])
    .filter((n) => n && typeof n === "object" && String(n.text || "").trim())
    .map((n) => ({
      id: str(n.id || newId(), 64),
      text: str(n.text, 2000),
      kind: NOTE_KINDS.includes(n.kind) ? n.kind : "general",
      product: n.product ? productEntry(n.product) : null,
      createdAt: num(n.createdAt) || Date.now(),
      updatedAt: num(n.updatedAt) || num(n.createdAt) || Date.now(),
    }))
    .slice(0, LIMITS.notes);
  s.memories = (Array.isArray(raw.memories) ? raw.memories : [])
    .filter((m) => m && typeof m === "object" && String(m.text || "").trim())
    .map((m) => ({
      id: str(m.id || newId(), 64),
      text: str(m.text, 300),
      source: m.source === "suggestion" ? "suggestion" : "manual",
      suggestionKey: str(m.suggestionKey, 80),
      active: m.active !== false,
      confirmedAt: num(m.confirmedAt) || null,
      createdAt: num(m.createdAt) || Date.now(),
    }))
    .slice(0, LIMITS.memories);
  const chats = raw.chats && typeof raw.chats === "object" ? raw.chats : {};
  for (const [ctx, msgs] of Object.entries(chats).slice(0, LIMITS.chats)) {
    if (!/^(personal|product:[0-9a-f-]{36})$/i.test(ctx) || !Array.isArray(msgs)) continue;
    s.chats[ctx] = msgs
      .filter((m) => m && (m.role === "user" || m.role === "ola"))
      .map((m) => ({ role: m.role, text: str(m.text, 2000), at: num(m.at) }))
      .slice(-LIMITS.messages);
  }
  s.settings = { useNotes: Boolean(raw.settings && raw.settings.useNotes) };
  s.onboarded = Boolean(raw.onboarded);
  return s;
}

export function load() {
  try {
    const raw = window.localStorage.getItem(KEY);
    ok = true;
    if (!raw) return fallback ? sanitize(fallback) : empty();
    return sanitize(JSON.parse(raw));
  } catch (err) {
    if (err instanceof SyntaxError) {
      // Corrupt data: start again rather than break every page.
      try {
        window.localStorage.removeItem(KEY);
      } catch (e) {
        ok = false;
      }
      return empty();
    }
    ok = false;
    return fallback ? sanitize(fallback) : empty();
  }
}

export function save(state) {
  const clean = sanitize(state);
  fallback = clean;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(clean));
    ok = true;
  } catch (err) {
    ok = false;
  }
  window.dispatchEvent(new CustomEvent("ola:change", { detail: clean }));
  return ok;
}

export function update(fn) {
  const state = load();
  fn(state);
  return save(state);
}

// Other tabs: keep every open page in step.
window.addEventListener("storage", (e) => {
  if (e.key === KEY) window.dispatchEvent(new CustomEvent("ola:change", { detail: load() }));
});

// ---------- Lists: Buy often / Avoid are exclusive; every action can be undone ----------
export function inList(state, list, id) {
  return state.lists[list].some((x) => x.id === id);
}

export function toggleList(list, entry) {
  let added = false;
  const saved = update((s) => {
    const other = list === "avoid" ? "buyOften" : "avoid";
    if (inList(s, list, entry.id)) {
      s.lists[list] = s.lists[list].filter((x) => x.id !== entry.id);
    } else {
      s.lists[list].unshift({ ...entry, at: Date.now() });
      s.lists[other] = s.lists[other].filter((x) => x.id !== entry.id);
      added = true;
    }
  });
  return { saved, added };
}

// ---------- Scans: recorded only from the scan / code entry flow ----------
export function recordScan(entry, code) {
  return update((s) => {
    s.scans.unshift({ ...entry, scanId: newId(), code: str(code, 32), at: Date.now() });
  });
}
