// Ask Foodo: open the full-screen dialog and keep it above the on-screen keyboard.
// Browser APIs only (dialog.showModal, visualViewport); the conversation itself is
// loaded and sent by the server through htmx. Sizes go through the CSSOM (allowed by the CSP).

function fitToViewport(dialog) {
  if (!dialog || !dialog.open) return;
  const vv = window.visualViewport;
  dialog.style.setProperty("--chat-h", `${Math.round(vv ? vv.height : window.innerHeight)}px`);
  dialog.style.setProperty("--chat-top", `${Math.round(vv ? vv.offsetTop : 0)}px`);
  const log = dialog.querySelector(".chat-log");
  if (log) log.scrollTop = log.scrollHeight; // scroll the messages, never the page
}

function openChat(dialog) {
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
  const loader = dialog.querySelector(".chat-loader");
  if (loader && window.htmx) window.htmx.trigger(loader, "chat-open");
  fitToViewport(dialog);
  // Phones: the keyboard opens only when the user taps the field.
  if (window.matchMedia("(pointer: fine)").matches) dialog.querySelector("input[name=text]")?.focus();
}

document.addEventListener("click", (e) => {
  const opener = e.target.closest("[data-chat-open]");
  if (opener) {
    const dialog = document.getElementById(opener.dataset.chatOpen);
    if (dialog) {
      e.preventDefault();
      openChat(dialog);
    }
    return;
  }
  if (e.target.closest("[data-chat-close]")) e.target.closest("dialog")?.close();
  else if (e.target.matches?.("dialog.chat-dialog")) e.target.close(); // click on the backdrop
});

// Full Ask Foodo page: the same idea without a dialog. The page is fixed to the visible
// viewport; with the on-screen keyboard open (the visible area shrinks) the tab bar and the site
// header step aside and the conversation keeps exactly the space above the keyboard.
let fullHeight = 0;
let lastWidth = window.innerWidth;
function fitChatPage() {
  const page = document.querySelector("[data-chat-page]");
  if (!page) return;
  const root = document.documentElement;
  const vv = window.visualViewport;
  const height = vv ? vv.height : window.innerHeight;
  // The tallest visible area seen so far is the "no keyboard" height (Android may shrink
  // innerHeight too, so it cannot be the reference).
  fullHeight = Math.max(fullHeight, height, window.innerHeight * 0.5);
  if (window.innerWidth !== lastWidth) { fullHeight = height; lastWidth = window.innerWidth; } // rotation
  const keyboard = fullHeight - height > 120;
  const header = document.querySelector(".site-header");
  root.style.setProperty("--vv-h", `${Math.round(height)}px`);
  root.style.setProperty("--vv-top", `${Math.round(vv ? vv.offsetTop : 0)}px`);
  root.style.setProperty("--header-h", `${header ? header.offsetHeight : 0}px`);
  document.body.classList.toggle("kb-open", Boolean(keyboard && document.activeElement?.matches("input, textarea")));
  const log = page.querySelector(".chat-log");
  if (log) log.scrollTop = log.scrollHeight;
}

const refit = () => {
  document.querySelectorAll("dialog.chat-dialog[open]").forEach(fitToViewport);
  fitChatPage();
};
fitChatPage();
document.addEventListener("focusin", () => setTimeout(fitChatPage, 50));
document.addEventListener("focusout", () => setTimeout(fitChatPage, 50));
window.visualViewport?.addEventListener("resize", refit);
window.visualViewport?.addEventListener("scroll", refit);
window.addEventListener("resize", refit);
document.addEventListener("htmx:afterSettle", (e) => {
  fitChatPage();
  const dialog = e.target.closest?.("dialog.chat-dialog");
  if (dialog) {
    fitToViewport(dialog);
    if (window.matchMedia("(pointer: fine)").matches) dialog.querySelector("input[name=text]")?.focus();
  }
});

// Day button (edit entry): show the chosen day on the button and close the calendar.
document.addEventListener("change", (e) => {
  const input = e.target;
  if (!input.matches?.(".day-drop .cal input[type=radio]")) return;
  const drop = input.closest(".day-drop");
  const value = drop.querySelector("[data-day-value]");
  if (value && input.dataset.dayLabel) value.textContent = input.dataset.dayLabel;
  drop.open = false;
});

// htmx errors (429 limit, 403, 500, network): htmx does not swap error responses, so say it.
function showError(text) {
  const box = document.getElementById("messages");
  if (!box) return;
  const p = document.createElement("p");
  p.className = "msg msg-error";
  p.setAttribute("role", "alert");
  p.textContent = text;
  box.replaceChildren(p);
  box.scrollIntoView({ block: "nearest" });
}
document.addEventListener("htmx:responseError", (e) => {
  const status = e.detail.xhr?.status;
  if (status === 429) showError("Too many requests. Please wait a moment and try again.");
  else if (status === 403) showError("This action was refused. Reload the page and try again.");
  else showError("Something went wrong. Please try again.");
});
document.addEventListener("htmx:sendError", () => showError("No connection. Check your internet and try again."));

// Food diary: the green "+" in each meal header opens that meal's Quick add form (the
// <details> under the meal). Without JavaScript the "Quick add" summary stays as before.
function armQuickAdd() {
  const buttons = document.querySelectorAll("[data-quick-toggle]");
  if (!buttons.length) return;
  document.documentElement.classList.add("js-quick");
  buttons.forEach((b) => (b.hidden = false));
}
armQuickAdd();
document.addEventListener("htmx:afterSettle", armQuickAdd);
document.addEventListener("click", (e) => {
  const button = e.target.closest?.("[data-quick-toggle]");
  if (!button) return;
  const details = document.getElementById(button.dataset.quickToggle);
  if (!details) return;
  details.open = !details.open;
  button.setAttribute("aria-expanded", String(details.open));
  if (details.open) details.querySelector("input:not([type=hidden])")?.focus();
});

// Notifications: links go straight to what they are about; the notification is marked read in
// the background (keepalive survives the navigation). CSRF token from <body hx-headers>.
document.addEventListener("click", (e) => {
  const link = e.target.closest?.("a[data-notif-read]");
  if (!link) return;
  let token = "";
  try { token = JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRFToken"] || ""; } catch { /* no token */ }
  fetch(link.dataset.notifRead, { method: "POST", keepalive: true, credentials: "same-origin", headers: { "X-CSRFToken": token } });
});

// Ask Foodo: the question appears in the conversation as soon as it is sent, followed by
// "Foodo is typing" dots until the server's answer arrives (it then replaces both with the
// real messages). On an error the question goes back into the field.
let chatSubmitter = null;
document.addEventListener("click", (e) => {
  const button = e.target.closest?.("form.chat-send button[type=submit]");
  if (button) chatSubmitter = button;
});

function chatBubble(cls, html) {
  const li = document.createElement("li");
  li.className = `chat-msg ${cls} is-pending`;
  li.innerHTML = html;
  return li;
}

document.addEventListener("htmx:beforeRequest", (e) => {
  const form = e.detail.elt;
  if (!form.matches?.("form.chat-send")) return;
  const input = form.querySelector("input[name=text]");
  const chip = chatSubmitter && chatSubmitter.matches(".chip-btn") ? chatSubmitter : null;
  chatSubmitter = null;
  const text = chip ? chip.textContent.trim() : (input?.value || "").trim();
  if (!text) return;
  const log = document.querySelector(form.getAttribute("hx-target"));
  if (!log) return;
  log.querySelector(".chat-welcome")?.remove();
  const mine = chatBubble("chat-user", "<p></p>");
  mine.querySelector("p").textContent = text; // the user's own words, as text only
  const avatar = log.closest(".chat")?.querySelector(".chat-avatar")?.outerHTML || "";
  const typing = chatBubble(
    "chat-foodo chat-typing",
    `${avatar}<p aria-label="Foodo is typing"><span></span><span></span><span></span></p>`,
  );
  log.append(mine, typing);
  form.dataset.pendingText = chip ? "" : text;
  if (input && !chip) input.value = "";
  log.scrollTop = log.scrollHeight;
});

function clearPending(form, restore) {
  const log = document.querySelector(form.getAttribute("hx-target"));
  log?.querySelectorAll(".is-pending").forEach((li) => li.remove());
  const input = form.querySelector("input[name=text]");
  if (restore && input && form.dataset.pendingText && !input.value) input.value = form.dataset.pendingText;
  delete form.dataset.pendingText;
}
document.addEventListener("htmx:beforeSwap", (e) => {
  if (e.detail.requestConfig?.elt?.matches?.("form.chat-send")) clearPending(e.detail.requestConfig.elt, !e.detail.shouldSwap);
});
["htmx:responseError", "htmx:sendError"].forEach((name) =>
  document.addEventListener(name, (e) => {
    const form = e.detail.requestConfig?.elt || e.detail.elt;
    if (form?.matches?.("form.chat-send")) clearPending(form, true);
  }),
);
