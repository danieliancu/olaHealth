// My Track: one local store for the profile, food diary, water, activity, weight, lists, scans,
// notes, memories and chats.
// Everything stays on this device (no account, no sync). Unavailable or corrupt storage is
// handled: the page keeps working with an in-memory copy and says that nothing is kept.
// Favourites keep their existing key (olahealth:favorites, see app.js), unchanged.

export const KEY = "olahealth:myola";
export const GOALS = ["lower_sugar", "more_protein"];
export const DIETS = ["vegetarian", "vegan", "pescatarian", "gluten_free", "lactose_free", "pork_free"];
export const PROFILE_GOALS = ["lose_weight", "muscle_and_fat", "muscle_first", "eat_healthier"];
export const GENDERS = ["male", "female", "non_binary", "prefer_not", "other"];
export const RESTRICTIONS = ["vegan", "vegetarian", "pescatarian", "gluten_free", "lactose", "nuts", "seafood", "religious", "other", "none"];
export const NUT_TYPES = ["en:peanuts", "en:nuts"];
export const SEAFOOD_TYPES = ["en:fish", "en:crustaceans", "en:molluscs"];
export const CONDITIONS = ["hypertension", "diabetes", "depression", "eating_disorder", "other", "none", "prefer_not"];
export const MEALS = ["breakfast", "lunch", "dinner", "snacks"];
export const ACTIVITY_TYPES = ["walking", "running", "gym", "cycling", "swimming", "other"];
// Valid ranges for the optional "About you" answers (anything else is not saved).
export const RANGES = { age: [13, 120], height: [100, 250], weight: [25, 400] };
export const ALLERGENS = [
  "en:gluten", "en:crustaceans", "en:eggs", "en:fish", "en:peanuts", "en:soybeans", "en:milk",
  "en:nuts", "en:celery", "en:mustard", "en:sesame-seeds", "en:sulphur-dioxide-and-sulphites",
  "en:lupin", "en:molluscs",
];
const LIMITS = { list: 300, scans: 100, notes: 300, memories: 100, messages: 60, chats: 30, diary: 3000, water: 3000, activity: 2000, weights: 1000 };
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const NUTRIENTS = ["energy-kcal", "proteins", "carbohydrates", "fat"];
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
    settings: { useNotes: false, showEnergy: true, trackWeight: false, targets: {} },
    onboarded: false,
    profile: { goal: null, gender: null, age: null, height: null, weight: null, restrictions: [], nutTypes: [], seafoodTypes: [], religiousNote: "", otherNote: "", conditions: [], conditionsNote: "" },
    diary: [],
    water: [],
    activity: [],
    weights: [],
  };
}

const inRange = (v, [lo, hi]) => {
  const n = Number(v);
  return v !== null && v !== undefined && v !== "" && Number.isFinite(n) && n >= lo && n <= hi ? Math.round(n * 10) / 10 : null;
};
const amount = (v, max) => {
  const n = Number(v);
  return v !== null && v !== "" && Number.isFinite(n) && n > 0 && n <= max ? Math.round(n * 10) / 10 : null;
};

function sanitizeProfile(p) {
  p = p && typeof p === "object" ? p : {};
  let restrictions = pick(p.restrictions, RESTRICTIONS);
  if (restrictions.includes("none")) restrictions = ["none"];
  let conditions = pick(p.conditions, CONDITIONS);
  if (conditions.includes("prefer_not")) conditions = ["prefer_not"];
  else if (conditions.includes("none")) conditions = ["none"];
  return {
    goal: PROFILE_GOALS.includes(p.goal) ? p.goal : null,
    gender: GENDERS.includes(p.gender) ? p.gender : null,
    age: inRange(p.age, RANGES.age),
    height: inRange(p.height, RANGES.height),
    weight: inRange(p.weight, RANGES.weight),
    restrictions,
    nutTypes: pick(p.nutTypes, NUT_TYPES),
    seafoodTypes: pick(p.seafoodTypes, SEAFOOD_TYPES),
    religiousNote: str(p.religiousNote, 300),
    otherNote: str(p.otherNote, 300),
    conditions,
    conditionsNote: str(p.conditionsNote, 300),
  };
}

/** Diets and allergens used by the product page, from the onboarding restrictions. */
export function derivePrefs(profile) {
  const diets = new Set();
  const allergens = new Set();
  const r = profile.restrictions;
  if (r.includes("vegan")) diets.add("vegan");
  if (r.includes("vegetarian")) diets.add("vegetarian");
  if (r.includes("pescatarian")) diets.add("pescatarian");
  if (r.includes("gluten_free")) diets.add("gluten_free");
  if (r.includes("lactose")) diets.add("lactose_free");
  if (r.includes("nuts")) (profile.nutTypes.length ? profile.nutTypes : NUT_TYPES).forEach((a) => allergens.add(a));
  if (r.includes("seafood")) (profile.seafoodTypes.length ? profile.seafoodTypes : SEAFOOD_TYPES).forEach((a) => allergens.add(a));
  // "Religious restriction" and "Other" are notes only: free text never creates a rule.
  return { goals: [], diets: DIETS.filter((d) => diets.has(d)), allergens: ALLERGENS.filter((a) => allergens.has(a)) };
}

function per100(values) {
  const out = {};
  for (const k of NUTRIENTS) {
    const v = values && values[k];
    out[k] = v === null || v === undefined || v === "" || !Number.isFinite(Number(v)) ? null : Number(v);
  }
  return out;
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
  const st = raw.settings || {};
  const t = st.targets || {};
  s.settings = {
    useNotes: Boolean(st.useNotes),
    showEnergy: st.showEnergy !== false,
    showTargets: st.showTargets !== false,
    trackWeight: Boolean(st.trackWeight),
    targets: {
      "energy-kcal": amount(t["energy-kcal"], 10000),
      proteins: amount(t.proteins, 1000),
      carbohydrates: amount(t.carbohydrates, 2000),
      fat: amount(t.fat, 1000),
    },
  };
  s.onboarded = Boolean(raw.onboarded);
  s.profile = sanitizeProfile(raw.profile);
  // The product page reads diets and allergens derived from the profile.
  s.prefs = raw.profile ? derivePrefs(s.profile) : s.prefs;
  // Diary: the per-100 values used at the time are kept, so later catalogue updates never
  // change the history.
  s.diary = (Array.isArray(raw.diary) ? raw.diary : [])
    .filter((d) => d && DATE.test(d.date) && MEALS.includes(d.meal) && amount(d.amount, 10000))
    .map((d) => ({
      id: str(d.id || newId(), 64),
      date: d.date,
      meal: d.meal,
      product: productEntry(d.product),
      name: str(d.name, 200),
      amount: amount(d.amount, 10000),
      unit: ["g", "ml"].includes(d.unit) ? d.unit : "g",
      basisKnown: Boolean(d.basisKnown),
      portion: str(d.portion, 80),
      per100: per100(d.per100),
      at: num(d.at) || Date.now(),
    }))
    .slice(-LIMITS.diary);
  s.water = (Array.isArray(raw.water) ? raw.water : [])
    .filter((w) => w && DATE.test(w.date) && amount(w.ml, 5000))
    .map((w) => ({ id: str(w.id || newId(), 64), date: w.date, ml: amount(w.ml, 5000), at: num(w.at) || Date.now() }))
    .slice(-LIMITS.water);
  s.activity = (Array.isArray(raw.activity) ? raw.activity : [])
    .filter((a) => a && DATE.test(a.date) && ACTIVITY_TYPES.includes(a.type) && amount(a.minutes, 1440))
    .map((a) => ({ id: str(a.id || newId(), 64), date: a.date, type: a.type, minutes: amount(a.minutes, 1440), at: num(a.at) || Date.now() }))
    .slice(-LIMITS.activity);
  s.weights = (Array.isArray(raw.weights) ? raw.weights : [])
    .filter((w) => w && DATE.test(w.date) && inRange(w.kg, RANGES.weight))
    .map((w) => ({ id: str(w.id || newId(), 64), date: w.date, kg: inRange(w.kg, RANGES.weight) }))
    .slice(-LIMITS.weights);
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

// ---------- Dates: the user's local calendar day ----------
export function localDate(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
export function shiftDate(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  return localDate(new Date(y, m - 1, d + days));
}
export function dateFrom(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

// ---------- Diary maths: unknown stays unknown, totals say when they are partial ----------
export const DIARY_NUTRIENTS = NUTRIENTS;
export function entryValues(e) {
  const out = {};
  for (const k of NUTRIENTS) {
    const v = e.per100[k];
    out[k] = v === null || !e.basisKnown ? null : (v * e.amount) / 100;
  }
  return out;
}
export function dayTotals(state, date) {
  const entries = state.diary.filter((e) => e.date === date);
  const totals = {};
  for (const k of NUTRIENTS) {
    const vals = entries.map((e) => entryValues(e)[k]);
    totals[k] = { value: vals.reduce((a, v) => a + (v || 0), 0), partial: vals.some((v) => v === null), count: entries.length };
  }
  return { entries, totals };
}

/** Forget everything My Track keeps in this browser (and the in-memory copy). */
export function reset() {
  fallback = null;
  try {
    window.localStorage.removeItem(KEY);
  } catch (e) {
    /* nothing stored */
  }
  window.dispatchEvent(new CustomEvent("ola:change", { detail: empty() }));
}
