// Staff panel on phones and tablets: the hamburger button opens and closes the menu column.
// Escape or a tap outside closes it. On desktop the menu is always shown (CSS).
const button = document.querySelector("[data-staff-menu]");
const nav = document.getElementById("staff-nav");

function setOpen(open) {
  if (!button || !nav) return;
  nav.classList.toggle("is-open", open);
  button.setAttribute("aria-expanded", String(open));
}

button?.addEventListener("click", () => setOpen(!nav.classList.contains("is-open")));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && nav?.classList.contains("is-open")) { setOpen(false); button.focus(); }
});
document.addEventListener("click", (e) => {
  if (nav?.classList.contains("is-open") && !nav.contains(e.target) && !button.contains(e.target)) setOpen(false);
});
