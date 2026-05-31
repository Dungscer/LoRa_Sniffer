import asyncio
import io
import json
import queue
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import config
import db
import logger
import pcap

_BASE = Path(__file__).parent

app = FastAPI(title="RTL-SDR Listener")
app.mount("/static", StaticFiles(directory=_BASE / "static"), name="static")
templates = Jinja2Templates(directory=_BASE / "templates")

# Shared FFT queue injected at startup from main.py
_fft_queue: queue.Queue = queue.Queue(maxsize=64)
_ws_clients: list[WebSocket] = []
# Per-client lock to prevent concurrent writes (avoids AssertionError in websockets drain)
_ws_locks: dict[int, asyncio.Lock] = {}


def set_fft_queue(q: queue.Queue) -> None:
    global _fft_queue
    _fft_queue = q


# ── Middleware ────────────────────────────────────────────────────────────────
@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    response = await call_next(request)
    ts = datetime.now(timezone.utc).isoformat()
    ip = request.client.host if request.client else "-"
    logger.log_web(ts, ip, request.method, str(request.url.path), response.status_code)
    return response


# ── Pages ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/frames", response_class=HTMLResponse)
async def frames_page(request: Request):
    return templates.TemplateResponse(request=request, name="frames.html")


# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/ws/spectrum")
async def ws_spectrum(websocket: WebSocket):
    await websocket.accept()
    ts = datetime.now(timezone.utc).isoformat()
    ip = websocket.client.host if websocket.client else "-"
    logger.get_audit_logger().info(f"{ts} [WS] {ip} connected")
    _ws_clients.append(websocket)
    _ws_locks[id(websocket)] = asyncio.Lock()
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)
        _ws_locks.pop(id(websocket), None)
        logger.get_audit_logger().info(
            f"{datetime.now(timezone.utc).isoformat()} [WS] {ip} disconnected"
        )


async def broadcast_fft(item: dict[str, Any]) -> None:
    if not _ws_clients:
        return
    payload = json.dumps(
        {
            "type": "fft",
            "power_db": item["power_db"].tolist(),
            "center_mhz": item["center_mhz"],
            "peak_freq_mhz": item["peak_freq_mhz"],
            "peak_rssi_dbm": item["peak_rssi_dbm"],
            "timestamp": item["timestamp"],
        }
    )
    dead: list[WebSocket] = []
    for ws in list(_ws_clients):
        lock = _ws_locks.get(id(ws))
        try:
            if lock:
                async with lock:
                    await ws.send_text(payload)
            else:
                await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
        _ws_locks.pop(id(ws), None)


async def broadcast_packet(frame: dict[str, Any]) -> None:
    """Push a decoded LoRa frame to all connected WebSocket clients."""
    if not _ws_clients:
        return
    payload = json.dumps({"type": "frame", "frame": frame})
    dead: list[WebSocket] = []
    for ws in list(_ws_clients):
        lock = _ws_locks.get(id(ws))
        try:
            if lock:
                async with lock:
                    await ws.send_text(payload)
            else:
                await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
        _ws_locks.pop(id(ws), None)


# ── REST API ──────────────────────────────────────────────────────────────────
@app.get("/api/frames")
async def api_frames(
    since: str | None = None,
    until: str | None = None,
    freq_min: float | None = None,
    freq_max: float | None = None,
    min_rssi: float | None = None,
    sort: str = "timestamp",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
):
    rows, total = db.get_frames(
        since=since,
        until=until,
        freq_min=freq_min,
        freq_max=freq_max,
        min_rssi=min_rssi,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    return {"total": total, "offset": offset, "limit": limit, "frames": rows}


@app.get("/api/export")
async def api_export(
    since: str | None = None,
    until: str | None = None,
    freq_min: float | None = None,
    freq_max: float | None = None,
    min_rssi: float | None = None,
):
    frames = db.export_frames_json(
        since=since,
        until=until,
        freq_min=freq_min,
        freq_max=freq_max,
        min_rssi=min_rssi,
    )
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("frames.json", json.dumps(frames, indent=2))
        pcap_buf = io.BytesIO()
        pcap.write_pcap(frames, pcap_buf)
        zf.writestr("frames.pcap", pcap_buf.getvalue())
    zip_buf.seek(0)

    _log_export("ZIP", ts_str, len(frames))
    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="export_{ts_str}.zip"'},
    )


@app.get("/api/export/pcap")
async def api_export_pcap(
    since: str | None = None,
    until: str | None = None,
    freq_min: float | None = None,
    freq_max: float | None = None,
    min_rssi: float | None = None,
):
    frames = db.export_frames_json(
        since=since,
        until=until,
        freq_min=freq_min,
        freq_max=freq_max,
        min_rssi=min_rssi,
    )
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    pcap_buf = io.BytesIO()
    pcap.write_pcap(frames, pcap_buf)
    pcap_buf.seek(0)

    _log_export("PCAP", ts_str, len(frames))
    return StreamingResponse(
        pcap_buf,
        media_type="application/vnd.tcpdump.pcap",
        headers={"Content-Disposition": f'attachment; filename="frames_{ts_str}.pcap"'},
    )


def _log_export(fmt: str, ts_str: str, count: int) -> None:
    logger.get_audit_logger().info(f"{ts_str} [EXPORT] format={fmt} rows={count}")


# ── Settings API ──────────────────────────────────────────────────────────────

class _SettingsIn(BaseModel):
    app_key: str = ""


@app.get("/api/settings")
async def api_settings_get():
    return {"app_key": config.APP_KEY}


@app.post("/api/settings")
async def api_settings_post(body: _SettingsIn):
    key = body.app_key.strip().lower()
    # Validate: must be empty or exactly 32 hex characters
    if key and (len(key) != 32 or not all(c in "0123456789abcdef" for c in key)):
        return JSONResponse(
            status_code=400,
            content={"error": "app_key must be a 32-character hex string (16 bytes) or empty"},
        )
    config.APP_KEY = key
    # Persist to settings.json
    try:
        config.SETTINGS_PATH.write_text(json.dumps({"app_key": key}))
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to persist settings: {e}"},
        )
    return {"app_key": config.APP_KEY, "saved": True}
