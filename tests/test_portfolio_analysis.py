"""Tests for portfolio_analysis.py — deterministic book-structure math."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "batch"))
import portfolio_analysis as pa  # noqa: E402


def test_hhi_and_eff_bets():
    assert pa.hhi([0.5, 0.5]) == 0.5          # 2 equal bets -> eff 2
    assert round(1 / pa.hhi([0.25] * 4), 1) == 4.0


def test_theme_mapping():
    assert pa.theme_of("NVDA") == "AI/Semis complex"
    assert pa.theme_of("BTC") == "Crypto"
    assert pa.theme_of("TSLA") == "Auto/EV+AV"
    assert pa.theme_of("ZZZZ") == "Other"     # unknown -> Other


def test_integration_concentration_and_caps(tmp_path):
    # 3 names: two 45% AI/Semis (NVDA,AMD) + one 10% crypto (BTC); a cash MMF excluded.
    holds = {"total_book": 100.0, "holdings": [
        {"symbol": "NVDA", "market_value": 45.0, "pct_of_book": 45.0},
        {"symbol": "AMD", "market_value": 45.0, "pct_of_book": 45.0},
        {"symbol": "BTC", "market_value": 10.0, "pct_of_book": 10.0},
        {"symbol": "FDRXX", "market_value": 20.0, "pct_of_book": 20.0},  # cash -> excluded
    ]}
    hf = tmp_path / "h.json"
    hf.write_text(json.dumps(holds))
    r = subprocess.run([sys.executable, str(ROOT / "scripts/batch/portfolio_analysis.py"),
                        str(hf), "1999-01-01", "0000"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads((ROOT / "runs/_portfolio-1999-01-01/portfolio.json").read_text())
    assert out["n_analyzable"] == 3                       # cash excluded
    assert out["cash_pct"] == 20.0
    # invested weights: NVDA/AMD 45/100=0.45 each, BTC 0.10 -> HHI 0.415, eff ~2.4
    assert out["concentration"]["eff_names"] == 2.4
    # AI/Semis theme = NVDA+AMD = 90% -> over the 25% cap
    over = {o["theme"] for o in out["rebalance"]["over_theme"]}
    assert "AI/Semis complex" in over
    # NVDA/AMD each 45% and BTC 10% are all over the 8% name cap (of invested)
    assert {o["symbol"] for o in out["rebalance"]["over_name"]} == {"NVDA", "AMD", "BTC"}
