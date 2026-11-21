// Staff icon picker: typing in the filter shows only the icons whose name matches.
document.addEventListener("input", (e) => {
  const box = e.target.closest("[data-icon-filter]");
  if (!box) return;
  const q = box.value.trim().toLowerCase();
  box.closest(".icon-picker").querySelectorAll("[data-icon-name]").forEach((opt) => {
    opt.hidden = q !== "" && !opt.dataset.iconName.includes(q);
  });
});
