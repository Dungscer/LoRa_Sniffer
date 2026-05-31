const canvas = document.getElementById("waterfall");
const ctx = canvas.getContext("2d");

const EU868_CHANNELS = [868.1, 868.3, 868.5, 867.1, 867.3, 867.5, 867.7, 867.9];

const MTYPE_RGB = {
  "JoinRequest":    [251, 146,  60],
  "JoinAccept":     [ 74, 222, 128],
  "UnconfDataUp":   [ 96, 165, 250],
  "ConfDataUp":     [ 96, 165, 250],
  "UnconfDataDown": [192, 132, 252],
  "ConfDataDown":   [192, 132, 252],
};

let centerMhz = 868.1;
let sampleRate = 2048000;
let fftSize = 1024;
let imageData = null;

function updateChannelLabels() {
  const labelsEl = document.getElementById("ch-labels");
  if (!labelsEl) return;
  const W = canvas.offsetWidth;
  const halfBwMhz = sampleRate / 2e6;
  const freqMin = centerMhz - halfBwMhz;
  const freqMax = centerMhz + halfBwMhz;
  labelsEl.innerHTML = "";
  EU868_CHANNELS.forEach(ch => {
    if (ch < freqMin || ch > freqMax) return;
    const pct = ((ch - freqMin) / (freqMax - freqMin)) * 100;
    const span = document.createElement("span");
    span.textContent = ch.toFixed(1);
    span.style.left = pct + "%";
    labelsEl.appendChild(span);
  });
}

function resize() {
  canvas.width = canvas.offsetWidth;
  canvas.height = canvas.offsetHeight;
  imageData = ctx.createImageData(canvas.width, 1);
  updateChannelLabels();
}
window.addEventListener("resize", resize);
resize();


// Throttle: only scroll one dark row every N FFT frames to avoid CPU waste
let _fftSkip = 0;
const FFT_SCROLL_EVERY = 8;  // scroll 1px per ~8 FFT updates ≈ ~4 rows/sec

function drawRow(_powerDb) {
  _fftSkip++;
  if (_fftSkip < FFT_SCROLL_EVERY) return;
  _fftSkip = 0;

  const W = canvas.width;
  const H = canvas.height - LABEL_H;  // usable area; label zone stays untouched
  if (!W || !H) return;

  // Shift existing image down by 1 pixel (time scroll)
  const existing = ctx.getImageData(0, 0, W, H - 1);
  ctx.putImageData(existing, 0, 1);

  // New row: pure black (packet events add all visible colour)
  const pixels = new Uint8ClampedArray(W * 4);
  ctx.putImageData(new ImageData(pixels, W, 1), 0, 0);

  // Redraw channel guide lines on top (text is handled by CSS overlay)
  const halfBwMhz = sampleRate / 2e6;
  const freqMin = centerMhz - halfBwMhz;
  const freqMax = centerMhz + halfBwMhz;
  ctx.strokeStyle = "rgba(0, 188, 212, 0.25)";
  ctx.lineWidth = 1;
  EU868_CHANNELS.forEach(ch => {
    if (ch < freqMin || ch > freqMax) return;
    const xPos = Math.round(((ch - freqMin) / (freqMax - freqMin)) * W);
    ctx.beginPath(); ctx.moveTo(xPos, 0); ctx.lineTo(xPos, H); ctx.stroke();
  });
}

function saveWaterfall() {
  canvas.toBlob(blob => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `waterfall_${Date.now()}.png`;
    a.click();
  });
}

const LABEL_H = 18;  // pixels reserved at bottom for CSS frequency labels

function drawPacketEvent(frame) {
  const W = canvas.width;
  const H = canvas.height - LABEL_H;  // usable area above label zone
  if (!W || !H) return;

  const halfBwMhz = sampleRate / 2e6;
  const freqMin = centerMhz - halfBwMhz;
  const freqMax = centerMhz + halfBwMhz;
  const freqMhz = frame.freq_mhz;
  const xCenter = ((freqMhz - freqMin) / (freqMax - freqMin)) * W;
  const bwPx    = Math.max(4, (0.125 / (freqMax - freqMin)) * W);
  const x0 = Math.max(0, Math.round(xCenter - bwPx / 2));
  const x1 = Math.min(W, Math.round(xCenter + bwPx / 2));

  const mtype = frame.MType || "Unknown";
  const [r, g, b] = MTYPE_RGB[mtype] || [160, 160, 160];

  // Draw 5 rows: bright pulse fading down (only within usable area)
  const fades = [1.0, 0.85, 0.6, 0.35, 0.15];
  fades.forEach(fade => {
    const existing = ctx.getImageData(0, 0, W, H - 1);
    ctx.putImageData(existing, 0, 1);
    const pixels = new Uint8ClampedArray(W * 4);
    for (let x = x0; x < x1; x++) {
      pixels[x * 4]     = Math.round(r * fade);
      pixels[x * 4 + 1] = Math.round(g * fade);
      pixels[x * 4 + 2] = Math.round(b * fade);
      pixels[x * 4 + 3] = 255;
    }
    ctx.putImageData(new ImageData(pixels, W, 1), 0, 0);
  });

  // Label at top of canvas
  const addr = frame.DevAddr || (frame.DevEUI ? frame.DevEUI.slice(-8) : "");
  const label = `${mtype}${addr ? " " + addr : ""}  SF${frame.sf}  ${frame.rssi_dbm?.toFixed(1)} dBm`;
  ctx.font = "10px monospace";
  ctx.fillStyle = `rgb(${r},${g},${b})`;
  const tx = Math.min(W - ctx.measureText(label).width - 4, Math.max(2, xCenter - 60));
  ctx.fillText(label, tx, 12);
}

// WebSocket connection
const statusBadge = document.getElementById("ws-status");
let ws;

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/spectrum`);

  ws.onopen = () => {
    statusBadge.textContent = "connected";
    statusBadge.className = "badge badge-ok";
  };

  ws.onmessage = e => {
    const msg = JSON.parse(e.data);
    const peakEl = document.getElementById("peak-info");

    if (msg.type === "frame") {
      drawPacketEvent(msg.frame);
      const pktEl = document.getElementById("last-packet-info");
      if (pktEl) {
        const f = msg.frame;
        const mtype = f.MType || "Packet";
        const addr  = f.DevAddr || (f.DevEUI ? f.DevEUI.slice(-8) : "");
        const [r, g, b] = MTYPE_RGB[mtype] || [160, 160, 160];
        pktEl.innerHTML =
          `<span style="color:rgb(${r},${g},${b})">${mtype}</span>` +
          `${addr ? " &middot; " + addr : ""}` +
          `  ${f.freq_mhz?.toFixed(3)} MHz` +
          `  ${f.rssi_dbm?.toFixed(1)} dBm  SF${f.sf}/${f.cr}`;
      }
      if (peakEl) peakEl.textContent = "";
    } else {
      // fft message
      centerMhz = msg.center_mhz;
      drawRow(msg.power_db);
      if (peakEl) {
        peakEl.textContent =
          `peak ${msg.peak_freq_mhz.toFixed(3)} MHz  ${msg.peak_rssi_dbm} dBm`;
      }
    }
  };

  ws.onclose = () => {
    statusBadge.textContent = "disconnected";
    statusBadge.className = "badge badge-warn";
    setTimeout(connectWs, 3000);
  };
}

connectWs();

document.addEventListener("keydown", e => { if (e.key === "s") saveWaterfall(); });
