// Today: the week arrows swap the day in place (htmx) and the days slide in from the side
// they come from, instead of a full page reload. No slide with reduced motion.

const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let direction = "";

document.addEventListener("click", (e) => {
  const link = e.target.closest("[data-week-nav]");
  if (link) direction = link.dataset.weekNav;
});

document.addEventListener("htmx:afterSwap", () => {
  if (!direction) return;
  const days = document.querySelector("#today .day-strip .days");
  if (days && !still) days.classList.add(direction === "next" ? "slide-from-right" : "slide-from-left");
  direction = "";
});

// Picking a day in the calendar: the kcal on the meal tiles (Breakfast, Lunch, …) count up
// from 0. A day click is a full page load, so a flag carries it across.
const FLAG = "foodo-count-up";

document.addEventListener("click", (e) => {
  if (e.target.closest("#today .day-strip a.day:not(.is-selected)")) {
    try { sessionStorage.setItem(FLAG, "1"); } catch { /* storage off: no animation */ }
  }
});

function countUp(root) {
  if (still || !root) return;
  root.querySelectorAll(".meal-tile-sub").forEach((el) => {
    const node = [...el.childNodes].find((n) => n.nodeType === 3 && /\d/.test(n.nodeValue));
    if (!node) return;
    const text = node.nodeValue;
    const m = text.match(/\d[\d,.]*/);
    // Odometer: each digit is a column 0-9 twice that rolls a full turn onto its value.
    const odo = document.createElement("span");
    odo.className = "odo";
    odo.setAttribute("aria-hidden", "true");
    const strips = [];
    [...m[0]].forEach((ch, i, all) => {
      if (!/\d/.test(ch)) { odo.append(ch); return; }
      const box = document.createElement("span");
      box.className = "odo-d";
      const strip = document.createElement("span");
      strip.className = "odo-strip";
      for (let k = 0; k < 20; k++) {
        const d = document.createElement("span");
        d.textContent = k % 10;
        strip.append(d);
      }
      strip.style.transitionDuration = `${1.1 + (i / all.length) * 0.7}s`;
      box.append(strip);
      odo.append(box);
      strips.push([strip, 10 + Number(ch)]);
    });
    const sr = document.createElement("span");
    sr.className = "sr-only";
    sr.textContent = m[0];
    const after = document.createTextNode(text.slice(m.index + m[0].length));
    node.nodeValue = text.slice(0, m.index);
    node.after(odo, sr, after);
    // Roll once the tiles have faded in, then put the plain text back.
    setTimeout(() => strips.forEach(([strip, n]) => { strip.style.transform = `translateY(${-n * 1.2}em)`; }), 450);
    setTimeout(() => { odo.remove(); sr.remove(); after.remove(); node.nodeValue = text; }, 2800);
  });
}

let pending = false;
try { pending = sessionStorage.getItem(FLAG) === "1"; sessionStorage.removeItem(FLAG); } catch { /* ignore */ }
if (pending) countUp(document.getElementById("today"));

// Phones: the date in Daily summary opens the calendar (the date input lies over it; a change
// loads that day through htmx). showPicker() opens it on browsers where a click alone doesn't.
document.addEventListener("click", (e) => {
  const input = e.target.closest("[data-day-pick]");
  if (input && typeof input.showPicker === "function") {
    try { input.showPicker(); } catch { /* already open or not allowed: the native tap works */ }
  }
});

// Phones: each meal in Food diary folds to one row. A tap on the row or the chevron opens it.
// Open meals stay open when the day body is swapped (adding or editing a food). On larger
// screens the chevron is hidden and nothing folds.
const openMeals = new Set();
const dayKey = () => document.querySelector("#today [data-day-pick]")?.value || "";

document.addEventListener("click", (e) => {
  let fold = e.target.closest("[data-meal-fold]");
  if (!fold) {
    const head = e.target.closest("#today .meal-head");
    if (!head || e.target.closest("a, button, summary, input, label")) return;
    fold = head.querySelector("[data-meal-fold]");
  }
  if (!fold || !fold.offsetParent) return;
  const meal = fold.closest(".meal");
  const open = !meal.classList.contains("is-open");
  meal.classList.toggle("is-open", open);
  fold.setAttribute("aria-expanded", String(open));
  const key = `${dayKey()}:${meal.dataset.meal}`;
  if (open) openMeals.add(key); else openMeals.delete(key);
});

document.addEventListener("htmx:afterSwap", () => {
  document.querySelectorAll("#today .meal[data-meal]").forEach((meal) => {
    if (!openMeals.has(`${dayKey()}:${meal.dataset.meal}`)) return;
    meal.classList.add("is-open");
    meal.querySelector("[data-meal-fold]")?.setAttribute("aria-expanded", "true");
  });
});
