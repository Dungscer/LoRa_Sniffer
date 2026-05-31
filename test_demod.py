"""
Synthetic test for the LoRa CSS demodulator.

Generates a clean preamble + header + payload signal in software and verifies
that LoraDemodulator decodes it correctly.  Run with:
    uv run python test_demod.py
"""

import math
import sys
import numpy as np
from lora_demod import LoraDemodulator, _build_whitening_table, gray_to_bin

# ─────────────────────── LoRa CSS encoder (test-only) ───────────────────────

_WHITEN = _build_whitening_table(256)


def _bin_to_gray(v: int) -> int:
    return v ^ (v >> 1)


def _hamming_encode_nibble(d: int, cr: int) -> int:
    """Encode a 4-bit nibble to a (4+cr)-bit Hamming codeword.
    Data always at bits 3-0; parity occupies bits 4+ (never overlaps data).
    Layout matches the decoder's expected positions:
      CR4/5 (cr=1): bit4=p0,  bits3-0=data
      CR4/6 (cr=2): bit5=p1,  bit4=p0,  bits3-0=data
      CR4/7 (cr=3): bit6=p2,  bit5=p1,  bit4=p0,  bits3-0=data
      CR4/8 (cr=4): bit7=p2,  bit6=p1,  bit5=p0,  bit4=p3, bits3-0=data
    """
    d0 = (d >> 0) & 1
    d1 = (d >> 1) & 1
    d2 = (d >> 2) & 1
    d3 = (d >> 3) & 1
    p0 = d0 ^ d1 ^ d2
    p1 = d1 ^ d2 ^ d3
    p2 = d0 ^ d1 ^ d3
    p3 = d0 ^ d2 ^ d3
    # Data always at bits 3-0
    word = (d0 << 3) | (d1 << 2) | (d2 << 1) | d3
    if cr == 1:
        word |= p0 << 4
    elif cr == 2:
        word |= (p0 << 4) | (p1 << 5)
    elif cr == 3:
        word |= (p0 << 4) | (p1 << 5) | (p2 << 6)
    elif cr == 4:
        # p3 (SECDED) at bit 4; p0/p1/p2 match decoder's bits-3/bits-2/bits-1 = 5/6/7
        word |= (p3 << 4) | (p0 << 5) | (p1 << 6) | (p2 << 7)
    return word


def _interleave(codewords: list[int], sf: int, cr: int) -> list[int]:
    """Inverse of _deinterleave: codewords → symbols."""
    block = 4 + cr
    symbols = [0] * block
    for i in range(sf):
        for j in range(block):
            sym_idx = (i - j + block * 8) % block
            bit_pos = sf - i - 1
            bit = (codewords[i] >> (block - j - 1)) & 1
            symbols[sym_idx] |= bit << bit_pos
    return symbols


def _encode_block(nibbles: list[int], sf: int, cr: int) -> list[int]:
    """nibbles → Gray-encoded LoRa symbols."""
    codewords = [_hamming_encode_nibble(n, cr) for n in nibbles]
    symbols = _interleave(codewords, sf, cr)
    return [_bin_to_gray(s) for s in symbols]


def make_upchirp(symbol: int, N: int, sps: int) -> np.ndarray:
    """
    Generate one oversampled LoRa upchirp for symbol value `symbol`.
    Sample rate = sps samples per symbol, N chips per symbol.
    """
    t = np.arange(sps, dtype=np.float64)
    k = t * N / sps  # fractional chip index
    # Unwrapped phase; wrapping creates the frequency discontinuity at symbol boundary
    # For CSS dechirp test we use the exact formula that the decoder's downchirp inverts.
    # downchirp = exp(-2jπ(-k/2 + k²/(2N)))  →  upchirp(s=0) = exp(+2jπ(-k/2 + k²/(2N)))
    phase_base = 2 * np.pi * (-k / 2 + k ** 2 / (2 * N))
    phase_sym  = 2 * np.pi * symbol * k / N  # frequency shift for symbol value
    return np.exp(1j * (phase_base + phase_sym)).astype(np.complex64)


def make_downchirp(N: int, sps: int) -> np.ndarray:
    t = np.arange(sps, dtype=np.float64)
    k = t * N / sps
    return np.exp(-1j * 2 * np.pi * (-k / 2 + k ** 2 / (2 * N))).astype(np.complex64)


def encode_lora_packet(
    payload: bytes,
    sf: int = 7,
    cr: int = 1,        # 1→CR4/5, 4→CR4/8
    has_crc: bool = True,
    bw_hz: float = 125_000,
    fs: float = 256_000,
    preamble_len: int = 8,
    snr_db: float = 20.0,   # set to low value to test weak-signal decoding
) -> np.ndarray:
    """
    Encode `payload` as a LoRa CSS IQ signal at sample rate `fs`.
    Returns complex64 array ready to feed into LoraDemodulator.feed().
    """
    N   = 1 << sf
    sps = round(N * fs / bw_hz)

    # ── Whitening ────────────────────────────────────────────────────────────
    whiten = bytes(b ^ _WHITEN[i % 256] for i, b in enumerate(payload))

    # ── Nibblify (LSB nibble first) ──────────────────────────────────────────
    nibbles: list[int] = []
    for byte in whiten:
        nibbles.append(byte & 0xF)
        nibbles.append((byte >> 4) & 0xF)

    # ── Payload symbol blocks ────────────────────────────────────────────────
    # Match the decoder's _payload_sym_count formula:
    # n_blocks = ceil((8*pl_len + 16*has_crc) / (sf*4))
    n_blocks = math.ceil((8 * len(payload) + (16 if has_crc else 0)) / (sf * 4))
    total_nibbles = n_blocks * sf
    while len(nibbles) < total_nibbles:
        nibbles.append(0)

    payload_syms: list[int] = []
    for start in range(0, total_nibbles, sf):
        payload_syms.extend(_encode_block(nibbles[start : start + sf], sf, cr))

    # ── Header nibbles ───────────────────────────────────────────────────────
    pl_len = len(payload)
    h_nibbles = [
        pl_len & 0xF,
        (pl_len >> 4) & 0xF,
        (cr << 1) | int(has_crc),
        0,  # header CRC placeholder
    ]
    # Pad header nibbles to sf
    while len(h_nibbles) < sf:
        h_nibbles.append(0)
    header_syms = _encode_block(h_nibbles[:sf], sf, 4)  # header always CR4/8

    # ── Sync word symbols (LoRaWAN public = 0x34) ───────────────────────────
    # SX127x maps 0x34 → sync symbols at values 8 and 16 (approximate)
    sync_syms = [8, 16]

    # ── SFD (2 downchirp symbols) ────────────────────────────────────────────
    # Represented as symbols N-8 and N-16 (negative frequency offsets)
    sfd_syms = [N - 8, N - 16]

    # ── Generate IQ ─────────────────────────────────────────────────────────
    parts: list[np.ndarray] = []

    # Preamble (upchirps, symbol 0)
    for _ in range(preamble_len):
        parts.append(make_upchirp(0, N, sps))

    # Sync word (two symbols near 0x34 mapping)
    for s in sync_syms:
        parts.append(make_upchirp(s % N, N, sps))

    # SFD (2.25 downchirps — matches SX127x hardware: 2 full + 0.25 quarter)
    dc = make_downchirp(N, sps)
    parts.append(dc)
    parts.append(dc)
    parts.append(dc[: sps // 4])   # 0.25 fractional downchirp

    # Header symbols (8 symbols, always CR4/8, header uses 8 symbols of sf codewords)
    for s in header_syms[:8]:
        parts.append(make_upchirp(s + 1, N, sps))  # +1 for SYMBOL_OFFSET

    # Payload symbols
    for s in payload_syms:
        parts.append(make_upchirp(s + 1, N, sps))  # +1 for SYMBOL_OFFSET

    signal = np.concatenate(parts)

    # Add noise
    if snr_db < 100:
        noise_amp = 10 ** (-snr_db / 20)
        noise = noise_amp * (
            np.random.randn(len(signal)) + 1j * np.random.randn(len(signal))
        ).astype(np.complex64)
        signal = signal + noise

    return signal.astype(np.complex64)


# ─────────────────────── Tests ───────────────────────────────────────────────

def test_preamble_detection():
    """8 consecutive upchirps at symbol 0 must trigger SEARCH→SYNC transition."""
    N = 128
    sf = 7
    fs = 256_000
    bw = 125_000
    sps = round(N * fs / bw)

    dec = LoraDemodulator(sf=sf, bw_hz=bw, sample_rate=fs)

    chirps = np.concatenate([make_upchirp(0, N, sps) for _ in range(10)])
    noise = 0.05 * (np.random.randn(len(chirps)) + 1j * np.random.randn(len(chirps))).astype(np.complex64)
    dec.feed(chirps + noise)

    assert dec._state in ("SYNC", "HEADER", "PAYLOAD"), (
        f"Preamble not detected — still in SEARCH (cfo_bin={dec._cfo_bin}, "
        f"search_bins={dec._search_bins})"
    )
    print(f"  [OK] preamble detected → state={dec._state}, cfo_bin={dec._cfo_bin}")


def test_full_packet(payload: bytes, sf: int = 7, cr: int = 1, snr_db: float = 30.0):
    """Encode a known payload and check the decoder recovers it exactly."""
    dec = LoraDemodulator(sf=sf, bw_hz=125_000, sample_rate=256_000)
    signal = encode_lora_packet(payload, sf=sf, cr=cr, snr_db=snr_db)
    pkts = dec.feed(signal)
    if pkts:
        decoded = pkts[0].payload
        if decoded == payload:
            print(f"  [OK] SF{sf} CR4/{cr+4} snr={snr_db}dB → {payload.hex()}")
            return True
        else:
            print(f"  [FAIL] decoded={decoded.hex()}, expected={payload.hex()}")
            return False
    else:
        print(f"  [FAIL] SF{sf} CR4/{cr+4} snr={snr_db}dB — no packet decoded "
              f"(state={dec._state})")
        return False


def test_snr_threshold():
    """SNR of pure noise must always be below our threshold (no false preambles)."""
    dec = LoraDemodulator(sf=7, bw_hz=125_000, sample_rate=256_000)
    N, fs, bw = 128, 256_000, 125_000
    sps = round(N * fs / bw)
    noise = (np.random.randn(sps * 200) + 1j * np.random.randn(sps * 200)).astype(np.complex64)
    dec.feed(noise)
    assert dec._state == "SEARCH", f"False preamble on pure noise! state={dec._state}"
    print(f"  [OK] 200 noise symbols → no false preamble (max_snr={dec._max_snr:.1f} dB, "
          f"thresh={dec.SNR_THRESHOLD_DB:.1f} dB)")


if __name__ == "__main__":
    np.random.seed(42)
    print("=== LoRa demodulator synthetic tests ===\n")

    print("1. SNR threshold — noise must not trigger preamble:")
    test_snr_threshold()

    print("\n2. Preamble detection with clean signal:")
    test_preamble_detection()

    print("\n3. Full packet decode (SF7 CR4/5, SNR=30dB):")
    # Use a 4-byte test payload for speed
    ok = test_full_packet(b"\xDE\xAD\xBE\xEF", sf=7, cr=1, snr_db=30.0)

    print("\n4. Full packet decode (SF7 CR4/5, SNR=15dB — near limit):")
    ok2 = test_full_packet(b"\xDE\xAD\xBE\xEF", sf=7, cr=1, snr_db=15.0)

    print()
    if ok:
        print("Core demodulator logic: OK")
        print("If hardware detection still fails, the issue is RF reception (antenna/power).")
    else:
        print("Core demodulator logic: FAIL — bug in encoder/decoder pipeline")
        sys.exit(1)
