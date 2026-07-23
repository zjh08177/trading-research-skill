"""Offline tests for build_datapack.build_p9 history-failure resilience.

No network / no real subprocess: build_datapack.run_cli and
build_datapack.subprocess.run are monkeypatched. Asserts that a tiingo history
failure no longer kills the history-free `stretch` script, that the five
history-dependent scripts collapse to ONE named gap, and that the happy path is
byte-identical to today."""
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "batch"))
import build_datapack as bd  # noqa: E402


class _FakeProc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _script_of(cmd):
    """The <script>.py basename (without .py) from a build_p9 subprocess cmd."""
    for part in cmd:
        if isinstance(part, str) and part.endswith(".py"):
            return os.path.basename(part)[:-3]
    return None


@pytest.fixture(autouse=True)
def _asof(monkeypatch):
    monkeypatch.setattr(bd, "ASOF", "2026-07-22")


def _base_pack():
    def f(v):
        return {"v": v, "unit": "USD", "asof": "2026-07-22", "src": "test"}
    # stretch.py REQUIRED-ish inputs (sma50/sma200/atr14); content is irrelevant
    # here because subprocess.run is faked, but seed a realistic pack anyway.
    return {"P2.sma50": f(100.0), "P2.sma200": f(90.0), "P2.atr14": f(3.0),
            "P1.price": f(105.0)}


def test_P1_history_dead_stretch_still_runs(monkeypatch, tmp_path):
    invoked = []

    def fake_run_cli(name, args):
        return 4, None, "tiingo HTTP 429 after retries"

    def fake_subprocess_run(cmd, capture_output=True, text=True):
        script = _script_of(cmd)
        invoked.append(script)
        assert script == "stretch"   # nothing history-dependent should be invoked
        return _FakeProc(0, json.dumps({"P9.stretch_z": {"v": 1.2, "unit": "z",
                                                          "asof": "2026-07-22",
                                                          "src": "stretch"}}))

    monkeypatch.setattr(bd, "run_cli", fake_run_cli)
    monkeypatch.setattr(bd.subprocess, "run", fake_subprocess_run)

    facts, gaps = bd.build_p9("X", str(tmp_path), _base_pack())

    assert "P9.stretch_z" in facts
    assert invoked == ["stretch"]
    hist_gaps = [g for g in gaps if re.match(r"^P9 MISSING\(history:", g)]
    assert len(hist_gaps) == 1
    assert len(gaps) == 1
    for s in ("percentile", "volume_climax", "move_cluster",
              "move_base_rate", "exhaustion"):
        assert s in hist_gaps[0]
    assert not os.path.exists(f"{tmp_path}/11-history.json")


def test_P2_history_dead_and_stretch_dead_catchall(monkeypatch, tmp_path):
    def fake_run_cli(name, args):
        return 4, None, "tiingo HTTP 429 after retries"

    def fake_subprocess_run(cmd, capture_output=True, text=True):
        assert _script_of(cmd) == "stretch"
        return _FakeProc(3, stdout="", stderr="stretch: missing P2.sma200")

    monkeypatch.setattr(bd, "run_cli", fake_run_cli)
    monkeypatch.setattr(bd.subprocess, "run", fake_subprocess_run)

    facts, gaps = bd.build_p9("X", str(tmp_path), _base_pack())

    assert facts == {}
    assert any(re.match(r"^P9 MISSING\(history:", g) for g in gaps)
    assert any(g.startswith("P9 MISSING(stretch:") for g in gaps)
    assert any(g.startswith("MISSING(P9):") for g in gaps)


def test_P3_happy_path_byte_identical(monkeypatch, tmp_path):
    invoked = []

    def fake_run_cli(name, args):
        assert name == "tiingo_history"
        return 0, {"ticker": "X", "asof": "2026-07-22",
                   "bars": [{"date": "2020-01-01", "close": 1}]}, ""

    def fake_subprocess_run(cmd, capture_output=True, text=True):
        script = _script_of(cmd)
        invoked.append(script)
        return _FakeProc(0, json.dumps({f"P9.{script}_out": {
            "v": 1, "unit": "x", "asof": "2026-07-22", "src": script}}))

    monkeypatch.setattr(bd, "run_cli", fake_run_cli)
    monkeypatch.setattr(bd.subprocess, "run", fake_subprocess_run)

    facts, gaps = bd.build_p9("X", str(tmp_path), _base_pack())

    assert invoked == bd.P9_ORDER          # all six, in order
    assert os.path.exists(f"{tmp_path}/11-history.json")
    assert not any(re.match(r"^P9 MISSING\(history:", g) for g in gaps)
    assert gaps == []
    for s in bd.P9_ORDER:
        assert f"P9.{s}_out" in facts
