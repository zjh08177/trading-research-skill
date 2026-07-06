#!/usr/bin/env python3
"""Per-holding action plan (portfolio monitoring companion, v2.3 F follow-on).

Joins four existing artifacts — the live decision-levels registry
(publish_levels.py output), the ledger's latest rating per ticker, a SnapTrade
holdings dump (snaptrade_holdings.py output), and a {ticker: price} snapshot —
into one action-plan markdown + HTML: fired triggers first (the action queue),
then every monitored holding with rating, book weight, move vs the registry's
reference close, and distance to each decision level. Deterministic join: emits
NO new ratings; every action string comes verbatim from the levels registry
(i.e. from the QA'd per-name reports). ATR distances derive from the registry's
published atr_dist (ATR as of the registry asof, never re-fetched).

Usage: action_plan.py <levels_dir> <ledger.jsonl> <holdings.json> <prices.json>
                      <classmap.json> <out_md> <asof> [price_time]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import monitor_invalidations as mon  # noqa: E402

KNIFE_EDGE_PCT = 0.5  # within this % of an un-fired level → "AT TRIGGER"


def latest_ratings(ledger_path):
    """{ticker: {rating, as_of, votes}} from the newest row per ticker; count bad lines."""
    latest, bad = {}, 0
    for line in open(ledger_path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            t = r["ticker"]
        except Exception:
            bad += 1
            continue
        if t not in latest or r.get("as_of", "") >= latest[t].get("as_of", ""):
            latest[t] = r
    out = {}
    for t, r in latest.items():
        dist = r.get("distribution") or {}
        votes = "/".join(f"{n}×{k}" for k, n in dist.items() if n)
        out[t] = {"rating": r.get("mode_rating", "?"), "as_of": r.get("as_of", "?"),
                  "votes": votes}
    return out, bad


def derived_atr(entry):
    """ATR implied by the registry's own spot/level/atr_dist triple (either side)."""
    for side in ("downside", "upside"):
        s = entry.get(side)
        if s and s.get("level") is not None and s.get("atr_dist") and entry.get("spot"):
            if s["atr_dist"] > 0:
                return abs(entry["spot"] - s["level"]) / s["atr_dist"]
    return None


def _side(entry, key, price, atr):
    """One level side → {level, action, basis, dist_pct(signed, + = not fired), dist_atr}."""
    s = entry.get(key)
    if not (s and s.get("level") is not None):
        return None
    d = {"level": s["level"], "action": s.get("action", ""), "basis": s.get("basis", "")}
    if price is not None:
        sign = 1 if key == "downside" else -1
        d["dist_pct"] = (price - s["level"]) / price * 100 * sign
        if atr:
            d["dist_atr"] = abs(price - s["level"]) / atr
    return d


def build_rows(reg, ratings, holdings_map, prices, classmap):
    rows = []
    for e in reg:
        t = e["ticker"]
        p = prices.get(t)
        fired = [f for f in mon.evaluate(e, p) if f["fired"]] if p is not None else []
        h = holdings_map.get(t, {})
        r = ratings.get(t, {})
        atr = derived_atr(e)
        spot = e.get("spot")
        row = {
            "ticker": t,
            "sector": (classmap.get(t) or {}).get("sector", "?"),
            "rating": r.get("rating", "—"), "votes": r.get("votes", ""),
            "rating_asof": r.get("as_of", ""),
            "pct_book": h.get("pct_of_book"), "qty": h.get("qty"),
            "price": p,
            "chg_pct": (p - spot) / spot * 100 if (p is not None and spot) else None,
            "dn": _side(e, "downside", p, atr), "up": _side(e, "upside", p, atr),
            "fired": fired,
        }
        edges = [s for s in (row["dn"], row["up"])
                 if s and s.get("dist_pct") is not None and 0 < s["dist_pct"] <= KNIFE_EDGE_PCT]
        row["knife_edge"] = bool(edges) and not fired
        if p is None:
            row["plan"] = "PRICE UNAVAILABLE — re-check"
        elif fired:
            row["plan"] = " · ".join(f"ACT — {f['action']} ({f['dir']} {f['level']:g} crossed)"
                                     for f in fired)
        elif row["knife_edge"]:
            row["plan"] = "AT TRIGGER — treat as live"
        else:
            row["plan"] = f"Follow {row['rating']}; alerts armed"
        rows.append(row)
    rows.sort(key=lambda x: (not x["fired"], -(x["pct_book"] or 0)))
    return rows


def filter_registry_to_holdings(reg, holdings_map):
    """Keep only registry entries for symbols in the current holdings dump."""
    held_symbols = set(holdings_map)
    kept, not_held = [], []
    for entry in reg:
        ticker = entry.get("ticker")
        if ticker in held_symbols:
            kept.append(entry)
        else:
            not_held.append(ticker)
    return kept, sorted(t for t in not_held if t)


def _lvl_cell(s):
    if not s:
        return "—"
    txt = f"{s['level']:g} {s['basis']} → {s['action']}"
    if s.get("dist_pct") is not None:
        txt += f" · {s['dist_pct']:+.1f}%"
        if s.get("dist_atr") is not None:
            txt += f" / {s['dist_atr']:.1f} ATR"
    return txt


def _sector_heat(rows):
    agg = {}
    for r in rows:
        if r["price"] is None or r["chg_pct"] is None:
            continue
        mv = (r["pct_book"] or 0)
        a = agg.setdefault(r["sector"], {"w": 0.0, "wchg": 0.0, "names": []})
        a["w"] += mv
        a["wchg"] += mv * r["chg_pct"]
        a["names"].append(r["ticker"])
    out = []
    for sec, a in agg.items():
        out.append({"sector": sec, "weight": a["w"],
                    "chg": a["wchg"] / a["w"] if a["w"] else 0.0,
                    "names": " ".join(a["names"])})
    out.sort(key=lambda x: -x["chg"])
    return out


def render_md(rows, asof, meta):
    fired = [r for r in rows if r["fired"]]
    edge = [r for r in rows if r["knife_edge"]]
    L = [f"# Portfolio action plan — {asof}", "",
         f"Monitored **{len(rows)}** holdings (levels registry asof {meta['reg_asof']}) · "
         f"book ${meta['book']:,.0f} across {meta['n_accounts']} accounts · "
         f"prices {meta['price_time']} · **{len(fired)} fired** / {len(edge)} at-trigger.", ""]

    L.append("## Action queue")
    L.append("")
    if fired or edge:
        L += ["| Holding | Sector | % book | Rating | Price | Δ vs ref | Trigger | Action now |",
              "|---|---|---|---|---|---|---|---|"]
        for r in fired + edge:
            trig = " · ".join(f"{f['dir']} {f['level']:g} ({f['basis']})" for f in r["fired"]) \
                if r["fired"] else "knife-edge"
            pb = f"{r['pct_book']:.1f}%" if r["pct_book"] is not None else "—"
            chg = f"{r['chg_pct']:+.1f}%" if r["chg_pct"] is not None else "—"
            L.append(f"| **{r['ticker']}** | {r['sector']} | {pb} | {r['rating']} | "
                     f"{r['price']:g} | {chg} | {trig} | **{r['plan']}** |")
    else:
        L.append("**All clear** — no holding at or through a decision level.")
    L.append("")

    L += ["## Per-stock plan", "",
          "| Holding | Sector | Rating (votes) | % book | Price | Δ vs ref | "
          "Downside guard | Upside trigger | Plan now |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: -(x["pct_book"] or 0)):
        pb = f"{r['pct_book']:.1f}%" if r["pct_book"] is not None else "—"
        chg = f"{r['chg_pct']:+.1f}%" if r["chg_pct"] is not None else "—"
        px = f"{r['price']:g}" if r["price"] is not None else "—"
        L.append(f"| {r['ticker']} | {r['sector']} | {r['rating']} ({r['votes']}) | {pb} | "
                 f"{px} | {chg} | {_lvl_cell(r['dn'])} | {_lvl_cell(r['up'])} | {r['plan']} |")
    L.append("")

    L += ["## Sector heat (move vs registry ref close, book-weighted)", "",
          "| Sector | Weight | Δ | Names |", "|---|---|---|---|"]
    for s in _sector_heat(rows):
        L.append(f"| {s['sector']} | {s['weight']:.1f}% | {s['chg']:+.1f}% | {s['names']} |")
    L.append("")

    L += ["## Provenance & caveats", ""]
    L += [f"- Levels + actions verbatim from the levels registry (published {meta['reg_asof']} "
          "from QA'd per-name reports); this artifact emits **no new ratings**.",
          "- ATR distances use the ATR implied by the registry's published atr_dist "
          f"(as of {meta['reg_asof']}), not a live re-fetch.",
          f"- Δ vs ref = move against the registry's reference close ({meta['reg_asof']} pack).",
          "- Prices: equities/ETFs Schwab realtime NBBO; crypto Crypto.com public REST.",
          f"- Ratings basis: newest ledger row per ticker"
          + (f"; {meta['bad_ledger']} malformed ledger line(s) skipped." if meta["bad_ledger"]
             else "."),
          ]
    if meta.get("unmonitored"):
        L.append(f"- Unmonitored holdings (no levels registry entry): {meta['unmonitored']}.")
    if meta.get("not_held"):
        L.append(f"- Registry entries skipped because not currently held: {meta['not_held']}.")
    if meta.get("malformed"):
        L.append(f"- Malformed registry entries skipped: {meta['malformed']}.")
    L += ["", "---", "*Deterministic monitor artifact — no LLM judgment ran. "
          "Decision support only; not financial advice.*", ""]
    return "\n".join(L)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 7:
        sys.stderr.write("usage: action_plan.py <levels_dir> <ledger.jsonl> <holdings.json> "
                         "<prices.json> <classmap.json> <out_md> <asof> [price_time]\n")
        return 2
    levels_dir, ledger_p, hold_p, price_p, class_p, out_md, asof = argv[:7]
    price_time = argv[7] if len(argv) > 7 else "snapshot"

    reg, malformed = mon.load_registry(levels_dir)
    ratings, bad = latest_ratings(ledger_p)
    hold = json.load(open(hold_p))
    holdings_map = {h["symbol"]: h for h in hold.get("holdings", [])}
    reg, not_held = filter_registry_to_holdings(reg, holdings_map)
    prices = json.load(open(price_p))
    classmap = json.load(open(class_p))

    rows = build_rows(reg, ratings, holdings_map, prices, classmap)
    monitored = {r["ticker"] for r in rows}
    unmon = [h["symbol"] for h in hold.get("holdings", [])
             if h["symbol"] not in monitored and h.get("kind") != "mutualfund"]
    meta = {"reg_asof": max((e.get("asof", "?") for e in reg), default="?"),
            "book": hold.get("total_book", 0.0), "n_accounts": hold.get("n_accounts", 0),
            "price_time": price_time, "bad_ledger": bad,
            "unmonitored": ", ".join(sorted(unmon)),
            "not_held": ", ".join(not_held),
            "malformed": ", ".join(malformed)}
    md = render_md(rows, asof, meta)

    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    open(out_md, "w").write(md)
    from render_report import CSS, PAGE_TMPL, md_to_html
    html = PAGE_TMPL.format(title=f"Portfolio action plan — {asof}", css=CSS,
                            inner=md_to_html(md))
    open(os.path.splitext(out_md)[0] + ".html", "w").write(html)
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
