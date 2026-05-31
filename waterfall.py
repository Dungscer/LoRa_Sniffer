import os
import queue
import sys

import numpy as np

import config

# Headless Linux guard — must be set before importing pyplot
if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    import matplotlib

    matplotlib.use("Agg")
    print(
        "[waterfall] No DISPLAY detected — matplotlib set to Agg backend. "
        "--no-web mode requires a graphical display.",
        file=sys.stderr,
    )

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation


class WaterfallDisplay:
    def __init__(
        self,
        fft_queue: queue.Queue,
        center_mhz: float = config.CENTER_FREQ_MHZ,
        sample_rate: int = config.SAMPLE_RATE,
        fft_size: int = config.FFT_SIZE,
        depth: int = config.WATERFALL_DEPTH,
    ) -> None:
        self.fft_queue = fft_queue
        self.center_mhz = center_mhz
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.depth = depth

        half_bw = sample_rate / 2e6
        self.freq_axis = np.linspace(
            center_mhz - half_bw, center_mhz + half_bw, fft_size
        )

        self._data = np.full((depth, fft_size), config.WATERFALL_VMIN, dtype=np.float32)
        self._fig, self._ax = plt.subplots(figsize=(12, 6))
        self._fig.patch.set_facecolor("#0d0d0d")
        self._ax.set_facecolor("#0d0d0d")

        self._im = self._ax.imshow(
            self._data,
            aspect="auto",
            origin="upper",
            cmap="inferno",
            vmin=config.WATERFALL_VMIN,
            vmax=config.WATERFALL_VMAX,
            extent=[
                self.freq_axis[0],
                self.freq_axis[-1],
                depth,
                0,
            ],
        )

        cbar = self._fig.colorbar(self._im, ax=self._ax)
        cbar.set_label("dBm", color="white")
        cbar.ax.yaxis.set_tick_params(color="white")
        plt.setp(cbar.ax.yaxis.get_ticklabels(), color="white")

        self._ax.set_xlabel("Frequency (MHz)", color="white")
        self._ax.set_ylabel("Time (rows)", color="white")
        self._ax.tick_params(colors="white")
        self._ax.set_title(
            f"RTL-SDR Listener — {center_mhz:.3f} MHz centre", color="white"
        )

        for ch_mhz in config.EU868_CHANNELS:
            if self.freq_axis[0] <= ch_mhz <= self.freq_axis[-1]:
                self._ax.axvline(x=ch_mhz, color="cyan", linewidth=0.6, alpha=0.7)
                self._ax.text(
                    ch_mhz,
                    depth * 0.02,
                    f"{ch_mhz}",
                    color="cyan",
                    fontsize=6,
                    rotation=90,
                    va="top",
                )

        self._anim: FuncAnimation | None = None

    def _update(self, _frame: int) -> list:
        updated = False
        while not self.fft_queue.empty():
            item = self.fft_queue.get_nowait()
            row = item.get("power_db")
            if row is not None and len(row) == self.fft_size:
                self._data = np.roll(self._data, 1, axis=0)
                self._data[0] = row
                updated = True
        if updated:
            self._im.set_data(self._data)
        return [self._im]

    def show(self) -> None:
        self._anim = FuncAnimation(
            self._fig, self._update, interval=100, blit=True, cache_frame_data=False
        )
        self._fig.canvas.mpl_connect(
            "key_press_event",
            lambda e: self._save_png() if e.key == "s" else None,
        )
        plt.tight_layout()
        plt.show()

    def _save_png(self) -> None:
        path = f"waterfall_{int(__import__('time').time())}.png"
        self._fig.savefig(path, dpi=150, bbox_inches="tight")
        print(f"[waterfall] Saved {path}")
