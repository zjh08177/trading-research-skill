"""Tests for action_plan.py — the pure join/format logic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "batch"))
import action_plan as ap  # noqa: E402

REG = [
    {"ticker": "AAA", "kind": "equity", "asof": "2026-07-05", "spot": 100.0,
     "downside": {"level": 90.0, "action": "Exit", "basis": "SMA200", "atr_dist": 2.0},
     "upside": {"level": 110.0, "action": "Add", "basis": "SMA20", "atr_dist": 2.0}},
    {"ticker": "BBB", "kind": "equity", "asof": "2026-07-05", "spot": 50.0,
     "downside": {"level": 45.0, "action": "Trim", "basis": "SMA50", "atr_dist": 1.0},
     "upside": None},
]
RATINGS = {"AAA": {"rating": "Hold", "as_of": "2026-07-05", "votes": "5×Hold"},
           "BBB": {"rating": "Sell", "as_of": "2026-07-05", "votes": "5×Sell"}}
HOLD = {"AAA": {"symbol": "AAA", "pct_of_book": 8.0, "qty": 10},
        "BBB": {"symbol": "BBB", "pct_of_book": 2.0, "qty": 5}}
CLS = {"AAA": {"sector": "Semis"}, "BBB": {"sector": "Fintech"}}


def rows(prices):
    return {r["ticker"]: r for r in ap.build_rows(REG, RATINGS, HOLD, prices, CLS)}


def test_derived_atr_from_registry_triple():
    # |100-90| / 2.0 ATR-multiples = ATR 5.0
    assert ap.derived_atr(REG[0]) == 5.0


def test_upside_fire_verbatim_action_and_sort_first():
    out = ap.build_rows(REG, RATINGS, HOLD, {"AAA": 111.0, "BBB": 48.0}, CLS)
    assert out[0]["ticker"] == "AAA" and out[0]["fired"][0]["action"] == "Add"
    assert "REVIEW" in out[0]["plan"]
    assert "ACT — Add" not in out[0]["plan"]


def test_signed_distances_both_sides():
    r = rows({"AAA": 105.0, "BBB": 48.0})["AAA"]
    # downside cushion: (105-90)/105 = +14.3%; upside headroom: (110-105)/105 = +4.8%
    assert round(r["dn"]["dist_pct"], 1) == 14.3
    assert round(r["up"]["dist_pct"], 1) == 4.8
    assert round(r["dn"]["dist_atr"], 1) == 3.0  # 15/5


def test_knife_edge_flags_near_level():
    r = rows({"AAA": 109.8, "BBB": 48.0})["AAA"]  # 0.18% below Add trigger
    assert r["knife_edge"] and "WAIT" in r["plan"]
    assert "AT TRIGGER" not in r["plan"]


def test_missing_price_plan():
    r = rows({"BBB": 48.0})["AAA"]
    assert r["plan"] == "PRICE UNAVAILABLE — re-check"


def test_filter_registry_to_current_holdings():
    kept, dropped = ap.filter_registry_to_holdings(REG, {"AAA": HOLD["AAA"]})
    assert [r["ticker"] for r in kept] == ["AAA"]
    assert dropped == ["BBB"]


def test_render_contains_queue_and_provenance(tmp_path):
    out = ap.build_rows(REG, RATINGS, HOLD, {"AAA": 111.0, "BBB": 48.0}, CLS)
    md = ap.render_md(out, "2026-07-06",
                      {"reg_asof": "2026-07-05", "book": 1000.0, "n_accounts": 2,
                       "price_time": "t", "bad_ledger": 0, "unmonitored": "CCC",
                       "not_held": "DDD", "malformed": ""})
    assert "## Action queue" in md and "**REVIEW" in md
    assert "**ACT — Add" not in md
    assert "no new ratings" in md and "CCC" in md and "DDD" in md


def test_amd_hold_replay_crossed_add_is_review_not_act():
    reg = [{"ticker": "AMD", "kind": "equity", "asof": "2026-07-05", "spot": 517.82,
            "downside": {"level": 460.38, "action": "Exit", "basis": "SMA50", "atr_dist": 1.6},
            "upside": {"level": 547.65, "action": "Add", "basis": "day-high", "atr_dist": 0.83}}]
    ratings = {"AMD": {"rating": "Hold", "as_of": "2026-07-05", "votes": "5×Hold"}}
    hold = {"AMD": {"symbol": "AMD", "pct_of_book": 7.9, "qty": 60}}
    row = ap.build_rows(reg, ratings, hold, {"AMD": 551.444}, {"AMD": {"sector": "Semis"}})[0]
    assert row["fired"][0]["state"] == "crossed_unconfirmed"
    assert row["plan"].startswith("REVIEW")
    assert "ACT" not in row["plan"]


def test_msft_hold_replay_near_trim_is_wait_not_live():
    reg = [{"ticker": "MSFT", "kind": "equity", "asof": "2026-07-05", "spot": 390.49,
            "downside": {"level": 386.96, "action": "Trim", "basis": "SMA20", "atr_dist": 0.27},
            "upside": {"level": 407.6, "action": "Add", "basis": "SMA50", "atr_dist": 1.31}}]
    ratings = {"MSFT": {"rating": "Hold", "as_of": "2026-07-05", "votes": "5×Hold"}}
    hold = {"MSFT": {"symbol": "MSFT", "pct_of_book": 5.0, "qty": 50}}
    row = ap.build_rows(reg, ratings, hold, {"MSFT": 387.168}, {"MSFT": {"sector": "Megacap"}})[0]
    assert row["knife_edge"]
    assert row["plan"].startswith("WAIT")
    assert "AT TRIGGER" not in row["plan"]


def test_confirmed_act_trigger_still_emits_act():
    reg = [{"ticker": "BBB", "kind": "equity", "asof": "2026-07-05", "schema": 2, "spot": 100.0,
            "triggers": [{"side": "downside", "level": 90.0, "intended_action": "Exit",
                          "basis": "risk stop", "comparison": "intraday_below",
                          "action_strength": "act", "conditions": []}]}]
    ratings = {"BBB": {"rating": "Sell", "as_of": "2026-07-05", "votes": "5×Sell"}}
    hold = {"BBB": {"symbol": "BBB", "pct_of_book": 2.0, "qty": 5}}
    row = ap.build_rows(reg, ratings, hold, {"BBB": 85.0}, {"BBB": {"sector": "Fintech"}})[0]
    assert row["fired"][0]["state"] == "confirmed_act"
    assert row["plan"].startswith("ACT — Exit")


def test_main_consumes_snapshot_envelope(tmp_path):
    """The <holdings.json> arg now points at the day's snapshot envelope (single
    holdings SSOT); action_plan must unwrap it exactly like a raw dump."""
    import json
    levels = tmp_path / "levels"; levels.mkdir()
    (levels / "AAA.json").write_text(json.dumps(REG[0]))
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({"ticker": "AAA", "as_of": "2026-07-05",
                                  "mode_rating": "Hold", "distribution": {"Hold": 5}}) + "\n")
    raw = {"total_book": 1000.0, "n_accounts": 2,
           "holdings": [{"symbol": "AAA", "kind": "equity", "pct_of_book": 8.0, "qty": 10}]}
    envelope = {"kind": "holdings-snapshot", "schema": 1, "vendor": raw}
    hold = tmp_path / "2026-07-06.json"; hold.write_text(json.dumps(envelope))
    (tmp_path / "prices.json").write_text(json.dumps({"AAA": 105.0}))
    (tmp_path / "cls.json").write_text(json.dumps(CLS))
    out_md = tmp_path / "action-plan-2026-07-06.md"
    rc = ap.main([str(levels), str(ledger), str(hold), str(tmp_path / "prices.json"),
                  str(tmp_path / "cls.json"), str(out_md), "2026-07-06"])
    assert rc == 0
    md = out_md.read_text()
    assert "AAA" in md and "$1,000" in md          # book from the unwrapped envelope


def test_latest_ratings_skips_malformed(tmp_path):
    p = tmp_path / "ledger.jsonl"
    p.write_text('{"ticker":"AAA","as_of":"2026-07-01","mode_rating":"Sell",'
                 '"distribution":{"Sell":3}}\n'
                 'not json\n'
                 '{"ticker":"AAA","as_of":"2026-07-05","mode_rating":"Hold",'
                 '"distribution":{"Hold":5}}\n')
    ratings, bad = ap.latest_ratings(str(p))
    assert bad == 1 and ratings["AAA"]["rating"] == "Hold"
    assert ratings["AAA"]["votes"] == "5×Hold"


def test_latest_ratings_ignores_future_rows(tmp_path):
    p = tmp_path / "ledger.jsonl"
    p.write_text('{"ticker":"AAA","as_of":"2026-07-01","mode_rating":"Sell",'
                 '"distribution":{"Sell":3}}\n'
                 '{"ticker":"AAA","as_of":"2026-07-07","mode_rating":"Buy",'
                 '"distribution":{"Buy":5}}\n')
    ratings, bad = ap.latest_ratings(str(p), before="2026-07-06")
    assert bad == 0 and ratings["AAA"]["rating"] == "Sell"
    ratings, _ = ap.latest_ratings(str(p))
    assert ratings["AAA"]["rating"] == "Buy"
