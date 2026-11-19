// Camera barcode scanning in the browser. Frames never leave the device: only the
// decoded number is sent to /api/v1/lookup/.
// Decoding works on the area inside the on-screen frame, cropped from the full-resolution
// video and enlarged, alternating plain and contrast-boosted copies: a barcode on a real
// package is small, curved and glossy, and decodes far better at more pixels per bar.
// Native BarcodeDetector is tried first when it supports EAN/UPC; zxing-wasm decodes every
// frame the native detector misses (on some Android devices it is present but never finds
// anything) and is the only decoder where BarcodeDetector does not exist (iOS, desktop).

import { announce, spinner } from "./app.js";

const FORMATS_NATIVE = ["ean_13", "ean_8", "upc_a", "upc_e", "itf"];
const FORMATS_ZXING = ["EAN13", "EAN8", "UPCA", "UPCE", "ITF14"];
const SCAN_INTERVAL_MS = 120;
// EAN/UPC/ITF-14 carry a check digit that is verified below, so one valid read is enough.
const CONFIRMATIONS = 1;
const DUPLICATE_WINDOW_MS = 8000;

const root = document.querySelector("[data-scanner]");
if (root) init(root);

function init(root) {
  const video = root.querySelector("[data-video]");
  const idle = root.querySelector("[data-idle]");
  const startBtn = root.querySelector("[data-start]");
  const stopBtn = root.querySelector("[data-stop]");
  const statusEl = root.querySelector("[data-scan-status]");
  const resultEl = root.querySelector("[data-result]");
  const template = root.querySelector("[data-contribute-template]");
  const lookupUrl = root.dataset.lookupUrl;
  const viewfinder = root.querySelector(".viewfinder");
  const tools = root.querySelector("[data-tools]");
  const torchBtn = root.querySelector("[data-torch]");
  const zoomBtn = root.querySelector("[data-zoom]");
  const zoomLabel = root.querySelector("[data-zoom-label]");

  let stream = null;
  let detector = null; // { detect(video, region) -> Promise<{text, format}|null> }
  let camera = null; // { track, caps } for torch / zoom / focus
  let zoomSteps = [];
  let timer = null;
  let busy = false;
  let candidate = { text: "", count: 0 };
  let lastLookup = { text: "", at: 0 };

  // busy = true shows a small spinner next to the message (camera starting, lookup...).
  const setStatus = (msg, busy = false) => {
    statusEl.textContent = msg;
    if (busy) statusEl.prepend(spinner());
  };

  if (!window.isSecureContext) {
    setStatus("The camera needs a secure (HTTPS) connection. Type the barcode or use a photo instead.");
    startBtn.disabled = true;
    return;
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setStatus("This browser cannot use the camera. Type the barcode or use a photo instead.");
    startBtn.disabled = true;
    return;
  }

  startBtn.addEventListener("click", start);
  torchBtn.addEventListener("click", async () => {
    const on = torchBtn.getAttribute("aria-pressed") !== "true";
    if (await applyCamera({ torch: on })) torchBtn.setAttribute("aria-pressed", String(on));
  });
  zoomBtn.addEventListener("click", async () => {
    const current = Number(zoomBtn.dataset.value) || zoomSteps[0];
    const next = zoomSteps[(zoomSteps.indexOf(current) + 1) % zoomSteps.length];
    if (await applyCamera({ zoom: next })) showZoom(next);
  });
  // Tap the picture to refocus (where the camera supports it).
  video.addEventListener("click", (event) => {
    if (!camera || !(camera.caps.focusMode || []).includes("single-shot")) return;
    const rect = video.getBoundingClientRect();
    const point = { x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height };
    applyCamera({ focusMode: "single-shot", pointsOfInterest: [point] }).then(() =>
      setTimeout(() => stream && applyCamera({ focusMode: "continuous" }), 1500),
    );
  });

  async function applyCamera(settings) {
    if (!camera || !camera.track) return false;
    try {
      await camera.track.applyConstraints({ advanced: [settings] });
      return true;
    } catch (err) {
      return false;
    }
  }

  function showZoom(value) {
    zoomBtn.dataset.value = String(value);
    zoomLabel.textContent = `${Number(value.toFixed(1))}×`;
  }

  // Continuous autofocus, a 2× start zoom (so the phone is held far enough away to focus)
  // and the light/zoom buttons, where the camera supports them.
  async function setupCamera() {
    const track = stream.getVideoTracks()[0];
    const caps = track && track.getCapabilities ? track.getCapabilities() : {};
    camera = { track, caps };
    if ((caps.focusMode || []).includes("continuous")) await applyCamera({ focusMode: "continuous" });
    zoomSteps = [];
    if (caps.zoom && caps.zoom.max >= 1.5) {
      const min = caps.zoom.min || 1;
      zoomSteps = [...new Set([1, 2, 3].map((z) => Math.min(Math.max(z, min), caps.zoom.max)))];
      const first = zoomSteps.includes(2) ? 2 : zoomSteps[zoomSteps.length - 1];
      if (await applyCamera({ zoom: first })) showZoom(first);
    }
    zoomBtn.hidden = zoomSteps.length < 2;
    torchBtn.hidden = !caps.torch;
    torchBtn.setAttribute("aria-pressed", "false");
    tools.hidden = zoomBtn.hidden && torchBtn.hidden;
  }

  // The part of the video that is on screen inside (a little beyond) the guide frame, in
  // video pixels. The video is shown with object-fit: cover.
  function scanRegion() {
    const vw = video.videoWidth;
    const vh = video.videoHeight;
    const cw = viewfinder.clientWidth;
    const ch = viewfinder.clientHeight;
    if (!vw || !vh || !cw || !ch) return null;
    const scale = Math.max(cw / vw, ch / vh);
    const shownW = cw / scale;
    const shownH = ch / scale;
    const x0 = (vw - shownW) / 2;
    const y0 = (vh - shownH) / 2;
    // the guide frame is inset 22% / 12%; take a wider band so long barcodes still fit
    return { x: x0 + shownW * 0.04, y: y0 + shownH * 0.16, w: shownW * 0.92, h: shownH * 0.68 };
  }
  stopBtn.addEventListener("click", () => stop("Camera stopped."));
  window.addEventListener("pagehide", () => stop());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") stop("Camera stopped because the page was hidden.");
  });

  async function start() {
    resultEl.replaceChildren();
    startBtn.disabled = true;
    setStatus("Starting camera…", true);
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      });
    } catch (err) {
      startBtn.disabled = false;
      setStatus(cameraErrorMessage(err));
      announce(statusEl.textContent);
      return;
    }
    video.srcObject = stream;
    video.hidden = false;
    idle.hidden = true;
    stopBtn.hidden = false;
    try {
      await video.play();
      await setupCamera();
      detector = detector || (await createDetector(root));
    } catch (err) {
      stop("The barcode reader could not start. Type the barcode or use a photo instead.");
      return;
    }
    candidate = { text: "", count: 0 };
    setStatus("Point the camera at the barcode.", true);
    announce("Camera on. Point it at the barcode.");
    loop();
  }

  function stop(message) {
    clearTimeout(timer);
    timer = null;
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
    }
    video.srcObject = null;
    video.hidden = true;
    idle.hidden = false;
    stopBtn.hidden = true;
    tools.hidden = true;
    camera = null;
    startBtn.disabled = false;
    if (message) setStatus(message);
  }

  function loop() {
    if (!stream) return;
    timer = setTimeout(async () => {
      if (!stream || busy || video.readyState < 2) return loop();
      try {
        const found = await detector.detect(video, scanRegion());
        if (found) await onCandidate(found);
      } catch (err) {
        // A single failed frame is not fatal; keep scanning.
      }
      loop();
    }, SCAN_INTERVAL_MS);
  }

  async function onCandidate(found) {
    if (!validCheckDigit(found.text)) return;
    if (found.text === candidate.text) candidate.count += 1;
    else candidate = { text: found.text, count: 1 };
    if (candidate.count < CONFIRMATIONS) return;
    const now = Date.now();
    if (found.text === lastLookup.text && now - lastLookup.at < DUPLICATE_WINDOW_MS) return;
    lastLookup = { text: found.text, at: now };
    stop();
    if (navigator.vibrate) navigator.vibrate(60);
    await lookup(found.text, found.format);
  }

  async function lookup(code, format, attempt = 0) {
    busy = true;
    setStatus(`Barcode ${code} read. Looking it up…`, true);
    announce(statusEl.textContent);
    let response;
    let body = {};
    try {
      const params = new URLSearchParams({ code, format });
      response = await fetch(`${lookupUrl}?${params}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      body = await response.json().catch(() => ({}));
    } catch (err) {
      busy = false;
      renderMessage(code, "Network problem. Check your connection and try again.");
      return;
    }
    busy = false;
    if (response.status === 202 && attempt < 5) {
      const wait = Math.min(Number(response.headers.get("Retry-After")) || 2, 5) * 1000;
      setTimeout(() => lookup(code, format, attempt + 1), wait);
      return;
    }
    if (body.status === "found" && body.product) return renderProduct(code, format, body);
    if (body.status === "not_found") return renderNotFound(code, format, body);
    renderMessage(code, body.message || "Lookup failed. Please try again.");
  }

  function contributeForm(problem, { product = "", code = "", format = "" }, label) {
    const form = template.cloneNode(true);
    form.hidden = false;
    form.removeAttribute("data-contribute-template");
    form.querySelector("[name=problem_type]").value = problem;
    form.querySelector("[name=product]").value = product;
    form.querySelector("[name=code]").value = code;
    form.querySelector("[name=format]").value = format;
    form.querySelector("button").textContent = label;
    return form;
  }

  function renderProduct(code, format, body) {
    const p = body.product;
    const card = el("div", "result-card");
    if (p.image_url) {
      const img = el("img", "product-photo");
      img.src = p.image_url;
      img.alt = "Front of the package";
      img.width = 96;
      img.height = 96;
      card.append(img);
    }
    const info = el("div");
    info.append(el("h3", "", p.name || "Name not available"));
    info.append(el("p", "meta", [p.brand, p.quantity].filter(Boolean).join(" · ") || "Brand not available"));
    if (body.stale) info.append(el("p", "hint", `Saved data from ${(body.synced_at || "").slice(0, 10)}; a refresh was requested.`));
    if (body.code && body.code.kind === "restricted")
      info.append(el("p", "tag tag-warn", "Store-internal code: it may identify different products in different shops."));
    // From the Food diary: go straight to "how much?" (the full product page stays one tap away).
    const diary = root.dataset.diaryQuery ? new URLSearchParams(root.dataset.diaryQuery) : null;
    const open = el("a", "btn btn-primary", diary ? "Add to diary" : "Open product");
    open.href = diary
      ? `/today/add/${p.id}/?${new URLSearchParams({ meal: diary.get("add"), date: diary.get("date") || "", scanned: code })}`
      : `${p.url}?scanned=${encodeURIComponent(code)}`;
    if (diary) window.location.assign(open.href);
    open.classList.add("btn-block", "open-product");
    card.append(info, open);
    resultEl.replaceChildren(card);
    setStatus(`Found: ${p.name || code}.`);
    announce(statusEl.textContent);
    revealCard(card);
    open.focus({ preventScroll: true });
  }

  function renderNotFound(code, format, body) {
    const card = el("div", "result-card");
    const info = el("div");
    info.append(el("h3", "", "Product not found"));
    info.append(el("p", "", body.message || "This product is not in our database yet."));
    info.append(contributeForm("new_product", { code, format }, "Add this product"));
    card.append(info);
    resultEl.replaceChildren(card);
    revealCard(card);
    setStatus(`Barcode ${code}: product not found.`);
    announce(statusEl.textContent);
  }

  function renderMessage(code, message) {
    const card = el("div", "result-card");
    card.append(el("p", "error", message));
    const retry = el("button", "btn", "Try again");
    retry.type = "button";
    retry.addEventListener("click", () => lookup(code, "manual"));
    card.append(retry);
    resultEl.replaceChildren(card);
    setStatus(message);
    announce(message);
  }
}

// Scroll so the whole card, including its bottom button, sits above the tab bar.
function revealCard(card) {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  card.scrollIntoView({ block: "end", behavior: reduce ? "auto" : "smooth" });
}

function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function cameraErrorMessage(err) {
  const name = err && err.name;
  if (name === "NotAllowedError" || name === "SecurityError")
    return "Camera permission was refused. Allow camera access in your browser settings, or type the barcode.";
  if (name === "NotFoundError" || name === "OverconstrainedError" || name === "DevicesNotFoundError")
    return "No camera was found on this device. Type the barcode or use a photo instead.";
  if (name === "NotReadableError" || name === "TrackStartError")
    return "The camera is being used by another app. Close it and try again.";
  return "The camera could not be started. Type the barcode or use a photo instead.";
}

// GTIN check digit (EAN-8, UPC-A, EAN-13, ITF-14; UPC-E is accepted on its length).
function validCheckDigit(text) {
  if (!/^\d+$/.test(text)) return false;
  if (text.length === 6 || text.length === 7) return true;
  if (![8, 12, 13, 14].includes(text.length)) return false;
  const digits = [...text].map(Number);
  const check = digits.pop();
  const sum = digits.reverse().reduce((acc, d, i) => acc + d * (i % 2 === 0 ? 3 : 1), 0);
  return (10 - (sum % 10)) % 10 === check;
}

// Draws the scan region enlarged (long side ~1400 px), alternating a plain copy and a
// grey, contrast-boosted one (helps with glare and low-contrast print).
function regionPainter() {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  let boosted = false;
  return (video, region) => {
    const r = region || { x: 0, y: 0, w: video.videoWidth, h: video.videoHeight };
    if (!r.w || !r.h) return null;
    const scale = Math.min(3, Math.max(1, 1400 / Math.max(r.w, r.h)));
    canvas.width = Math.round(r.w * scale);
    canvas.height = Math.round(r.h * scale);
    boosted = !boosted;
    ctx.filter = boosted ? "grayscale(1) contrast(1.6)" : "none";
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(video, r.x, r.y, r.w, r.h, 0, 0, canvas.width, canvas.height);
    ctx.filter = "none";
    return { canvas, ctx };
  };
}

async function nativeDetector() {
  if (!("BarcodeDetector" in window)) return null;
  try {
    const supported = await window.BarcodeDetector.getSupportedFormats();
    const formats = FORMATS_NATIVE.filter((f) => supported.includes(f));
    if (!formats.includes("ean_13")) return null;
    const native = new window.BarcodeDetector({ formats });
    return async (source) => {
      const codes = await native.detect(source);
      return codes.length ? { text: codes[0].rawValue, format: codes[0].format } : null;
    };
  } catch (err) {
    return null;
  }
}

async function zxingDetector(root) {
  const zxing = await import(root.dataset.zxingUrl);
  const wasmUrl = root.dataset.zxingWasm;
  await zxing.prepareZXingModule({
    overrides: {
      locateFile: (path, prefix) => (path.endsWith(".wasm") ? wasmUrl : prefix + path),
    },
    fireImmediately: true,
  });
  let binarizer = "LocalAverage";
  return async ({ canvas, ctx }) => {
    // alternate binarizers: LocalAverage copes with uneven light, GlobalHistogram with blur
    binarizer = binarizer === "LocalAverage" ? "GlobalHistogram" : "LocalAverage";
    const results = await zxing.readBarcodes(ctx.getImageData(0, 0, canvas.width, canvas.height), {
      formats: FORMATS_ZXING,
      tryHarder: true,
      tryRotate: true,
      tryInvert: false,
      tryDownscale: true,
      binarizer,
      maxNumberOfSymbols: 1,
    });
    const ok = results.find((r) => r.isValid && r.text);
    return ok ? { text: ok.text, format: ok.format } : null;
  };
}

async function createDetector(root) {
  const native = await nativeDetector();
  const paint = regionPainter();
  let zxing = null;
  let zxingFailed = false;
  const getZxing = async () => {
    if (!zxing && !zxingFailed) {
      try {
        zxing = await zxingDetector(root);
      } catch (err) {
        zxingFailed = true;
      }
    }
    return zxing;
  };
  if (!native) await getZxing(); // the only decoder: a failure here is reported by start()
  if (!native && !zxing) throw new Error("no barcode decoder");
  if (native) getZxing(); // warm up in the background
  let tick = 0;
  return {
    async detect(video, region) {
      tick += 1;
      const crop = paint(video, region);
      if (!crop) return null;
      if (native) {
        try {
          // the enlarged crop every frame, the whole picture every third frame
          const found = (await native(crop.canvas)) || (tick % 3 === 0 ? await native(video) : null);
          if (found) return found;
        } catch (err) {
          // fall through to zxing
        }
      }
      const z = await getZxing();
      return z ? z(crop) : null;
    },
  };
}
