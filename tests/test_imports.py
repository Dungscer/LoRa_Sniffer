import pytest


def test_import_config():
    import config

    assert hasattr(config, "CENTER_FREQ_MHZ")


def test_import_db():
    import db

    assert hasattr(db, "init_db")
    assert hasattr(db, "insert_frame")
    assert hasattr(db, "get_frames")
    assert hasattr(db, "export_frames_json")


def test_import_logger():
    import logger

    assert hasattr(logger, "get_sniff_logger")
    assert hasattr(logger, "get_audit_logger")


def test_import_pcap():
    import pcap

    assert hasattr(pcap, "pcap_global_header")
    assert hasattr(pcap, "build_packet")
    assert hasattr(pcap, "write_pcap")


def test_import_radio():
    pytest.importorskip(
        "rtlsdr", reason="rtlsdr driver not installed", exc_type=ImportError
    )
    import radio

    assert hasattr(radio, "SpectrumCapture")
    assert hasattr(radio, "compute_fft")


def test_import_web():
    from web.app import app

    assert app is not None
