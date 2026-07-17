import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import price_crosscheck as mod


def _pack(schwab=None, tiingo=None, finnhub=None, asof="2026-07-17"):
    def f(v):
        return None if v is None else {"v": v, "unit": "USD", "asof": asof, "src": "test"}
    pack = {}
    if schwab is not None:
        pack["P1.last"] = f(schwab)
    if tiingo is not None:
        pack["P1.px_last_oob"] = f(tiingo)
    if finnhub is not None:
        pack["P1.px_finnhub_oob"] = f(finnhub)
    return pack


def test_ok_when_schwab_and_tiingo_agree_no_finnhub_needed():
    facts = mod.build(_pack(schwab=100.0, tiingo=100.2))
    assert facts["P1.crosscheck_status"]["v"] == "ok"


def test_unavailable_when_baseline_sources_missing():
    facts = mod.build(_pack(schwab=100.0))  # no tiingo
    assert facts["P1.crosscheck_status"]["v"] == "unavailable"


def test_fail_unresolved_when_disagree_and_no_finnhub():
    facts = mod.build(_pack(schwab=100.0, tiingo=105.0))
    assert facts["P1.crosscheck_status"]["v"] == "fail_unresolved"


def test_resolved_2of3_schwab_finnhub_outvote_tiingo():
    facts = mod.build(_pack(schwab=100.0, tiingo=105.0, finnhub=100.1))
    assert facts["P1.crosscheck_status"]["v"] == "resolved_2of3"
    assert facts["P1.crosscheck_resolved_price"]["v"] == 100.0


def test_resolved_2of3_tiingo_finnhub_outvote_schwab():
    facts = mod.build(_pack(schwab=100.0, tiingo=105.0, finnhub=105.1))
    assert facts["P1.crosscheck_status"]["v"] == "resolved_2of3"
    assert facts["P1.crosscheck_resolved_price"]["v"] == 105.0


def test_fail_3way_when_all_pairwise_disagree():
    facts = mod.build(_pack(schwab=100.0, tiingo=105.0, finnhub=110.0))
    assert facts["P1.crosscheck_status"]["v"] == "fail_3way"


def test_fail_3way_when_finnhub_agrees_with_both_but_they_disagree_with_each_other():
    # a wide tolerance edge case: finnhub sits close to both, but schwab and
    # tiingo are still outside tolerance of each other -- no clean majority
    facts = mod.build(_pack(schwab=100.0, tiingo=100.9, finnhub=100.45), )
    # with default 0.5% tolerance this is schwab-tiingo disagree (0.9%),
    # schwab-finnhub agree (0.45%), tiingo-finnhub agree (0.45%) -> both agree
    assert facts["P1.crosscheck_status"]["v"] == "fail_3way"


def test_real_live_aapl_snapshot_agrees():
    # frozen real live snapshot fetched during Phase 0 development
    # (schwab_quote.py / tiingo_oracle.py / finnhub_oracle.py, 2026-07-17)
    facts = mod.build(_pack(schwab=333.9988, tiingo=333.85, finnhub=333.74))
    assert facts["P1.crosscheck_status"]["v"] == "ok"


def test_main_cli_smoke(tmp_path, capsys):
    p = tmp_path / "10-datapack.json"
    p.write_text(json.dumps(_pack(schwab=100.0, tiingo=105.0, finnhub=100.1)))
    code = mod.main([str(p)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["P1.crosscheck_status"]["v"] == "resolved_2of3"
