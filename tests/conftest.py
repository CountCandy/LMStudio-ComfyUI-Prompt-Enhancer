"""Loads the pack's modules as package `lmse` without running __init__.py (which needs ComfyUI)."""

import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if "lmse" not in sys.modules:
    pkg = types.ModuleType("lmse")
    pkg.__path__ = [ROOT]
    sys.modules["lmse"] = pkg

from fake_lmstudio import FakeLMStudio  # noqa: E402


@pytest.fixture()
def fake():
    server = FakeLMStudio().start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LMSE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LMSTUDIO_API_TOKEN", raising=False)
    return tmp_path / "data"
