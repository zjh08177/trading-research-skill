"""Cursor-host judge routing (ledger side): judge_mix survives append and
resolve into the sidecar, and drives a regime-aware calibration footer so a mixed
Cursor panel never blends into the opus-only rate. Stdlib + pytest."""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ledger  # noqa: E402

CURSOR_MIX = ["gpt-5.5-extra-high", "claude-opus-4-8-thinking-max", "glm-5.2-high"]
CURSOR_REGIME = "+".join(sorted(set(CURSOR_MIX)))


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / "ledger.py"), *args],
                          capture_output=True, text=True)


def _footer(tmp, resolved_rows, ticker="AAOI", before="2026-07-05"):
    main = tmp / "ledger.jsonl"
    (tmp / "ledger-resolved.jsonl").write_text(
        "\n".join(json.dumps(r) for r in resolved_rows) + "\n")
    return ledger.calibration_footer(main, ticker, date.fromisoformat(before))


# ---------- regime_key ----------

def test_regime_key_legacy_and_mixed():
    assert ledger.regime_key(None) == "claude/opus"
    assert ledger.regime_key([]) == "claude/opus"
    assert ledger.regime_key(CURSOR_MIX) == CURSOR_REGIME
    # order-independent: same set of models = same regime
    assert ledger.regime_key(list(reversed(CURSOR_MIX))) == CURSOR_REGIME


# ---------- append / resolve carry ----------

def test_append_preserves_judge_mix(tmp_path):
    led = tmp_path / "ledger.jsonl"
    row = {"run_id": "c1", "ticker": "TSLA", "date_utc": "2026-06-01",
           "as_of": "2026-06-01T20:00:00Z", "job": "J1", "mode_rating": "Buy",
           "distribution": {"Buy": 3}, "spread": 0, "no_call": False, "gaps": [],
           "report_path": "t.md", "cost_usd": 0.0, "wall_s": 500,
           "judge_mix": CURSOR_MIX}
    r = _run("--ledger", str(led), "append", "--row", json.dumps(row))
    assert r.returncode == 0
    assert json.loads(led.read_text().strip())["judge_mix"] == CURSOR_MIX


def test_resolve_rows_carries_judge_mix_to_sidecar():
    prices = {("TSLA", "2026-06-01"): 100.0, ("TSLA", "2026-06-08"): 110.0,
              ("SPY", "2026-06-01"): 100.0, ("SPY", "2026-06-08"): 105.0}
    rows = [{"run_id": "c1", "ticker": "TSLA", "date_utc": "2026-06-01",
             "mode_rating": "Buy", "no_call": False, "judge_mix": CURSOR_MIX}]
    out, _ = ledger.resolve_rows(rows, set(), "TSLA", 5, "SPY", date(2026, 7, 1),
                                 lambda s, d: prices.get((s, d)))
    assert out[0]["judge_mix"] == CURSOR_MIX


def test_resolve_rows_legacy_row_judge_mix_none():
    prices = {("A", "2026-06-01"): 100.0, ("A", "2026-06-08"): 110.0,
              ("SPY", "2026-06-01"): 100.0, ("SPY", "2026-06-08"): 105.0}
    rows = [{"run_id": "l1", "ticker": "A", "date_utc": "2026-06-01",
             "mode_rating": "Buy", "no_call": False}]   # no judge_mix (legacy)
    out, _ = ledger.resolve_rows(rows, set(), "A", 5, "SPY", date(2026, 7, 1),
                                 lambda s, d: prices.get((s, d)))
    assert out[0]["judge_mix"] is None


# ---------- calibration footer regime split ----------

def test_footer_legacy_single_line_unchanged(tmp_path):
    foot = _footer(tmp_path, [
        {"ticker": "AAOI", "resolution_date": "2026-06-29", "alpha": 0.05,
         "hit": True, "benchmark": "SPY", "horizon_td": 21}])   # no judge_mix
    assert "Resolved calls (N=1):" in foot      # bare legacy form, no [regime] label
    assert "[" not in foot


def test_footer_cursor_only_is_labelled(tmp_path):
    foot = _footer(tmp_path, [
        {"ticker": "AAOI", "resolution_date": "2026-06-21", "alpha": 0.03,
         "hit": True, "benchmark": "SPY", "horizon_td": 21, "judge_mix": CURSOR_MIX}])
    assert f"Resolved calls [{CURSOR_REGIME}] (N=1):" in foot


def test_footer_splits_regimes_never_blends(tmp_path):
    foot = _footer(tmp_path, [
        {"ticker": "AAOI", "resolution_date": "2026-06-20", "alpha": 0.05,
         "hit": True, "benchmark": "SPY", "horizon_td": 21},           # legacy opus
        {"ticker": "AAOI", "resolution_date": "2026-06-21", "alpha": -0.02,
         "hit": False, "benchmark": "SPY", "horizon_td": 21,
         "judge_mix": CURSOR_MIX}])                                     # cursor panel
    assert "[claude/opus] (N=1)" in foot
    assert f"[{CURSOR_REGIME}] (N=1)" in foot
    assert foot.count("Resolved calls") == 2    # two regime lines, never blended


def test_read_cli_shows_regime_footer(tmp_path):
    led = tmp_path / "ledger.jsonl"
    led.write_text(json.dumps({"run_id": "c1", "ticker": "AAOI",
                               "date_utc": "2026-06-01", "mode_rating": "Buy",
                               "spread": 0, "no_call": False, "report_path": "a.md"}) + "\n")
    (tmp_path / "ledger-resolved.jsonl").write_text(
        json.dumps({"ticker": "AAOI", "resolution_date": "2026-06-29", "alpha": 0.05,
                    "hit": True, "benchmark": "SPY", "horizon_td": 21,
                    "judge_mix": CURSOR_MIX}) + "\n")
    r = _run("--ledger", str(led), "read", "--ticker", "AAOI", "--before", "2026-07-05")
    assert r.returncode == 0 and f"[{CURSOR_REGIME}]" in r.stdout
