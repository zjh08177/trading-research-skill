#!/usr/bin/env python3
"""Batch datapack + position builder for the trading-research v2 pipeline.
Deterministic layer: runs vendor CLIs, merges JSON, derives mcap/PE, tiingo
cross-check, renders 10-datapack.md/.json + 15-position.md/.json per ticker.
Crypto handled out-of-band (MCP) — this driver marks equities CLIs MISSING for it.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import statistics
import uuid

SK = "/Users/bytedance/.claude/skills/trading-research"
PY = SK + "/.venv/bin/python"
V = SK + "/scripts/vendors"
RUNS = SK + "/runs"
USAGE = SK + "/scripts/usage.py"

sys.path.insert(0, SK + "/scripts")
import replay  # noqa: E402 - historical as-of replay contract (parse_cutoff_token/mode_for_cutoff/write_scope)

# _DEFAULT_* are the fixed argparse defaults for --asof/--stamp/--ledger/--holdings
# (main(), below) — unchanged from the values these used to be hardcoded to.
# ASOF/STAMP/LEDGER/HOLD stay as mutable module globals: build_facts, render_md,
# run_ledger, build_position, etc. read them as bare names at call time, and
# main() overwrites them (post-normalization) before the per-ticker loop runs.
# This keeps the live (non-replay) path byte-identical to today.
_DEFAULT_LEDGER = ("/Users/bytedance/Library/Mobile Documents/iCloud~md~obsidian/Documents/"
                    "second-brain/Projects/personal/tradingagents/reports/ledger.jsonl")
_DEFAULT_ASOF = "2026-07-05"
_DEFAULT_STAMP = "1300"
_DEFAULT_HOLD = "/Users/bytedance/.claude/jobs/f5e850a4/tmp/holdings.json"

LEDGER = _DEFAULT_LEDGER
ASOF = _DEFAULT_ASOF
STAMP = _DEFAULT_STAMP
HOLD = _DEFAULT_HOLD


def fact(v, unit, asof, src):
    return {"v": v, "unit": unit, "asof": asof, "src": src}


def run_cli(name, args):
    p = subprocess.run([PY, f"{V}/{name}.py", *args], capture_output=True, text=True)
    if p.returncode == 0 and p.stdout.strip():
        try:
            return 0, json.loads(p.stdout), ""
        except Exception as e:  # noqa
            return 1, None, f"jsonparse {e}: {p.stdout[:150]}"
    return p.returncode, None, (p.stderr.strip() or p.stdout.strip())[:250]


def run_ledger(ticker):
    env = {**os.environ, "TRADING_RESEARCH_LEDGER": LEDGER}
    p = subprocess.run([PY, f"{SK}/scripts/ledger.py", "read", "--ticker", ticker,
                        "--before", ASOF], capture_output=True, text=True, env=env)
    return p.stdout.strip() or "No prior track record."


def run_usage_start(ticker, kind, run_id, run_dir, batch_id, position_aware, mode="report"):
    """Best-effort L1 usage start for batch children.

    Telemetry must be visible but must not kill the report run. The helper itself
    fail-louds with a manual-append banner; this batch spine surfaces stderr/stdout
    and continues with invocation_id=None so the workflow can still finish.
    `mode` is "report" for the live path (default, byte-identical to today) or
    "replay" for the historical as-of replay path.
    """
    args = [PY, USAGE, "start", "--mode", mode, "--ticker", ticker,
            "--job-tier", "J1 POSITION-AWARE", "--asset-class", kind,
            "--run-id", run_id, "--run-dir", run_dir, "--batch-id", batch_id]
    if position_aware:
        args.append("--position-aware")
    env = {**os.environ, "TRADING_RESEARCH_BATCH_ID": batch_id}
    p = subprocess.run(args, capture_output=True, text=True, env=env)
    if p.returncode != 0:
        sys.stderr.write("WARN: usage.py start failed for "
                         f"{ticker}: {p.stderr or p.stdout}\n")
        return None
    for line in p.stdout.splitlines():
        if line.startswith("export TRADING_RESEARCH_INVOCATION_ID="):
            return line.split("=", 1)[1].strip()
    sys.stderr.write(f"WARN: usage.py start returned no invocation id for {ticker}\n")
    return None


def add_options(ticker, kind, facts, gaps, options):
    """Options wiring. Flag off (or a non-optionable kind) → the shipped Schwab P4
    behavior. `--options` → fetch the UW P8 pack first (spot from P1.last/price,
    ATR from P2.atr14); on success suppress the light Schwab P4 (P8 is the primary
    options source, D2), routing P8._gaps into Data gaps; on P8 failure fall back
    to Schwab P4 (D2/EC4)."""
    def schwab_p4():
        ex, d, err = run_cli("schwab_options", ["--ticker", ticker])
        if ex == 0:
            facts.update(d)
        else:
            gaps.append(f"P4 MISSING(options: {err})")

    if not options:
        if kind in ("equity", "etf"):
            schwab_p4()
        else:
            gaps.append(f"P4 MISSING(by-design: {kind})")
        return
    if kind not in ("equity", "etf"):
        gaps.append(f"P8 MISSING(by-design: {kind} has no options chain)")
        return
    spot = facts.get("P1.last", {}).get("v") or facts.get("P1.price", {}).get("v")
    if not spot:
        gaps.append("P8 MISSING(no spot price for uw_options); Schwab P4 fallback")
        schwab_p4()
        return
    a = ["--ticker", ticker, "--spot", str(spot)]
    atr = facts.get("P2.atr14", {}).get("v")
    if atr:
        a += ["--atr", str(atr)]
    ex, d, err = run_cli("uw_options", a)
    if ex == 0 and d:
        for g in (d.pop("P8._gaps", None) or []):
            gaps.append(f"P8 {g}")
        facts.update(d)
        # D2/EC4: P8 is primary; the Schwab IV backfills ONLY when the P8 IV group
        # itself gapped (no rank AND no IV) — other P4 fields stay suppressed.
        if "P8.iv_rank_1y" in facts or "P8.iv_now" in facts:
            gaps.append("P4 suppressed under --options (UW P8 is the primary "
                        "options source)")
        else:
            ex2, d2, err2 = run_cli("schwab_options", ["--ticker", ticker])
            iv = {k: v for k, v in (d2 or {}).items() if k == "P4.atm_iv_near"}
            if iv:
                facts.update(iv)  # src=schwab already stamped by schwab_options
                gaps.append("P4.atm_iv_near backfilled from Schwab (src=schwab) — "
                            "P8 IV group gapped (D2/EC4); other P4 fields suppressed")
            else:
                gaps.append("P8 IV group gapped and Schwab IV backfill unavailable "
                            f"({err2 if ex2 else 'no atm_iv_near'})")
    else:
        gaps.append(f"P8 MISSING(uw_options exit {ex}: {err}); Schwab P4 fallback")
        schwab_p4()


def build_facts(ticker, kind, options=False):
    facts, gaps, degraded = {}, [], []
    ex, d, err = run_cli("schwab_bars", ["--ticker", ticker, "--asof", ASOF])
    if ex == 0:
        facts.update(d)
    else:
        gaps.append(f"P1/P2 GATE FAIL schwab_bars: {err}")
    ex, d, err = run_cli("schwab_quote", ["--ticker", ticker, "--asof", ASOF])
    if ex == 0:
        facts.update(d)
    else:
        degraded.append(f"P1.last quote unavailable ({err}); headline uses settled close")
    ex, d, err = run_cli("tiingo_oracle", ["--ticker", ticker, "--asof", ASOF, "--live"])
    if ex == 0:
        facts.update(d)
    else:
        degraded.append(f"tiingo CROSS-CHECK UNAVAILABLE ({err})")
    if kind in ("equity", "adr"):
        ex, d, err = run_cli("edgar_fundamentals", ["--ticker", ticker])
        if ex == 0:
            facts.update(d)
        else:
            gaps.append(f"P3 MISSING(edgar: {err})")
    else:
        gaps.append(f"P3 MISSING(by-design: {kind} has no SEC fundamentals)")
    # marketaux (P5/P6) runs BEFORE options so news/earnings precede the P8 fetch.
    ex, d, err = run_cli("marketaux_news", ["--ticker", ticker, "--days", "7", "--asof", ASOF])
    if ex == 0:
        facts.update(d)
        arts = d.get("P5.headlines", {}).get("v", [])
        sents = [a["sentiment"] for a in arts if isinstance(a, dict)
                 and isinstance(a.get("sentiment"), (int, float))]
        if sents:
            facts["P6.news_tone"] = fact(round(statistics.mean(sents), 4), "score[-1,1]",
                                         ASOF, f"derived(marketaux,n={len(sents)})")
        else:
            gaps.append("P6 news_tone: marketaux carried no article sentiment")
    else:
        gaps.append(f"P5 marketaux none/thin ({err}); sentiment analyst enriches via WebSearch(discovery)")
        gaps.append("P6 news_tone DATA GAP (no marketaux articles)")
    add_options(ticker, kind, facts, gaps, options)
    px = facts.get("P1.price", {}).get("v")
    sh = facts.get("P3.shares_outstanding", {})
    if px and sh.get("v"):
        facts["P1.mcap"] = fact(round(px * sh["v"]), "USD", sh["asof"], "derived(schwab*sec-edgar)")
    eps = facts.get("P3.eps_diluted_ttm", {}).get("v")
    if px and isinstance(eps, (int, float)) and eps > 0:
        facts["P3.pe_ttm"] = fact(round(px / eps, 2), "ratio", ASOF, "derived(schwab/edgar)")
    elif isinstance(eps, (int, float)) and eps <= 0:
        gaps.append(f"P3.pe_ttm omitted: EPS TTM {eps} not positive (P/E not meaningful)")
    oob = facts.get("P1.px_close_oob", {}).get("v")
    sw = facts.get("P1.price", {}).get("v")
    xline = "CROSS-CHECK UNAVAILABLE (tiingo returned no settled close)"
    if oob and sw:
        rel = abs(sw - oob) / abs(sw)
        ok = "OK" if rel <= 0.005 else "FAIL"
        xline = f"CROSS-CHECK {ok} (schwab {sw} vs tiingo {oob}, rel {rel*100:.4f}% {'<=' if ok=='OK' else '>'} 0.5%)"
        if ok == "FAIL":
            gaps.append(xline)
    return facts, gaps, degraded, xline


def probe_entry_market_asof(cutoff, fetch_fn, max_probe_days=10):
    """Find the first settled bar strictly after `cutoff` by probing successive
    calendar dates (deliberately NO weekday/holiday math — the vendor's own
    settled-bar date is the source of truth).

    `fetch_fn(probe_date_iso) -> settled_bar_date_iso | None` is injected so this
    stays pure and unit-testable with a fake (no network access here). Returns
    the first settled bar date strictly greater than `cutoff` (ISO string), or
    None if no such date turns up within `max_probe_days` calendar days.
    """
    cutoff_date = cutoff if isinstance(cutoff, dt.date) else dt.date.fromisoformat(str(cutoff)[:10])
    for i in range(1, max_probe_days + 1):
        probe_date = cutoff_date + dt.timedelta(days=i)
        bar_date_str = fetch_fn(probe_date.isoformat())
        if not bar_date_str:
            continue
        bar_date = dt.date.fromisoformat(str(bar_date_str)[:10])
        if bar_date > cutoff_date:
            return bar_date.isoformat()
    return None


def resolve_entry_market_asof(ticker, cutoff, effective_market_asof):
    """entry_market_asof = first settled close strictly after `cutoff`, found via
    `probe_entry_market_asof` against real schwab_bars probes. Falls back to
    `effective_market_asof` (conservative_fallback=True) when the probe can't
    determine a post-cutoff close within its calendar-day budget."""
    def fetch(probe_date_iso):
        ex, d, _err = run_cli("schwab_bars", ["--ticker", ticker, "--asof", probe_date_iso])
        if ex != 0 or not d:
            return None
        return d.get("P1.price", {}).get("asof")

    entry = probe_entry_market_asof(cutoff, fetch)
    if entry is None:
        return effective_market_asof, True
    return entry, False


def build_facts_replay(ticker, kind, cutoff):
    """Replay variant of build_facts: PIT-safe vendor calls only. No live quote
    (schwab_quote), no live Tiingo IEX cross-check (tiingo_oracle called without
    --live), no options (P4/P8 both MISSING by design). SEC/edgar and marketaux
    both receive --asof; marketaux additionally gets --replay.

    Returns (facts, gaps, degraded, xline, effective_market_asof) where
    effective_market_asof is the P1.price.asof schwab_bars emitted (its latest
    settled bar <= cutoff).
    """
    facts, gaps, degraded = {}, [], []
    ex, d, err = run_cli("schwab_bars", ["--ticker", ticker, "--asof", cutoff])
    if ex == 0:
        facts.update(d)
    else:
        gaps.append(f"P1/P2 GATE FAIL schwab_bars: {err}")
    effective_market_asof = facts.get("P1.price", {}).get("asof")
    ex, d, err = run_cli("tiingo_oracle", ["--ticker", ticker, "--asof", cutoff])
    if ex == 0:
        facts.update(d)
    else:
        degraded.append(f"tiingo CROSS-CHECK UNAVAILABLE ({err})")
    if kind in ("equity", "adr"):
        ex, d, err = run_cli("edgar_fundamentals", ["--ticker", ticker, "--asof", cutoff])
        if ex == 0:
            facts.update(d)
        else:
            gaps.append(f"P3 MISSING(edgar: {err})")
    else:
        gaps.append(f"P3 MISSING(by-design: {kind} has no SEC fundamentals)")
    ex, d, err = run_cli("marketaux_news",
                          ["--ticker", ticker, "--days", "7", "--asof", cutoff, "--replay"])
    if ex == 0:
        for g in (d.pop("P5._gaps", None) or []):
            gaps.append(f"P5 {g}")
        facts.update(d)
        arts = d.get("P5.headlines", {}).get("v", [])
        sents = [a["sentiment"] for a in arts if isinstance(a, dict)
                 and isinstance(a.get("sentiment"), (int, float))]
        if sents:
            facts["P6.news_tone"] = fact(round(statistics.mean(sents), 4), "score[-1,1]",
                                         cutoff, f"derived(marketaux,n={len(sents)})")
        else:
            gaps.append("P6 news_tone: marketaux carried no article sentiment")
    else:
        gaps.append(f"P5 marketaux none/thin ({err}); sentiment analyst enriches via WebSearch(discovery)")
        gaps.append("P6 news_tone DATA GAP (no marketaux articles)")
    gaps.append("P4 MISSING(live-only options source in replay)")
    gaps.append("P8 MISSING(live-only UW options source in replay)")
    px = facts.get("P1.price", {}).get("v")
    sh = facts.get("P3.shares_outstanding", {})
    if px and sh.get("v"):
        facts["P1.mcap"] = fact(round(px * sh["v"]), "USD", sh["asof"], "derived(schwab*sec-edgar)")
    eps = facts.get("P3.eps_diluted_ttm", {}).get("v")
    if px and isinstance(eps, (int, float)) and eps > 0:
        facts["P3.pe_ttm"] = fact(round(px / eps, 2), "ratio", cutoff, "derived(schwab/edgar)")
    elif isinstance(eps, (int, float)) and eps <= 0:
        gaps.append(f"P3.pe_ttm omitted: EPS TTM {eps} not positive (P/E not meaningful)")
    oob = facts.get("P1.px_close_oob", {}).get("v")
    sw = facts.get("P1.price", {}).get("v")
    xline = "CROSS-CHECK UNAVAILABLE (tiingo returned no settled close)"
    if oob and sw:
        rel = abs(sw - oob) / abs(sw)
        ok = "OK" if rel <= 0.005 else "FAIL"
        xline = f"CROSS-CHECK {ok} (schwab {sw} vs tiingo {oob}, rel {rel*100:.4f}% {'<=' if ok=='OK' else '>'} 0.5%)"
        if ok == "FAIL":
            gaps.append(xline)
    return facts, gaps, degraded, xline, effective_market_asof


SECT = {"P1": "P1 Quote", "P2": "P2 Technicals", "P3": "P3 Fundamentals (SEC XBRL)",
        "P4": "P4 Options", "P5": "P5 News/events", "P6": "P6 Sentiment",
        "P8": "P8 Dealer positioning & options (UW)"}


def render_md(ticker, kind, facts, gaps, degraded, xline, p7):
    run_id = f"{ticker}-{ASOF}-{STAMP}"
    last = facts.get("P1.last")
    px = facts.get("P1.price")
    lines = [f"# Data pack — {ticker} ({kind}), as-of {ASOF} (run {run_id})", "",
             f"P1 cross-check: {xline}", ""]
    if last:
        ld = str(last["asof"])[:10]
        stale = ld < ASOF
        note = (f"Headline price note: last trade [P1.last]={last['v']} is the {ld} session"
                f"{' (STALE: market closed since; ' + ASOF + ' is a weekend/holiday)' if stale else ''}. "
                f"Prior close/chg% from settled bars: {px['v'] if px else 'n/a'} close.")
        lines += [note, ""]
    for sec in ("P1", "P2", "P3", "P4", "P5", "P6", "P8"):
        keys = [k for k in facts if k.startswith(sec + ".")]
        if not keys:
            continue
        lines.append(f"## {SECT[sec]}")
        for k in keys:
            f = facts[k]
            v = f["v"]
            vs = json.dumps(v) if isinstance(v, (list, dict)) else v
            lines.append(f"- [{k}] = {vs} {f['unit']} (asof {f['asof']}, src {f['src']})")
        lines.append("")
    lines += ["## P7 Track record", p7, ""]
    lines.append("## Data gaps")
    for g in (gaps or ["none"]):
        lines.append(f"- {g}")
    if degraded:
        lines += ["", "## Degraded sources"]
        for dg in degraded:
            lines.append(f"- DEGRADED: {dg}")
    return "\n".join(lines) + "\n", run_id


def build_position(ticker, holdings):
    row = next((r for r in holdings["holdings"] if r["symbol"] == ticker), None)
    if not row:
        return ({"H1.held": {"v": False, "unit": "bool", "asof": ASOF, "src": "snaptrade"}},
                f"# Position — {ticker}\n- [H1.held] = False (flat)\n")
    hf = {"H1.held": fact(True, "bool", ASOF, "snaptrade"),
          "H1.shares": fact(round(row["qty"], 4), "shares", ASOF, "snaptrade"),
          "H1.market_value": fact(round(row["market_value"], 2), "USD", ASOF, "snaptrade"),
          "H1.pct_of_book": fact(round(row["pct_of_book"], 2), "pct", ASOF, "snaptrade"),
          "H1.brokers": fact(row["brokers"], "brokers", ASOF, "snaptrade"),
          "H1.n_accounts": fact(row["n_accounts"], "accounts", ASOF, "snaptrade")}
    if row.get("avg_cost") is not None:
        hf["H1.avg_cost"] = fact(round(row["avg_cost"], 2), "USD", ASOF, "snaptrade")
    if row.get("unrealized_pl") is not None:
        hf["H1.unrealized_pl"] = fact(round(row["unrealized_pl"], 2), "USD", ASOF, "snaptrade")
    if row.get("unrealized_pl_pct") is not None:
        hf["H1.unrealized_pl_pct"] = fact(round(row["unrealized_pl_pct"], 2), "pct", ASOF, "snaptrade")
    md = [f"# Position — {ticker} (SnapTrade cross-broker: {row['brokers']})", ""]
    md += [f"- [{k}] = {f['v']} {f['unit']} (asof {f['asof']}, src {f['src']})" for k, f in hf.items()]
    md += ["", "Note: WITHHELD from analysts/debate/risk/judges (invariant 12). Read only by writer + qa_check."]
    return hf, "\n".join(md) + "\n"


def _build_arg_parser():
    p = argparse.ArgumentParser()
    p.add_argument("tickers", help="JSON list of [ticker, kind] pairs")
    p.add_argument("--options", action="store_true", default=False)
    p.add_argument("--asof", default=_DEFAULT_ASOF)
    p.add_argument("--stamp", default=_DEFAULT_STAMP)
    p.add_argument("--ledger", default=_DEFAULT_LEDGER)
    p.add_argument("--holdings", default=_DEFAULT_HOLD)
    p.add_argument("--replay", action="store_true", default=False,
                    help="force historical as-of replay mode even if --asof is today")
    return p


def main(argv=None):
    global ASOF, STAMP, LEDGER, HOLD
    args = _build_arg_parser().parse_args(argv if argv is not None else sys.argv[1:])

    # Guardrail #1: normalize EVERY --asof (incl. YYYY/MM/DD) through
    # replay.parse_cutoff_token BEFORE any vendor call, so a slash-date token
    # can never reach an EDGAR/vendor string comparison.
    cutoff_date = replay.parse_cutoff_token(args.asof)
    ASOF = cutoff_date.isoformat()
    STAMP = args.stamp
    LEDGER = args.ledger
    HOLD = args.holdings

    mode = "replay" if (args.replay or replay.mode_for_cutoff(cutoff_date) == "replay") else "live"
    tickers = json.loads(args.tickers)
    batch_id = os.environ.get("TRADING_RESEARCH_BATCH_ID") or str(uuid.uuid4())
    summary = []

    if mode == "live":
        # Byte-identical to the pre-replay live path.
        holdings = json.load(open(HOLD))
        for ticker, kind in tickers:
            run_dir = f"{RUNS}/{ticker}-{ASOF}-{STAMP}"
            os.makedirs(run_dir, exist_ok=True)
            facts, gaps, degraded, xline = build_facts(ticker, kind, args.options)
            p7 = run_ledger(ticker)
            md, run_id = render_md(ticker, kind, facts, gaps, degraded, xline, p7)
            json.dump(facts, open(f"{run_dir}/10-datapack.json", "w"), indent=1)
            open(f"{run_dir}/10-datapack.md", "w").write(md)
            hf, hmd = build_position(ticker, holdings)
            json.dump(hf, open(f"{run_dir}/15-position.json", "w"), indent=1)
            open(f"{run_dir}/15-position.md", "w").write(hmd)
            open(f"{run_dir}/00-scope.md", "w").write(
                f"# Scope\n- Query: portfolio holding deep-dive (top-10 combined book).\n"
                f"- Job class: J1 single-name deep dive, POSITION-AWARE.\n"
                f"- Ticker: {ticker} · kind: {kind} · As-of: {ASOF} (Sunday; market closed, last settled 2026-07-02).\n")
            invocation_id = run_usage_start(ticker, kind, run_id, run_dir, batch_id, True)
            gate = "GATE-FAIL" if any("GATE FAIL" in g for g in gaps) else "ok"
            summary.append({"ticker": ticker, "kind": kind, "run_id": run_id, "run_dir": run_dir,
                            "batch_id": batch_id, "invocation_id": invocation_id,
                            "gate": gate, "n_facts": len(facts), "held": hf.get("H1.held", {}).get("v"),
                            "gaps": len(gaps), "degraded": len(degraded)})
    else:
        for ticker, kind in tickers:
            run_dir = f"{RUNS}/{ticker}-{ASOF}-{STAMP}"
            os.makedirs(run_dir, exist_ok=True)
            facts, gaps, degraded, xline, effective_market_asof = build_facts_replay(ticker, kind, ASOF)
            entry_market_asof, conservative_fallback = resolve_entry_market_asof(
                ticker, ASOF, effective_market_asof)
            p7 = run_ledger(ticker)
            md, run_id = render_md(ticker, kind, facts, gaps, degraded, xline, p7)
            json.dump(facts, open(f"{run_dir}/10-datapack.json", "w"), indent=1)
            open(f"{run_dir}/10-datapack.md", "w").write(md)
            generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
            scope_json_path, _scope_md_path = replay.write_scope(run_dir, {
                "mode": "replay", "ticker": ticker, "asset_class": kind,
                "requested_cutoff": ASOF, "effective_market_asof": effective_market_asof,
                "entry_market_asof": entry_market_asof, "generated_at": generated_at,
                "conservative_fallback": conservative_fallback,
            })
            invocation_id = run_usage_start(ticker, kind, run_id, run_dir, batch_id, False,
                                            mode="replay")
            gate = "GATE-FAIL" if any("GATE FAIL" in g for g in gaps) else "ok"
            summary.append({"ticker": ticker, "kind": kind, "run_id": run_id, "run_dir": run_dir,
                            "batch_id": batch_id, "invocation_id": invocation_id,
                            "gate": gate, "n_facts": len(facts), "gaps": len(gaps),
                            "degraded": len(degraded), "mode": "replay",
                            "requested_cutoff": ASOF, "effective_market_asof": effective_market_asof,
                            "entry_market_asof": entry_market_asof, "scope_path": scope_json_path})
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
