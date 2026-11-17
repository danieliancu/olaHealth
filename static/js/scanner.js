// Camera barcode scanning in the browser. Frames never leave the device: only the
// decoded number is sent to /api/v1/lookup/.
// Native BarcodeDetector is used when it supports EAN/UPC; otherwise zxing-wasm.

import { announce } from "./app.js";

const FORMATS_NATIVE = ["ean_13", "ean_8", "upc_a", "upc_e", "itf"];
const FORMATS_ZXING = ["EAN13", "EAN8", "UPCA", "UPCE", "ITF14"];
const SCAN_INTERVAL_MS = 200;
const CONFIRMATIONS = 2; // same code read twice in a row before lookup
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

  let stream = null;
  let detector = null; // { detect(video, canvas) -> Promise<{text, format}|null> }
  let timer = null;
  let busy = false;
  let candidate = { text: "", count: 0 };
  let lastLookup = { text: "", at: 0 };

  const setStatus = (msg) => {
    statusEl.textContent = msg;
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
  stopBtn.addEventListener("click", () => stop("Camera stopped."));
  window.addEventListener("pagehide", () => stop());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") stop("Camera stopped because the page was hidden.");
  });

  async function start() {
    resultEl.replaceChildren();
    startBtn.disabled = true;
    setStatus("Starting camera…");
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
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
      detector = detector || (await createDetector(root));
    } catch (err) {
      stop("The barcode reader could not start. Type the barcode or use a photo instead.");
      return;
    }
    candidate = { text: "", count: 0 };
    setStatus("Point the camera at the barcode.");
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
    startBtn.disabled = false;
    if (message) setStatus(message);
  }

  function loop() {
    if (!stream) return;
    timer = setTimeout(async () => {
      if (!stream || busy || video.readyState < 2) return loop();
      try {
        const found = await detector.detect(video);
        if (found) await onCandidate(found);
      } catch (err) {
        // A single failed frame is not fatal; keep scanning.
      }
      loop();
    }, SCAN_INTERVAL_MS);
  }

  async function onCandidate(found) {
    if (!/^\d{6,14}$/.test(found.text)) return;
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
    setStatus(`Barcode ${code} read. Looking it up…`);
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
    const open = el("a", "btn btn-primary", "Open product");
    open.href = `${p.url}?scanned=${encodeURIComponent(code)}`;
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
    info.append(el("p", "", body.message || "This product is not in Open Food Facts or our catalogue yet."));
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

async function createDetector(root) {
  if ("BarcodeDetector" in window) {
    try {
      const supported = await window.BarcodeDetector.getSupportedFormats();
      const formats = FORMATS_NATIVE.filter((f) => supported.includes(f));
      if (formats.includes("ean_13")) {
        const native = new window.BarcodeDetector({ formats });
        return {
          async detect(video) {
            const codes = await native.detect(video);
            return codes.length ? { text: codes[0].rawValue, format: codes[0].format } : null;
          },
        };
      }
    } catch (err) {
      // fall through to zxing-wasm
    }
  }
  const zxing = await import(root.dataset.zxingUrl);
  const wasmUrl = root.dataset.zxingWasm;
  await zxing.prepareZXingModule({
    overrides: {
      locateFile: (path, prefix) => (path.endsWith(".wasm") ? wasmUrl : prefix + path),
    },
    fireImmediately: true,
  });
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  return {
    async detect(video) {
      const w = video.videoWidth;
      const h = video.videoHeight;
      if (!w || !h) return null;
      // Decode the central band where the user aims the barcode.
      const cropH = Math.round(h * 0.6);
      canvas.width = w;
      canvas.height = cropH;
      ctx.drawImage(video, 0, (h - cropH) / 2, w, cropH, 0, 0, w, cropH);
      const results = await zxing.readBarcodes(ctx.getImageData(0, 0, w, cropH), {
        formats: FORMATS_ZXING,
        tryHarder: true,
        maxNumberOfSymbols: 1,
      });
      const ok = results.find((r) => r.isValid && r.text);
      return ok ? { text: ok.text, format: ok.format } : null;
    },
  };
}
