"""Lean pytest suite for the three trading-research scripts. Stdlib + pytest.
Run with the skill venv: `.venv/bin/python -m pytest tests`."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import ensemble  # noqa: E402
import qa_check  # noqa: E402


def _votes(tmp, specs):
    """specs: list of (rating, conviction) or a raw string for malformed."""
    for i, spec in enumerate(specs, 1):
        p = tmp / f"vote-{i}.md"
        if isinstance(spec, str):
            p.write_text(spec + "\n")
        else:
            r, c = spec
            p.write_text(f"analysis line\nVERDICT: {r} | CONVICTION: {c} | "
                         f"ENTRY-PATH: n/a - trend setup | WHY: reason {i} "
                         f"in one sentence.\n")
    return tmp


def _run(script, *args, **kw):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                          capture_output=True, text=True, **kw)


# ---------- ensemble ----------

def test_decide_boundaries():
    assert ensemble.decide(0, 3, 3) == "publish"
    assert ensemble.decide(1, 3, 3) == "publish"
    assert ensemble.decide(2, 3, 3) == "escalate"      # spread>=2 at N=3
    assert ensemble.decide(2, 5, 5) == "publish"
    assert ensemble.decide(3, 5, 5) == "no-call"       # spread>=3 at N=5
    assert ensemble.decide(0, 2, 3) == "no-call"       # thin ensemble


def test_six_votes_mode_spread_malformed(tmp_path):
    _votes(tmp_path, [("Buy", 7), ("Buy", 6), ("Buy", 5), ("StrongBuy", 8),
                      ("Hold", 4), "VERDICT: garbage line no contract"])
    votes, malformed = ensemble.collect(tmp_path)
    assert malformed == ["vote-6.md"]
    block, dec = ensemble.render(votes, malformed, 5)
    assert dec["mode_label"] == "Buy"          # 3 Buy is the mode
    assert dec["spread"] == 2                   # StrongBuy(5) - Hold(3)
    assert dec["decision"] == "publish"         # spread<3 at N=5
    assert dec["n_valid"] == 5
    assert "Excluded (malformed, 1): vote-6.md" in block
    assert "Actual N: 5 valid of 6 votes" in block
    assert "Most bullish:" in block and "Most bearish:" in block


def test_escalate_and_no_call_render(tmp_path):
    _votes(tmp_path, [("Sell", 6), ("Buy", 6), ("Hold", 5)])   # spread 2
    _, dec = ensemble.render(*ensemble.collect(tmp_path), 3)
    assert dec["decision"] == "escalate"
    d2 = tmp_path / "d2"
    d2.mkdir()
    _votes(d2, [("StrongSell", 8), ("Buy", 7), ("Hold", 5),
                ("Sell", 6), ("Buy", 6)])                      # spread 3
    block, dec = ensemble.render(*ensemble.collect(d2), 5)
    assert dec["decision"] == "no-call"
    assert "NO-CALL" in block


def test_cli_tally_writes_block_and_json(tmp_path):
    _votes(tmp_path, [("Buy", 7), ("Buy", 6), ("Hold", 5)])
    r = _run("ensemble.py", "tally", str(tmp_path), "--n-target", "3")
    assert r.returncode == 0
    assert "Ensemble Rating" in r.stdout
    assert json.loads(r.stderr.strip())["decision"] == "publish"


# ---------- ensemble: median-notch (#8) ----------

def test_render_includes_central_tendency(tmp_path):
    """Block shows a median + mean notch line beside the mode headline."""
    _votes(tmp_path, [("Buy", 7), ("Buy", 6), ("Hold", 5)])
    block, dec = ensemble.render(*ensemble.collect(tmp_path), 3)
    assert "Central tendency:" in block
    assert "median" in block and "mean" in block
    assert dec["median_notch"] == 4.0        # median of [4,4,3]
    assert dec["mean_notch"] == round((4 + 4 + 3) / 3, 1)


def test_mode_headline_byte_identical(tmp_path):
    """Adding central tendency must not alter the mode headline line."""
    _votes(tmp_path, [("Buy", 7), ("Buy", 6), ("Hold", 5)])
    block, _ = ensemble.render(*ensemble.collect(tmp_path), 3)
    assert "### Ensemble Rating: **Buy**" in block
    # the median/mean must live on their OWN line, never in the headline
    head = [ln for ln in block.splitlines() if ln.startswith("### Ensemble Rating:")][0]
    assert "median" not in head and "mean" not in head


def test_unanimous_all_aggregates_equal_mode(tmp_path):
    """Unanimous ensemble → median label == mean == mode."""
    _votes(tmp_path, [("Buy", 7), ("Buy", 6), ("Buy", 8)])
    block, dec = ensemble.render(*ensemble.collect(tmp_path), 3)
    assert dec["mode_label"] == "Buy"
    assert dec["median_notch"] == 4.0 and dec["mean_notch"] == 4.0
    ct = [ln for ln in block.splitlines() if ln.startswith("Central tendency:")][0]
    assert "mode Buy" in ct and "median Buy" in ct


def _votes_v2(tmp, specs):
    """specs: list of (rating, conviction, entry_path) or a raw malformed string."""
    for i, spec in enumerate(specs, 1):
        p = tmp / f"vote-{i}.md"
        if isinstance(spec, str):
            p.write_text(spec + "\n")
        else:
            r, c, ep = spec
            p.write_text(
                f"analysis line\nVERDICT: {r} | CONVICTION: {c} | "
                f"ENTRY-PATH: {ep} | WHY: reason {i} in one sentence.\n")
    return tmp


def test_parse_vote_reads_entry_path(tmp_path):
    _votes_v2(tmp_path, [("Hold", 6, "left-side pending (2/4 conditions met)")])
    parsed = ensemble.parse_vote(tmp_path / "vote-1.md")
    assert parsed is not None
    notch, conv, entry_path, why, verbatim, model = parsed
    assert entry_path == "left-side pending (2/4 conditions met)"
    assert notch == 3 and conv == 6


def test_parse_vote_missing_entry_path_is_malformed(tmp_path):
    # A 3-field legacy-shaped line (no ENTRY-PATH) must be treated as
    # malformed, never silently accepted as a degraded 3-field vote.
    p = tmp_path / "vote-1.md"
    p.write_text("VERDICT: Hold | CONVICTION: 6 | WHY: legacy shape, no entry path.\n")
    assert ensemble.parse_vote(p) is None


def test_render_lists_entry_path_per_vote(tmp_path):
    _votes_v2(tmp_path, [
        ("Hold", 6, "left-side pending (2/4 conditions met)"),
        ("Hold", 5, "n/a - trend setup"),
        ("Hold", 7, "right-side confirmed"),
    ])
    votes, malformed = ensemble.collect(tmp_path)
    assert not malformed
    block, decision = ensemble.render(votes, malformed, 3)
    assert "left-side pending (2/4 conditions met)" in block
    assert "n/a - trend setup" in block
    assert "right-side confirmed" in block


# ---------- ledger ----------

def test_read_before_excludes_same_day(tmp_path):
    led = tmp_path / "ledger.jsonl"
    rows = [
        {"run_id": "r1", "ticker": "AAOI", "date_utc": "2026-07-01",
         "mode_rating": "Buy", "spread": 1, "no_call": False,
         "report_path": "a.md"},
        {"run_id": "r2", "ticker": "AAOI", "date_utc": "2026-07-03",
         "mode_rating": "Hold", "spread": 2, "no_call": False,
         "report_path": "b.md"},
    ]
    led.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    r = _run("ledger.py", "--ledger", str(led), "read", "--ticker", "aaoi",
             "--before", "2026-07-03T10:00:00Z")
    assert "2026-07-01" in r.stdout          # prior day kept
    assert "2026-07-03" not in r.stdout      # same day excluded (guard)
    assert r.returncode == 0


def test_read_no_rows(tmp_path):
    led = tmp_path / "ledger.jsonl"
    led.write_text("")
    r = _run("ledger.py", "--ledger", str(led), "read", "--ticker", "XYZ",
             "--before", "2026-07-03")
    assert "No prior track record" in r.stdout


def test_append_and_readback(tmp_path):
    led = tmp_path / "sub" / "ledger.jsonl"
    row = {"run_id": "r9", "ticker": "NVDA", "date_utc": "2026-06-01",
           "as_of": "2026-06-01T20:00:00Z", "job": "J1", "mode_rating": "Buy",
           "distribution": {"Buy": 3}, "spread": 0, "no_call": False,
           "gaps": [], "report_path": "nvda.md", "cost_usd": 5.1,
           "wall_s": 700}
    r = _run("ledger.py", "--ledger", str(led), "append", "--row",
             json.dumps(row))
    assert r.returncode == 0 and "appended: r9" in r.stdout
    r2 = _run("ledger.py", "--ledger", str(led), "read", "--ticker", "NVDA",
              "--before", "2026-07-01")
    assert "Buy" in r2.stdout and "2026-06-01" in r2.stdout


def test_append_missing_key_exits_2(tmp_path):
    led = tmp_path / "ledger.jsonl"
    r = _run("ledger.py", "--ledger", str(led), "append", "--row",
             json.dumps({"run_id": "x"}))
    assert r.returncode == 2 and "missing keys" in r.stderr


def test_append_write_failure_prints_row_exits_2(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    led = blocker / "ledger.jsonl"          # parent is a file -> mkdir fails
    row = {k: "x" for k in ["run_id", "ticker", "date_utc", "as_of", "job",
                            "mode_rating", "distribution", "spread", "no_call",
                            "gaps", "report_path", "cost_usd", "wall_s"]}
    r = _run("ledger.py", "--ledger", str(led), "append", "--row",
             json.dumps(row))
    assert r.returncode == 2
    assert "MANUAL-APPEND REQUIRED" in r.stdout
    assert '"run_id": "x"' in r.stdout      # the row is printed for recovery


# ---------- ledger: resolve loop + calibration (#7) ----------

def test_add_trading_days_skips_weekends():
    import ledger
    from datetime import date
    # 2026-07-01 is a Wednesday; +2 td = Fri 07-03; +3 td = Mon 07-06 (weekend skipped)
    assert ledger.add_trading_days(date(2026, 7, 1), 2) == date(2026, 7, 3)
    assert ledger.add_trading_days(date(2026, 7, 1), 3) == date(2026, 7, 6)


def test_resolve_rows_computes_alpha_and_hit():
    import ledger
    from datetime import date
    prices = {("AAOI", "2026-06-01"): 100.0, ("AAOI", "2026-06-08"): 110.0,
              ("SPY", "2026-06-01"): 100.0, ("SPY", "2026-06-08"): 105.0}
    rows = [{"run_id": "r1", "ticker": "AAOI", "date_utc": "2026-06-01",
             "mode_rating": "Buy", "no_call": False}]
    out, skipped = ledger.resolve_rows(rows, set(), "AAOI", 5, "SPY", date(2026, 7, 1),
                                       lambda s, d: prices.get((s, d)))
    assert len(out) == 1 and skipped == 0
    row = out[0]
    assert row["resolution_date"] == "2026-06-08"        # +5 td from Mon 06-01
    assert abs(row["realized_return"] - 0.10) < 1e-9
    assert abs(row["alpha"] - 0.05) < 1e-9               # 10% - 5%
    assert row["direction"] == 1 and row["hit"] is True


def test_resolve_rows_short_call_hit_on_negative_alpha():
    import ledger
    from datetime import date
    prices = {("X", "2026-06-01"): 100.0, ("X", "2026-06-08"): 90.0,
              ("SPY", "2026-06-01"): 100.0, ("SPY", "2026-06-08"): 100.0}
    rows = [{"run_id": "s1", "ticker": "X", "date_utc": "2026-06-01",
             "mode_rating": "Sell", "no_call": False}]
    out, _ = ledger.resolve_rows(rows, set(), "X", 5, "SPY", date(2026, 7, 1),
                                 lambda s, d: prices.get((s, d)))
    assert out[0]["direction"] == -1 and out[0]["hit"] is True   # -10% alpha, short → hit


def test_resolve_rows_skips_unaged_and_resolved_and_hold():
    import ledger
    from datetime import date
    pf = lambda s, d: 100.0
    base = {"ticker": "AAOI", "date_utc": "2026-06-25", "no_call": False}
    rows = [
        {**base, "run_id": "recent", "mode_rating": "Buy"},          # unaged
        {**base, "run_id": "done", "date_utc": "2026-06-01", "mode_rating": "Buy"},  # resolved
        {**base, "run_id": "hold", "date_utc": "2026-06-01", "mode_rating": "Hold"}, # no direction
    ]
    out, skipped = ledger.resolve_rows(rows, {"done"}, "AAOI", 5, "SPY", date(2026, 6, 30), pf)
    assert out == [] and skipped == 0   # recent: unaged; done: resolved; hold: excluded


def test_resolve_rows_counts_missing_price_skips():
    """A settled directional call whose price can't be fetched → skipped count,
    not a silent forever-omission."""
    import ledger
    from datetime import date
    rows = [{"run_id": "gap", "ticker": "DEAD", "date_utc": "2026-06-01",
             "mode_rating": "Buy", "no_call": False}]
    out, skipped = ledger.resolve_rows(rows, set(), "DEAD", 5, "SPY",
                                       date(2026, 7, 1), lambda s, d: None)
    assert out == [] and skipped == 1


def test_read_skips_malformed_sidecar_line_keeps_table(tmp_path):
    """A corrupt sidecar line must NOT crash read or discard the valid table."""
    led = tmp_path / "ledger.jsonl"
    led.write_text(json.dumps({"run_id": "r1", "ticker": "AAOI",
                               "date_utc": "2026-06-01", "mode_rating": "Buy",
                               "spread": 0, "no_call": False, "report_path": "a.md"}) + "\n")
    side = tmp_path / "ledger-resolved.jsonl"
    side.write_text('not valid json {{{\n' +
                    json.dumps({"run_id": "r1", "ticker": "AAOI",
                                "date_utc": "2026-06-01", "resolution_date": "2026-06-29",
                                "alpha": 0.05, "direction": 1, "hit": True}) + "\n")
    r = _run("ledger.py", "--ledger", str(led), "read", "--ticker", "AAOI",
             "--before", "2026-07-05")
    assert r.returncode == 0
    assert "2026-06-01" in r.stdout            # valid table still printed
    assert "Resolved calls (N=1)" in r.stdout  # aggregate from the good line
    assert "skip" in r.stderr.lower() or "malformed" in r.stderr.lower()


def test_read_malformed_main_ledger_line_no_crash(tmp_path):
    led = tmp_path / "ledger.jsonl"
    led.write_text('garbage not json\n' +
                   json.dumps({"run_id": "r1", "ticker": "AAOI",
                               "date_utc": "2026-06-01", "mode_rating": "Buy",
                               "spread": 0, "no_call": False, "report_path": "a.md"}) + "\n")
    r = _run("ledger.py", "--ledger", str(led), "read", "--ticker", "AAOI",
             "--before", "2026-07-05")
    assert r.returncode == 0 and "2026-06-01" in r.stdout


def test_read_appends_calibration_from_sidecar(tmp_path):
    led = tmp_path / "ledger.jsonl"
    led.write_text(json.dumps({"run_id": "r1", "ticker": "AAOI",
                               "date_utc": "2026-06-01", "mode_rating": "Buy",
                               "spread": 0, "no_call": False, "report_path": "a.md"}) + "\n")
    side = tmp_path / "ledger-resolved.jsonl"
    side.write_text(json.dumps({"run_id": "r1", "ticker": "AAOI",
                                "date_utc": "2026-06-01", "resolution_date": "2026-06-29",
                                "alpha": 0.05, "direction": 1, "hit": True}) + "\n")
    r = _run("ledger.py", "--ledger", str(led), "read", "--ticker", "AAOI",
             "--before", "2026-07-05")
    assert "Resolved calls (N=1)" in r.stdout and "hit-rate" in r.stdout


def test_read_calibration_lookahead_excludes_same_or_future_resolution(tmp_path):
    led = tmp_path / "ledger.jsonl"
    led.write_text(json.dumps({"run_id": "r1", "ticker": "AAOI",
                               "date_utc": "2026-06-01", "mode_rating": "Buy",
                               "spread": 0, "no_call": False, "report_path": "a.md"}) + "\n")
    side = tmp_path / "ledger-resolved.jsonl"
    # resolution_date == before → must be excluded (strict look-ahead guard)
    side.write_text(json.dumps({"run_id": "r1", "ticker": "AAOI",
                                "date_utc": "2026-06-01", "resolution_date": "2026-07-05",
                                "alpha": 0.05, "direction": 1, "hit": True}) + "\n")
    r = _run("ledger.py", "--ledger", str(led), "read", "--ticker", "AAOI",
             "--before", "2026-07-05")
    assert "Resolved calls" not in r.stdout


def test_resolve_cli_no_aged_rows_is_clean(tmp_path):
    """CLI resolve on only-recent rows resolves 0 (no price fetch needed) — honest."""
    led = tmp_path / "ledger.jsonl"
    led.write_text(json.dumps({"run_id": "r1", "ticker": "AAOI",
                               "date_utc": "2026-07-04", "mode_rating": "Buy",
                               "spread": 0, "no_call": False, "report_path": "a.md"}) + "\n")
    r = _run("ledger.py", "--ledger", str(led), "resolve", "--ticker", "AAOI",
             "--asof", "2026-07-05")
    assert r.returncode == 0 and "0 resolved" in r.stdout
    assert not (tmp_path / "ledger-resolved.jsonl").exists()  # nothing written


# ---------- ledger: historical as-of replay lane ----------

def _replay_row(run_id, **over):
    row = {"run_id": run_id, "ticker": "AAOI", "generated_at": "2026-06-01T12:00:00Z",
           "requested_cutoff": "2026-06-01T20:00:00Z",
           "effective_market_asof": "2026-06-01", "entry_market_asof": "2026-06-01",
           "job": "J1", "mode_rating": "Buy", "distribution": {"Buy": 3},
           "spread": 0, "no_call": False, "gaps": [],
           "judge_mix": ["opus", "opus", "opus"], "report_path": "aaoi.md",
           "cost_usd": 5.1, "wall_s": 700, "evidence_type": "replay"}
    row.update(over)
    return row


def test_replay_path_helpers():
    import ledger
    main = Path("/tmp/x/ledger.jsonl")
    assert ledger.replay_path(main) == Path("/tmp/x/ledger-replay.jsonl")
    assert ledger.replay_resolved_path(main) == Path("/tmp/x/ledger-replay-resolved.jsonl")
    row = {"run_id": "r1", "horizon_td": 5, "benchmark": "SPY", "evidence_type": "replay"}
    assert ledger.resolved_key(row) == ("r1", 5, "SPY", "replay")


def test_replay_append_writes_replay_ledger_leaves_live_unchanged(tmp_path):
    led = tmp_path / "ledger.jsonl"
    led.write_text(json.dumps({"run_id": "live1", "ticker": "AAOI",
                               "date_utc": "2026-05-01", "as_of": "2026-05-01T20:00:00Z",
                               "job": "J0", "mode_rating": "Hold", "distribution": {},
                               "spread": 0, "no_call": False, "gaps": [],
                               "report_path": "x.md", "cost_usd": 1.0, "wall_s": 10}) + "\n")
    before = led.read_text()
    row = _replay_row("rep1")
    r = _run("ledger.py", "--ledger", str(led), "append", "--replay",
             "--row", json.dumps(row))
    assert r.returncode == 0 and "appended: rep1" in r.stdout
    assert led.read_text() == before                        # live ledger byte-identical
    rpath = tmp_path / "ledger-replay.jsonl"
    assert rpath.exists()
    assert json.loads(rpath.read_text().strip()) == row


def test_replay_append_duplicate_identical_run_id_is_noop(tmp_path):
    led = tmp_path / "ledger.jsonl"
    row = _replay_row("rep2")
    r1 = _run("ledger.py", "--ledger", str(led), "append", "--replay",
              "--row", json.dumps(row))
    r2 = _run("ledger.py", "--ledger", str(led), "append", "--replay",
              "--row", json.dumps(row))
    assert r1.returncode == 0 and r2.returncode == 0
    lines = (tmp_path / "ledger-replay.jsonl").read_text().splitlines()
    assert len(lines) == 1                     # identical repeat is a no-op, not a dup line


def test_replay_append_conflicting_duplicate_run_id_fails(tmp_path):
    led = tmp_path / "ledger.jsonl"
    row = _replay_row("rep3")
    r1 = _run("ledger.py", "--ledger", str(led), "append", "--replay",
              "--row", json.dumps(row))
    row2 = _replay_row("rep3", mode_rating="Sell")
    r2 = _run("ledger.py", "--ledger", str(led), "append", "--replay",
              "--row", json.dumps(row2))
    assert r1.returncode == 0
    assert r2.returncode == 2 and "already exists" in r2.stderr
    lines = (tmp_path / "ledger-replay.jsonl").read_text().splitlines()
    assert len(lines) == 1                     # rejected write never landed


def test_replay_append_rejects_non_replay_evidence_type(tmp_path):
    led = tmp_path / "ledger.jsonl"
    row = _replay_row("rep4", evidence_type="live")
    r = _run("ledger.py", "--ledger", str(led), "append", "--replay",
             "--row", json.dumps(row))
    assert r.returncode == 2 and "evidence_type" in r.stderr
    assert not (tmp_path / "ledger-replay.jsonl").exists()


def test_replay_append_missing_key_exits_2(tmp_path):
    led = tmp_path / "ledger.jsonl"
    r = _run("ledger.py", "--ledger", str(led), "append", "--replay",
             "--row", json.dumps({"run_id": "x", "evidence_type": "replay"}))
    assert r.returncode == 2 and "missing keys" in r.stderr


def test_read_ignores_replay_ledger(tmp_path):
    led = tmp_path / "ledger.jsonl"
    led.write_text(json.dumps({"run_id": "live1", "ticker": "AAOI",
                               "date_utc": "2026-05-01", "mode_rating": "Hold",
                               "spread": 0, "no_call": False, "report_path": "x.md"}) + "\n")
    rpath = tmp_path / "ledger-replay.jsonl"
    rpath.write_text(json.dumps(_replay_row("rep5", entry_market_asof="2026-05-15")) + "\n")
    r = _run("ledger.py", "--ledger", str(led), "read", "--ticker", "AAOI",
             "--before", "2026-07-01")
    assert r.returncode == 0
    assert "2026-05-01" in r.stdout
    assert "rep5" not in r.stdout and "2026-05-15" not in r.stdout


def test_resolve_replay_rows_multi_horizon_coexist():
    """1td and 5td resolved rows for the same run_id coexist via resolved_key,
    and a horizon already present is skipped on a re-run."""
    import ledger
    from datetime import date
    prices = {
        ("AAOI", "2026-06-01"): (100.0, "2026-06-01"),
        ("AAOI", "2026-06-02"): (102.0, "2026-06-02"),
        ("AAOI", "2026-06-08"): (110.0, "2026-06-08"),
        ("SPY", "2026-06-01"): (100.0, "2026-06-01"),
        ("SPY", "2026-06-02"): (100.5, "2026-06-02"),
        ("SPY", "2026-06-08"): (105.0, "2026-06-08"),
    }
    pf = lambda s, d: prices.get((s, d))
    row = _replay_row("multi1")
    out1, sk1 = ledger.resolve_replay_rows([row], set(), "AAOI", 1, "SPY",
                                           date(2026, 7, 1), pf)
    out5, sk5 = ledger.resolve_replay_rows([row], set(), "AAOI", 5, "SPY",
                                           date(2026, 7, 1), pf)
    assert len(out1) == 1 and sk1 == 0 and out1[0]["horizon_td"] == 1
    assert len(out5) == 1 and sk5 == 0 and out5[0]["horizon_td"] == 5
    assert out1[0]["resolution_date"] == "2026-06-02"
    assert out5[0]["resolution_date"] == "2026-06-08"
    resolved_keys = {ledger.resolved_key(out1[0]), ledger.resolved_key(out5[0])}
    out1_again, _ = ledger.resolve_replay_rows([row], resolved_keys, "AAOI", 1, "SPY",
                                                date(2026, 7, 1), pf)
    assert out1_again == []                    # already-resolved horizon is skipped


def test_resolve_replay_rows_skips_stale_entry_bar():
    """price_fn returning a bar_date that != the requested entry date (e.g. a
    holiday landing on the last settled prior close) is a hard skip."""
    import ledger
    from datetime import date

    def pf(sym, d):
        if d == "2026-06-01":
            return (100.0, "2026-05-29")       # stale prior close, not the requested date
        return (105.0, d)

    row = _replay_row("stale1")
    out, skipped = ledger.resolve_replay_rows([row], set(), "AAOI", 5, "SPY",
                                              date(2026, 7, 1), pf)
    assert out == [] and skipped == 1


def test_resolve_replay_rows_skips_stale_benchmark_bar():
    """Same guard on the benchmark leg: a stale SPY bar must not be substituted."""
    import ledger
    from datetime import date

    def pf(sym, d):
        if sym == "SPY" and d == "2026-06-08":
            return (105.0, "2026-06-05")       # stale benchmark exit bar
        return (110.0, d)

    row = _replay_row("stale2")
    out, skipped = ledger.resolve_replay_rows([row], set(), "AAOI", 5, "SPY",
                                              date(2026, 7, 1), pf)
    assert out == [] and skipped == 1


def test_resolve_replay_cli_no_aged_rows_is_clean(tmp_path):
    led = tmp_path / "ledger.jsonl"
    rpath = tmp_path / "ledger-replay.jsonl"
    rpath.write_text(json.dumps(_replay_row("repclean", entry_market_asof="2026-07-08")) + "\n")
    r = _run("ledger.py", "--ledger", str(led), "resolve", "--replay",
             "--ticker", "AAOI", "--asof", "2026-07-10")
    assert r.returncode == 0 and "0 resolved" in r.stdout
    assert not (tmp_path / "ledger-replay-resolved.jsonl").exists()


# ---------- qa_check ----------

PACK = {"P2.atr14": {"v": 19.86}, "P3.margin": {"v": 42.5}}


def _write(tmp, body):
    rp = tmp / "rep.md"
    pp = tmp / "pack.json"
    rp.write_text(body)
    pp.write_text(json.dumps(PACK))
    return rp, pp


def test_tagged_hit(tmp_path):
    rp, pp = _write(tmp_path, "## Risk\nATR14 is 19.86 [P2.atr14] today.\n")
    r = _run("qa_check.py", str(rp), str(pp))
    assert r.returncode == 0 and "PASS P2.atr14" in r.stdout


def test_tagged_miss_exits_1(tmp_path):
    rp, pp = _write(tmp_path, "## Risk\nATR14 is 25.0 [P2.atr14] today.\n")
    r = _run("qa_check.py", str(rp), str(pp))
    assert r.returncode == 1 and "FAIL P2.atr14" in r.stdout


def test_untagged_number_in_thesis_flagged(tmp_path):
    rp, pp = _write(tmp_path, "## Thesis\nRevenue grew 40% last year.\n")
    r = _run("qa_check.py", str(rp), str(pp))
    assert r.returncode == 0                      # warning, not failure
    assert "untagged number '40%'" in r.stdout


def test_untagged_number_with_url_not_flagged(tmp_path):
    rp, pp = _write(tmp_path,
                    "## Thesis\nGuided to 40% growth (https://x.co/pr).\n")
    r = _run("qa_check.py", str(rp), str(pp))
    assert "untagged number" not in r.stdout


def test_approx_tolerance(tmp_path):
    # 20 vs pack 19.86 => 0.70% rel: fails exact (0.5%), passes approx (5%)
    ra, pa = _write(tmp_path, "## Risk\nATR is 20 [P2.atr14].\n")
    strict = _run("qa_check.py", str(ra), str(pa))
    assert strict.returncode == 1                 # 0.70% > 0.5% exact
    rb = tmp_path / "rep_approx.md"
    rb.write_text("## Risk\nATR ~20 [P2.atr14].\n")
    approx = _run("qa_check.py", str(rb), str(pa))
    assert approx.returncode == 0                 # 0.70% <= 5% approx


# ---------- risk_box (#1) ----------

RISK_PACK = {
    "P1.last": {"v": 244.42, "unit": "USD", "src": "schwab"},
    "P1.price": {"v": 241.91, "unit": "USD", "src": "schwab"},
    "P1.chg_pct_1d": {"v": -6.63, "unit": "pct", "src": "schwab"},
    "P2.atr14": {"v": 27.46, "unit": "USD", "src": "schwab"},
    "P2.atr14_pct": {"v": 11.35, "unit": "pct", "src": "schwab"},
    "P2.sigma30": {"v": 6.69, "unit": "pct", "src": "schwab"},
    "P2.sma50": {"v": 217.35, "unit": "USD", "src": "schwab"},
}


def _pack(tmp, pack):
    p = tmp / "10-datapack.json"
    p.write_text(json.dumps(pack))
    return p


def test_risk_box_emits_sentinel_block(tmp_path):
    r = _run("risk_box.py", str(_pack(tmp_path, RISK_PACK)))
    assert r.returncode == 0, r.stderr
    assert "riskbox-block: inserted verbatim" in r.stdout
    assert "riskbox-block: end" in r.stdout
    for tag in ("[P1.chg_pct_1d]", "[P2.atr14]", "[P2.sigma30]", "[P2.sma50]"):
        assert tag in r.stdout, tag
    assert "Context:" in r.stdout


def test_risk_box_today_move_in_atr(tmp_path):
    """6.63% move / 11.35% ATR = 0.58x ATR, sub-ATR → NORMAL context."""
    r = _run("risk_box.py", str(_pack(tmp_path, RISK_PACK)))
    assert "0.58" in r.stdout          # move/ATR ratio
    assert "NORMAL" in r.stdout and "ABNORMAL" not in r.stdout


def test_risk_box_abnormal_on_large_move(tmp_path):
    pack = {**RISK_PACK, "P1.chg_pct_1d": {"v": -20.0, "unit": "pct", "src": "schwab"}}
    r = _run("risk_box.py", str(_pack(tmp_path, pack)))
    assert "ABNORMAL" in r.stdout      # 20/11.35 = 1.76x >= 1.5x threshold


def test_risk_box_invalidation_anchor(tmp_path):
    """SMA50 217.35 − 1x ATR14 27.46 = 189.89 long-invalidation level."""
    r = _run("risk_box.py", str(_pack(tmp_path, RISK_PACK)))
    assert "189.89" in r.stdout


def test_risk_box_datagap_context_unknown(tmp_path):
    """chg absent → honest DATA GAP + Context UNKNOWN (not a false NORMAL)."""
    pack = {k: v for k, v in RISK_PACK.items() if k != "P1.chg_pct_1d"}
    r = _run("risk_box.py", str(_pack(tmp_path, pack)))
    assert r.returncode == 0
    assert "DATA GAP" in r.stdout and "P1.chg_pct_1d" in r.stdout
    assert "Context: UNKNOWN" in r.stdout
    assert "Context: NORMAL" not in r.stdout


def test_risk_box_atr_pct_zero_not_false_datagap(tmp_path):
    """chg IS present but atr14_pct==0 must NOT claim 'P1.chg_pct_1d absent'."""
    pack = {**RISK_PACK, "P2.atr14_pct": {"v": 0.0, "unit": "pct", "src": "schwab"}}
    r = _run("risk_box.py", str(_pack(tmp_path, pack)))
    assert r.returncode == 0
    assert "P1.chg_pct_1d absent" not in r.stdout    # the fact is present!
    assert "Context: UNKNOWN" in r.stdout


def test_risk_box_high_price_no_scientific_notation(tmp_path):
    """BRK.A-scale prices must not render in scientific notation / lose dollars."""
    pack = {**RISK_PACK,
            "P1.last": {"v": 712345.67, "unit": "USD", "src": "schwab"},
            "P2.sma50": {"v": 700000.5, "unit": "USD", "src": "schwab"},
            "P2.atr14": {"v": 5321.09, "unit": "USD", "src": "schwab"}}
    r = _run("risk_box.py", str(_pack(tmp_path, pack)))
    assert "e+0" not in r.stdout and "E+0" not in r.stdout
    assert "712345.67" in r.stdout


def test_risk_box_missing_fact_exits_3(tmp_path):
    pack = {k: v for k, v in RISK_PACK.items() if k != "P2.atr14"}
    r = _run("risk_box.py", str(_pack(tmp_path, pack)))
    assert r.returncode == 3 and "P2.atr14" in r.stderr


def test_qa_exempts_riskbox_block(tmp_path):
    """Derived (untagged) numbers inside the verbatim risk box never fail QA."""
    block = subprocess.run(
        [sys.executable, str(SCRIPTS / "risk_box.py"), str(_pack(tmp_path, RISK_PACK))],
        capture_output=True, text=True).stdout
    rp = tmp_path / "rep.md"
    rp.write_text("## Risk box\n" + block + "\nPlain risk prose with no numbers.\n")
    pp = tmp_path / "pack.json"
    pp.write_text(json.dumps(RISK_PACK))
    r = _run("qa_check.py", str(rp), str(pp))
    assert r.returncode == 0, r.stdout
    # the derived level 189.89 (no tag) must NOT be flagged untagged
    assert "189.89" not in r.stdout
    assert "untagged number '0.58" not in r.stdout


def test_qa_unterminated_riskbox_does_not_hide_later_errors(tmp_path):
    """Fail-safe: a riskbox start with NO end sentinel must not swallow the rest
    of the report — a wrong tagged number after it must still hard-fail."""
    rp = tmp_path / "r.md"
    rp.write_text("## Risk box\n<!-- riskbox-block: inserted verbatim, do not edit -->\n"
                  "### Risk box (computed)\n- derived 189.89 no end sentinel\n\n"
                  "## Thesis\nATR14 is 999.0 [P2.atr14] — WRONG, must FAIL.\n")
    pp = tmp_path / "p.json"
    pp.write_text('{"P2.atr14": {"v": 27.46, "unit": "USD", "src": "schwab"}}')
    r = _run("qa_check.py", str(rp), str(pp))
    assert r.returncode == 1 and "FAIL P2.atr14" in r.stdout


# ---------- mock end-to-end ----------

def test_end_to_end_chain(tmp_path):
    """ensemble tally -> insert rating block -> qa_check -> ledger append/read."""
    votes, malformed = ensemble.collect(FIXTURES / "votes")
    block, dec = ensemble.render(votes, malformed, 3)
    assert dec["decision"] == "publish" and dec["mode_label"] == "Buy"

    report_src = (FIXTURES / "report.md").read_text()
    assert "<!-- RATING-BLOCK-SLOT -->" in report_src
    report = report_src.replace("<!-- RATING-BLOCK-SLOT -->", block.rstrip())
    rp = tmp_path / "final.md"
    rp.write_text(report)

    qa = _run("qa_check.py", str(rp), str(FIXTURES / "datapack.json"))
    assert qa.returncode == 0, qa.stdout          # all tagged numbers hit

    led = tmp_path / "ledger.jsonl"
    row = {"run_id": "e2e", "ticker": "DEMO", "date_utc": "2026-07-03",
           "as_of": "2026-07-03T20:00:00Z", "job": "J1",
           "mode_rating": dec["mode_label"], "distribution": {"Buy": 2, "Hold": 1},
           "spread": dec["spread"], "no_call": dec["decision"] == "no-call",
           "gaps": [], "report_path": str(rp), "cost_usd": 4.2, "wall_s": 690}
    ap = _run("ledger.py", "--ledger", str(led), "append", "--row",
              json.dumps(row))
    assert ap.returncode == 0
    rd = _run("ledger.py", "--ledger", str(led), "read", "--ticker", "DEMO",
              "--before", "2026-07-05")
    assert "Buy" in rd.stdout


def test_qa_comma_before_tag_not_a_number(tmp_path):
    """Regression: '(derived, [P1.mcap]/...)' must not crash PAIR_RE (bare ',' matched as number)."""
    from qa_check import check_pairs
    pack = {"P1.mcap": {"v": 9705362669}}
    results = check_pairs("at ~20.5x trailing sales (derived, [P1.mcap]/[P3.revenue_ttm])", pack)
    assert results == []  # no numeric pair — and no ValueError


def test_qa_list_fact_numeric_tag_fails_not_crashes():
    """Regression: '126 [P4.iv_term]' with a list-valued pack fact must FAIL, not crash."""
    from qa_check import check_pairs
    pack = {"P4.iv_term": {"v": [["2026-07-10", 1.23]]}}
    results = check_pairs("ATM IV 126 [P4.iv_term]", pack)
    assert len(results) == 1 and results[0][0] is False and "non-scalar" in results[0][1]


def test_qa_pair_false_positive_classes():
    """Regression trio: 'ATR14' token, '-$0.81' sign-before-dollar, date-string facts."""
    from qa_check import check_pairs
    pack = {"P2.atr14": {"v": 20.29}, "P3.eps_diluted_ttm": {"v": -0.81},
            "P5.next_earnings": {"v": "2026-07-30"}}
    # word-embedded digits must not pair with the tag
    assert check_pairs("stated in ATR14 [P2.atr14] multiples", pack) == []
    # minus before dollar parses as negative
    ok, msg = check_pairs("EPS -$0.81 [P3.eps_diluted_ttm]", pack)[0]
    assert ok, msg
    # string-valued facts (dates) are skipped, not failed
    assert check_pairs("earnings 2026-07-30 [P5.next_earnings]", pack) == []


# ---------- qa_check: H1 position tags (15-position.json 2nd source) ----------

def test_qa_h_tag_matches_position(tmp_path):
    rp = tmp_path / "r.md"
    rp.write_text("## Your position\nYou hold 8.0% [H1.pct_of_book] of book, "
                  "up 60.0% [H1.unrealized_pl_pct].\n")
    pp = tmp_path / "p.json"
    pp.write_text("{}")
    posf = tmp_path / "pos.json"
    posf.write_text('{"H1.pct_of_book": {"v": 8.0, "unit": "%", "asof": "x", "src": "schwab"}, '
                    '"H1.unrealized_pl_pct": {"v": 60.0, "unit": "%", "asof": "x", "src": "schwab"}}')
    r = _run("qa_check.py", str(rp), str(pp), str(posf))
    assert r.returncode == 0, r.stdout
    assert "PASS H1.pct_of_book" in r.stdout


def test_qa_h_tag_mismatch_fails(tmp_path):
    rp = tmp_path / "r.md"
    rp.write_text("## Your position\n12.0% [H1.pct_of_book] of book.\n")
    pp = tmp_path / "p.json"
    pp.write_text("{}")
    posf = tmp_path / "pos.json"
    posf.write_text('{"H1.pct_of_book": {"v": 8.0, "unit": "%", "asof": "x", "src": "schwab"}}')
    r = _run("qa_check.py", str(rp), str(pp), str(posf))
    assert r.returncode == 1 and "FAIL H1.pct_of_book" in r.stdout


def test_qa_h_tag_without_source_fails(tmp_path):
    rp = tmp_path / "r.md"
    rp.write_text("## Your position\n8.0% [H1.pct_of_book].\n")
    pp = tmp_path / "p.json"
    pp.write_text("{}")
    r = _run("qa_check.py", str(rp), str(pp))
    assert r.returncode == 1 and "no pack entry" in r.stdout


# ---------- qa_check: unit-aware ratio/% + derived recompute (#2) ----------

def test_qa_ratio_percent_cite_passes(tmp_path):
    """A %-suffixed cite of a unit:ratio fact compares num/100 (the IV trap)."""
    rp = tmp_path / "r.md"
    rp.write_text("## Risk\nATM IV is 109.1% [P4.atm_iv_near] near-dated.\n")
    pp = tmp_path / "p.json"
    pp.write_text('{"P4.atm_iv_near": {"v": 1.091, "unit": "ratio", "src": "schwab"}}')
    r = _run("qa_check.py", str(rp), str(pp))
    assert r.returncode == 0, r.stdout
    assert "PASS P4.atm_iv_near" in r.stdout


def test_qa_ratio_percent_genuinely_wrong_still_fails(tmp_path):
    """120% vs ratio 1.091 (=1.20) is a real 10% error → still FAIL."""
    rp = tmp_path / "r.md"
    rp.write_text("## Risk\nATM IV is 120% [P4.atm_iv_near].\n")
    pp = tmp_path / "p.json"
    pp.write_text('{"P4.atm_iv_near": {"v": 1.091, "unit": "ratio", "src": "schwab"}}')
    r = _run("qa_check.py", str(rp), str(pp))
    assert r.returncode == 1 and "FAIL P4.atm_iv_near" in r.stdout


def test_qa_ratio_bare_cite_still_passes(tmp_path):
    """A bare (no %) ratio cite is unchanged — compares directly."""
    rp = tmp_path / "r.md"
    rp.write_text("## Risk\nPut/call OI 0.856 [P4.put_call_oi_ratio].\n")
    pp = tmp_path / "p.json"
    pp.write_text('{"P4.put_call_oi_ratio": {"v": 0.856, "unit": "ratio", "src": "schwab"}}')
    r = _run("qa_check.py", str(rp), str(pp))
    assert r.returncode == 0 and "PASS P4.put_call_oi_ratio" in r.stdout


def test_qa_derived_consistent_pack_passes():
    from qa_check import recompute_derived
    pack = {
        "P1.price": {"v": 241.91, "unit": "USD", "src": "schwab"},
        "P3.shares_outstanding": {"v": 186477898, "unit": "shares", "src": "sec-edgar"},
        "P1.mcap": {"v": 45110000000.0, "unit": "USD",
                    "src": "derived (P1.price x P3.shares_outstanding)"},
        "P3.eps_diluted_ttm": {"v": 1.76, "unit": "USD", "src": "sec-edgar"},
        "P3.pe_ttm": {"v": 137.4, "unit": "x",
                      "src": "derived (P1.price / P3.eps_diluted_ttm)"},
    }
    results, warnings = recompute_derived(pack)
    assert all(ok for ok, _ in results), [m for ok, m in results if not ok]
    assert len(results) == 2  # mcap + pe_ttm both recomputed


def test_qa_derived_tampered_fact_hard_fails():
    from qa_check import recompute_derived
    pack = {
        "P1.price": {"v": 241.91, "src": "schwab"},
        "P3.shares_outstanding": {"v": 186477898, "src": "sec-edgar"},
        "P1.mcap": {"v": 90000000000.0,  # 2x too big — tampered
                    "src": "derived (P1.price x P3.shares_outstanding)"},
    }
    results, _ = recompute_derived(pack)
    assert any((not ok) and "P1.mcap" in m for ok, m in results)


def test_qa_derived_missing_constituent_warns_not_fails():
    from qa_check import recompute_derived
    pack = {  # no P3.shares_outstanding present
        "P1.price": {"v": 241.91, "src": "schwab"},
        "P1.mcap": {"v": 45110000000.0,
                    "src": "derived (P1.price x P3.shares_outstanding)"},
    }
    results, warnings = recompute_derived(pack)
    assert all(ok for ok, _ in results)  # no hard fail
    assert any("P1.mcap" in w for w in warnings)


def test_qa_none_valued_fact_no_crash():
    """A tagged number against a fact with v=None must not crash float(None)."""
    from qa_check import check_pairs
    results = check_pairs("value 5 [P1.x]", {"P1.x": {"v": None}})
    assert isinstance(results, list)  # no TypeError


def test_qa_double_sign_token_no_crash():
    """A '--5' token captured by the sign-prefix regex must not crash to_float."""
    from qa_check import check_pairs
    results = check_pairs("double sign --5 [P3.x]", {"P3.x": {"v": -5}})
    assert isinstance(results, list)  # no ValueError


def test_qa_missing_position_file_no_crash(tmp_path):
    # back-dated/auth-fail runs pass a 15-position.json path that does not exist;
    # qa_check must fall back to 2-arg semantics, never crash with a traceback.
    rp, pp = _write(tmp_path, "## Risk\nATR14 is 19.86 [P2.atr14].\n")
    r = _run("qa_check.py", str(rp), str(pp), str(tmp_path / "absent-15-position.json"))
    assert r.returncode == 0 and "PASS P2.atr14" in r.stdout
    assert "Traceback" not in r.stderr
