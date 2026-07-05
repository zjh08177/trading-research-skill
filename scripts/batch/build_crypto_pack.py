#!/usr/bin/env python3
"""Batch crypto datapack builder (Crypto.com native JSON) for the v2 pipeline.

Crypto is not covered by the equity vendor CLIs, so its P1/P2 come from Crypto.com
market data fetched via MCP (only the orchestrator can call MCP) and saved to a raw
file. This turns that raw file into the same 10-datapack.* / 15-position.* artifacts
build_datapack.py emits for equities.

Usage: build_crypto_pack.py <TICKER> <raw.json> <holdings.json> [asof] [stamp]
  raw.json = {"ticker": <get_ticker response>, "candles": <get_candlestick response>}
P1 = spot + 24h range/vol/chg (real-time; crypto trades 24/7, no STALE issue).
P2 = RSI14 + ATR14 from the daily candles (Crypto.com caps ~15-50 → SMA20/50/200,
     MACD, sigma30 = DATA GAP). P3 MISSING(by-design), P4 N/A, P6 DATA GAP (LunarCrush gated).
"""
import json
import os
import sys

SK = "/Users/bytedance/.claude/skills/trading-research"
RUNS = SK + "/runs"


def fact(v, unit, asof, src):
    return {"v": v, "unit": unit, "asof": asof, "src": src}


def main():
    ticker, rawf, holdf = sys.argv[1], sys.argv[2], sys.argv[3]
    asof = sys.argv[4] if len(sys.argv) > 4 else "2026-07-05"
    stamp = sys.argv[5] if len(sys.argv) > 5 else "1300"
    raw = json.load(open(rawf))
    tk = raw["ticker"]
    candles = raw["candles"]["data"]  # newest first; string fields
    c = [{k: float(x[k]) for k in ("open", "high", "low", "close")} for x in candles]
    closes = [x["close"] for x in c]
    last = float(tk["last"])
    src = "cryptocom"
    # RSI14 (Wilder) + ATR14 from oldest->newest
    chron = list(reversed(c))
    cl = [x["close"] for x in chron]
    deltas = [cl[i + 1] - cl[i] for i in range(len(cl) - 1)]
    gains = sum(max(d, 0) for d in deltas) / max(len(deltas), 1)
    losses = sum(-min(d, 0) for d in deltas) / max(len(deltas), 1)
    rsi = 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)
    trs = [max(chron[i]["high"] - chron[i]["low"],
               abs(chron[i]["high"] - chron[i - 1]["close"]),
               abs(chron[i]["low"] - chron[i - 1]["close"])) for i in range(1, len(chron))]
    atr = sum(trs) / max(len(trs), 1)

    facts = {
        "P1.price": fact(last, "USD", asof, src + "(spot)"),
        "P1.chg_pct_24h": fact(round(float(tk["change"]) * 100, 4), "pct", asof, src),
        "P1.day_high": fact(float(tk["high"]), "USD", asof, src),
        "P1.day_low": fact(float(tk["low"]), "USD", asof, src),
        "P1.day_volume": fact(round(float(tk["volume"]), 2), "coin", asof, src),
        "P1.day_volume_usd": fact(round(float(tk.get("volume_value", 0))), "USD", asof, src),
        "P2.rsi14": fact(round(rsi, 2), "index", asof, f"derived(cryptocom {len(c)}d candles)"),
        "P2.atr14": fact(round(atr, 4), "USD", asof, f"derived(cryptocom {len(c)}d candles)"),
        "P2.atr14_pct": fact(round(100 * atr / last, 4), "pct", asof, "derived(cryptocom)"),
    }
    gaps = [
        f"P2.sma20/sma50/sma200 DATA GAP: Crypto.com returned {len(c)} daily candles (<200); long MAs uncomputable",
        "P2.macd/sigma30 DATA GAP: insufficient candle history",
        "P3 MISSING(by-design: crypto has no SEC fundamentals)",
        "P4 MISSING(by-design: crypto, no listed options chain here)",
        "P5 marketaux N/A for crypto; sentiment analyst enriches via WebSearch(discovery)",
        "P6 news_tone DATA GAP: LunarCrush subscription-gated; sentiment analyst uses WebSearch(discovery)",
    ]

    def line(k):
        f = facts[k]
        return f"- [{k}] = {f['v']} {f['unit']} (asof {f['asof']}, src {f['src']})"

    md = [f"# Data pack — {ticker} (crypto), as-of {asof} (run {ticker}-{asof}-{stamp})", "",
          f"P1 source: Crypto.com spot {ticker}_USDT (live). Crypto trades 24/7 — price is real-time, no STALE.", "",
          "## P1 Quote"] + [line(k) for k in facts if k.startswith("P1.")] + \
         ["", "## P2 Technicals (partial — daily candles)"] + [line(k) for k in facts if k.startswith("P2.")] + \
         ["", "## P7 Track record", f"No prior track record for {ticker} before {asof}.", "",
          "## Data gaps"] + [f"- {g}" for g in gaps]
    md = "\n".join(md) + "\n"

    run_dir = f"{RUNS}/{ticker}-{asof}-{stamp}"
    os.makedirs(run_dir, exist_ok=True)
    json.dump(facts, open(f"{run_dir}/10-datapack.json", "w"), indent=1)
    open(f"{run_dir}/10-datapack.md", "w").write(md)
    open(f"{run_dir}/00-scope.md", "w").write(
        f"# Scope\n- Query: portfolio holding deep-dive (full-book batch).\n"
        f"- Job class: J1 single-name deep dive, POSITION-AWARE.\n"
        f"- Ticker: {ticker} · kind: crypto · As-of: {asof}. Crypto 24/7; price real-time (Crypto.com spot).\n")

    holdings = json.load(open(holdf))
    row = next((r for r in holdings["holdings"] if r["symbol"] == ticker), None)
    if row:
        hf = {"H1.held": fact(True, "bool", asof, "snaptrade"),
              "H1.shares": fact(round(row["qty"], 6), "coin", asof, "snaptrade"),
              "H1.market_value": fact(round(row["market_value"], 2), "USD", asof, "snaptrade"),
              "H1.pct_of_book": fact(round(row["pct_of_book"], 2), "pct", asof, "snaptrade"),
              "H1.brokers": fact(row["brokers"], "brokers", asof, "snaptrade"),
              "H1.n_accounts": fact(row["n_accounts"], "accounts", asof, "snaptrade")}
        for k, src_k in (("avg_cost", "H1.avg_cost"), ("unrealized_pl", "H1.unrealized_pl"),
                         ("unrealized_pl_pct", "H1.unrealized_pl_pct")):
            if row.get(k) is not None:
                hf[src_k] = fact(round(row[k], 2), "USD" if k != "unrealized_pl_pct" else "pct", asof, "snaptrade")
        hmd = [f"# Position — {ticker} (SnapTrade cross-broker: {row['brokers']})", ""]
        hmd += [f"- [{k}] = {f['v']} {f['unit']} (asof {f['asof']}, src {f['src']})" for k, f in hf.items()]
        hmd += ["", "Note: WITHHELD from analysts/debate/risk/judges (invariant 12). Read only by writer + qa_check."]
        json.dump(hf, open(f"{run_dir}/15-position.json", "w"), indent=1)
        open(f"{run_dir}/15-position.md", "w").write("\n".join(hmd) + "\n")

    print(json.dumps({"ticker": ticker, "run_dir": run_dir, "n_facts": len(facts),
                      "rsi14": round(rsi, 2), "atr14": round(atr, 4), "price": last}))


if __name__ == "__main__":
    main()
