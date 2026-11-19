// Styled confirmation (native <dialog>) for `hx-confirm` (htmx:confirm) and `form[data-confirm]`.
// Without JavaScript the form is simply submitted; every confirmed action is reversible or
// shows its own server-side confirmation.
// Optional attributes: data-confirm-title, data-confirm-label, data-confirm-tone="danger".

document.documentElement.classList.add("js");

export function confirmDialog({ title, message = "", confirmLabel, tone = "default" } = {}) {
  const dialog = document.querySelector("[data-dialog]");
  if (!dialog || typeof dialog.showModal !== "function") {
    return Promise.resolve(window.confirm([title, message].filter(Boolean).join("\n\n")));
  }
  const danger = tone === "danger";
  const confirmBtn = dialog.querySelector("[data-dialog-confirm]");
  const cancelBtn = dialog.querySelector("[data-dialog-cancel]");
  const text = dialog.querySelector("[data-dialog-message]");
  dialog.querySelector("[data-dialog-title]").textContent = title || dialog.dataset.defaultTitle;
  text.textContent = message;
  text.hidden = !message;
  confirmBtn.textContent = confirmLabel || dialog.dataset.defaultConfirm;
  confirmBtn.classList.toggle("btn-danger", danger);
  confirmBtn.classList.toggle("btn-primary", !danger);
  dialog.classList.toggle("is-danger", danger);
  dialog.querySelector("[data-dialog-icon]").hidden = !danger;
  const opener = document.activeElement;
  dialog.returnValue = "";
  dialog.showModal();
  (danger ? cancelBtn : confirmBtn).focus(); // safer default for destructive actions
  return new Promise((resolve) => {
    dialog.addEventListener(
      "close",
      () => {
        opener?.focus?.();
        resolve(dialog.returnValue === "confirm");
      },
      { once: true }
    );
  });
}

const options = (el, message) => ({
  title: el.dataset.confirmTitle,
  message,
  confirmLabel: el.dataset.confirmLabel,
  tone: el.dataset.confirmTone,
});

// Click on the backdrop = cancel.
document.addEventListener("click", (event) => {
  if (event.target.matches?.("[data-dialog]")) event.target.close("cancel");
});

// htmx: hx-confirm="Message" uses this dialog instead of the browser's confirm box.
document.addEventListener("htmx:confirm", (event) => {
  const question = event.detail.question;
  if (!question) return;
  event.preventDefault();
  confirmDialog(options(event.detail.elt, question)).then((ok) => ok && event.detail.issueRequest(true));
});

// Plain forms: <form data-confirm="Message">.
document.addEventListener(
  "submit",
  (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-confirm")) return;
    if (form.dataset.confirmed === "1") return;
    event.preventDefault();
    confirmDialog(options(form, form.dataset.confirm)).then((ok) => {
      if (!ok) return;
      form.dataset.confirmed = "1";
      form.requestSubmit(event.submitter || undefined);
    });
  },
  true
);
