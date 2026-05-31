import json
import sqlite3
import db


def test_init_creates_tables(tmp_db):
    db.init_db(tmp_db)
    conn = sqlite3.connect(tmp_db)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "frames" in tables
    assert "spectrum_snapshots" in tables


def test_insert_and_retrieve_frame(tmp_db, sample_frame):
    db.init_db(tmp_db)
    db.insert_frame(**sample_frame, db_path=tmp_db)
    rows, total = db.get_frames(db_path=tmp_db)
    assert total == 1
    assert rows[0]["raw_hex"] == sample_frame["raw_hex"]


def test_frequency_stored_as_float(tmp_db, sample_frame):
    db.init_db(tmp_db)
    db.insert_frame(**sample_frame, db_path=tmp_db)
    rows, _ = db.get_frames(db_path=tmp_db)
    assert abs(rows[0]["frequency_mhz"] - 868.097) < 1e-6


def test_get_frames_filter_since(tmp_db):
    db.init_db(tmp_db)
    db.insert_frame("2025-05-22T10:00:00+00:00", 868.1, -80.0, "aabb", db_path=tmp_db)
    db.insert_frame("2025-05-22T12:00:00+00:00", 868.1, -80.0, "ccdd", db_path=tmp_db)
    rows, total = db.get_frames(since="2025-05-22T11:00:00+00:00", db_path=tmp_db)
    assert total == 1
    assert rows[0]["raw_hex"] == "ccdd"


def test_get_frames_filter_freq_range(tmp_db):
    db.init_db(tmp_db)
    db.insert_frame("2025-05-22T10:00:00+00:00", 868.100, -80.0, "aabb", db_path=tmp_db)
    db.insert_frame("2025-05-22T10:00:01+00:00", 868.300, -80.0, "ccdd", db_path=tmp_db)
    rows, total = db.get_frames(freq_min=868.0, freq_max=868.2, db_path=tmp_db)
    assert total == 1
    assert rows[0]["raw_hex"] == "aabb"


def test_get_frames_filter_min_rssi(tmp_db):
    db.init_db(tmp_db)
    db.insert_frame("2025-05-22T10:00:00+00:00", 868.1, -70.0, "strong", db_path=tmp_db)
    db.insert_frame("2025-05-22T10:00:01+00:00", 868.1, -100.0, "weak", db_path=tmp_db)
    rows, total = db.get_frames(min_rssi=-80.0, db_path=tmp_db)
    assert total == 1
    assert rows[0]["raw_hex"] == "strong"


def test_get_frames_sort_rssi_desc(tmp_db):
    db.init_db(tmp_db)
    for rssi, hex_ in [(-60.0, "aa"), (-80.0, "bb"), (-70.0, "cc")]:
        db.insert_frame("2025-05-22T10:00:00+00:00", 868.1, rssi, hex_, db_path=tmp_db)
    rows, _ = db.get_frames(sort="rssi_dbm", order="desc", db_path=tmp_db)
    assert [r["raw_hex"] for r in rows] == ["aa", "cc", "bb"]


def test_get_frames_pagination(tmp_db):
    db.init_db(tmp_db)
    for i in range(10):
        db.insert_frame(
            f"2025-05-22T10:00:{i:02d}+00:00",
            868.1,
            -80.0,
            f"{i:02x}{i:02x}",
            db_path=tmp_db,
        )
    rows, total = db.get_frames(
        limit=3, offset=3, sort="id", order="asc", db_path=tmp_db
    )
    assert total == 10
    assert len(rows) == 3
    assert rows[0]["id"] == 4


def test_export_returns_list(tmp_db, sample_frame):
    db.init_db(tmp_db)
    db.insert_frame(**sample_frame, db_path=tmp_db)
    result = db.export_frames_json(db_path=tmp_db)
    assert isinstance(result, list)
    assert len(result) == 1
    assert "raw_hex" in result[0]


def test_export_respects_filters(tmp_db):
    db.init_db(tmp_db)
    db.insert_frame("2025-05-22T10:00:00+00:00", 868.100, -80.0, "aabb", db_path=tmp_db)
    db.insert_frame("2025-05-22T10:00:01+00:00", 868.300, -80.0, "ccdd", db_path=tmp_db)
    result = db.export_frames_json(freq_max=868.2, db_path=tmp_db)
    assert len(result) == 1
    assert result[0]["raw_hex"] == "aabb"
