"""Lean pytest suite for the three trading-research scripts. Stdlib + pytest.
Run with the system python3: `python3 -m pytest trading-research-skill/tests`."""
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
            p.write_text(f"analysis line\nVERDICT: {r} | CONVICTION: {c} "
                         f"| WHY: reason {i} in one sentence.\n")
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


def test_qa_missing_position_file_no_crash(tmp_path):
    # back-dated/auth-fail runs pass a 15-position.json path that does not exist;
    # qa_check must fall back to 2-arg semantics, never crash with a traceback.
    rp, pp = _write(tmp_path, "## Risk\nATR14 is 19.86 [P2.atr14].\n")
    r = _run("qa_check.py", str(rp), str(pp), str(tmp_path / "absent-15-position.json"))
    assert r.returncode == 0 and "PASS P2.atr14" in r.stdout
    assert "Traceback" not in r.stderr
