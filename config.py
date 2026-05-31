from pathlib import Path

# ── RTL-SDR v4 ────────────────────────────────────────────────────────────────
CENTER_FREQ_MHZ: float = 868.1
SAMPLE_RATE: int = 2_048_000
FFT_SIZE: int = 1024
GAIN_DB: float = 32.8
DEVICE_INDEX: int = 0

# ── Waterfall ─────────────────────────────────────────────────────────────────
WATERFALL_DEPTH: int = 200  # number of rows visible in the diagram
WATERFALL_VMIN: float = -120.0  # dBm floor
WATERFALL_VMAX: float = -40.0  # dBm ceiling
SNAPSHOT_INTERVAL_S: float = 5.0  # how often to persist an FFT snapshot to DB

# ── EU868 channel centres (MHz) ───────────────────────────────────────────────
# All 8 EU868 uplink channels; all fall within the 2 MHz RTL-SDR capture window
# centered at 868.1 MHz (867.076 – 869.124 MHz).
EU868_CHANNELS: list[float] = [
    868.1,
    868.3,
    868.5,
    867.1,
    867.3,
    867.5,
    867.7,
    867.9,
]

# ── Web server ────────────────────────────────────────────────────────────────
WEB_HOST: str = "0.0.0.0"
WEB_PORT: int = 8000

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR: Path = Path(__file__).parent
LOG_DIR: Path = BASE_DIR / "logs"
DB_PATH: Path = BASE_DIR / "data" / "listener.db"

# ── Decryption ────────────────────────────────────────────────────────────────
# 32-hex-char string (= 16 bytes).  Empty = no decryption.
# Loaded from settings.json at startup; can be changed at runtime via /api/settings.
APP_KEY: str = ""
SETTINGS_PATH: Path = BASE_DIR / "settings.json"


def _load_settings() -> None:
    import json as _json
    if SETTINGS_PATH.exists():
        try:
            data = _json.loads(SETTINGS_PATH.read_text())
            global APP_KEY
            if "app_key" in data and isinstance(data["app_key"], str):
                APP_KEY = data["app_key"]
        except Exception:
            pass


_load_settings()
