// Unread notifications in the browser itself: a red dot on the tab icon, "(n)" before the
// page title and, for the installed app, the app badge (Badging API, where supported).
// The page is rendered with the right state; this keeps it in step when the bell's count
// (#notif-count) changes without a reload (htmx).
const icon = document.querySelector("link[rel='icon'][data-alert]");
const plainTitle = document.title.replace(/^\(\d+\+?\)\s*/, "");

function unread(el) {
  if (!el || el.hidden) return 0;
  const text = el.textContent.trim();
  return text.endsWith("+") ? 10 : Number.parseInt(text, 10) || 0;
}

function show(n) {
  if (icon) icon.href = n ? icon.dataset.alert : icon.dataset.plain;
  document.title = n ? `(${n > 9 ? "9+" : n}) ${plainTitle}` : plainTitle;
  if ("setAppBadge" in navigator) {
    (n ? navigator.setAppBadge(n) : navigator.clearAppBadge()).catch(() => {});
  }
}

const start = Number.parseInt(document.body.dataset.notifUnread || "0", 10) || 0;
show(start);
new MutationObserver(() => {
  const el = document.getElementById("notif-count");
  if (el) show(unread(el));
}).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["hidden"] });
