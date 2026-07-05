#!/usr/bin/env python3
"""Build the self-contained portfolio dossier artifact: overview scorecard +
all 10 full reports inline, with client-side nav. Body-only (Artifact wraps it)."""
import html
import sys

sys.path.insert(0, "/Users/bytedance/.claude/skills/trading-research/scripts")
from render_report import md_to_html, CSS as REPORT_CSS  # noqa: E402

SK = "/Users/bytedance/.claude/skills/trading-research"
OUT = "/Users/bytedance/.claude/jobs/f5e850a4/tmp/scorecard.html"

# ticker, kind, book%, barW, pl, sign(pos/neg), rating(sell/hold), conv, convW, convhi,
# ensemble, split(bool), action, watch, detail
ROWS = [
 ("TSLA","Equity","8.2%","100","+54.4%","pos","sell","6.0","60",False,"5–0",False,
  "Trim / reduce","invalidate: close &gt; SMA20 $399",
  "406× P/E on −3.2% revenue &amp; −50% EPS inside a full bearish SMA stack; net-cash balance sheet funds survival, not the multiple. Gap risk into ~Jul 22 earnings."),
 ("AMD","Equity","7.9%","96","+284.9%","pos","hold","6.2","62",False,"5–0",False,
  "Hold, don't add","201× P/E vs 45% growth standoff",
  "Real 44.7%-YoY-growth franchise pinned against a 201× multiple on a ~10% operating margin — no edge either way. Adding near highs is the trade the panel declines."),
 ("META","Equity","7.5%","90","−4.4%","neg","hold","6.0","60",False,"3 Hold / 2 Buy",True,
  "Hold, don't add","accumulate on SMA50 $605 reclaim",
  "Cheapest-quality name here: 31% growth, 41% op margin, $46B FCF at an undemanding 23× — but tape is in an intact downtrend. Two judges see enough value to vote Buy."),
 ("AAOI","Equity","5.3%","64","−27.0%","neg","sell","7.2","72",True,"5–0",False,
  "Trim toward exit","no reclaim of SMA20 $162 + RSI&gt;50",
  "Confirmed downtrend after the −13% Jul-2 break; price ~2× ATR below SMA20, still-negative operating margin (−11.9%) gives the market no cushion. Highest-conviction call in the book."),
 ("MSFT","Equity","5.0%","60","−8.2%","neg","hold","6.2","62",False,"5–0",False,
  "Hold, don't add","wait for SMA50 $408 reclaim",
  "Elite GARP compounder — 20% revenue &amp; 34% EPS growth at a fair 23.5× — but price sits −4.2 ATR below SMA200. No confirmed edge to average down into yet."),
 ("BTC","Crypto","4.7%","57","+12.7%","pos","hold","7.0","70",True,"5–0",False,
  "Hold","invalidate: close &lt; $62,432",
  "Dead-neutral tape — RSI 47.6, sub-0.5× ATR daily range. Small weight on a gain; no directional edge to add or trim. Fundamentals MISSING by design."),
 ("NVDA","Equity","4.1%","50","+61.1%","pos","hold","6.2","62",False,"5–0",False,
  "Hold, don't add","don't buy the falling knife",
  "Standoff: elite fundamentals (110% rev growth, 59% net margin) at a non-stretched 35× vs a confirmed medium-term breakdown (~2.1× ATR below SMA50). Wait for confirmation."),
 ("XLE","ETF · Energy","3.3%","40","+95.3%","pos","hold","6.4","64",False,"5–0",False,
  "Hold","invalidate: close &lt; SMA200 $51.34",
  "Long-run winner, soft-bearish tape, still ~1.6× ATR above SMA200. Above that line the uptrend it rode is intact; a close below is where the thesis breaks."),
 ("NOK","ADR","3.1%","38","−18.1%","neg","hold","6.0","60",False,"4 Hold / 1 Sell",True,
  "Hold, don't add","no fundamental floor (20-F filer)",
  "Broken intermediate trend (~2× ATR below SMA20/50) plus a fundamental blackout — foreign 20-F filer, almost no SEC data. One judge dissented to Sell."),
 ("AMZN","Equity","3.1%","38","+5.8%","pos","hold","6.2","62",False,"5–0",False,
  "Hold","invalidate: close &lt; SMA200 $233",
  "Near-net-debt-free compounder (EPS +73.5%) trapped ~1.55 ATR below SMA50 while holding above SMA200. Range-bound; no signal to trim or add."),
]
ORDER = [r[0] for r in ROWS]
RATING = {r[0]: r[6] for r in ROWS}


def scorecard_rows():
    out = []
    for (tk, kind, bk, barW, pl, sign, rating, conv, convW, convhi, ens, split,
         action, watch, detail) in ROWS:
        rcls = "sell" if rating == "sell" else ""
        chip = f'<span class="chip {rating}">{"Sell" if rating=="sell" else "Hold"}</span>'
        convcls = "conv hi" if convhi else "conv"
        enscls = "ens split" if split else "ens"
        out.append(f'''<tr class="row {rcls} rowlink" data-open="{tk}" tabindex="0" role="button" aria-label="Open {tk} report">
  <td class="tk">{tk}<span class="kind">{kind}</span></td>
  <td><div class="book"><span class="pct">{bk}</span><span class="bar"><i style="width:{barW}%"></i></span></div></td>
  <td class="pl {sign}">{pl}</td>
  <td class="l">{chip}</td>
  <td><div class="{convcls}"><span class="meter"><i style="width:{convW}%"></i></span><span class="n">{conv}</span></div></td>
  <td><span class="{enscls}">{ens}</span></td>
  <td class="act"><b>{action}</b><br><span class="watch">{watch}</span></td>
</tr>
<tr class="detail {rcls} rowlink" data-open="{tk}"><td colspan="7">{detail}<span class="rmore">Read full report →</span></td></tr>''')
    return "\n".join(out)


def detail_views():
    out = []
    for tk in ORDER:
        md = open(f"{SK}/runs/{tk}-2026-07-05-1300/60-report.md").read()
        body = md_to_html(md)
        idx = ORDER.index(tk)
        prev = ORDER[idx - 1] if idx > 0 else ""
        nxt = ORDER[idx + 1] if idx < len(ORDER) - 1 else ""
        chip = f'<span class="chip {RATING[tk]}">{"Sell" if RATING[tk]=="sell" else "Hold"}</span>'
        pnav = (f'<button class="navbtn" data-open="{prev}">‹ {prev}</button>' if prev
                else '<span class="navbtn disabled">‹</span>')
        nnav = (f'<button class="navbtn" data-open="{nxt}">{nxt} ›</button>' if nxt
                else '<span class="navbtn disabled">›</span>')
        out.append(f'''<section class="view rptview" id="rpt-{tk}" hidden>
  <div class="topbar">
    <button class="backbtn" data-open="overview">‹ All holdings</button>
    <div class="topmid"><span class="topmono">{tk}</span> {chip}</div>
    <div class="topnav">{pnav}{nnav}</div>
  </div>
  <div class="rptwrap"><article class="rpt">{body}</article></div>
</section>''')
    return "\n".join(out)


SCORECARD_CSS = """
.wrap{max-width:1060px;margin:0 auto}
header.hd{background:var(--surface);border:1px solid var(--hair);border-top:3px solid var(--accent);
  border-radius:3px;padding:clamp(18px,3vw,30px);margin-bottom:16px}
.eyebrow{font:600 11px/1 var(--mono);letter-spacing:.16em;text-transform:uppercase;color:var(--accent);margin-bottom:12px}
header.hd h1{font-size:clamp(23px,3.3vw,34px);font-weight:680;letter-spacing:-.02em;text-wrap:balance;margin:0 0 4px;color:var(--ink)}
.sub{color:var(--muted);font-size:14.5px;max-width:64ch;margin:0}
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
/* report detail views */
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
@media(max-width:560px){.stat{flex-basis:50%;border-bottom:1px solid var(--hair2)}header.hd h1{font-size:24px}
  .topmid .chip{display:none}}
"""

BODY = f'''<style>
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
    <div class="eyebrow">Trading-Research v2 · N=5 Opus Ensemble · Position-Blind</div>
    <h1>Combined Portfolio — Top 10 Holdings</h1>
    <p class="sub">Deep-dive rating for each holding, adjudicated by a 5-judge ensemble on live vendor data. Ratings are position-blind; action framing is added afterward for a holder. <b>Tap any row for the full report.</b></p>
    <div class="stats">
      <div class="stat"><div class="k">Book value</div><div class="v">$391,430</div></div>
      <div class="stat"><div class="k">Accounts</div><div class="v">11 <small>· 3 brokers</small></div></div>
      <div class="stat"><div class="k">As-of</div><div class="v">Jul 5 <small>· settled 07-02</small></div></div>
      <div class="stat flag"><div class="k">Actionable</div><div class="v">2 Sell <small style="color:var(--hold)">· 8 Hold</small></div></div>
    </div>
  </header>
  <div class="card"><div class="scroll">
    <table class="grid">
      <thead><tr>
        <th class="l">Holding</th><th>% Book</th><th>Your P/L</th><th class="l">Rating</th>
        <th>Conviction</th><th>Ensemble</th><th class="l">Action &amp; watch level</th>
      </tr></thead>
      <tbody>
{scorecard_rows()}
      </tbody>
    </table>
  </div></div>
  <div class="note"><b>One pattern, not ten stories.</b> Eight of ten land on Hold for the same reason — the business is fine but the tape is broken (below SMA50/200 after the broad Jul-2 down day), so the ensemble sees no entry edge → "hold, don't add." The two Sells (TSLA, AAOI) are where valuation or fundamentals <b>also</b> fail — and they sit on opposite P/L: TSLA a +54% winner to trim, AAOI a −27% loser to cut. Both calls are position-blind.</div>
  <footer class="ft">
    184 agents · 0 errors · ~9.5M tokens · ~23 min · every figure [P#.fact]-tagged &amp; cite-checked (10/10 pass).<br>
    Data: Schwab · SEC EDGAR · Tiingo · Marketaux · Crypto.com · positions via SnapTrade (read-only, cross-broker).<br>
    Not financial advice — decision support only; you decide and execute.
  </footer>
 </div>
</section>
{detail_views()}
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
    el.addEventListener('click', function(){{ show(el.dataset.open==='overview'?'overview':'rpt-'+el.dataset.open.replace(/^rpt-/,'')); }});
    if(el.matches('tr,[role=button]')){{
      el.addEventListener('keydown', function(e){{ if(e.key==='Enter'||e.key===' '){{ e.preventDefault(); el.click(); }} }});
    }}
  }});
  window.addEventListener('popstate', function(){{ var h=location.hash.slice(1); show(h||'overview'); }});
  var h0=location.hash.slice(1); show(h0||'overview');
}})();
</script>'''

open(OUT, "w").write(BODY)
print(f"wrote {OUT} ({len(BODY)} bytes), {len(ORDER)} reports embedded")
