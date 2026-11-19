// Home: example products slide by, and the diary card rolls its numbers to each product's
// values. Everything shown is in the HTML (data attributes); this only animates it.
// Pauses on mouse hover, keyboard focus or a hidden tab; with reduced motion it still changes, without animation.

const root = document.querySelector("[data-showcase]");

if (root) {
  const slides = [...root.querySelectorAll(".showcase-img")];
  const dots = [...root.querySelectorAll("[data-showcase-dots] button")];
  const field = (name) => root.querySelector(`[data-sc="${name}"]`);
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const KEYS = ["kcal", "protein", "carbs", "fat", "water", "activity"];
  let index = 0;
  let timer = null;
  let paused = false;

  const decimals = (text) => (String(text).split(".")[1] || "").length;
  const ease = (t) => 1 - Math.pow(1 - t, 3);

  // Rolls a number from its current value to the target, keeping the target's decimals.
  function roll(el, target) {
    const from = Number(el.textContent) || 0;
    const to = Number(target);
    const places = Math.max(decimals(target), decimals(el.textContent));
    if (reduce || from === to) {
      el.textContent = target;
      return;
    }
    el.classList.remove("is-rolling");
    void el.offsetWidth; // restart the CSS roll
    el.classList.add("is-rolling");
    const start = performance.now();
    const duration = 750;
    const step = (now) => {
      const t = Math.min(1, (now - start) / duration);
      el.textContent = (from + (to - from) * ease(t)).toFixed(places);
      if (t < 1) requestAnimationFrame(step);
      else el.textContent = target;
    };
    requestAnimationFrame(step);
  }

  function swapText(el, text) {
    if (reduce) return void (el.textContent = text);
    el.classList.add("is-leaving");
    window.setTimeout(() => {
      el.textContent = text;
      el.classList.remove("is-leaving");
    }, 220);
  }

  function show(next) {
    if (next === index) return;
    const prev = slides[index];
    index = (next + slides.length) % slides.length;
    const slide = slides[index];
    // Slide, no fade: the new product comes in from the right while the old one leaves left.
    slide.classList.add("no-anim");
    slide.classList.remove("is-leaving");
    void slide.offsetWidth; // start the new slide from the right, without animating there
    slide.classList.remove("no-anim");
    prev.classList.remove("is-active");
    prev.classList.add("is-leaving");
    window.setTimeout(() => prev.classList.remove("is-leaving"), 650);
    slide.classList.add("is-active");
    dots.forEach((d, i) => d.setAttribute("aria-pressed", String(i === index)));
    const d = slide.dataset;
    swapText(field("name"), d.name);
    swapText(field("meta"), d.meta);
    root.querySelectorAll("[data-meal-icon]").forEach((icon) => (icon.hidden = icon.dataset.mealIcon !== d.meal));
    KEYS.forEach((k) => roll(field(k), d[k]));
  }

  function schedule() {
    window.clearTimeout(timer);
    // With reduced motion it still changes, without the sliding and rolling animations.
    if (paused || document.hidden) return;
    timer = window.setTimeout(() => {
      show(index + 1);
      schedule();
    }, 3800);
  }

  dots.forEach((dot, i) =>
    dot.addEventListener("click", () => {
      show(i);
      schedule();
    })
  );
  const pause = (on) => {
    paused = on;
    schedule();
  };
  // Hover pauses with a mouse only: a tap on a phone must not stop the carousel.
  root.addEventListener("pointerenter", (e) => e.pointerType === "mouse" && pause(true));
  root.addEventListener("pointerleave", (e) => e.pointerType === "mouse" && pause(false));
  // Keyboard focus on the dots pauses it; a tap does not.
  root.addEventListener("focusin", (e) => e.target.matches(":focus-visible") && pause(true));
  root.addEventListener("focusout", () => pause(false));
  document.addEventListener("visibilitychange", schedule);
  // Swipe on touch screens.
  let x0 = null;
  root.addEventListener("touchstart", (e) => (x0 = e.touches[0].clientX), { passive: true });
  root.addEventListener("touchend", (e) => {
    if (x0 === null) return;
    const dx = e.changedTouches[0].clientX - x0;
    x0 = null;
    if (Math.abs(dx) > 40) {
      show(index + (dx < 0 ? 1 : -1));
      schedule();
    }
  });
  schedule();
}
