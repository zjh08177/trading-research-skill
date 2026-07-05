#!/usr/bin/env python3
"""Deterministic portfolio layer (v2.3 workstream D) for the full-book batch.

Reads the SnapTrade holdings file + each name's built datapack (for ATR%) and emits
book-level structure the single-name reports can't show: concentration, sector/theme
weights, diversification (effective names vs effective themes — the "diversification
illusion" when names cluster in one correlated theme), book-weighted volatility, and a
rebalance view against soft caps. Pure stdlib, no network, no LLM — the synthesis agent
turns this JSON into the dossier's Portfolio-tab narrative.

Correlation note: an empirical N-day return correlation matrix is NOT computed — the
equity CLIs expose only summary bars (no close series) and crypto history is vendor-
capped (~15-50d), so a uniform cross-asset matrix isn't available. Diversification here
uses THEME CLUSTERS as the correlated unit (transparent, reproducible); empirical
correlation is a backlog upgrade.

Usage: portfolio_analysis.py <holdings.json> [asof] [stamp]
Writes <SK>/runs/_portfolio-<asof>/portfolio.json + portfolio.md.
"""
import json
import os
import sys

SK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNS = SK + "/runs"

# Taxonomy (SSOT mirrors classify_holdings.py). kind drives the pack builder; sector →
# coarse theme drives cluster-level diversification. EXCLUDE = cash MMFs / dust (not
# analyzable). Unknown symbols default equity / "Other".
SECTOR = {
    'TSLA': 'Auto/EV', 'AMD': 'Semis', 'META': 'Megacap platform', 'AAOI': 'Optical/Networking',
    'MSFT': 'Megacap platform', 'NVDA': 'Semis', 'AMZN': 'Megacap platform', 'AVGO': 'Semis',
    'VST': 'Power/Utilities', 'AAPL': 'Megacap platform', 'SNPS': 'Semis (EDA)', 'HOOD': 'Fintech',
    'MRVL': 'Semis', 'TEM': 'Healthcare-AI', 'COST': 'Consumer staples', 'GOOG': 'Megacap platform',
    'SOFI': 'Fintech', 'RGTI': 'Quantum', 'CRDO': 'Semis/Networking', 'FIGR': 'Fintech/Crypto',
    'PONY': 'Auto/AV', 'WRD': 'Auto/AV', 'SPCX': 'Space', 'CAI': 'Speculative/Other', 'KLAR': 'Fintech',
    'RVI': 'Speculative/Other', 'RKLB': 'Space', 'ASTS': 'Space', 'NOK': 'Telecom/Networking',
    'XLE': 'Energy (ETF)', 'XLF': 'Financials (ETF)', 'NASA': 'Space (ETF)', 'METU': 'Leveraged META (ETF)',
    'BTC': 'Crypto', 'ETH': 'Crypto', 'DOGE': 'Crypto', 'XRP': 'Crypto',
}
GROUP = {
    'Semis': 'AI/Semis complex', 'Semis (EDA)': 'AI/Semis complex', 'Semis/Networking': 'AI/Semis complex',
    'Optical/Networking': 'AI/Semis complex', 'Megacap platform': 'Megacap platform',
    'Crypto': 'Crypto', 'Fintech': 'Fintech', 'Fintech/Crypto': 'Fintech', 'Space': 'Space/Frontier',
    'Space (ETF)': 'Space/Frontier', 'Quantum': 'Space/Frontier', 'Auto/AV': 'Auto/EV+AV', 'Auto/EV': 'Auto/EV+AV',
}
EXCLUDE = {'FDRXX', 'SPAXX', 'SPRXX', 'O92E', 'TG3Y', 'PS'}

NAME_CAP = 0.08   # single-name soft cap (% of invested)
THEME_CAP = 0.25  # single-theme soft cap (% of invested)


def theme_of(sym):
    sec = SECTOR.get(sym, 'Other')
    return GROUP.get(sec, sec)


def atr_pct(sym, asof, stamp):
    f = f"{RUNS}/{sym}-{asof}-{stamp}/10-datapack.json"
    if not os.path.exists(f):
        return None
    v = json.load(open(f)).get('P2.atr14_pct')
    return v.get('v') if isinstance(v, dict) and isinstance(v.get('v'), (int, float)) else None


def hhi(weights):
    return sum(w * w for w in weights)


def main():
    holdf = sys.argv[1]
    asof = sys.argv[2] if len(sys.argv) > 2 else "2026-07-05"
    stamp = sys.argv[3] if len(sys.argv) > 3 else "1300"
    h = json.load(open(holdf))
    book = h['total_book']

    rows, excluded = [], []
    for r in h['holdings']:
        s = r['symbol']
        if s in EXCLUDE:
            excluded.append((s, r['market_value']))
            continue
        rows.append({'symbol': s, 'sector': SECTOR.get(s, 'Other'), 'theme': theme_of(s),
                     'mv': r['market_value'], 'atr_pct': atr_pct(s, asof, stamp)})
    invested = sum(r['mv'] for r in rows)
    cash = sum(mv for _, mv in excluded)
    for r in rows:
        r['w'] = r['mv'] / invested          # weight within invested book
        r['pct_book'] = r['mv'] / book * 100  # weight of total book
    rows.sort(key=lambda r: -r['mv'])

    # concentration (weights over invested)
    name_hhi = hhi(r['w'] for r in rows)
    eff_names = 1 / name_hhi
    top5 = sum(r['w'] for r in rows[:5]) * 100

    # sector + theme aggregation
    def agg(keyfn):
        d = {}
        for r in rows:
            d.setdefault(keyfn(r), {'w': 0.0, 'n': 0, 'names': []})
            d[keyfn(r)]['w'] += r['w']
            d[keyfn(r)]['n'] += 1
            d[keyfn(r)]['names'].append(r['symbol'])
        return d
    sectors = agg(lambda r: r['sector'])
    themes = agg(lambda r: r['theme'])
    theme_hhi = hhi(t['w'] for t in themes.values())
    eff_themes = 1 / theme_hhi
    dom = max(themes.items(), key=lambda kv: kv[1]['w'])

    # book-weighted volatility (undiversified daily-move proxy); coverage-honest
    vol_rows = [r for r in rows if r['atr_pct'] is not None]
    covw = sum(r['w'] for r in vol_rows)
    book_wtd_atr = sum(r['w'] * r['atr_pct'] for r in vol_rows) / covw if covw else None
    crypto_pct = sum(r['w'] for r in rows if r['theme'] == 'Crypto') * 100

    # rebalance view vs soft caps
    over_name = [{'symbol': r['symbol'], 'pct': round(r['w'] * 100, 2),
                  'trim_to_pct': round(NAME_CAP * 100, 1),
                  'trim_usd': round((r['w'] - NAME_CAP) * invested)} for r in rows if r['w'] > NAME_CAP]
    over_theme = [{'theme': k, 'pct': round(v['w'] * 100, 2), 'n': v['n'],
                   'trim_to_pct': round(THEME_CAP * 100, 1),
                   'trim_usd': round((v['w'] - THEME_CAP) * invested)}
                  for k, v in sorted(themes.items(), key=lambda kv: -kv[1]['w']) if v['w'] > THEME_CAP]

    out = {
        'asof': asof, 'book_total': round(book, 2), 'invested': round(invested, 2),
        'cash_dust': round(cash, 2), 'cash_pct': round(cash / book * 100, 2),
        'n_analyzable': len(rows),
        'concentration': {
            'name_hhi': round(name_hhi, 4), 'eff_names': round(eff_names, 1),
            'top5_pct': round(top5, 1),
            'largest': {'symbol': rows[0]['symbol'], 'pct': round(rows[0]['w'] * 100, 2)},
            'positions': [{'symbol': r['symbol'], 'sector': r['sector'], 'theme': r['theme'],
                           'mv': round(r['mv'], 2), 'pct': round(r['w'] * 100, 2),
                           'atr_pct': r['atr_pct']} for r in rows],
        },
        'sectors': [{'sector': k, 'pct': round(v['w'] * 100, 2), 'n': v['n']}
                    for k, v in sorted(sectors.items(), key=lambda kv: -kv[1]['w'])],
        'themes': [{'theme': k, 'pct': round(v['w'] * 100, 2), 'n': v['n'], 'names': v['names']}
                   for k, v in sorted(themes.items(), key=lambda kv: -kv[1]['w'])],
        'diversification': {
            'eff_names': round(eff_names, 1), 'eff_themes': round(eff_themes, 1),
            'theme_hhi': round(theme_hhi, 4),
            'illusion_ratio': round(eff_names / eff_themes, 2),
            'dominant_theme': {'theme': dom[0], 'pct': round(dom[1]['w'] * 100, 1), 'n': dom[1]['n']},
            'method': 'theme-cluster (empirical return corr not available — vendor-capped)',
        },
        'risk': {
            'book_wtd_atr_pct': round(book_wtd_atr, 2) if book_wtd_atr is not None else None,
            'vol_coverage': f"{len(vol_rows)}/{len(rows)}",
            'crypto_pct': round(crypto_pct, 2),
            'highest_vol': sorted(({'symbol': r['symbol'], 'atr_pct': r['atr_pct']} for r in vol_rows),
                                  key=lambda x: -x['atr_pct'])[:5],
        },
        'rebalance': {'name_cap_pct': NAME_CAP * 100, 'theme_cap_pct': THEME_CAP * 100,
                      'over_name': over_name, 'over_theme': over_theme},
    }

    def bar(pct, width=22):
        n = int(round(pct / 100 * width))
        return '█' * n + '·' * (width - n)

    md = [f"# Portfolio structure — full book, as-of {asof}", "",
          f"Book **${book:,.0f}** · invested **${invested:,.0f}** ({100 - out['cash_pct']:.1f}%) · "
          f"cash/dust **${cash:,.0f}** ({out['cash_pct']:.1f}%) · **{len(rows)}** analyzable names.", "",
          "## Concentration",
          f"- Effective bets by NAME: **{eff_names:.1f}** of {len(rows)} (HHI {name_hhi:.3f}).",
          f"- Effective bets by THEME: **{eff_themes:.1f}** of {len(themes)} — diversification-illusion "
          f"ratio **{eff_names / eff_themes:.1f}×** (names look spread but cluster in {len(themes)} themes).",
          f"- Top-5 names = **{top5:.0f}%** of invested; largest {rows[0]['symbol']} **{rows[0]['w'] * 100:.1f}%**.", "",
          "## Theme weights (of invested)"]
    for t in out['themes']:
        md.append(f"- `{bar(t['pct'])}` **{t['theme']}** {t['pct']:.1f}% ({t['n']}) — {', '.join(t['names'])}")
    md += ["", "## Book volatility",
           f"- Book-weighted ATR14: **{book_wtd_atr:.2f}%/day** (undiversified proxy; coverage {len(vol_rows)}/{len(rows)})."
           if book_wtd_atr is not None else "- Book-weighted ATR: n/a.",
           f"- Crypto sleeve **{crypto_pct:.1f}%** of invested; highest-vol names: "
           + ", ".join(f"{v['symbol']} {v['atr_pct']:.1f}%" for v in out['risk']['highest_vol']) + ".", "",
           "## Rebalance vs soft caps"]
    if over_name:
        md += [f"- Over the {NAME_CAP * 100:.0f}% name cap: "
               + "; ".join(f"**{o['symbol']}** {o['pct']:.1f}% (trim ~${o['trim_usd']:,})" for o in over_name)]
    else:
        md.append(f"- No single name over the {NAME_CAP * 100:.0f}% cap.")
    if over_theme:
        md += [f"- Over the {THEME_CAP * 100:.0f}% theme cap: "
               + "; ".join(f"**{o['theme']}** {o['pct']:.1f}% (trim ~${o['trim_usd']:,})" for o in over_theme)]
    else:
        md.append(f"- No theme over the {THEME_CAP * 100:.0f}% cap.")
    md += ["", "_Diversification uses theme clusters as the correlated unit; empirical return "
           "correlation is vendor-capped and not computed._"]

    outdir = f"{RUNS}/_portfolio-{asof}"
    os.makedirs(outdir, exist_ok=True)
    json.dump(out, open(f"{outdir}/portfolio.json", "w"), indent=1)
    open(f"{outdir}/portfolio.md", "w").write("\n".join(md) + "\n")
    print(json.dumps({"out": outdir, "eff_names": round(eff_names, 1), "eff_themes": round(eff_themes, 1),
                      "dom_theme": dom[0], "dom_pct": round(dom[1]['w'] * 100, 1),
                      "book_wtd_atr": round(book_wtd_atr, 2) if book_wtd_atr else None}))


if __name__ == "__main__":
    main()
