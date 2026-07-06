#!/usr/bin/env python3
"""Build the self-contained portfolio dossier: Overview scorecard + Portfolio-structure
tab + every full report inline, with client-side nav. Body-only (Artifact wraps it).

Data-driven: the scorecard, counts, and ordering are derived from each run's
55-decision.json / 15-position.json / 56-levels.json / 60-report.md — no hand-authored
rows. The Portfolio tab renders _portfolio-<asof>/portfolio.md (+ optional
portfolio-synth.md narrative). Names are ordered by book weight.

Usage: build_dossier.py <holdings.json> [asof] [stamp] [out_html]
"""
import html
import json
import os
import re
import sys

SK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, SK + "/scripts")
from render_report import md_to_html, CSS as REPORT_CSS  # noqa: E402

RUNS = SK + "/runs"
NOTCH = ["StrongSell", "Sell", "Hold", "Buy", "StrongBuy"]
KIND = {'PONY': 'ADR', 'WRD': 'ADR', 'NOK': 'ADR', 'NASA': 'ETF', 'METU': 'ETF',
        'XLF': 'ETF', 'XLE': 'ETF', 'BTC': 'Crypto', 'ETH': 'Crypto', 'DOGE': 'Crypto', 'XRP': 'Crypto'}
EXCLUDE = {'FDRXX', 'SPAXX', 'SPRXX', 'O92E', 'TG3Y', 'PS'}
RCLASS = {'StrongSell': 'sell', 'Sell': 'sell', 'Hold': 'hold', 'Buy': 'buy', 'StrongBuy': 'buy'}
ACTION = {'StrongSell': 'Trim / reduce', 'Sell': 'Trim / reduce', 'Hold': 'Hold',
          'Buy': 'Add', 'StrongBuy': 'Add'}


def vote_dist(run_dir):
    d = {k: 0 for k in NOTCH}
    for i in range(1, 6):
        p = f"{run_dir}/50-votes/vote-{i}.md"
        if not os.path.exists(p):
            continue
        lines = [x for x in open(p).read().splitlines() if x.strip()]
        if lines:
            m = re.search(r"VERDICT:\s*(StrongSell|Sell|Hold|Buy|StrongBuy)", lines[-1])
            if m:
                d[m.group(1)] += 1
    return d


def ens_str(dist):
    nz = [(k, v) for k, v in dist.items() if v]
    if len(nz) == 1:
        return f"{nz[0][1]}–0", False
    return " / ".join(f"{v} {k}" for k, v in sorted(nz, key=lambda x: -x[1])), True


def exec_summary(md):
    """First substantive prose paragraph (the thesis) for the scorecard blurb."""
    for para in re.split(r"\n\s*\n", md):
        s = para.strip()
        if len(s) > 110 and s[0] not in "#>|_-" and not s.startswith("LEVELS:") \
                and "riskbox-block" not in s and "rating-block" not in s:
            s = re.sub(r"\[[PH]\d+\.[^\]]*\]", "", s)             # strip cite tags (incl. multi-tag)
            s = re.sub(r"\*\*|__|\*|`", "", s)                    # strip md emphasis
            s = re.sub(r"\s+", " ", s).strip()
            return html.escape(s[:250] + ("…" if len(s) > 250 else ""))
    return ""


def load(tk, asof, stamp):
    d = f"{RUNS}/{tk}-{asof}-{stamp}"
    if not os.path.exists(f"{d}/60-report.md"):
        return None
    dec = json.load(open(f"{d}/55-decision.json")) if os.path.exists(f"{d}/55-decision.json") else {}
    pos = json.load(open(f"{d}/15-position.json")) if os.path.exists(f"{d}/15-position.json") else {}
    lv = json.load(open(f"{d}/56-levels.json")) if os.path.exists(f"{d}/56-levels.json") else {}
    rating = dec.get("mode_label") or dec.get("decision") or "Hold"
    dist = vote_dist(d)
    ens, split = ens_str(dist)

    def pv(k):
        v = pos.get(k)
        return v.get("v") if isinstance(v, dict) else None
    pl = pv("H1.unrealized_pl_pct")
    bk = pv("H1.pct_of_book")

    def lvpart(side, arrow):
        s = lv.get(side) or {}
        return f"{arrow} ${s['level']:g} {s['action']}" if s.get("level") is not None else ""
    watch = " · ".join(x for x in (lvpart("downside", "▼"), lvpart("upside", "▲")) if x)
    return {
        "tk": tk, "kind": KIND.get(tk, "Equity"), "rating": rating, "rcls": RCLASS.get(rating, "hold"),
        "conv": dec.get("mean_conviction"), "spread": dec.get("spread"),
        "ens": ens, "split": split, "book_pct": bk, "pl_pct": pl,
        "action": ACTION.get(rating, "Hold"), "watch": watch,
        "detail": exec_summary(open(f"{d}/60-report.md").read()),
    }


def scorecard_rows(rows):
    out = []
    for r in rows:
        chip = f'<span class="chip {r["rcls"]}">{html.escape(r["rating"])}</span>'
        bk = f'{r["book_pct"]:.1f}%' if r["book_pct"] is not None else "—"
        barW = min(100, round((r["book_pct"] or 0) / 9 * 100))
        pl = f'{r["pl_pct"]:+.1f}%' if r["pl_pct"] is not None else "—"
        sign = "pos" if (r["pl_pct"] or 0) >= 0 else "neg"
        conv = f'{r["conv"]:.1f}' if r["conv"] is not None else "—"
        convW = round((r["conv"] or 0) * 10)
        convcls = "conv hi" if (r["conv"] or 0) >= 7 else "conv"
        enscls = "ens split" if r["split"] else "ens"
        out.append(f'''<tr class="row {r["rcls"]} rowlink" data-open="{r["tk"]}" tabindex="0" role="button" aria-label="Open {r["tk"]} report">
  <td class="tk">{r["tk"]}<span class="kind">{r["kind"]}</span></td>
  <td><div class="book"><span class="pct">{bk}</span><span class="bar"><i style="width:{barW}%"></i></span></div></td>
  <td class="pl {sign}">{pl}</td>
  <td class="l">{chip}</td>
  <td><div class="{convcls}"><span class="meter"><i style="width:{convW}%"></i></span><span class="n">{conv}</span></div></td>
  <td><span class="{enscls}">{html.escape(r["ens"])}</span></td>
  <td class="act"><b>{html.escape(r["action"])}</b><br><span class="watch">{html.escape(r["watch"])}</span></td>
</tr>
<tr class="detail {r["rcls"]} rowlink" data-open="{r["tk"]}"><td colspan="7">{r["detail"]}<span class="rmore">Read full report →</span></td></tr>''')
    return "\n".join(out)


def detail_views(order, asof, stamp):
    out = []
    for i, tk in enumerate(order):
        md = open(f"{RUNS}/{tk}-{asof}-{stamp}/60-report.md").read()
        body = md_to_html(md)
        prev = order[i - 1] if i > 0 else ""
        nxt = order[i + 1] if i < len(order) - 1 else ""
        pnav = (f'<button class="navbtn" data-open="{prev}">‹ {prev}</button>' if prev
                else '<span class="navbtn disabled">‹</span>')
        nnav = (f'<button class="navbtn" data-open="{nxt}">{nxt} ›</button>' if nxt
                else '<span class="navbtn disabled">›</span>')
        out.append(f'''<section class="view rptview" id="rpt-{tk}" hidden>
  <div class="topbar">
    <button class="backbtn" data-open="overview">‹ All holdings</button>
    <div class="topmid"><span class="topmono">{tk}</span></div>
    <div class="topnav">{pnav}{nnav}</div>
  </div>
  <div class="rptwrap"><article class="rpt">{body}</article></div>
</section>''')
    return "\n".join(out)


def portfolio_view(asof):
    pdir = f"{RUNS}/_portfolio-{asof}"
    if not os.path.exists(f"{pdir}/portfolio.md"):
        return "", ""
    synth = md_to_html(open(f"{pdir}/portfolio-synth.md").read()) if os.path.exists(f"{pdir}/portfolio-synth.md") else ""
    body = md_to_html(open(f"{pdir}/portfolio.md").read())
    view = f'''<section class="view rptview" id="portfolio" hidden>
  <div class="topbar">
    <button class="backbtn" data-open="overview">‹ All holdings</button>
    <div class="topmid"><span class="topmono">Portfolio structure</span></div>
    <div class="topnav"></div>
  </div>
  <div class="rptwrap"><article class="rpt">{synth}{body}</article></div>
</section>'''
    return view, '<button class="navbtn" data-open="portfolio">Portfolio structure →</button>'


SCORECARD_CSS = """
.wrap{max-width:1060px;margin:0 auto}
header.hd{background:var(--surface);border:1px solid var(--hair);border-top:3px solid var(--accent);
  border-radius:3px;padding:clamp(18px,3vw,30px);margin-bottom:16px}
.eyebrow{font:600 11px/1 var(--mono);letter-spacing:.16em;text-transform:uppercase;color:var(--accent);margin-bottom:12px}
header.hd h1{font-size:clamp(23px,3.3vw,34px);font-weight:680;letter-spacing:-.02em;text-wrap:balance;margin:0 0 4px;color:var(--ink)}
.sub{color:var(--muted);font-size:14.5px;max-width:64ch;margin:0}
.hdnav{margin-top:14px}
.hdnav .navbtn{font:600 12.5px/1 var(--mono);color:var(--ink);background:var(--surface);
  border:1px solid var(--hair);border-radius:4px;padding:8px 12px;cursor:pointer}
.hdnav .navbtn:hover{border-color:var(--accent);color:var(--accent)}
.stats{display:flex;flex-wrap:wrap;gap:0;margin-top:20px;border:1px solid var(--hair);border-radius:3px;overflow:hidden}
.stat{flex:1 1 120px;padding:12px 16px;border-right:1px solid var(--hair2)}
.stat:last-child{border-right:0}
.stat .k{font:600 10.5px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--faint);margin-bottom:6px}
.stat .v{font:600 19px/1.1 var(--sans);font-variant-numeric:tabular-nums;color:var(--ink)}
.stat .v small{font-size:12px;color:var(--muted);font-weight:500}
.flag .v{color:var(--sell)}
.card{background:var(--surface);border:1px solid var(--hair);border-radius:3px;overflow:hidden}
.scroll{overflow-x:auto}
table.grid{width:100%;border-collapse:collapse;min-width:740px}
table.grid thead th{font:600 10.5px/1.2 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--faint);
  text-align:right;padding:13px 14px;border-bottom:1.5px solid var(--hair);white-space:nowrap;background:#F7F9FB}
table.grid thead th.l{text-align:left}
table.grid td{padding:13px 14px;border-bottom:1px solid var(--hair2);font-variant-numeric:tabular-nums;text-align:right;vertical-align:middle;color:var(--ink)}
tr.row td{border-bottom:0}
tr.detail td{padding:0 14px 14px;border-bottom:1px solid var(--hair2);text-align:left;color:var(--muted);font-size:13px}
.rmore{display:inline-block;margin-left:8px;color:var(--accent);font:600 12px/1 var(--mono);white-space:nowrap}
tr.rowlink{cursor:pointer;transition:background .12s}
tbody tr.sell td{background:linear-gradient(90deg,rgba(169,50,38,.05),rgba(169,50,38,0) 42%)}
tbody tr.buy td{background:linear-gradient(90deg,rgba(30,110,70,.06),rgba(30,110,70,0) 42%)}
tbody:hover tr.rowlink:not(:hover){opacity:.72;transition:opacity .12s}
tr.row.rowlink:hover td,tr.row.rowlink:focus-visible td{background:var(--accent-soft)}
tr.rowlink:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.tk{font:650 15px/1 var(--mono);letter-spacing:.01em;text-align:left;color:var(--ink)}
.tk .kind{display:block;font:500 10.5px/1.3 var(--mono);color:var(--faint);letter-spacing:.02em;text-transform:uppercase;margin-top:3px}
.pl{font-weight:600;font-family:var(--mono);font-size:13.5px}
.pl.pos{color:var(--gain)} .pl.neg{color:var(--loss)}
.book{display:flex;align-items:center;gap:9px;justify-content:flex-end}
.book .bar{width:52px;height:6px;border-radius:3px;background:var(--hair);overflow:hidden}
.book .bar i{display:block;height:100%;background:var(--accent);opacity:.8}
.book .pct{font-family:var(--mono);font-size:12.5px;color:var(--muted);min-width:38px}
.chip{display:inline-block;font:650 11px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;padding:6px 10px;border-radius:3px}
.chip.sell{background:var(--sell-bg);color:var(--sell);border:1px solid rgba(169,50,38,.28)}
.chip.hold{background:var(--hold-bg);color:var(--hold);border:1px solid var(--hair)}
.chip.buy{background:rgba(30,110,70,.1);color:#1E6E46;border:1px solid rgba(30,110,70,.28)}
.conv{display:flex;align-items:center;gap:8px;justify-content:flex-end}
.conv .meter{width:46px;height:6px;border-radius:3px;background:var(--hair);overflow:hidden}
.conv .meter i{display:block;height:100%;background:var(--hold)}
.conv.hi .meter i{background:var(--accent)}
.conv .n{font-family:var(--mono);font-size:12.5px;color:var(--muted);min-width:26px;text-align:left}
.ens{font-family:var(--mono);font-size:12px;color:var(--muted);white-space:nowrap}
.ens.split{color:var(--ink)}
.act{text-align:left;font-size:13px;max-width:230px}
.watch{color:var(--faint);font-family:var(--mono);font-size:11.5px}
.note{margin:16px 0;padding:15px 18px;background:var(--surface);border:1px solid var(--hair);border-left:3px solid var(--accent);border-radius:3px;font-size:13.5px;color:var(--muted)}
.note b{color:var(--ink);font-weight:640}
footer.ft{margin-top:18px;color:var(--faint);font-size:11.5px;font-family:var(--mono);line-height:1.7}
.rptview .topbar{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:12px;
  background:rgba(238,241,244,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--hair);
  padding:11px clamp(6px,2vw,14px);margin:0 0 clamp(8px,2vw,22px)}
.backbtn,.navbtn{font:600 12.5px/1 var(--mono);color:var(--ink);background:var(--surface);
  border:1px solid var(--hair);border-radius:4px;padding:8px 12px;cursor:pointer;white-space:nowrap}
.backbtn:hover,.navbtn:hover{border-color:var(--accent);color:var(--accent)}
.navbtn.disabled{opacity:.35;cursor:default;border-color:var(--hair2)}
.topmid{margin-left:2px;display:flex;align-items:center;gap:9px}
.topmono{font:650 14px/1 var(--mono);color:var(--ink)}
.topnav{margin-left:auto;display:flex;gap:7px}
.rptwrap{max-width:768px;margin:0 auto;padding:0 clamp(4px,2vw,20px) 30px}
.backbtn:focus-visible,.navbtn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media(max-width:560px){.stat{flex-basis:50%;border-bottom:1px solid var(--hair2)}header.hd h1{font-size:24px}}
"""


def main():
    holdf = sys.argv[1]
    asof = sys.argv[2] if len(sys.argv) > 2 else "2026-07-05"
    stamp = sys.argv[3] if len(sys.argv) > 3 else "1300"
    out = sys.argv[4] if len(sys.argv) > 4 else "/Users/bytedance/.claude/jobs/f5e850a4/tmp/scorecard.html"
    h = json.load(open(holdf))
    book = h["total_book"]
    n_acct = max((r.get("n_accounts", 0) for r in h["holdings"]), default=0)

    held = sorted((r for r in h["holdings"] if r["symbol"] not in EXCLUDE),
                  key=lambda r: -r["market_value"])
    rows, order, missing = [], [], []
    for r in held:
        d = load(r["symbol"], asof, stamp)
        if d is None:
            missing.append(r["symbol"])
            continue
        rows.append(d)
        order.append(r["symbol"])

    tally = {"sell": 0, "hold": 0, "buy": 0}
    for d in rows:
        tally[d["rcls"]] += 1
    pview, pnav = portfolio_view(asof)

    stats = f'''<div class="stats">
      <div class="stat"><div class="k">Book value</div><div class="v">${book:,.0f}</div></div>
      <div class="stat"><div class="k">Accounts</div><div class="v">{n_acct} <small>· SnapTrade</small></div></div>
      <div class="stat"><div class="k">As-of</div><div class="v">{asof}</div></div>
      <div class="stat flag"><div class="k">Ratings</div><div class="v">{tally["sell"]} Sell <small style="color:var(--hold)">· {tally["hold"]} Hold</small> <small style="color:#1E6E46">· {tally["buy"]} Buy</small></div></div>
    </div>'''

    body = f'''<style>
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);font-family:var(--sans);color:var(--ink);
  line-height:1.5;-webkit-font-smoothing:antialiased;padding:0}}
.view{{padding:clamp(14px,3vw,40px)}}
.view[hidden]{{display:none}}
@media(prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
{REPORT_CSS}
{SCORECARD_CSS}
</style>
<section class="view" id="overview">
 <div class="wrap">
  <header class="hd">
    <div class="eyebrow">Trading-Research v2.3 · N=5 Opus Ensemble · Position-Blind</div>
    <h1>Full Book — {len(order)} Holdings</h1>
    <p class="sub">Deep-dive rating for every holding, adjudicated by a 5-judge ensemble on live vendor data. Ratings are position-blind; action framing is added afterward for a holder. <b>Tap any row for the full report.</b></p>
    <div class="hdnav">{pnav}</div>
    {stats}
  </header>
  <div class="card"><div class="scroll">
    <table class="grid">
      <thead><tr>
        <th class="l">Holding</th><th>% Book</th><th>Your P/L</th><th class="l">Rating</th>
        <th>Conviction</th><th>Ensemble</th><th class="l">Action &amp; watch level</th>
      </tr></thead>
      <tbody>
{scorecard_rows(rows)}
      </tbody>
    </table>
  </div></div>
  <footer class="ft">
    {len(order)} holdings deep-dived · every figure [P#.fact]-tagged &amp; cite-checked.<br>
    Data: Schwab · SEC EDGAR · Tiingo · Marketaux · Crypto.com · positions via SnapTrade (read-only, cross-broker).<br>
    Not financial advice — decision support only; you decide and execute.
  </footer>
 </div>
</section>
{pview}
{detail_views(order, asof, stamp)}
<script>
(function(){{
  var views = Array.prototype.slice.call(document.querySelectorAll('.view'));
  function show(id){{
    if(!document.getElementById(id)) id='overview';
    views.forEach(function(v){{ v.hidden = (v.id!==id); }});
    window.scrollTo(0,0);
    if(id==='overview'){{ if(location.hash) history.replaceState(null,'',location.pathname+location.search); }}
    else if(location.hash.slice(1)!==id){{ history.pushState(null,'','#'+id); }}
  }}
  document.querySelectorAll('[data-open]').forEach(function(el){{
    el.addEventListener('click', function(){{ var t=el.dataset.open; show(t==='overview'?'overview':(t==='portfolio'?'portfolio':'rpt-'+t.replace(/^rpt-/,''))); }});
    if(el.matches('tr,[role=button]')){{
      el.addEventListener('keydown', function(e){{ if(e.key==='Enter'||e.key===' '){{ e.preventDefault(); el.click(); }} }});
    }}
  }});
  window.addEventListener('popstate', function(){{ var hh=location.hash.slice(1); show(hh||'overview'); }});
  var h0=location.hash.slice(1); show(h0||'overview');
}})();
</script>'''

    open(out, "w").write(body)
    print(json.dumps({"out": out, "bytes": len(body), "reports": len(order),
                      "tally": tally, "missing": missing}))


if __name__ == "__main__":
    main()
