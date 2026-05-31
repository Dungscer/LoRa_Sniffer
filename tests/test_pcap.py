import io
import struct
import zipfile

import pcap


def test_global_header_magic():
    header = pcap.pcap_global_header()
    assert header[:4] == b"\xd4\xc3\xb2\xa1"


def test_global_header_length():
    assert len(pcap.pcap_global_header()) == 24


def test_global_header_link_type():
    header = pcap.pcap_global_header()
    link_type = struct.unpack("<I", header[20:24])[0]
    assert link_type == 1  # DLT_EN10MB


def test_build_packet_udp_ports():
    pkt = pcap.build_packet("aabb", "2025-05-22T14:00:00+00:00")
    # Skip Ethernet (14) + IP (20) = offset 34
    src_port = struct.unpack("!H", pkt[34:36])[0]
    dst_port = struct.unpack("!H", pkt[36:38])[0]
    assert src_port == 1700
    assert dst_port == 1700


def test_build_packet_payload_preserved():
    raw = "604012"
    pkt = pcap.build_packet(raw, "2025-05-22T14:00:00+00:00")
    assert pkt.endswith(bytes.fromhex(raw))


def test_packet_record_header_length():
    data = b"\x00" * 20
    record = pcap.pcap_packet_record(data, ts_sec=0, ts_usec=0)
    assert len(record) == 16 + len(data)


def test_write_pcap_valid_file(sample_frame):
    buf = io.BytesIO()
    pcap.write_pcap([sample_frame], buf)
    buf.seek(0)
    content = buf.read()
    assert content[:4] == b"\xd4\xc3\xb2\xa1"
    assert len(content) > 24


def test_write_pcap_empty_input():
    buf = io.BytesIO()
    pcap.write_pcap([], buf)
    buf.seek(0)
    assert len(buf.read()) == 24  # global header only


def test_api_export_zip_contains_pcap(tmp_db):
    import logging
    import config
    import db as db_mod

    for name in ("sniff", "audit"):
        logging.getLogger(name).handlers.clear()

    original = config.DB_PATH
    config.DB_PATH = tmp_db
    db_mod.init_db(tmp_db)
    try:
        from fastapi.testclient import TestClient
        from web.app import app

        with TestClient(app) as client:
            resp = client.get("/api/export")
        assert resp.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert "frames.pcap" in zf.namelist()
    finally:
        config.DB_PATH = original


def test_api_export_pcap_content_type(tmp_db):
    import logging
    import config
    import db as db_mod

    for name in ("sniff", "audit"):
        logging.getLogger(name).handlers.clear()

    original = config.DB_PATH
    config.DB_PATH = tmp_db
    db_mod.init_db(tmp_db)
    try:
        from fastapi.testclient import TestClient
        from web.app import app

        with TestClient(app) as client:
            resp = client.get("/api/export/pcap")
        assert resp.status_code == 200
        ct = resp.headers["content-type"]
        assert "tcpdump" in ct or "octet-stream" in ct
    finally:
        config.DB_PATH = original
