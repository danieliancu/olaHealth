// Staff → Magazine → Cover: the desktop preview (in the page) and the phone preview (the cover
// page in a frame) follow the photo, the month and the sliders before anything is saved.
const preview = document.querySelector("[data-cover-preview]");
if (preview) {
  const phone = document.querySelector("[data-cover-phone]");
  const vars = { h: "--photo-h", x: "--photo-x", y: "--photo-y", mx: "--m-x", my: "--m-y", mz: "--m-zoom" };
  const defaults = { h: 133, x: -26, y: -7, mx: 50, my: 0, mz: 100 };
  const groups = { desktop: ["h", "x", "y"], phone: ["mx", "my", "mz"] };
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
      const img = r.querySelector("[data-cover-photo]");
      if (!photoUrl || !img) return;
      img.removeAttribute("srcset");
      img.src = photoUrl;
      r.querySelector(".mag-head")?.remove(); // the cut-out belongs to the built-in photo
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
