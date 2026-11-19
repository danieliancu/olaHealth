// Selection only: the restriction states and their reasons come from the server
// (myola/restrictions.py). This module picks the ones that apply to the preferences stored
// on this device.

export const DIET_LABELS = {
  vegetarian: "Vegetarian",
  vegan: "Vegan",
  pescatarian: "Pescetarian",
  gluten_free: "Gluten-free",
  lactose_free: "Lactose-free",
  pork_free: "Pork-free",
};
export const GOAL_LABELS = { lower_sugar: "Lower sugar", more_protein: "More protein" };
export const ALLERGEN_LABELS = {
  "en:gluten": "Gluten (cereals)",
  "en:crustaceans": "Crustaceans",
  "en:eggs": "Eggs",
  "en:fish": "Fish",
  "en:peanuts": "Peanuts",
  "en:soybeans": "Soya",
  "en:milk": "Milk",
  "en:nuts": "Tree nuts",
  "en:celery": "Celery",
  "en:mustard": "Mustard",
  "en:sesame-seeds": "Sesame",
  "en:sulphur-dioxide-and-sulphites": "Sulphites",
  "en:lupin": "Lupin",
  "en:molluscs": "Molluscs",
};

/** Active strict restrictions that are not confirmed as respected. */
export function blocking(statuses, prefs) {
  const out = [];
  if (!statuses) return out;
  for (const d of prefs.diets) {
    const s = statuses.diets[d];
    if (s && s.status !== "compatible") out.push({ kind: "diet", key: d, label: DIET_LABELS[d], ...s });
  }
  for (const a of prefs.allergens) {
    const s = statuses.allergens[a];
    if (s && s.status !== "not_declared") out.push({ kind: "allergen", key: a, label: ALLERGEN_LABELS[a], ...s });
  }
  return out;
}

/** Restrictions not contradicted by the source. */
export function confirmed(statuses, prefs) {
  const out = [];
  if (!statuses) return out;
  for (const d of prefs.diets) {
    const s = statuses.diets[d];
    if (s && s.status === "compatible") out.push({ kind: "diet", key: d, label: DIET_LABELS[d], ...s });
  }
  for (const a of prefs.allergens) {
    const s = statuses.allergens[a];
    if (s && s.status === "not_declared") out.push({ kind: "allergen", key: a, label: ALLERGEN_LABELS[a], ...s });
  }
  return out;
}

export function statusText(s) {
  return {
    compatible: "Confirmed by the source",
    incompatible: "Not compatible",
    may_contain: "May contain (traces)",
    unknown: "Cannot be confirmed",
    contains: "Contains",
    not_declared: "Not declared",
  }[s] || s;
}
