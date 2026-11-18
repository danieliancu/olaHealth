// Homepage magazine carousel: an endless loop that slides forward through the opener pages.
// A clone of the first page sits after the last one; once the track has slid onto it, it
// jumps back to the real first page without animation, so the motion never runs backwards.
// It only runs where the carousel is visible (desktop); it pauses while a control has
// keyboard focus, while the tab is hidden and under reduced motion.

const root = document.querySelector("[data-mag]");
if (root) init(root);

function init(root) {
  const track = root.querySelector(".mag-track");
  const slides = [...root.querySelectorAll("[data-mag-slide]")];
  const dotsBox = root.querySelector("[data-mag-dots]");
  const dots = [...dotsBox.querySelectorAll("button")];
  if (slides.length < 2) return;

  const INTERVAL_MS = 5000;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let position = 0; // 0..slides.length (slides.length = the clone of the first page)
  let timer = null;
  let paused = false;

  const clone = slides[0].cloneNode(true);
  clone.removeAttribute("data-mag-slide");
  clone.setAttribute("aria-hidden", "true");
  clone.setAttribute("inert", "");
  clone.querySelectorAll("img").forEach((img) => img.setAttribute("alt", ""));
  track.append(clone);

  dotsBox.hidden = false;

  function move(to, animate = true) {
    position = to;
    track.style.transition = animate ? "" : "none";
    track.style.transform = `translateX(${-100 * position}%)`;
    if (!animate) void track.offsetWidth; // apply the jump before transitions return
    const active = position % slides.length;
    slides.forEach((s, n) => s.toggleAttribute("inert", n !== active));
    dots.forEach((d, n) => (n === active ? d.setAttribute("aria-current", "true") : d.removeAttribute("aria-current")));
  }

  track.addEventListener("transitionend", (event) => {
    if (event.target === track && position === slides.length) move(0, false);
  });

  function schedule() {
    clearTimeout(timer);
    if (paused || reduceMotion.matches || !root.offsetParent) return;
    timer = setTimeout(() => {
      if (position === slides.length) move(0, false); // a jump that missed its transitionend
      move(position + 1);
      schedule();
    }, INTERVAL_MS);
  }

  dots.forEach((d, n) =>
    d.addEventListener("click", () => {
      if (position === slides.length) move(0, false);
      move(n);
      schedule();
    }),
  );

  root.addEventListener("focusin", (event) => {
    if (event.target.matches(":focus-visible")) {
      paused = true;
      clearTimeout(timer);
    }
  });
  root.addEventListener("focusout", () => {
    paused = false;
    schedule();
  });
  document.addEventListener("visibilitychange", () => (document.hidden ? clearTimeout(timer) : schedule()));
  window.matchMedia("(min-width: 768px)").addEventListener("change", schedule);
  reduceMotion.addEventListener("change", schedule);
  move(0, false);
  schedule();
}
