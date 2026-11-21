// Settings → Daily targets → "Calculate automatically": opens the calculator in a dialog.
// Without JavaScript the button is a link to the same calculator as a page.
const dialog = () => document.getElementById("targets-dialog");

document.addEventListener("click", (event) => {
  const opener = event.target.closest("[data-targets-open]");
  const box = dialog();
  if (opener && box && typeof box.showModal === "function" && window.htmx) {
    event.preventDefault();
    box.querySelector("#targets-dialog-body").replaceChildren();
    box.showModal();
    window.htmx.ajax("GET", opener.dataset.targetsOpen, {
      target: "#targets-dialog-body",
      swap: "innerHTML",
    });
    return;
  }
  if (box?.open && event.target.closest("#targets-dialog [data-targets-close]")) {
    event.preventDefault();
    box.close();
  } else if (event.target === box) {
    box.close(); // click on the backdrop
  }
});
