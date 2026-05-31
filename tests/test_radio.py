import numpy as np
import pytest

from radio import compute_fft, _bin_to_freq_mhz


def _make_iq(fft_size: int) -> np.ndarray:
    rng = np.random.default_rng(0)
    return (rng.standard_normal(fft_size) + 1j * rng.standard_normal(fft_size)).astype(
        np.complex64
    )


def test_fft_output_length():
    iq = _make_iq(1024)
    result = compute_fft(iq, fft_size=1024)
    assert len(result) == 1024


def test_fft_output_dtype():
    iq = _make_iq(1024)
    result = compute_fft(iq, fft_size=1024)
    assert result.dtype == np.float32


def test_fft_values_are_dbm():
    iq = _make_iq(1024)
    result = compute_fft(iq, fft_size=1024)
    # dBm values should be finite and in a plausible range
    assert np.all(np.isfinite(result))
    assert result.min() < -10


def test_fft_dc_bin_at_center():
    fft_size = 1024
    # Pure DC signal: constant real value
    iq = np.ones(fft_size, dtype=np.complex64) * 10.0
    result = compute_fft(iq, fft_size)
    center = fft_size // 2
    assert np.argmax(result) == center


def test_fft_tone_bin_position():
    fft_size = 1024
    # Tone at bin offset +32 from center
    bin_offset = 32
    t = np.arange(fft_size)
    freq_norm = bin_offset / fft_size
    iq = np.exp(2j * np.pi * freq_norm * t).astype(np.complex64)
    result = compute_fft(iq, fft_size)
    expected_bin = fft_size // 2 + bin_offset
    # Allow ±2 bin tolerance for windowing
    assert abs(np.argmax(result) - expected_bin) <= 2


def test_bin_to_freq_mhz_center():
    freq = _bin_to_freq_mhz(512, center_mhz=868.1, sample_rate=2_048_000, fft_size=1024)
    assert abs(freq - 868.1) < 0.001


def test_bin_to_freq_mhz_edge():
    freq = _bin_to_freq_mhz(0, center_mhz=868.1, sample_rate=2_048_000, fft_size=1024)
    expected = 868.1 - 1.024
    assert abs(freq - expected) < 0.001


def test_hardware_init():
    rtlsdr = pytest.importorskip(
        "rtlsdr", reason="rtlsdr driver not installed", exc_type=ImportError
    )
    import queue
    from radio import SpectrumCapture

    q = queue.Queue()
    cap = SpectrumCapture(fft_queue=q)
    assert cap is not None
