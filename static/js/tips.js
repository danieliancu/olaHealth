// Tips carousel (Today / Home): arrows and a gentle timer. The track holds the six tips twice,
// so moving from the 6th to the 1st tip is seamless. Positions go through a CSS custom property
// (CSSOM, allowed by the CSP). Pauses on hover and focus; no timer with reduced motion.

const COUNT = 6;
const DELAY = 5000;

function setup(section) {
  const track = section.querySelector(".tips-track");
  if (!track) return;
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let index = 0;
  let timer = null;
  section.classList.add("tips-js");
  section.querySelectorAll("[data-tips-prev], [data-tips-next], [data-tips-dots]").forEach((b) => (b.hidden = false));
  const dots = [...section.querySelectorAll("[data-tips-dot]")];
  const paint = () => dots.forEach((d, i) => d.setAttribute("aria-current", String(i === index % COUNT)));

  const place = (animate) => {
    track.classList.toggle("no-anim", !animate);
    track.style.setProperty("--tip-i", index);
    paint();
  };
  const go = (step) => {
    if (index + step < 0) {
      index = COUNT; // jump to the copy, then slide back
      place(false);
      void track.offsetWidth;
    }
    index += step;
    place(true);
  };
  track.addEventListener("transitionend", () => {
    if (index >= COUNT) {
      index -= COUNT;
      place(false);
    }
  });
  const stop = () => { clearInterval(timer); timer = null; };
  const start = () => { if (!still && !timer) timer = setInterval(() => go(1), DELAY); };

  section.querySelector("[data-tips-prev]")?.addEventListener("click", () => { go(-1); stop(); start(); });
  section.querySelector("[data-tips-next]")?.addEventListener("click", () => { go(1); stop(); start(); });
  // Swipe (phones have no arrows).
  let startX = null;
  section.addEventListener("touchstart", (e) => { startX = e.touches[0].clientX; stop(); }, { passive: true });
  section.addEventListener("touchend", (e) => {
    if (startX === null) return;
    const dx = e.changedTouches[0].clientX - startX;
    if (Math.abs(dx) > 40) go(dx < 0 ? 1 : -1);
    startX = null;
    start();
  });
  dots.forEach((d, i) => d.addEventListener("click", () => {
    index = i;
    place(true);
    stop();
    start();
  }));
  section.addEventListener("mouseenter", stop);
  section.addEventListener("mouseleave", start);
  section.addEventListener("focusin", stop);
  section.addEventListener("focusout", start);
  place(false);
  start();
}

const init = () => document.querySelectorAll("[data-tips]:not(.tips-js)").forEach(setup);
init();
document.addEventListener("htmx:afterSettle", init); // Today re-renders after each change
