from pathlib import Path
import config


def test_center_freq_is_float():
    assert isinstance(config.CENTER_FREQ_MHZ, float)


def test_center_freq_in_eu868_range():
    assert 863.0 <= config.CENTER_FREQ_MHZ <= 870.0


def test_sample_rate_positive():
    assert config.SAMPLE_RATE > 0


def test_fft_size_power_of_two():
    n = config.FFT_SIZE
    assert n > 0 and (n & (n - 1)) == 0


def test_gain_non_negative():
    assert config.GAIN_DB >= 0


def test_log_dir_is_path():
    assert isinstance(config.LOG_DIR, Path)


def test_db_path_is_path():
    assert isinstance(config.DB_PATH, Path)


def test_waterfall_vmin_less_than_vmax():
    assert config.WATERFALL_VMIN < config.WATERFALL_VMAX


def test_eu868_channels_non_empty():
    assert len(config.EU868_CHANNELS) > 0


def test_eu868_channels_in_band():
    for ch in config.EU868_CHANNELS:
        assert 863.0 <= ch <= 870.0
