import io
import struct
from datetime import datetime, timezone

_PCAP_MAGIC = 0xA1B2C3D4
_PCAP_VERSION_MAJOR = 2
_PCAP_VERSION_MINOR = 4
_PCAP_SNAPLEN = 65535
_DLT_EN10MB = 1

# Fake Ethernet/IP/UDP addresses — Wireshark opens without configuration
_ETH_DST = b"\xff\xff\xff\xff\xff\xff"
_ETH_SRC = b"\x00\x00\x00\x00\x00\x00"
_ETH_TYPE = b"\x08\x00"  # IPv4
_IP_SRC = b"\x7f\x00\x00\x01"  # 127.0.0.1
_IP_DST = b"\x7f\x00\x00\x01"
_LORA_PORT = 1700  # standard LoRa packet forwarder port


def pcap_global_header() -> bytes:
    return struct.pack(
        "<IHHiIII",
        _PCAP_MAGIC,
        _PCAP_VERSION_MAJOR,
        _PCAP_VERSION_MINOR,
        0,  # thiszone
        0,  # sigfigs
        _PCAP_SNAPLEN,
        _DLT_EN10MB,
    )


def _ip_checksum(header: bytes) -> int:
    if len(header) % 2:
        header += b"\x00"
    total = sum(struct.unpack(f"!{len(header) // 2}H", header))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def build_packet(raw_hex: str, timestamp: str) -> bytes:
    payload = bytes.fromhex(raw_hex)

    udp_len = 8 + len(payload)
    udp = struct.pack("!HHHH", _LORA_PORT, _LORA_PORT, udp_len, 0) + payload

    ip_len = 20 + len(udp)
    ip_header_no_csum = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,  # version/IHL, DSCP/ECN
        ip_len,
        0,
        0,  # identification, flags/fragment offset
        64,
        17,  # TTL, protocol (UDP)
        0,  # checksum placeholder
        _IP_SRC,
        _IP_DST,
    )
    csum = _ip_checksum(ip_header_no_csum)
    ip = ip_header_no_csum[:10] + struct.pack("!H", csum) + ip_header_no_csum[12:]

    eth = _ETH_DST + _ETH_SRC + _ETH_TYPE
    return eth + ip + udp


def pcap_packet_record(packet: bytes, ts_sec: int, ts_usec: int) -> bytes:
    length = len(packet)
    return struct.pack("<IIII", ts_sec, ts_usec, length, length) + packet


def write_pcap(frames: list[dict], dest: io.BytesIO) -> None:
    dest.write(pcap_global_header())
    for frame in frames:
        try:
            dt = datetime.fromisoformat(frame["timestamp"]).replace(tzinfo=timezone.utc)
        except (ValueError, KeyError):
            dt = datetime.now(timezone.utc)
        ts_sec = int(dt.timestamp())
        ts_usec = dt.microsecond
        packet = build_packet(frame.get("raw_hex", ""), frame.get("timestamp", ""))
        dest.write(pcap_packet_record(packet, ts_sec, ts_usec))
