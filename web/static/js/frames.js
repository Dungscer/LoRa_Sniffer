let currentSort = "timestamp";
let currentOrder = "desc";
let currentOffset = 0;
let currentLimit = 50;
let currentTotal = 0;

function getFilterParams() {
  const params = new URLSearchParams();
  const since = document.getElementById("f-since").value;
  const until = document.getElementById("f-until").value;
  const freqMin = document.getElementById("f-freq-min").value;
  const freqMax = document.getElementById("f-freq-max").value;
  const rssi = document.getElementById("f-rssi").value;
  if (since) params.set("since", since.replace("T", "T") + ":00");
  if (until) params.set("until", until.replace("T", "T") + ":00");
  if (freqMin) params.set("freq_min", freqMin);
  if (freqMax) params.set("freq_max", freqMax);
  if (rssi) params.set("min_rssi", rssi);
  return params;
}

function updateExportLinks() {
  const params = getFilterParams();
  document.getElementById("export-zip").href = "/api/export?" + params.toString();
  document.getElementById("export-pcap").href = "/api/export/pcap?" + params.toString();
}

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
  const tbody = document.getElementById("frame-tbody");
  tbody.innerHTML = "";
  frames.forEach(row => {
    const p = parsedFields(row);
    const mtype   = p.MType     || "–";
    const dir     = p.Direction ? (p.Direction === "uplink" ? "↑ Up" : "↓ Down")
                                : (mtype === "JoinRequest" ? "↑ Up"
                                :  mtype === "JoinAccept"  ? "↓ Down" : "–");
    const addr    = p.DevAddr   ? p.DevAddr
                  : p.DevEUI   ? p.DevEUI
                  : "–";
    const fcnt    = p.FCnt != null ? p.FCnt : "–";
    const sfcr    = p.sf  ? `SF${p.sf} / ${p.cr || "–"}` : "–";
    const snr     = p.snr_db != null ? p.snr_db.toFixed(1) : "–";
    const hexSnip = row.raw_hex.length > 20
      ? row.raw_hex.slice(0, 20) + "…"
      : row.raw_hex;
    const mtypeClass = MTYPE_CLASS[mtype] || "";
    const isInvalid  = !!p.error;

    const tr = document.createElement("tr");
    if (isInvalid) tr.classList.add("row-invalid");
    tr.innerHTML = `
      <td>${row.timestamp}</td>
      <td>${row.frequency_mhz.toFixed(3)}</td>
      <td>${row.rssi_dbm.toFixed(1)}</td>
      <td>${snr}</td>
      <td>${isInvalid
        ? `<span class="badge mtype-invalid" title="${p.error}">⚠ ${mtype}</span>`
        : `<span class="badge ${mtypeClass}">${mtype}</span>`}</td>
      <td>${dir}</td>
      <td class="mono">${addr}</td>
      <td>${fcnt}</td>
      <td>${sfcr}</td>
      <td class="hex">${hexSnip}</td>
    `;
    tr.addEventListener("click", () => toggleDetail(tr, row));
    tbody.appendChild(tr);
  });
}

function toggleDetail(tr, row) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("detail-row")) {
    next.remove();
    return;
  }
  const detailTr = document.createElement("tr");
  detailTr.className = "detail-row";
  const parsed = row.parsed_json
    ? JSON.stringify(JSON.parse(row.parsed_json), null, 2)
    : "No parsed data";
  detailTr.innerHTML = `<td colspan="10"><pre>${parsed}</pre></td>`;
  tr.after(detailTr);
}

function updateSortHeaders() {
  document.querySelectorAll("thead th[data-col]").forEach(th => {
    th.classList.remove("sort-asc", "sort-desc");
    if (th.dataset.col === currentSort) {
      th.classList.add(currentOrder === "asc" ? "sort-asc" : "sort-desc");
    }
  });
}

async function fetchFrames() {
  const params = getFilterParams();
  params.set("sort", currentSort);
  params.set("order", currentOrder);
  params.set("limit", currentLimit);
  params.set("offset", currentOffset);

  const res = await fetch("/api/frames?" + params.toString());
  const data = await res.json();
  currentTotal = data.total || 0;

  renderRows(data.frames || []);
  updateExportLinks();

  const pageNum = Math.floor(currentOffset / currentLimit) + 1;
  const pageCount = Math.max(1, Math.ceil(currentTotal / currentLimit));
  document.getElementById("result-count").textContent =
    `(${currentTotal} total)`;
  document.getElementById("page-info").textContent =
    `Page ${pageNum} / ${pageCount}`;
  document.getElementById("btn-prev").disabled = currentOffset === 0;
  document.getElementById("btn-next").disabled =
    currentOffset + currentLimit >= currentTotal;
}

function resetFilters() {
  document.getElementById("filter-form").reset();
  currentOffset = 0;
  fetchFrames();
}

document.getElementById("filter-form").addEventListener("submit", e => {
  e.preventDefault();
  currentOffset = 0;
  fetchFrames();
});

document.getElementById("btn-prev").addEventListener("click", () => {
  currentOffset = Math.max(0, currentOffset - currentLimit);
  fetchFrames();
});
document.getElementById("btn-next").addEventListener("click", () => {
  currentOffset += currentLimit;
  fetchFrames();
});
document.getElementById("page-size").addEventListener("change", e => {
  currentLimit = parseInt(e.target.value);
  currentOffset = 0;
  fetchFrames();
});

document.querySelectorAll("thead th[data-col]").forEach(th => {
  th.addEventListener("click", () => {
    if (currentSort === th.dataset.col) {
      currentOrder = currentOrder === "asc" ? "desc" : "asc";
    } else {
      currentSort = th.dataset.col;
      currentOrder = "desc";
    }
    currentOffset = 0;
    updateSortHeaders();
    fetchFrames();
  });
});

updateSortHeaders();
fetchFrames();
