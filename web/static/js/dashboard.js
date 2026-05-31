const tbody = document.getElementById("frame-tbody");

const MTYPE_CLASS = {
  "JoinRequest":    "mtype-join-req",
  "JoinAccept":     "mtype-join-acc",
  "UnconfDataUp":   "mtype-data-up",
  "ConfDataUp":     "mtype-data-up",
  "UnconfDataDown": "mtype-data-down",
  "ConfDataDown":   "mtype-data-down",
};

function parsedFields(row) {
  if (!row.parsed_json) return {};
  try { return JSON.parse(row.parsed_json); } catch { return {}; }
}

function renderRows(frames) {
  tbody.innerHTML = "";
  frames.forEach(row => {
    const p       = parsedFields(row);
    const mtype   = p.MType || "–";
    const addr    = p.DevAddr ? p.DevAddr
                  : p.DevEUI  ? p.DevEUI
                  : "–";
    const fcnt    = p.FCnt != null ? p.FCnt : "–";
    const hexSnip = row.raw_hex.length > 24
      ? row.raw_hex.slice(0, 24) + "…"
      : row.raw_hex;
    const mtypeClass = MTYPE_CLASS[mtype] || "";
    const isInvalid  = !!p.error;

    const tr = document.createElement("tr");
    if (isInvalid) tr.classList.add("row-invalid");
    tr.innerHTML = `
      <td>${row.timestamp}</td>
      <td>${row.frequency_mhz.toFixed(3)}</td>
      <td>${row.rssi_dbm.toFixed(1)}</td>
      <td>${isInvalid
        ? `<span class="badge mtype-invalid" title="${p.error}">&#9888; ${mtype}</span>`
        : `<span class="badge ${mtypeClass}">${mtype}</span>`}</td>
      <td class="mono">${addr}</td>
      <td>${fcnt}</td>
      <td class="hex">${hexSnip}</td>
    `;
    tbody.appendChild(tr);
  });
}

// ── WebSocket live feed (separate connection from waterfall.js) ───────────────
let _dashWs = null;

function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  _dashWs = new WebSocket(`${proto}://${location.host}/ws/spectrum`);
  _dashWs.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "frame") prependFrame(msg.frame);
    } catch (_) {}
  };
  _dashWs.onclose = () => setTimeout(connectWS, 3000);
}

function prependFrame(f) {
  const p       = f;
  const mtype   = p.MType || "–";
  const addr    = p.DevAddr ? p.DevAddr : p.DevEUI ? p.DevEUI : "–";
  const fcnt    = p.FCnt != null ? p.FCnt : "–";
  const rawHex  = (p.payload_hex || p.raw_hex || "");
  const hexSnip = rawHex.length > 24 ? rawHex.slice(0, 24) + "…" : rawHex;
  const mtypeClass = MTYPE_CLASS[mtype] || "";
  const isInvalid  = !!p.error;

  const tr = document.createElement("tr");
  if (isInvalid) tr.classList.add("row-invalid");
  tr.innerHTML = `
    <td>${p.timestamp || "–"}</td>
    <td>${(p.freq_mhz || 0).toFixed(3)}</td>
    <td>${(p.rssi_dbm || 0).toFixed(1)}</td>
    <td>${isInvalid
      ? `<span class="badge mtype-invalid" title="${p.error}">&#9888; ${mtype}</span>`
      : `<span class="badge ${mtypeClass}">${mtype}</span>`}</td>
    <td class="mono">${addr}</td>
    <td>${fcnt}</td>
    <td class="hex">${hexSnip}</td>
  `;
  tbody.insertBefore(tr, tbody.firstChild);
  // Keep at most 50 rows
  while (tbody.rows.length > 50) tbody.deleteRow(tbody.rows.length - 1);
}

// ── Initial fetch ─────────────────────────────────────────────────────────────
async function fetchFrames() {
  try {
    const res = await fetch("/api/frames?limit=20&sort=timestamp&order=desc");
    const data = await res.json();
    const frames = data.frames || [];
    renderRows(frames);
    const ageEl = document.getElementById("frame-age");
    if (ageEl && frames.length) {
      const diffS = Math.round((Date.now() - new Date(frames[0].timestamp).getTime()) / 1000);
      ageEl.textContent = diffS < 60 ? `· last ${diffS}s ago` : `· last ${Math.round(diffS/60)}m ago`;
    }
  } catch (_) {}
}

fetchFrames();
setInterval(fetchFrames, 3000);
connectWS();

// ── Settings form ─────────────────────────────────────────────────────────────
const settingsForm = document.getElementById("settings-form");
const appKeyInput  = document.getElementById("app-key-input");
const settingsMsg  = document.getElementById("settings-msg");

async function loadSettings() {
  try {
    const res  = await fetch("/api/settings");
    const data = await res.json();
    if (appKeyInput) appKeyInput.value = data.app_key || "";
  } catch (_) {}
}

if (settingsForm) {
  loadSettings();
  settingsForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const key = (appKeyInput ? appKeyInput.value : "").trim();
    try {
      const res  = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app_key: key }),
      });
      const data = await res.json();
      if (res.ok) {
        if (settingsMsg) {
          settingsMsg.textContent = "Saved.";
          setTimeout(() => { settingsMsg.textContent = ""; }, 2000);
        }
      } else {
        if (settingsMsg) settingsMsg.textContent = data.error || "Error saving.";
      }
    } catch (e) {
      if (settingsMsg) settingsMsg.textContent = "Network error.";
    }
  });
}
