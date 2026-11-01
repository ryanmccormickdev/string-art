(() => {
  const el = (id) => document.getElementById(id);

  const imageInput = el("imageInput");
  const imagePreview = el("imagePreview");
  const generateBtn = el("generateBtn");
  const statusEl = el("status");
  const exportSection = el("exportSection");
  const placeholder = el("placeholder");
  const canvas = el("buildCanvas");
  const ctx = canvas.getContext("2d");
  const targetImg = el("targetImg");
  const simulationImg = el("simulationImg");
  const transport = el("transport");
  const playBtn = el("playBtn");
  const scrubber = el("scrubber");
  const lineCounter = el("lineCounter");
  const speedSlider = el("speedSlider");
  const metaEl = el("meta");
  const threadColorInput = el("threadColor");
  const bgColorInput = el("bgColor");

  let uploadedImage = null;   // server-side filename
  let currentRunId = null;
  let path = [];
  let nNails = 0;
  let alpha = 0.3;
  let threadColor = threadColorInput.value;
  let bgColor = bgColorInput.value;
  let nailXY = [];            // unit-circle positions, filled on generate
  let drawnUpTo = -1;         // last index actually painted on the canvas
  let playing = false;
  let rafId = null;
  let lastTick = 0;
  let carry = 0;              // fractional line carry between frames

  // Color is purely cosmetic -- repaint immediately without re-solving.
  threadColorInput.addEventListener("input", () => {
    threadColor = threadColorInput.value;
    if (path.length) redrawUpTo(drawnUpTo);
  });
  bgColorInput.addEventListener("input", () => {
    bgColor = bgColorInput.value;
    if (path.length) redrawUpTo(drawnUpTo);
  });

  // ---------------------------------------------------------------- upload

  imageInput.addEventListener("change", async () => {
    const file = imageInput.files[0];
    if (!file) return;
    imagePreview.src = URL.createObjectURL(file);
    imagePreview.hidden = false;

    const fd = new FormData();
    fd.append("image", file);
    setStatus("uploading...");
    try {
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "upload failed");
      uploadedImage = data.image;
      setStatus("");
    } catch (e) {
      setStatus(e.message, true);
    }
  });

  // -------------------------------------------------------------- generate

  function paramValue(id) {
    const node = el(id);
    if (node.type === "checkbox") return node.checked;
    return node.value;
  }

  function collectParams() {
    return {
      image: uploadedImage,
      nails: paramValue("nails"),
      diameter: paramValue("diameter"),
      thread_mm: paramValue("thread_mm"),
      opacity: paramValue("opacity"),
      weight: paramValue("weight"),
      res: paramValue("res"),
      max_lines: paramValue("max_lines"),
      min_gap: paramValue("min_gap"),
      contrast: paramValue("contrast"),
      gamma: paramValue("gamma"),
      start_nail: paramValue("start_nail"),
      allow_repeat_pairs: paramValue("allow_repeat_pairs"),
      invert: paramValue("invert"),
      no_autocontrast: paramValue("no_autocontrast"),
    };
  }

  function setStatus(msg, isError = false) {
    statusEl.textContent = msg;
    statusEl.classList.toggle("error", isError);
  }

  generateBtn.addEventListener("click", async () => {
    if (!uploadedImage) {
      setStatus("pick a photo first", true);
      return;
    }
    stopPlayback();
    generateBtn.disabled = true;
    setStatus("solving... this can take a while at high resolution");

    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collectParams()),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "generation failed");
      onGenerated(data);
      setStatus(`done in ${data.elapsed}s`);
    } catch (e) {
      setStatus(e.message, true);
    } finally {
      generateBtn.disabled = false;
    }
  });

  function onGenerated(data) {
    currentRunId = data.run_id;
    path = data.path;
    nNails = data.n_nails;
    alpha = Math.min(0.6, Math.max(0.12, data.meta.weight * 0.7));
    nailXY = computeNailPositions(nNails);

    targetImg.src = data.target_url;
    simulationImg.src = data.simulation_url;

    placeholder.hidden = true;
    exportSection.hidden = false;
    transport.hidden = false;

    scrubber.max = String(path.length - 1);
    scrubber.value = String(path.length - 1);
    drawnUpTo = -1;
    redrawUpTo(path.length - 1);

    const m = data.meta;
    metaEl.innerHTML = `
      <span><b>${m.n_lines}</b> lines</span>
      <span><b>${m.thread_yards.toFixed(0)}</b> yd thread</span>
      <span><b>${(m.mean_coverage * 100).toFixed(0)}%</b> coverage</span>
      <span><b>${m.weight.toFixed(3)}</b> ink/line</span>
      ${m.max_lines_hit ? '<span style="color:#e0a060">hit max-lines cap</span>' : ""}
    `;

    setView(currentView);
  }

  // --------------------------------------------------------------- canvas

  function computeNailPositions(n) {
    // Matches nail_positions() in stringart.py: nail 0 at 12 o'clock,
    // numbering clockwise. Unit circle here; scaled to canvas at draw time.
    const pts = [];
    for (let i = 0; i < n; i++) {
      const theta = (2 * Math.PI * i) / n;
      pts.push([Math.sin(theta), -Math.cos(theta)]);
    }
    return pts;
  }

  function layout() {
    const w = canvas.width, h = canvas.height;
    const cx = w / 2, cy = h / 2;
    const r = Math.min(w, h) / 2 - 14;
    return { cx, cy, r };
  }

  function toCanvas([ux, uy]) {
    const { cx, cy, r } = layout();
    return [cx + ux * r, cy + uy * r];
  }

  function drawNails() {
    const { cx, cy, r } = layout();
    ctx.save();
    ctx.strokeStyle = "#ddd";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, r + 4, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = "#999";
    for (const p of nailXY) {
      const [x, y] = toCanvas(p);
      ctx.beginPath();
      ctx.arc(x, y, 1.4, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  function clearCanvas() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = bgColor;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }

  function drawSegment(i) {
    // segment from path[i] -> path[i+1]
    const a = nailXY[path[i]];
    const b = nailXY[path[i + 1]];
    if (!a || !b) return;
    const [ax, ay] = toCanvas(a);
    const [bx, by] = toCanvas(b);
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
    ctx.stroke();
  }

  function redrawUpTo(k) {
    clearCanvas();
    drawNails();
    ctx.save();
    ctx.strokeStyle = threadColor;
    ctx.globalAlpha = alpha;
    ctx.lineWidth = 1;
    for (let i = 0; i < k; i++) drawSegment(i);
    ctx.restore();
    drawnUpTo = k;
    updateCounter(k);
  }

  function advanceTo(k) {
    if (k <= drawnUpTo) { redrawUpTo(k); return; }
    ctx.save();
    ctx.strokeStyle = threadColor;
    ctx.globalAlpha = alpha;
    ctx.lineWidth = 1;
    for (let i = drawnUpTo; i < k; i++) drawSegment(i);
    ctx.restore();
    drawnUpTo = k;
    updateCounter(k);
  }

  function updateCounter(k) {
    const total = path.length - 1;
    lineCounter.textContent = `${k} / ${total}`;
    scrubber.value = String(k);
  }

  // ------------------------------------------------------------ transport

  scrubber.addEventListener("input", () => {
    stopPlayback();
    advanceTo(parseInt(scrubber.value, 10));
  });

  playBtn.addEventListener("click", () => {
    if (playing) stopPlayback();
    else startPlayback();
  });

  function startPlayback() {
    if (!path.length) return;
    const total = path.length - 1;
    if (drawnUpTo >= total) redrawUpTo(0);
    playing = true;
    playBtn.innerHTML = "&#10074;&#10074;";
    lastTick = performance.now();
    carry = 0;
    rafId = requestAnimationFrame(tick);
  }

  function stopPlayback() {
    playing = false;
    playBtn.innerHTML = "&#9654;";
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
  }

  function tick(now) {
    if (!playing) return;
    const dt = (now - lastTick) / 1000;
    lastTick = now;
    const linesPerSec = parseFloat(speedSlider.value);
    carry += dt * linesPerSec;
    const step = Math.floor(carry);
    if (step > 0) {
      carry -= step;
      const total = path.length - 1;
      const next = Math.min(total, drawnUpTo + step);
      advanceTo(next);
      if (next >= total) {
        stopPlayback();
        return;
      }
    }
    rafId = requestAnimationFrame(tick);
  }

  // -------------------------------------------------------------- tabs

  let currentView = "build";
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });

  function setView(view) {
    currentView = view;
    document.querySelectorAll(".tab").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === view));
    canvas.hidden = view !== "build";
    targetImg.hidden = view !== "target";
    simulationImg.hidden = view !== "simulation";
    transport.hidden = view !== "build" || !path.length;
  }

  // ------------------------------------------------------------- export

  document.querySelectorAll("#exportSection button[data-fmt]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!currentRunId) return;
      const color = encodeURIComponent(threadColor);
      const bg = encodeURIComponent(bgColor);
      window.location = `/api/export/${currentRunId}/${btn.dataset.fmt}?color=${color}&bg=${bg}`;
    });
  });
})();
