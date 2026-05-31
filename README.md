# RTL-SDR Listener

Passive RF spectrum monitor for LoRaWAN networks using an RTL-SDR v4 dongle.
Captures and visualises the EU868 frequency band in real time via a browser-based
waterfall diagram. Detected frames are stored in a local SQLite database and can be
exported as JSON or PCAP (Wireshark-compatible).

## Features

- **Live waterfall** — real-time spectrogram streamed over WebSocket to the browser
- **Frame browser** — filterable, sortable, paginated table of captured frames
- **EU868 channel markers** — 868.1 / 868.3 / 868.5 MHz (and others) annotated on the waterfall
- **SQLite storage** — every frame persisted with timestamp and sub-MHz frequency (`REAL` MHz)
- **Export** — ZIP archive containing `frames.json` + `frames.pcap`, or raw PCAP for direct Wireshark import
- **Dual-mode** — web dashboard (default) or local matplotlib waterfall (`--no-web`)
- **Cross-platform** — Linux and Windows supported

## Requirements

### Hardware

- RTL-SDR v4 (or compatible RTL2832U-based dongle)

### Software

- Python 3.10+
- [`uv`](https://github.com/astral-sh/uv) package manager

### System libraries

**Linux**
```bash
sudo apt install librtlsdr-dev rtl-sdr

# Allow non-root access to the dongle (one-time)
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666"' \
  | sudo tee /etc/udev/rules.d/20-rtlsdr.rules
sudo udevadm control --reload-rules
```

**Windows**
1. Install [Zadig](https://zadig.akeo.ie/) and select the RTL-SDR device, then install the **WinUSB** driver
2. Download `rtlsdr.dll` from the [rtlsdrblog releases](https://github.com/rtlsdrblog/rtl-sdr-blog/releases) and place it on `PATH`

## Installation

```bash
# Install uv (Linux)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install uv (Windows)
winget install astral-sh.uv

# Clone and install dependencies
git clone <repo-url>
cd RTL_SDR_Listener
uv sync
```

## Usage

```bash
# Start with web dashboard (default) — open http://localhost:8000
uv run main.py

# Custom frequency and gain
uv run main.py --freq 868.3 --gain 40.2

# Local matplotlib waterfall (no browser required)
uv run main.py --no-web

# All options
uv run main.py --help
```

| Flag         | Default   | Description                     |
|--------------|-----------|---------------------------------|
| `--freq`     | `868.1`   | Centre frequency in MHz         |
| `--gain`     | `32.8`    | RTL-SDR gain in dB              |
| `--fft-size` | `1024`    | FFT size (must be power of two) |
| `--depth`    | `200`     | Waterfall rows                  |
| `--host`     | `0.0.0.0` | Web server bind address         |
| `--port`     | `8000`    | Web server port                 |
| `--no-web`   | —         | Use local matplotlib waterfall  |

## Web interface

| Page           | URL       | Description                                |
|----------------|-----------|--------------------------------------------|
| Live dashboard | `/`       | Real-time waterfall + last 20 frames       |
| Frame browser  | `/frames` | Filter, sort, paginate all captured frames |

**Keyboard shortcut:** press `s` on the dashboard to save the waterfall as a PNG screenshot.

### Frame browser filterss

- **Timestamp range** — from / to datetime pickers
- **Frequency range** — `freq_min` / `freq_max` in MHz (e.g. `868.05` to `868.15`)
- **Min RSSI** — hide frames below a signal level threshold
- **Sortable columns** — click any column header to toggle asc/desc
- **Pagination** — 25 / 50 / 100 rows per page
- **Row detail** — click a row to expand the full parsed JSON inline

## Export

Two export formats are available from both pages:

| Button      | Endpoint               | Contents                                       |
|-------------|------------------------|------------------------------------------------|
| Export ZIP  | `GET /api/export`      | `frames.json` + `frames.pcap` in a ZIP archive |
| Export PCAP | `GET /api/export/pcap` | Raw PCAP file for direct Wireshark import      |

Both exports respect the currently active filters (timestamp, frequency, RSSI).

### Wireshark

Open the `.pcap` file in Wireshark. Frames are wrapped in UDP on port 1700
(standard LoRa packet forwarder port), which activates the built-in `lorawan`
dissector automatically. No configuration required.

Useful display filters:

```
lorawan                          # all LoRaWAN frames
lorawan.ftype == 0               # Join Requests only
lorawan.devaddr == 01020304      # filter by DevAddr
```

## Logs

| File             | Contents                                                     |
|------------------|--------------------------------------------------------------|
| `logs/sniff.log` | One line per captured frame: timestamp, frequency, RSSI, hex |
| `logs/audit.log` | One line per web request: IP, method, path, status code      |

Logs rotate daily and are kept for 7 days. Both files are created automatically on first run.

## Development

```bash
# Run tests (no dongle required)
uv run pytest

# Verbose
uv run pytest -v

# Format code
uv run black .
```

Test coverage: 53 tests across config, database, logger, PCAP writer, FFT math,
and all web endpoints. Hardware-dependent tests (RTL-SDR init) are skipped
automatically when no dongle is present.

## Project structure

```
RTL_SDR_Listener/
├── main.py         -- entry point, wires all components
├── config.py       -- constants and path settings
├── radio.py        -- RTL-SDR capture + FFT computation
├── db.py           -- SQLite schema, queries, export
├── logger.py       -- sniff.log + audit.log setup
├── pcap.py         -- PCAP file writer (stdlib only, no extra deps)
├── waterfall.py    -- local matplotlib waterfall (--no-web mode)
├── web/
│   ├── app.py      -- FastAPI application, REST + WebSocket
│   ├── templates/  -- Jinja2 HTML pages
│   └── static/     -- JS + CSS
├── tests/          -- pytest test suite
├── logs/           -- rotating log files (auto-created)
└── data/           -- SQLite database (auto-created)
```

## License

See the repository root for license information.
