// Foodo Score helpers (no inline code: the CSP allows only scripts from this site).
// 1. The alternatives card asks the server for products listed where the device is: its time
//    zone (e.g. "Europe/London") is added to that request. Nothing else is sent, no prompt.
// 2. The marker on the 0-100 bar is placed from data-value through the CSSOM (allowed by the
//    CSP, unlike style attributes).

function timeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    return "";
  }
}

document.addEventListener("htmx:configRequest", (event) => {
  const path = event.detail.path || "";
  if (/\/p\/[^/]+\/alternatives\/?$/.test(path.split("?")[0])) {
    const tz = timeZone();
    if (tz) event.detail.parameters.tz = tz;
  }
});

function placeMarkers(root) {
  root.querySelectorAll(".foodo-meter[data-value]").forEach((meter) => {
    const value = Number(meter.dataset.value);
    if (Number.isFinite(value)) meter.style.setProperty("--foodo", String(Math.max(0, Math.min(100, value))));
  });
}

placeMarkers(document);
document.addEventListener("htmx:afterSwap", (event) => placeMarkers(event.target));

// The no-script link to the full alternatives page also carries the time zone.
document.querySelectorAll("a[data-alternatives-link]").forEach((link) => {
  const tz = timeZone();
  if (tz) link.href = `${link.pathname}?tz=${encodeURIComponent(tz)}`;
});
