// Staff → Magazine → Cover: the desktop preview (in the page) and the phone preview (the cover
// page in a frame) follow the photo, the month and the sliders before anything is saved.
const preview = document.querySelector("[data-cover-preview]");
if (preview) {
  const phone = document.querySelector("[data-cover-phone]");
  const vars = { h: "--photo-h", x: "--photo-x", y: "--photo-y", mz: "--m-zoom", mx: "--m-x", my: "--m-y" };
  // A 3:2 photo over the whole 3:2 cover, and over the whole 3:2 phone card.
  const defaults = { h: 100, x: -75, y: 0, mz: 100, mx: 0, my: 0 };
  const groups = { desktop: ["h", "x", "y"], phone: ["mz", "mx", "my"] };
  const sliders = [...document.querySelectorAll("input[data-photo]")];
  let photoUrl = "";
  let monthText = "";

  // Both previews: the in-page one and, once loaded, the framed page.
  const roots = () => {
    const list = [preview];
    try {
      if (phone?.contentDocument?.body) list.push(phone.contentDocument);
    } catch {
      /* not loaded yet */
    }
    return list;
  };
  const setVar = (key, value) =>
    roots().forEach((r) => r.querySelector(".mag-cover")?.style.setProperty(vars[key], value));
  const apply = (input) => {
    setVar(input.dataset.photo, input.value);
    const out = document.querySelector(`[data-photo-out="${input.dataset.photo}"]`);
    if (out) out.textContent = input.value;
  };
  const setPhoto = () =>
    roots().forEach((r) => {
      if (!photoUrl) return;
      r.querySelectorAll("[data-cover-photo]").forEach((img) => {
        img.removeAttribute("srcset");
        img.src = photoUrl;
      });
    });
  const setMonth = () =>
    roots().forEach((r) => {
      const el = r.querySelector("[data-cover-month]");
      if (monthText && el) el.textContent = monthText;
    });
  const refresh = () => {
    sliders.forEach(apply);
    setPhoto();
    setMonth();
  };

  // The desktop cover is drawn at 1440 × 960 and scaled to the preview box.
  const inner = preview.querySelector(".mag-preview-inner");
  const fit = () => inner?.style.setProperty("--scale", String(preview.clientWidth / 1440));
  fit();
  if ("ResizeObserver" in window) new ResizeObserver(fit).observe(preview);

  sliders.forEach((s) => s.addEventListener("input", () => apply(s)));
  phone?.addEventListener("load", refresh);
  refresh();

  document.querySelectorAll("[data-photo-reset]").forEach((btn) =>
    btn.addEventListener("click", () => {
      sliders
        .filter((s) => groups[btn.dataset.photoReset].includes(s.dataset.photo))
        .forEach((s) => {
          s.value = defaults[s.dataset.photo];
          apply(s);
        });
    }),
  );
  document.querySelector('input[type="file"][name="image"]')?.addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    photoUrl = URL.createObjectURL(file);
    setPhoto();
  });
  document.querySelector('input[name="month"]')?.addEventListener("input", (e) => {
    const [y, m] = (e.target.value || "").split("-").map(Number);
    if (!y || !m) return;
    monthText = `${new Date(y, m - 1, 1).toLocaleString("en-GB", { month: "long" })} ${y}`;
    setMonth();
  });

  // Full screen: the desktop preview and its sliders together.
  const stage = document.querySelector("[data-cover-stage]");
  const full = document.querySelector("[data-cover-fullscreen]");
  if (!stage?.requestFullscreen) full?.setAttribute("hidden", "");
  full?.addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else stage.requestFullscreen();
  });
  document.addEventListener("fullscreenchange", () => {
    if (full) full.textContent = document.fullscreenElement ? "Exit full screen" : "Full screen";
  });
}
