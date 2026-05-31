import argparse
import asyncio
import json
import queue
import signal
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")

import config
import db
import logger
import lorawan_parser
from web.app import app, broadcast_fft, broadcast_packet, set_fft_queue


def _apply_windows_asyncio_fix() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def run_web(
    host: str,
    port: int,
    fft_queue: queue.Queue,
    lora_queue: queue.Queue,
    stop_event: threading.Event,
) -> None:
    import uvicorn

    async def _serve() -> None:
        cfg = uvicorn.Config(
            app, host=host, port=port, log_level="warning",
            ws_ping_interval=None,  # disable auto-pings (prevents concurrent drain AssertionError)
        )
        server = uvicorn.Server(cfg)
        await asyncio.gather(
            server.serve(),
            _fft_broadcaster(fft_queue, stop_event),
            _lora_handler(lora_queue, stop_event),
        )

    asyncio.run(_serve())


def run_local_waterfall(fft_queue: queue.Queue) -> None:
    from waterfall import WaterfallDisplay

    display = WaterfallDisplay(fft_queue)
    display.show()


async def _fft_broadcaster(fft_queue: queue.Queue, stop_event: threading.Event) -> None:
    loop = asyncio.get_event_loop()
    while not stop_event.is_set():
        try:
            item = await loop.run_in_executor(None, fft_queue.get, True, 0.1)
        except Exception:
            continue
        await broadcast_fft(item)
        db.insert_snapshot(item["timestamp"], item["center_mhz"], "")


def _frame_score(pkt, parsed: dict) -> int:
    """
    Quality score for a decoded frame — higher is better.
    Used to pick the best decode when multiple aliased channels
    produce different bytes for the same physical packet.
    """
    score = 0
    if pkt.has_crc:
        score += 4
    mtype = parsed.get("MType", "")
    n = len(pkt.payload)
    if mtype == "JoinRequest"  and n == 23: score += 8
    elif mtype == "JoinAccept" and n in (13, 33): score += 8
    elif mtype in ("UnconfDataUp","ConfDataUp","UnconfDataDown","ConfDataDown") and 8 <= n <= 250:
        score += 6
    elif mtype not in ("RFU", "Proprietary", "Unknown", ""): score += 2
    if not parsed.get("error"):
        score += 4
    return score


async def _lora_handler(lora_queue: queue.Queue, stop_event: threading.Event) -> None:
    loop = asyncio.get_event_loop()

    # RSSI-bucket buffer: collect all aliased decodes of the same physical
    # packet (same RSSI within 1s window), then store only the best-scoring one.
    # Bucket key = round(rssi_dbm, 1)
    # Bucket value = (best_score, best_pkt, best_parsed, first_seen_mono)
    _BUFFER_S = 0.2   # collect window: aliased frames arrive within ~64ms (2 SDR chunks)
    _DEDUP_S  = 5.0   # remember stored hex keys to avoid true duplicates
    _bucket: dict[float, tuple] = {}   # rssi_key → (score, pkt, parsed, first_seen)
    _seen_hex: dict[str, float] = {}   # hex → mono_time

    async def _commit(rssi_key: float) -> None:
        """Store and broadcast the best-scoring frame in the bucket."""
        entry = _bucket.pop(rssi_key, None)
        if entry is None:
            return
        _, best_pkt, best_parsed, _ = entry
        hex_key = best_pkt.payload.hex()
        if hex_key in _seen_hex:
            return
        _seen_hex[hex_key] = time.monotonic()
        parsed_str = json.dumps({**best_pkt.to_dict(), **best_parsed})
        db.insert_frame(best_pkt.timestamp, best_pkt.freq_mhz, best_pkt.rssi_dbm,
                        best_pkt.payload.hex(), parsed_str)
        logger.log_frame(best_pkt.timestamp, best_pkt.freq_mhz,
                         best_pkt.rssi_dbm, best_pkt.payload.hex())
        await broadcast_packet({**best_pkt.to_dict(), **best_parsed})
        mtype = best_parsed.get("MType", "?")
        print(f"\n[lora] {mtype} SF{best_pkt.sf} {best_pkt.freq_mhz:.3f} MHz  "
              f"rssi={best_pkt.rssi_dbm:.1f} dBm  {len(best_pkt.payload)}B  "
              f"{best_pkt.payload[:8].hex()}...")

    while not stop_event.is_set():
        # Try to get next packet (0.1s timeout so we can flush old buckets)
        pkt = None
        try:
            pkt = await loop.run_in_executor(None, lora_queue.get, True, 0.1)
        except Exception:
            pass

        now = time.monotonic()
        _seen_hex = {k: v for k, v in _seen_hex.items() if now - v < _DEDUP_S}

        if pkt is not None:
            app_key = bytes.fromhex(config.APP_KEY) if config.APP_KEY else None
            parsed  = lorawan_parser.parse(pkt.payload, app_key=app_key)
            score   = _frame_score(pkt, parsed)
            # Bucket key: same RSSI (±0.1 dBm) within time window = same physical packet.
            # RSSI is derived from the wideband peak power — identical for all aliased
            # channel decoders processing the same SDR chunk.
            rssi_key = round(pkt.rssi_dbm, 1)

            if rssi_key in _bucket:
                old_score, _, _, first_seen = _bucket[rssi_key]
                if score > old_score:
                    _bucket[rssi_key] = (score, pkt, parsed, first_seen)
                # else keep existing best (don't reset timer)
            else:
                _bucket[rssi_key] = (score, pkt, parsed, now)

        # Flush buckets whose collection window has expired
        for rssi_key in [k for k, v in _bucket.items() if now - v[3] >= _BUFFER_S]:
            await _commit(rssi_key)

    # Drain on shutdown
    for rssi_key in list(_bucket.keys()):
        await _commit(rssi_key)
        parsed_str = json.dumps({**pkt.to_dict(), **parsed})
        db.insert_frame(
            pkt.timestamp,
            pkt.freq_mhz,
            pkt.rssi_dbm,
            pkt.payload.hex(),
            parsed_str,
        )
        logger.log_frame(pkt.timestamp, pkt.freq_mhz, pkt.rssi_dbm, pkt.payload.hex())
        await broadcast_packet({**pkt.to_dict(), **parsed})
        print(
            f"\n[lora] SF{pkt.sf} {pkt.freq_mhz:.3f} MHz  "
            f"rssi={pkt.rssi_dbm:.1f} dBm  "
            f"{len(pkt.payload)}B  {pkt.payload[:8].hex()}..."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="RTL-SDR Listener")
    parser.add_argument("--freq", type=float, default=config.CENTER_FREQ_MHZ)
    parser.add_argument("--gain", type=float, default=config.GAIN_DB)
    parser.add_argument("--fft-size", type=int, default=config.FFT_SIZE)
    parser.add_argument("--depth", type=int, default=config.WATERFALL_DEPTH)
    parser.add_argument("--host", default=config.WEB_HOST)
    parser.add_argument("--port", type=int, default=config.WEB_PORT)
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Show local matplotlib waterfall instead of web server",
    )
    args = parser.parse_args()

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db.init_db()

    fft_queue:  queue.Queue = queue.Queue(maxsize=128)
    lora_queue: queue.Queue = queue.Queue(maxsize=64)
    set_fft_queue(fft_queue)

    from radio import SpectrumCapture

    capture = SpectrumCapture(
        fft_queue=fft_queue,
        lora_queue=lora_queue,
        center_mhz=args.freq,
        gain_db=args.gain,
        fft_size=args.fft_size,
    )

    stop_event = threading.Event()

    def _shutdown(sig, frame):
        print("\n[main] Stopping...")
        stop_event.set()
        capture.stop()   # closes SDR, joins radio thread (non-daemon → clean release)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    capture.start()
    print(
        f"[main] RTL-SDR Listener started — "
        f"{args.freq:.3f} MHz, FFT {args.fft_size}, "
        f"{'local waterfall' if args.no_web else f'web http://{args.host}:{args.port}'}"
    )

    if args.no_web:
        run_local_waterfall(fft_queue)
    else:
        _apply_windows_asyncio_fix()
        run_web(args.host, args.port, fft_queue, lora_queue, stop_event)


if __name__ == "__main__":
    main()
