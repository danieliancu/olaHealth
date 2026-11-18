// Reusable confirmation dialog (native <dialog>: modal, focus kept inside, Esc cancels).
//
// 1) From code (import confirmDialog from this module):
//      if (await confirmDialog({ title, message, confirmLabel, tone: "danger" })) { ... }
//
// 2) Declaratively, on any <form> (no extra JS needed):
//      <form method="post" data-confirm="Message"
//            data-confirm-title="Delete product?" data-confirm-label="Delete"
//            data-confirm-tone="danger">
//    On confirm the form is submitted with confirmed=1. Without JavaScript the server
//    receives no "confirmed" field and shows its own confirmation page instead.

const DEFAULTS = {
  title: "Are you sure?",
  message: "",
  confirmLabel: "Confirm",
  cancelLabel: "Cancel",
  tone: "default", // "default" | "danger"
};

function dialogElement() {
  return document.querySelector("[data-dialog]");
}

export function confirmDialog(options = {}) {
  const opts = { ...DEFAULTS, ...options };
  const dialog = dialogElement();
  if (!dialog || typeof dialog.showModal !== "function") {
    // Very old browsers: fall back to the built-in confirm box.
    return Promise.resolve(window.confirm([opts.title, opts.message].filter(Boolean).join("\n\n")));
  }
  dialog.querySelector("[data-dialog-title]").textContent = opts.title;
  const message = dialog.querySelector("[data-dialog-message]");
  message.textContent = opts.message;
  message.hidden = !opts.message;
  const confirmBtn = dialog.querySelector("[data-dialog-confirm]");
  confirmBtn.textContent = opts.confirmLabel;
  confirmBtn.classList.toggle("btn-danger", opts.tone === "danger");
  confirmBtn.classList.toggle("btn-primary", opts.tone !== "danger");
  dialog.querySelector("[data-dialog-cancel]").textContent = opts.cancelLabel;
  dialog.classList.toggle("is-danger", opts.tone === "danger");
  dialog.querySelector("[data-dialog-icon]").hidden = opts.tone !== "danger";

  const opener = document.activeElement;
  dialog.returnValue = "";
  dialog.showModal();
  // Safer default for destructive actions: focus Cancel.
  (opts.tone === "danger" ? dialog.querySelector("[data-dialog-cancel]") : confirmBtn).focus();
  return new Promise((resolve) => {
    dialog.addEventListener(
      "close",
      () => {
        if (opener && typeof opener.focus === "function") opener.focus();
        resolve(dialog.returnValue === "confirm");
      },
      { once: true }
    );
  });
}

// Click on the backdrop = cancel.
document.addEventListener("click", (event) => {
  const dialog = dialogElement();
  if (dialog && event.target === dialog) dialog.close("cancel");
});

// Declarative forms. Capture phase: runs before other submit handlers (e.g. busy spinner).
document.addEventListener(
  "submit",
  async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-confirm")) return;
    if (form.dataset.confirmed === "1") return; // second pass, already confirmed
    event.preventDefault();
    event.stopImmediatePropagation();
    const ok = await confirmDialog({
      title: form.dataset.confirmTitle || DEFAULTS.title,
      message: form.dataset.confirm,
      confirmLabel: form.dataset.confirmLabel || DEFAULTS.confirmLabel,
      cancelLabel: form.dataset.cancelLabel || DEFAULTS.cancelLabel,
      tone: form.dataset.confirmTone || DEFAULTS.tone,
    });
    if (!ok) return;
    let flag = form.querySelector("input[name=confirmed]");
    if (!flag) {
      flag = document.createElement("input");
      flag.type = "hidden";
      flag.name = "confirmed";
      form.append(flag);
    }
    flag.value = "1";
    form.dataset.confirmed = "1";
    form.requestSubmit(event.submitter || undefined);
  },
  true
);
