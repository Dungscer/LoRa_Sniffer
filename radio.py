import fcntl
import glob
import queue
import sys
import threading
from datetime import datetime, timezone

import numpy as np

import config
from lora_demod import LoraMultiSFDecoder

try:
    from rtlsdr import RtlSdr
    from rtlsdr.librtlsdr import librtlsdr as _librtlsdr

    _RTL_AVAILABLE = True
except ImportError:
    _RTL_AVAILABLE = False
    _librtlsdr = None


def _get_device_count() -> int:
    try:
        return int(_librtlsdr.rtlsdr_get_device_count())
    except Exception:
        return 0

# Linux USB ioctl — sends a USB reset to the device (re-enumerates without physical unplug)
_USBDEVFS_RESET = 0x5514
_REALTEK_VENDOR  = "0bda"


def _find_rtlsdr_sysfs() -> str | None:
    """Return the sysfs device directory for the first Realtek RTL-SDR, or None."""
    for vpath in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        try:
            with open(vpath) as f:
                if f.read().strip().lower() == _REALTEK_VENDOR:
                    return str(vpath).replace("/idVendor", "")
        except Exception:
            pass
    return None


def _usb_reset_rtlsdr() -> bool:
    """Reset the RTL2838 dongle via ioctl USBDEVFS_RESET (no root needed, just device permission)."""
    dev_dir = _find_rtlsdr_sysfs()
    if not dev_dir:
        return False
    try:
        bus = int(open(f"{dev_dir}/busnum").read())
        dev = int(open(f"{dev_dir}/devnum").read())
        usb_path = f"/dev/bus/usb/{bus:03d}/{dev:03d}"
        with open(usb_path, "wb") as f:
            fcntl.ioctl(f, _USBDEVFS_RESET, 0)
        print(f"[radio] USB reset sent to {usb_path}", file=sys.stderr)
        return True
    except Exception:
        return False


def _disable_usb_autosuspend() -> None:
    """Disable USB autosuspend for the RTL-SDR (prevents kernel-driven disconnects)."""
    dev_dir = _find_rtlsdr_sysfs()
    if not dev_dir:
        return
    try:
        with open(f"{dev_dir}/power/autosuspend_delay_ms", "w") as f:
            f.write("-1")
        print("[radio] USB autosuspend disabled", file=sys.stderr)
    except Exception:
        pass

# Decimation factor: 2048000 / 8 = 256000 sps for LoRa demodulation.
# OSR ≈ 2.05 (256000 / 125000) — close enough for correct CSS demodulation.
# Decimation 4 → 512 000 sps, Nyquist ±256 kHz.
# This keeps all 3 mandatory EU868 channels (868.1/868.3/868.5 = 0/±200/±400 kHz)
# cleanly within the Nyquist limit.  Decim=8 (256 kHz) caused sideband folding for
# 868.5 MHz which corrupted weak JoinRequests from the node (while the stronger
# gateway JoinAccept still decoded due to higher SNR margin).
_LORA_DECIM = 4
_LORA_FS    = config.SAMPLE_RATE / _LORA_DECIM  # 512 000 sps

# Read this many samples per SDR call.  At 2 Msps this is 32 ms of data,
# covering ~31 SF7 symbols per read.  Larger chunks amortise Python overhead
# and raise the effective capture rate from ~8 % to ~75 %+.
_SDR_READ_SIZE = 65_536


def _check_driver() -> None:
    if _RTL_AVAILABLE:
        return
    print("[radio] rtlsdr library not found.", file=sys.stderr)
    if sys.platform.startswith("win"):
        print(
            "[radio] Windows: download rtlsdr.dll from "
            "https://github.com/rtlsdrblog/rtl-sdr-blog/releases "
            "and place it on PATH.",
            file=sys.stderr,
        )
    else:
        print(
            "[radio] Linux: sudo apt install librtlsdr-dev rtl-sdr",
            file=sys.stderr,
        )
    sys.exit(1)


def compute_fft(samples: np.ndarray, fft_size: int = config.FFT_SIZE) -> np.ndarray:
    windowed = samples[:fft_size] * np.hanning(fft_size)
    spectrum = np.fft.fftshift(np.fft.fft(windowed, n=fft_size))
    power_db = 20 * np.log10(np.abs(spectrum) / fft_size + 1e-12)
    return power_db.astype(np.float32)


def _bin_to_freq_mhz(
    bin_index: int,
    center_mhz: float,
    sample_rate: int,
    fft_size: int,
) -> float:
    bin_offset = bin_index - fft_size // 2
    hz_per_bin = sample_rate / fft_size
    return center_mhz + (bin_offset * hz_per_bin) / 1e6


class SpectrumCapture:
    def __init__(
        self,
        fft_queue: queue.Queue,
        center_mhz: float = config.CENTER_FREQ_MHZ,
        sample_rate: int = config.SAMPLE_RATE,
        fft_size: int = config.FFT_SIZE,
        gain_db: float = config.GAIN_DB,
        device_index: int = config.DEVICE_INDEX,
        lora_queue: queue.Queue | None = None,
    ) -> None:
        _check_driver()
        self.fft_queue = fft_queue
        self.center_mhz = center_mhz
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.gain_db = gain_db
        self.device_index = device_index
        self.lora_queue = lora_queue
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sdr: "RtlSdr | None" = None  # kept for emergency close on shutdown

        if lora_queue is not None:
            self._lora_decoders: list[LoraMultiSFDecoder] = [
                LoraMultiSFDecoder(bw_hz=125_000, sample_rate=_LORA_FS, freq_mhz=ch)
                for ch in config.EU868_CHANNELS
            ]
            # Precompute one-chunk frequency-shift arrays and per-chunk phase corrections.
            # This avoids recomputing exp() on every SDR read (saves ~6 ms/loop at 3 channels).
            n_dec = _SDR_READ_SIZE // _LORA_DECIM
            self._ch_base_shift: list[np.ndarray | None] = []
            self._ch_phase_corr: list[complex] = []
            self._ch_phase_acc:  list[complex] = []
            for ch_mhz in config.EU868_CHANNELS:
                offset_hz = (ch_mhz - self.center_mhz) * 1e6
                if abs(offset_hz) < 1.0:
                    self._ch_base_shift.append(None)
                    self._ch_phase_corr.append(1.0 + 0j)
                    self._ch_phase_acc.append(1.0 + 0j)
                else:
                    t = np.arange(n_dec, dtype=np.float64)
                    base = np.exp(-2j * np.pi * offset_hz / _LORA_FS * t).astype(np.complex64)
                    self._ch_base_shift.append(base)
                    corr = complex(np.exp(-2j * np.pi * offset_hz / _LORA_FS * n_dec))
                    self._ch_phase_corr.append(corr)
                    self._ch_phase_acc.append(1.0 + 0j)
        else:
            self._lora_decoders = []

    def start(self) -> None:
        self._stop_event.clear()
        # Non-daemon so sdr.close() runs before the process exits
        self._thread = threading.Thread(target=self._run, daemon=False)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        # Emergency close in case the thread is blocked inside librtlsdr
        if self._sdr is not None:
            try:
                self._sdr.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)

    def _wait_for_device(self) -> bool:
        """Block until the RTL-SDR appears in librtlsdr's device list or stop is requested."""
        print("[radio] Waiting for RTL-SDR to appear…", file=sys.stderr)
        while not self._stop_event.is_set():
            if _get_device_count() > self.device_index:
                return True
            self._stop_event.wait(1)
        return False

    def _run(self) -> None:
        while not self._stop_event.is_set():
            sdr = None
            try:
                sdr = RtlSdr(self.device_index)
                self._sdr = sdr
                _disable_usb_autosuspend()
                # RTL-SDR Blog V4 needs ~500 ms to stabilise after open
                # before I2C register writes (sample_rate, center_freq, gain) succeed.
                self._stop_event.wait(0.5)
                if self._stop_event.is_set():
                    break
                sdr.sample_rate = self.sample_rate
                sdr.center_freq = int(self.center_mhz * 1e6)
                sdr.gain = self.gain_db
                print(
                    f"[radio] RTL-SDR opened: {self.center_mhz:.3f} MHz, "
                    f"{self.sample_rate / 1e6:.3f} Msps, gain {self.gain_db} dB"
                )
                if self._lora_decoders:
                    print(
                        f"[radio] LoRa decoder active — "
                        f"{len(self._lora_decoders)} channels × SF7-12  "
                        f"(read={_SDR_READ_SIZE} samp = "
                        f"{1000*_SDR_READ_SIZE/self.sample_rate:.0f} ms/chunk)"
                    )

                while not self._stop_event.is_set():
                    samples = sdr.read_samples(_SDR_READ_SIZE)
                    iq = np.array(samples, dtype=np.complex64)
                    iq -= iq.mean()   # remove RTL-SDR LO leakage (DC spike at centre freq)

                    # ── Spectrum FFT (first fft_size samples only) ────────────────
                    power_db = compute_fft(iq, self.fft_size)
                    ts = datetime.now(timezone.utc).isoformat()
                    peak_bin = int(np.argmax(power_db))
                    peak_freq = _bin_to_freq_mhz(
                        peak_bin, self.center_mhz, self.sample_rate, self.fft_size
                    )
                    print(
                        f"\r[radio] peak {peak_freq:.3f} MHz  {power_db[peak_bin]:.1f} dBm   ",
                        end="",
                        flush=True,
                    )
                    if not self.fft_queue.full():
                        self.fft_queue.put_nowait(
                            {
                                "timestamp": ts,
                                "center_mhz": self.center_mhz,
                                "power_db": power_db,
                                "peak_freq_mhz": round(peak_freq, 3),
                                "peak_rssi_dbm": round(float(power_db[peak_bin]), 1),
                            }
                        )

                    # ── LoRa demodulation (per-channel with phase-continuous shift) ─
                    if not self._lora_decoders or self.lora_queue is None:
                        continue

                    rssi = round(float(power_db[peak_bin]), 1)
                    iq_dec = iq[::_LORA_DECIM]

                    for i, dec in enumerate(self._lora_decoders):
                        base = self._ch_base_shift[i]
                        if base is None:
                            shifted = iq_dec
                        else:
                            acc = self._ch_phase_acc[i]
                            shifted = iq_dec * (base * acc)
                            # Advance phase accumulator by one chunk, normalise to stay on unit circle
                            acc *= self._ch_phase_corr[i]
                            self._ch_phase_acc[i] = acc / abs(acc)

                        for pkt in dec.feed(shifted, ts, rssi):
                            if not self.lora_queue.full():
                                self.lora_queue.put_nowait(pkt)

            except IOError as exc:
                print(f"\n[radio] SDR error: {exc}", file=sys.stderr)
                if self._stop_event.is_set():
                    break
                # Try a USB-level reset (works if device is still visible in sysfs)
                print("[radio] Attempting USB reset…", file=sys.stderr)
                _usb_reset_rtlsdr()
                # Wait until librtlsdr actually sees the device again (max 60 s)
                if not self._wait_for_device():
                    break  # stop was requested while waiting
            finally:
                self._sdr = None
                if sdr is not None:
                    try:
                        sdr.close()
                    except Exception:
                        pass
                    print("[radio] RTL-SDR closed.")
