"""Static wiring checks for v2.4a usage/evolve integration, plus (below) the
v2.5 historical as-of replay wiring for scripts/batch/build_datapack.py.
The replay tests fake `run_cli`/`run_ledger`/`run_usage_start` (same style as
tests/test_build_datapack_options.py) so nothing hits the network or a real
subprocess."""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT / "scripts" / "batch"))
import build_datapack as bd  # noqa: E402


def test_skill_contract_wires_usage_and_evolve_modes():
    text = (ROOT / "SKILL.md").read_text()
    assert "scripts/usage.py start" in text
    assert "scripts/usage.py end" in text
    assert "scripts/usage.py fail" in text
    assert "--evolve" in text
    assert "scripts/evolve.py" in text
    assert "mode=evolve" in text


def test_batch_datapack_exports_usage_ids_for_children():
    text = (ROOT / "scripts" / "batch" / "build_datapack.py").read_text()
    assert "TRADING_RESEARCH_BATCH_ID" in text
    assert "usage.py" in text
    assert "TRADING_RESEARCH_INVOCATION_ID" in text
    assert '"invocation_id"' in text


def test_portfolio_workflow_terminates_usage_rows():
    text = (ROOT / "workflows" / "portfolio_pipeline.js").read_text()
    assert "usage.py end" in text
    assert "usage.py fail" in text
    assert "invocation_id" in text


def test_cursor_command_declares_host_and_mandatory_start():
    text = (ROOT / "hosts" / "cursor-command.md").read_text()
    assert "TRADING_RESEARCH_HOST=cursor" in text
    assert "/Users/bytedance/.claude/skills/trading-research/scripts/usage.py start" in text


# --- v2.5 historical as-of replay: build_datapack.py wiring -------------------

def _rfct(v, unit="x", asof="2026-07-01"):
    return {"v": v, "unit": unit, "asof": asof, "src": "test"}


def _replay_fake_run_cli(calls_out):
    """Canned vendor responses for the replay branch. schwab_bars always
    reports a settled bar dated 2026-07-01 regardless of the --asof it was
    probed with, so entry_market_asof deterministically falls back to
    effective_market_asof (conservative_fallback=True) -- no vendor call ever
    returns a bar strictly after the 2026-07-01 cutoff used by these tests."""
    def run_cli(name, args):
        calls_out.append((name, list(args)))
        if name == "schwab_bars":
            return 0, {"P1.price": _rfct(100.0, "USD", "2026-07-01"),
                       "P2.atr14": _rfct(5.0, "USD", "2026-07-01")}, ""
        if name == "tiingo_oracle":
            return 0, {"P1.px_close_oob": _rfct(100.05, "USD", "2026-07-01")}, ""
        if name == "edgar_fundamentals":
            return 0, {"P3.shares_outstanding": _rfct(1000000, "shares", "2026-07-01")}, ""
        if name == "marketaux_news":
            return 0, {"P5.headlines": {"v": [], "unit": "articles", "asof": "2026-07-01",
                                         "src": "marketaux"}}, ""
        return 1, None, f"unexpected call in replay branch: {name}"
    return run_cli


def test_build_datapack_exposes_replay_flag():
    parser = bd._build_arg_parser()
    on = parser.parse_args(["[]", "--replay"])
    off = parser.parse_args(["[]"])
    assert on.replay is True
    assert off.replay is False


def test_replay_invocation_writes_scope_skips_positions_and_live_only_facts(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(bd, "run_cli", _replay_fake_run_cli(calls))
    monkeypatch.setattr(bd, "run_ledger", lambda ticker: "No prior track record.")
    monkeypatch.setattr(bd, "run_usage_start", lambda *a, **k: "inv-test")
    monkeypatch.setattr(bd, "RUNS", str(tmp_path))

    bd.main(['[["TSLA", "equity"]]', "--asof", "2026-07-01", "--replay"])

    run_dir = tmp_path / "TSLA-2026-07-01-1300"
    scope = json.loads((run_dir / "00-scope.json").read_text())
    assert scope["mode"] == "replay"
    assert scope["requested_cutoff"] == "2026-07-01"
    assert scope["effective_market_asof"] == "2026-07-01"
    assert "entry_market_asof" in scope
    assert scope["entry_market_asof"] == "2026-07-01"
    assert scope["conservative_fallback"] is True

    assert not (run_dir / "15-position.json").exists()
    assert not (run_dir / "15-position.md").exists()

    datapack = json.loads((run_dir / "10-datapack.json").read_text())
    assert not any(k.startswith("P4.") for k in datapack)
    assert not any(k.startswith("P8.") for k in datapack)
    assert not any(name in ("schwab_quote", "schwab_options", "uw_options") for name, _ in calls)


def test_replay_asof_slash_date_normalized_before_vendor_calls(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(bd, "run_cli", _replay_fake_run_cli(calls))
    monkeypatch.setattr(bd, "run_ledger", lambda ticker: "No prior track record.")
    monkeypatch.setattr(bd, "run_usage_start", lambda *a, **k: "inv-test")
    monkeypatch.setattr(bd, "RUNS", str(tmp_path))

    bd.main(['[["TSLA", "equity"]]', "--asof", "2026/07/01", "--replay"])

    run_dir = tmp_path / "TSLA-2026-07-01-1300"
    assert run_dir.exists()
    scope = json.loads((run_dir / "00-scope.json").read_text())
    assert scope["requested_cutoff"] == "2026-07-01"

    # No vendor call ever sees a slash-date token -- the FIRST schwab_bars
    # call (the cutoff fetch, before any post-cutoff probing) must carry the
    # normalized ISO cutoff, and no call anywhere carries a "/" in --asof.
    assert calls[0] == ("schwab_bars", ["--ticker", "TSLA", "--asof", "2026-07-01"])
    for name, args in calls:
        if "--asof" in args:
            asof_val = args[args.index("--asof") + 1]
            assert "/" not in asof_val, f"{name} received un-normalized --asof {asof_val!r}"


def test_probe_entry_market_asof_returns_first_settled_bar_strictly_after_cutoff():
    responses = {
        "2026-07-02": None,           # e.g. weekend/holiday -- no settled bar yet
        "2026-07-03": "2026-07-01",   # stale probe response, still <= cutoff
        "2026-07-04": "2026-07-04",   # first settled close strictly after cutoff
        "2026-07-05": "2026-07-04",   # would also match, but probing must stop earlier
    }
    calls = []

    def fake_fetch(probe_date_iso):
        calls.append(probe_date_iso)
        return responses.get(probe_date_iso)

    result = bd.probe_entry_market_asof("2026-07-01", fake_fetch)
    assert result == "2026-07-04"
    assert calls == ["2026-07-02", "2026-07-03", "2026-07-04"]


def test_probe_entry_market_asof_returns_none_when_no_post_cutoff_bar_found():
    result = bd.probe_entry_market_asof("2026-07-01", lambda probe_date_iso: None, max_probe_days=3)
    assert result is None


def test_live_default_path_still_writes_positions_and_options(tmp_path, monkeypatch):
    calls = []

    def fake_run_cli(name, args):
        calls.append(name)
        if name == "schwab_bars":
            return 0, {"P1.price": _rfct(100.0), "P2.atr14": _rfct(5.0)}, ""
        if name == "schwab_quote":
            return 0, {"P1.last": _rfct(101.0)}, ""
        if name == "tiingo_oracle":
            return 0, {"P1.px_close_oob": _rfct(100.1)}, ""
        if name == "edgar_fundamentals":
            return 0, {}, ""
        if name == "marketaux_news":
            return 0, {"P5.headlines": {"v": [], "unit": "articles", "asof": "2026-07-01",
                                         "src": "marketaux"}}, ""
        if name == "schwab_options":
            return 0, {"P4.atm_iv_near": _rfct(0.4, "ratio")}, ""
        return 1, None, f"unexpected call in live branch: {name}"

    monkeypatch.setattr(bd, "run_cli", fake_run_cli)
    monkeypatch.setattr(bd, "run_ledger", lambda ticker: "No prior track record.")
    monkeypatch.setattr(bd, "run_usage_start", lambda *a, **k: "inv-test")
    monkeypatch.setattr(bd, "RUNS", str(tmp_path))

    holdings_path = tmp_path / "holdings.json"
    holdings_path.write_text(json.dumps({"holdings": []}))

    today = dt.date.today().isoformat()
    # No --options: exercises the DEFAULT live path (schwab_p4 options, same as
    # the pre-replay behavior) rather than the --options/uw_options branch.
    bd.main(['[["TSLA", "equity"]]', "--asof", today, "--holdings", str(holdings_path)])

    run_dir = tmp_path / f"TSLA-{today}-1300"
    assert (run_dir / "15-position.json").exists()
    assert (run_dir / "15-position.md").exists()
    assert not (run_dir / "00-scope.json").exists()  # scope.json is replay-only

    datapack = json.loads((run_dir / "10-datapack.json").read_text())
    assert "P4.atm_iv_near" in datapack
    assert "schwab_quote" in calls
