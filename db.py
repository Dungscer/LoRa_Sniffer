import json
import sqlite3
from pathlib import Path
from typing import Any

import config


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = config.DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS frames (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT    NOT NULL,
                frequency_mhz REAL   NOT NULL,
                rssi_dbm     REAL    NOT NULL,
                raw_hex      TEXT    NOT NULL,
                parsed_json  TEXT
            );
            CREATE TABLE IF NOT EXISTS spectrum_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                center_mhz  REAL    NOT NULL,
                fft_json    TEXT    NOT NULL
            );
        """)


def insert_frame(
    timestamp: str,
    frequency_mhz: float,
    rssi_dbm: float,
    raw_hex: str,
    parsed_json: str | None = None,
    db_path: Path | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO frames (timestamp, frequency_mhz, rssi_dbm, raw_hex, parsed_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (timestamp, frequency_mhz, rssi_dbm, raw_hex, parsed_json),
        )


def insert_snapshot(
    timestamp: str,
    center_mhz: float,
    fft_json: str,
    db_path: Path | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO spectrum_snapshots (timestamp, center_mhz, fft_json) VALUES (?, ?, ?)",
            (timestamp, center_mhz, fft_json),
        )


def get_frames(
    since: str | None = None,
    until: str | None = None,
    freq_min: float | None = None,
    freq_max: float | None = None,
    min_rssi: float | None = None,
    sort: str = "timestamp",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    db_path: Path | None = None,
) -> tuple[list[dict[str, Any]], int]:
    allowed_sort = {"timestamp", "frequency_mhz", "rssi_dbm", "id"}
    if sort not in allowed_sort:
        sort = "timestamp"
    if order not in ("asc", "desc"):
        order = "desc"
    limit = min(max(1, limit), 500)

    conditions: list[str] = []
    params: list[Any] = []

    if since:
        conditions.append("timestamp >= ?")
        params.append(since)
    if until:
        conditions.append("timestamp <= ?")
        params.append(until)
    if freq_min is not None:
        conditions.append("frequency_mhz >= ?")
        params.append(freq_min)
    if freq_max is not None:
        conditions.append("frequency_mhz <= ?")
        params.append(freq_max)
    if min_rssi is not None:
        conditions.append("rssi_dbm >= ?")
        params.append(min_rssi)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with _connect(db_path) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM frames {where}", params).fetchone()[
            0
        ]
        rows = conn.execute(
            f"SELECT * FROM frames {where} ORDER BY {sort} {order} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

    return [dict(r) for r in rows], total


def export_frames_json(
    db_path: Path | None = None, **kwargs: Any
) -> list[dict[str, Any]]:
    rows, _ = get_frames(db_path=db_path, limit=500, **kwargs)
    return rows
