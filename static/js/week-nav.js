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
