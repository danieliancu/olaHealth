// Scroll reveal: cards below the fold fade and rise in as they come into view (as in the
// Figma app). Only elements that start off-screen are hidden, so nothing flashes on load.
// Classes only (CSSOM-free); no effect with reduced motion or without IntersectionObserver.

const SELECTOR = [
  ".card", ".tile", ".qa", ".meal-tile", ".article-card", ".topic-card", ".stat-card",
  ".achievement", ".tips", ".memory", ".product-card", ".chart-card", ".nudge",
].join(",");

const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function arm(root = document) {
  if (still || !("IntersectionObserver" in window)) return;
  const fold = window.innerHeight;
  const items = [...root.querySelectorAll(SELECTOR)].filter(
    // Home (visitors and Today, both .today): everything is shown at once, no reveal on scroll.
    (el) => !el.classList.contains("reveal") && !el.closest("dialog, .tips-track, .today")
      && el.getBoundingClientRect().top > fold * 0.92,
  );
  items.forEach((el) => el.classList.add("reveal"));
  items.forEach((el) => observer.observe(el));
}

const observer = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
  let n = 0;
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    const el = entry.target;
    el.style.setProperty("--reveal-delay", `${Math.min(n++, 4) * 60}ms`);
    el.classList.add("is-in");
    observer.unobserve(el);
  }
}, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 }) : null;

arm();
document.addEventListener("htmx:afterSettle", (e) => arm(e.target));
