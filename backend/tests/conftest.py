from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture
def data_dir() -> str:
    return str(DATA_DIR)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "test.db")
