from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _sanitize_voxera_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("VOXERA_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VOXERA_LOAD_DOTENV", "0")
