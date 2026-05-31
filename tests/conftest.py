import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def sample_fft():
    rng = np.random.default_rng(42)
    return rng.uniform(-120.0, -60.0, 1024).astype(np.float32)


@pytest.fixture
def sample_frame():
    return {
        "timestamp": "2025-05-22T14:03:11+00:00",
        "frequency_mhz": 868.097,
        "rssi_dbm": -87.3,
        "raw_hex": "604012ab",
        "parsed_json": json.dumps({"mtype": 2, "name": "Unconfirmed Data Up"}),
    }
