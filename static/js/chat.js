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

const refit = () => document.querySelectorAll("dialog.chat-dialog[open]").forEach(fitToViewport);
window.visualViewport?.addEventListener("resize", refit);
window.visualViewport?.addEventListener("scroll", refit);
window.addEventListener("resize", refit);
document.addEventListener("htmx:afterSettle", (e) => {
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
