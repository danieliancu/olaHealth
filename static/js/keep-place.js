// After an in-place htmx update, the page stays where it was: the card you used keeps its spot on
// screen, and fold-out panels (<details>) that were open stay open. Without this, a panel that
// closes on swap (Water "Other amount", Activity "Log activity", product notes…) shrinks the page
// and it jumps, often to the bottom. Swaps that choose their own scroll (show:/scroll:) are left alone.

let saved = null;

const keyOf = (el) =>
  el.id ? `#${CSS.escape(el.id)}` : `[aria-labelledby="${el.getAttribute("aria-labelledby")}"]`;

const summaryText = (d) => d.querySelector(":scope > summary")?.textContent.trim() || "";

document.addEventListener("htmx:beforeSwap", (e) => {
  saved = null;
  const elt = e.detail.requestConfig?.elt || e.detail.elt;
  const target = e.detail.target;
  if (!elt || !target || !e.detail.shouldSwap) return;
  const spec = elt.closest("[hx-swap]")?.getAttribute("hx-swap") || "";
  if (/\b(show|scroll):/.test(spec) || elt.closest("dialog")) return;
  const anchor = elt.isConnected
    ? elt.closest("section[aria-labelledby], [id]:not(form)")
    : null;
  saved = {
    anchor: anchor ? keyOf(anchor) : null,
    top: anchor ? anchor.getBoundingClientRect().top : 0,
    scrollY: window.scrollY,
    open: [...target.querySelectorAll("details[open]")].map(summaryText).filter(Boolean),
    rootKey: target.id ? `#${CSS.escape(target.id)}` : null,
  };
});

document.addEventListener("htmx:afterSettle", () => {
  if (!saved) return;
  const s = saved;
  saved = null;
  const root = (s.rootKey && document.querySelector(s.rootKey)) || document;
  if (s.open.length) {
    root.querySelectorAll("details:not([open])").forEach((d) => {
      if (s.open.includes(summaryText(d))) d.open = true;
    });
  }
  const anchor = s.anchor && document.querySelector(s.anchor);
  if (anchor) {
    const delta = anchor.getBoundingClientRect().top - s.top;
    if (Math.abs(delta) > 1) window.scrollBy({ top: delta, behavior: "instant" });
  } else if (Math.abs(window.scrollY - s.scrollY) > 1) {
    window.scrollTo({ top: s.scrollY, behavior: "instant" });
  }
});
