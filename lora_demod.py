"""
LoRa CSS (Chirp Spread Spectrum) demodulator for EU868 passive sniffing.

Implements the full LoRa PHY pipeline:
  IQ samples → dechirp+FFT → Gray decode → deinterleave → Hamming FEC → dewhiten → bytes

Supports SF7-SF12, BW=125kHz, CR4/5 through CR4/8, explicit header mode.
Based on: Mertens/Maene gr-lora, SX127x datasheet, LoRa reverse-engineering literature.
"""

import logging
import time
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

# ─────────────────────── Gray coding ────────────────────────────────────────


def gray_to_bin(g: int) -> int:
    mask = g >> 1
    while mask:
        g ^= mask
        mask >>= 1
    return g


# ─────────────────────── Hamming FEC ────────────────────────────────────────
#
# LoRa codeword layout (MSB → LSB):
#   CR4/5  (5 bits):  p0  d0 d1 d2 d3
#   CR4/6  (6 bits):  p1  p0 d0 d1 d2 d3
#   CR4/7  (7 bits):  p2  p1 p0 d0 d1 d2 d3
#   CR4/8  (8 bits):  p2  p1 p0 d0 d1 d2 d3 p3
#
# Parity definitions:
#   p0 = d0 ^ d1 ^ d2
#   p1 = d1 ^ d2 ^ d3
#   p2 = d0 ^ d1 ^ d3
#   p3 = d0 ^ d2 ^ d3  (CR4/8 only, overall SECDED parity)
#
# Syndrome → error position (for CR >= 3):
#   s = s2<<2 | s1<<1 | s0  →  5:d0  3:d1  7:d2  6:d3


def _hamming_decode_nibble(word: int, cr: int) -> int:
    """Decode a (4+cr)-bit LoRa Hamming codeword to a 4-bit nibble.

    Data bits are always at positions 3-0 (4 LSBs); parity at the MSBs.
    Layout per CR (MSB→LSB):
      CR4/5: [p0][d0 d1 d2 d3]
      CR4/6: [p1 p0][d0 d1 d2 d3]
      CR4/7: [p2 p1 p0][d0 d1 d2 d3]
      CR4/8: [p2 p1 p0][p3][d0 d1 d2 d3]
    """
    bits = cr + 4
    # Data bits are always at the 4 LSBs regardless of CR
    d = [(word >> (3 - i)) & 1 for i in range(4)]
    d0, d1, d2, d3 = d

    if cr >= 3:
        p0 = (word >> (bits - 3)) & 1
        p1 = (word >> (bits - 2)) & 1
        p2 = (word >> (bits - 1)) & 1
        s0 = p0 ^ d0 ^ d1 ^ d2
        s1 = p1 ^ d1 ^ d2 ^ d3
        s2 = p2 ^ d0 ^ d1 ^ d3
        syndrome = (s2 << 2) | (s1 << 1) | s0
        if syndrome:
            flip = {5: 0, 3: 1, 7: 2, 6: 3}.get(syndrome, -1)
            if flip >= 0:
                d[flip] ^= 1
                d0, d1, d2, d3 = d

    return d0 | (d1 << 1) | (d2 << 2) | (d3 << 3)


# ─────────────────────── Deinterleaver ──────────────────────────────────────
#
# Works on a block of (4+cr) symbols, each SF bits wide.
# Produces SF codewords, each (4+cr) bits wide.
# Implements the diagonal transposition from gr-lora (Mertens/Maene).


def _deinterleave(symbols: list[int], sf: int, cr: int) -> list[int]:
    block = 4 + cr
    codewords = []
    for i in range(sf):
        word = 0
        for j in range(block):
            sym_idx = (i - j + block * 8) % block
            bit_pos = sf - i - 1
            bit = (symbols[sym_idx] >> bit_pos) & 1
            word |= bit << (block - j - 1)
        codewords.append(word)
    return codewords


def _decode_block(symbols: list[int], sf: int, cr: int) -> list[int]:
    """Gray-decode + deinterleave + Hamming FEC one symbol block → nibbles."""
    gray_syms = [gray_to_bin(s) for s in symbols]
    codewords = _deinterleave(gray_syms, sf, cr)
    return [_hamming_decode_nibble(cw, cr) for cw in codewords]


# ─────────────────────── PHY whitening ──────────────────────────────────────
#
# 9-bit Fibonacci LFSR, polynomial x^9 + x^5 + 1, initial state 0x1FF.
# Applied byte-by-byte to the decoded payload (before LoRaWAN MAC decryption).


def _build_whitening_table(length: int = 256) -> bytes:
    reg = 0x1FF
    out = bytearray(length)
    for i in range(length):
        byte = 0
        for b in range(8):
            byte |= ((reg >> 8) & 1) << b
            feedback = ((reg >> 8) ^ (reg >> 4)) & 1
            reg = ((reg << 1) | feedback) & 0x1FF
        out[i] = byte
    return bytes(out)


_WHITEN = _build_whitening_table(256)


def _dewhiten(data: bytes) -> bytes:
    return bytes(b ^ _WHITEN[i % 256] for i, b in enumerate(data))


# ─────────────────────── Data class ─────────────────────────────────────────


@dataclass
class LoraPacket:
    sf: int
    bw_khz: float
    cr: int           # coding-rate denominator offset: 1→CR4/5 … 4→CR4/8
    has_crc: bool
    payload: bytes    # decoded payload bytes (LoRaWAN MAC; FRMPayload still AES-encrypted)
    freq_mhz: float
    rssi_dbm: float
    snr_db: float
    timestamp: str

    def to_dict(self) -> dict:
        return {
            "sf": self.sf,
            "bw_khz": self.bw_khz,
            "cr": f"4/{self.cr + 4}",
            "has_crc": self.has_crc,
            "payload_hex": self.payload.hex(),
            "payload_len": len(self.payload),
            "freq_mhz": round(self.freq_mhz, 4),
            "rssi_dbm": round(self.rssi_dbm, 1),
            "snr_db": round(self.snr_db, 1),
            "timestamp": self.timestamp,
        }


# ─────────────────────── Demodulator ────────────────────────────────────────


class LoraDemodulator:
    """
    Single-channel, single-SF LoRa CSS demodulator.

    State machine: SEARCH → SYNC → HEADER → PAYLOAD → (emit) → SEARCH

    Preamble detection is CFO-agnostic: looks for PREAMBLE_REQUIRED consecutive
    dechirp windows landing on the same FFT bin (within ±1), regardless of which
    bin that is.  The dominant bin becomes the CFO correction for all subsequent
    symbol reads.  This handles the ±20 ppm crystal offset of the RTL-SDR.

    Feed IQ samples at `sample_rate` Hz via .feed().
    Returned list contains decoded LoraPacket objects (usually empty).
    """

    # LoRaWAN default preamble is 8 upchirps; we require 6 to detect (tolerate 2 misses).
    PREAMBLE_TOTAL    = 8
    PREAMBLE_REQUIRED = 6

    # Symbol shift applied after dechirp+FFT (SX127x implementation detail).
    # 1 matches gr-lora captures; override to 0 if every decode fails.
    SYMBOL_OFFSET = 1

    def __init__(
        self,
        sf: int = 7,
        bw_hz: float = 125_000,
        sample_rate: float = 256_000,
        freq_mhz: float = 868.1,
        sync_word: int = 0x34,    # LoRaWAN public-network sync word
    ) -> None:
        assert sf in range(7, 13), "SF must be 7-12"
        self.sf = sf
        self.bw_hz = bw_hz
        self.fs = sample_rate
        self.freq_mhz = freq_mhz

        self.N   = 1 << sf                        # chips per symbol
        self.sps = round(self.N * sample_rate / bw_hz)  # samples per symbol at fs

        k = np.arange(self.N, dtype=np.float64)
        self._downchirp = np.exp(-2j * np.pi * (-k / 2 + k ** 2 / (2 * self.N)))
        # Decimation indices: map sps samples → N chips.
        # Use arange*sps/N (not linspace(0,sps-1,N)) so the effective chip index
        # at position i equals exactly i, giving zero bin-detection errors.
        self._dec_idx = np.minimum(
            np.round(np.arange(self.N) * self.sps / self.N).astype(int),
            self.sps - 1,
        )

        # SNR threshold just above the noise floor for this N.
        # For pure Gaussian noise: E[peak/avg] ≈ sqrt(4*ln(N)/π).
        # +2 dB margin keeps noise out while accepting signals near min RF SNR.
        self.SNR_THRESHOLD_DB = 10 * np.log10(4 * np.log(self.N) / np.pi) + 2.0

        self._reset()

    # ── internal helpers ────────────────────────────────────────────────────

    def _reset(self) -> None:
        self._buf:         np.ndarray  = np.zeros(0, dtype=np.complex64)
        self._state:       str         = "SEARCH"
        self._symbols:     list[int]   = []
        self._snrs:        list[float] = []
        self._cfo_bin:     int         = 0
        self._rssi:        float       = -100.0
        self._pl_len:      int         = 0
        self._cr:          int         = 1
        self._has_crc:     bool        = True
        self._pl_syms:     int         = 0
        # Sliding window for CFO-agnostic preamble detection
        self._search_bins: list[int]   = []
        # How many symbols to skip in SYNC state (set when preamble is detected)
        self._sync_skip:   int         = 4
        # SX127x emits 2.25 downchirps for the SFD; pending quarter-symbol advance
        # applied on the next loop entry after the last SYNC symbol is consumed.
        self._sfd_quarter: bool        = False
        # Diagnostics
        self._max_snr:     float       = -100.0
        self._diag_t:      float       = time.monotonic()
        self._sym_count:   int         = 0

    def _chips(self) -> np.ndarray:
        """Return the next symbol's worth of samples decimated to N chips."""
        raw = self._buf[: self.sps]
        if len(raw) < self.N:
            return raw.astype(np.complex64)
        return raw[self._dec_idx].astype(np.complex64)

    def _get_sym(self) -> tuple[int, float]:
        """Dechirp + FFT → (raw_bin, snr_db)."""
        chips = self._chips()
        fft = np.abs(np.fft.fft(chips * self._downchirp, n=self.N))
        peak = int(np.argmax(fft))
        noise = (np.sum(fft) - fft[peak]) / max(self.N - 1, 1)
        snr = 20 * np.log10(fft[peak] / (noise + 1e-12))
        return peak, float(snr)

    # ── header / payload decoding ────────────────────────────────────────────

    def _payload_sym_count(self, pl_len: int, cr: int, has_crc: bool) -> int:
        """LoRa standard formula for number of payload symbols (explicit header)."""
        n_bits = 8 * pl_len + (16 if has_crc else 0)
        # Each (4+cr) symbol block carries sf*4 payload bits
        bits_per_block = self.sf * 4
        n_blocks = (n_bits + bits_per_block - 1) // bits_per_block
        return n_blocks * (4 + cr)

    def _decode_header(self, symbols: list[int]) -> tuple[int, int, bool] | None:
        """Decode 8-symbol explicit header (always CR4/8). Returns (len, cr, crc)."""
        try:
            nibbles = _decode_block(
                [(s - self.SYMBOL_OFFSET) % self.N for s in symbols[:8]],
                self.sf, 4,
            )
            pl_len   = nibbles[0] | (nibbles[1] << 4)
            cr_field = (nibbles[2] >> 1) & 0x7   # bits [3:1] = CR2 CR1 CR0
            has_crc  = bool(nibbles[2] & 0x1)    # bit [0] = CRC-present flag
            cr = max(1, min(4, cr_field))
            if pl_len > 255:
                return None
            return pl_len, cr, has_crc
        except Exception as exc:
            log.debug("Header decode error: %s", exc)
            return None

    def _decode_payload(
        self, symbols: list[int], pl_len: int, cr: int, has_crc: bool
    ) -> bytes | None:
        try:
            block = 4 + cr
            nibbles: list[int] = []
            shifted = [(s - self.SYMBOL_OFFSET) % self.N for s in symbols]
            for i in range(0, len(shifted) - block + 1, block):
                nibbles.extend(_decode_block(shifted[i : i + block], self.sf, cr))
            raw = bytes(
                nibbles[i] | (nibbles[i + 1] << 4)
                for i in range(0, len(nibbles) - 1, 2)
            )
            return _dewhiten(raw[:pl_len])
        except Exception as exc:
            log.debug("Payload decode error: %s", exc)
            return None

    # ── public API ───────────────────────────────────────────────────────────

    def feed(
        self,
        samples: np.ndarray,
        timestamp: str = "",
        rssi_dbm: float = -100.0,
    ) -> list[LoraPacket]:
        """
        Feed IQ samples (at self.fs sample rate) and return decoded packets.
        The returned list is empty most of the time.
        """
        self._buf = np.concatenate([self._buf, samples.astype(np.complex64)])
        packets: list[LoraPacket] = []

        while len(self._buf) >= self.sps:
            # SX127x SFD is 2.25 downchirps; apply the pending 0.25-symbol advance
            # after the last SYNC symbol was consumed (set in SYNC block below).
            if self._sfd_quarter:
                self._sfd_quarter = False
                self._buf = self._buf[self.sps // 4 :]
                if len(self._buf) < self.sps:
                    break

            sym_raw, snr = self._get_sym()

            # ── SEARCH ────────────────────────────────────────────────────────
            if self._state == "SEARCH":
                self._sym_count += 1
                if snr > self._max_snr:
                    self._max_snr = snr

                if snr >= self.SNR_THRESHOLD_DB:
                    self._search_bins.append(sym_raw)
                    if len(self._search_bins) > self.PREAMBLE_REQUIRED + 2:
                        self._search_bins = self._search_bins[-self.PREAMBLE_REQUIRED - 2:]

                    if len(self._search_bins) >= self.PREAMBLE_REQUIRED:
                        last = self._search_bins[-self.PREAMBLE_REQUIRED:]
                        # Range check: all bins within ±1 of each other (robust vs. compare-to-last)
                        if max(last) - min(last) <= 2:
                            cfo_bin = round(sum(last) / len(last))
                            self._cfo_bin  = cfo_bin
                            self._rssi     = rssi_dbm
                            self._state    = "SYNC"
                            self._symbols  = []
                            self._search_bins.clear()
                            # remaining preamble + 2 sync word + 2 SFD symbols to skip
                            self._sync_skip = (self.PREAMBLE_TOTAL - self.PREAMBLE_REQUIRED) + 4
                            cfo_hz = cfo_bin * self.bw_hz / self.N
                            log.info(
                                "[SF%d] preamble @ bin %d (cfo=%.0f Hz) snr=%.1f",
                                self.sf, cfo_bin, cfo_hz, snr,
                            )
                            print(
                                f"\n[lora] SF{self.sf} preamble"
                                f"  bin={cfo_bin}  cfo={cfo_hz:+.0f} Hz"
                                f"  snr={snr:.1f} dB  @ {self.freq_mhz:.3f} MHz",
                                flush=True,
                            )
                else:
                    self._search_bins.clear()

                # Periodic diagnostic every 10 s for SF7 only (avoid spam)
                now = time.monotonic()
                if self.sf == 7 and (now - self._diag_t) >= 10.0:
                    bins_str = str(self._search_bins[-5:]) if self._search_bins else "[]"
                    print(
                        f"[diag SF7@{self.freq_mhz:.1f}] "
                        f"syms={self._sym_count}  max_snr={self._max_snr:.1f} dB  "
                        f"(thresh={self.SNR_THRESHOLD_DB:.1f})  "
                        f"search_bins={bins_str}",
                        flush=True,
                    )
                    self._diag_t = now
                    self._sym_count = 0
                    self._max_snr = -100.0

                self._buf = self._buf[self.sps :]
                continue

            # CFO-corrected symbol
            sym = (sym_raw - self._cfo_bin) % self.N

            # ── SYNC: skip remaining preamble + sync word + SFD ─────────────
            if self._state == "SYNC":
                self._symbols.append(sym)
                if len(self._symbols) >= self._sync_skip:
                    self._symbols    = []
                    self._state      = "HEADER"
                    self._sfd_quarter = True   # schedule 0.25-symbol advance
                    log.debug("[SF%d] sync done (skipped %d)", self.sf, self._sync_skip)

            # ── HEADER: 8 symbols → length / CR / CRC ────────────────────────
            elif self._state == "HEADER":
                self._symbols.append(sym)   # CFO-corrected
                if len(self._symbols) >= 8:
                    result = self._decode_header(self._symbols)
                    if result is not None:
                        self._pl_len, self._cr, self._has_crc = result
                        self._pl_syms = self._payload_sym_count(
                            self._pl_len, self._cr, self._has_crc
                        )
                        self._symbols = []
                        self._state   = "PAYLOAD"
                        log.debug(
                            "[SF%d] header OK len=%d cr=%d crc=%s syms=%d",
                            self.sf, self._pl_len, self._cr,
                            self._has_crc, self._pl_syms,
                        )
                    else:
                        log.debug("[SF%d] header failed, resetting", self.sf)
                        self._reset()
                        continue

            # ── PAYLOAD: collect → decode → emit ─────────────────────────────
            elif self._state == "PAYLOAD":
                self._symbols.append(sym)   # CFO-corrected
                self._snrs.append(snr)
                if len(self._symbols) >= self._pl_syms:
                    payload = self._decode_payload(
                        self._symbols, self._pl_len, self._cr, self._has_crc
                    )
                    if payload is not None:
                        pkt = LoraPacket(
                            sf=self.sf,
                            bw_khz=self.bw_hz / 1000,
                            cr=self._cr,
                            has_crc=self._has_crc,
                            payload=payload,
                            freq_mhz=self.freq_mhz,
                            rssi_dbm=self._rssi,
                            snr_db=float(np.mean(self._snrs)),
                            timestamp=timestamp,
                        )
                        packets.append(pkt)
                        log.info(
                            "[SF%d] packet %dB @ %.3f MHz rssi=%.1f snr=%.1f",
                            self.sf, len(payload), self.freq_mhz,
                            self._rssi, pkt.snr_db,
                        )
                    self._reset()
                    continue

            self._buf = self._buf[self.sps :]

        return packets


# ─────────────────────── Multi-SF wrapper ───────────────────────────────────


class LoraMultiSFDecoder:
    """
    Runs one LoraDemodulator per SF (7-12) on the same IQ stream.
    Automatically tries all spreading factors in parallel.
    """

    def __init__(
        self,
        bw_hz: float = 125_000,
        sample_rate: float = 256_000,
        freq_mhz: float = 868.1,
        sf_list: tuple[int, ...] = (7, 8, 9, 10, 11, 12),
    ) -> None:
        self.decoders = [
            LoraDemodulator(sf=sf, bw_hz=bw_hz, sample_rate=sample_rate, freq_mhz=freq_mhz)
            for sf in sf_list
        ]

    def feed(
        self,
        samples: np.ndarray,
        timestamp: str = "",
        rssi_dbm: float = -100.0,
    ) -> list[LoraPacket]:
        packets: list[LoraPacket] = []
        for dec in self.decoders:
            packets.extend(dec.feed(samples, timestamp, rssi_dbm))
        return packets
