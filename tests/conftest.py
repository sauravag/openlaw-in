from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def vaquill_fixture_dir() -> Path:
    """Directory holding sampled vaquill Parquet files."""
    return FIXTURE_DIR / "vaquill"


@pytest.fixture
def kanoongpt_fixture_dir() -> Path:
    """Directory holding sampled KanoonGPT Parquet files."""
    return FIXTURE_DIR / "kanoongpt"
