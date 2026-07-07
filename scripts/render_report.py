#!/usr/bin/env python3
"""Render a trading-research 60-report.md into a self-contained styled HTML page.

Markdown stays the canonical artifact (qa_check.py / ledger.py parse it); this is
the DELIVERY layer. Adds two deterministic dashboards built from the pack JSON
(no LLM): a KEY-INDICATORS panel and a TWO-SIDED DECISION RAIL (green add-zone /
red exit-zone, every level action-labeled). Reusable by the single-ticker
pipeline (Stage 7b) and the portfolio dossier builder.

Usage: render_report.py <60-report.md> [<out.html>]
  reads siblings 10-datapack.json / 15-position.json / 56-levels.json when present.
Import: md_to_html(md, dashboard); build_dashboard(pack,pos,levels); CSS; full_page.
"""
import html
import json
import re
import sys
from pathlib import Path
import levels_schema

CSS = """
:root{
  --ground:#EEF1F4;--surface:#FFFFFF;--ink:#19212E;--muted:#5A6675;--faint:#8994A3;
  --hair:#E1E6EC;--hair2:#EDF0F4;--accent:#0F6E68;--accent-soft:#E4F0EE;
  --sell:#A93226;--sell-bg:#FBEDEA;--hold:#4B5568;--hold-bg:#EEF1F5;
  --gain:#137A4B;--gain-soft:#E5F1EA;--loss:#B23B3B;--loss-soft:#FBEBEA;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
}
.rpt{font-family:var(--sans);color:var(--ink);line-height:1.62;font-size:15px}
.rpt h1{font-size:clamp(22px,3vw,30px);font-weight:680;letter-spacing:-.02em;text-wrap:balance;margin:0 0 6px}
.rpt h2{font-size:17px;font-weight:660;letter-spacing:-.01em;margin:30px 0 10px;
  padding-left:11px;border-left:3px solid var(--accent);text-wrap:balance}
.rpt h3{font-size:15px;font-weight:650;margin:20px 0 8px}
.rpt p{margin:0 0 13px}
.rpt p.muted{color:var(--muted);font-size:13px}
.rpt em{font-style:italic}
.rpt ul{margin:0 0 14px;padding-left:20px}
.rpt li{margin:0 0 6px}
.rpt strong{font-weight:660;color:var(--ink)}
.rpt code{font-family:var(--mono);font-size:12.5px;background:var(--hair2);padding:1px 5px;border-radius:3px;color:#2C3846}
.rpt a{color:var(--accent);text-decoration:none;border-bottom:1px solid var(--accent-soft)}
.rpt a:hover{border-bottom-color:var(--accent)}
.rpt hr{border:0;border-top:1px solid var(--hair);margin:26px 0}
.rpt blockquote{margin:0 0 20px;padding:12px 16px;background:var(--surface);border:1px solid var(--hair);
  border-left:3px solid var(--accent);border-radius:3px;color:var(--muted);font-size:13.5px;font-family:var(--mono);line-height:1.7}
.rpt blockquote p{margin:0}
.rpt .tag{font-family:var(--mono);font-size:10.5px;color:var(--accent);background:var(--accent-soft);
  padding:1px 5px;border-radius:3px;letter-spacing:.01em;white-space:nowrap;vertical-align:baseline}
.rpt .tag.h{color:#8a5a12;background:#F6EEDD}
.rpt .tblwrap{overflow-x:auto;margin:0 0 18px}
.rpt table{border-collapse:collapse;width:100%;min-width:340px;font-size:13.5px}
.rpt th,.rpt td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--hair2);font-variant-numeric:tabular-nums}
.rpt th{font:600 10.5px/1.2 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--faint);
  border-bottom:1.5px solid var(--hair);background:#F7F9FB}
.rpt .rating-callout{background:var(--surface);border:1px solid var(--hair);border-top:3px solid var(--accent);
  border-radius:4px;padding:16px 18px;margin:18px 0}
.rpt .rating-callout h3{margin-top:0}
.rpt .rating-callout table{background:transparent}
.rpt .risk-callout{background:#FCFAF6;border:1px solid #ECE4D3;border-left:3px solid #B8862A;border-radius:4px;padding:4px 18px;margin:18px 0}
/* --- key-indicators panel (A) --- */
.rpt .dash{margin:2px 0 24px}
.rpt .kpanel{display:grid;grid-template-columns:repeat(auto-fill,minmax(148px,1fr));gap:1px;
  background:var(--hair);border:1px solid var(--hair);border-radius:4px;overflow:hidden;margin:0 0 14px}
.rpt .kgroup{grid-column:1/-1;background:#F2F5F8;font:600 10px/1 var(--mono);letter-spacing:.11em;
  text-transform:uppercase;color:var(--faint);padding:9px 12px}
.rpt .ktile{background:var(--surface);padding:9px 12px}
.rpt .ktile .kl{font:600 9.5px/1.2 var(--mono);letter-spacing:.03em;text-transform:uppercase;color:var(--faint);margin-bottom:3px}
.rpt .ktile .kv{font:600 14.5px/1.15 var(--sans);font-variant-numeric:tabular-nums;color:var(--ink)}
.rpt .ktile .ks{font:500 10.5px/1.35 var(--mono);color:var(--muted);margin-top:2px}
.rpt .ktile .kv.pos,.rpt .ktile .ks.pos{color:var(--gain)}
.rpt .ktile .kv.neg,.rpt .ktile .ks.neg{color:var(--loss)}
/* --- decision rail (B) --- */
.rpt .rail{background:var(--surface);border:1px solid var(--hair);border-radius:4px;padding:13px 16px 11px;margin:0 0 16px}
.rpt .rail .rt{font:600 10px/1 var(--mono);letter-spacing:.11em;text-transform:uppercase;color:var(--faint);margin-bottom:2px}
.rpt .rail svg{width:100%;height:auto;display:block;overflow:visible}
.rpt .rail .gauge{display:flex;gap:10px 22px;flex-wrap:wrap;margin-top:9px;font:600 12.5px/1.35 var(--mono)}
.rpt .rail .gauge .ex{color:var(--loss)}
.rpt .rail .gauge .ad{color:var(--gain)}
.rpt .rail .gauge small{color:var(--muted);font-weight:500}
"""

PAGE_TMPL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:var(--ground)}}
.page{{max-width:820px;margin:0 auto;padding:clamp(16px,3vw,44px)}}
{css}
</style></head><body><div class="page"><article class="rpt">{inner}</article></div></body></html>"""

TAG_RE = re.compile(r"\[([PH])(\d+)\.([A-Za-z0-9_]+)\]")
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
CODE_RE = re.compile(r"`([^`]+)`")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


# ----------------------------- inline markdown -----------------------------

def _inline(text):
    out, last = [], 0
    for m in LINK_RE.finditer(text):
        out.append(("t", text[last:m.start()]))
        out.append(("a", m.group(1), m.group(2)))
        last = m.end()
    out.append(("t", text[last:]))
    res = []
    for seg in out:
        if seg[0] == "a":
            res.append(f'<a href="{html.escape(seg[2], quote=True)}" target="_blank" '
                       f'rel="noopener">{_fmt(seg[1])}</a>')
        else:
            res.append(_fmt(seg[1]))
    return "".join(res)


def _fmt(text):
    t = html.escape(text)
    t = CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", t)
    t = BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", t)
    t = TAG_RE.sub(lambda m: f'<span class="tag{" h" if m.group(1)=="H" else ""}">'
                             f'[{m.group(1)}{m.group(2)}.{m.group(3)}]</span>', t)
    return t


def md_to_html(md, dashboard=""):
    md = COMMENT_RE.sub("\n\x00COMMENT\x00\n", md)
    lines = md.split("\n")
    html_parts, i, n = [], 0, len(lines)
    in_rating = in_risk = False

    def close_callouts():
        nonlocal in_rating, in_risk
        if in_rating:
            html_parts.append("</div>")
            in_rating = False
        if in_risk:
            html_parts.append("</div>")
            in_risk = False

    para = []

    def flush_para():
        if para:
            txt = " ".join(para).strip()
            mi = re.match(r"^_(.+)_$", txt)
            if mi:
                html_parts.append('<p class="muted"><em>' + _inline(mi.group(1)) + "</em></p>")
            else:
                html_parts.append("<p>" + _inline(txt) + "</p>")
            para.clear()

    while i < n:
        line = lines[i]
        s = line.strip()
        if s == "\x00COMMENT\x00":
            i += 1
            continue
        if not s:
            flush_para()
            i += 1
            continue
        m = re.match(r"(#{1,6})\s+(.*)", s)
        if m:
            flush_para()
            level = len(m.group(1))
            txt = m.group(2)
            if level == 2:
                close_callouts()
            is_rating = "Ensemble Rating" in txt
            is_risk = txt.startswith("Risk Box") or txt.startswith("Risk box")
            if is_rating and not in_rating:
                html_parts.append('<div class="rating-callout">')
                in_rating = True
            elif is_risk and not in_risk:
                html_parts.append('<div class="risk-callout">')
                in_risk = True
            html_parts.append(f"<h{level}>{_inline(txt)}</h{level}>")
            i += 1
            continue
        if s in ("---", "***"):
            flush_para()
            close_callouts()
            html_parts.append("<hr>")
            i += 1
            continue
        if s.startswith(">"):
            flush_para()
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            html_parts.append("<blockquote>" +
                              "".join(f"<p>{_inline(b)}</p>" for b in buf if b.strip()) +
                              "</blockquote>")
            continue
        if s.startswith("|") and i + 1 < n and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]):
            flush_para()
            header = [c.strip() for c in s.strip("|").split("|")]
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{_inline(c)}</th>" for c in header)
            trs = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            html_parts.append(f'<div class="tblwrap"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>')
            continue
        if re.match(r"^[-*]\s+", s):
            flush_para()
            items = []
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*]\s+", "", lines[i].strip()))
                i += 1
            html_parts.append("<ul>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + "</ul>")
            continue
        para.append(s)
        i += 1
    flush_para()
    close_callouts()
    body = "\n".join(html_parts)
    if dashboard:
        if "</blockquote>" in body:
            body = body.replace("</blockquote>", "</blockquote>\n" + dashboard, 1)
        elif "</h1>" in body:
            body = body.replace("</h1>", "</h1>\n" + dashboard, 1)
        else:
            body = dashboard + body
    return body


# ----------------------------- dashboards (A/B/E) -----------------------------

def _v(pack, fid):
    x = pack.get(fid)
    return x.get("v") if isinstance(x, dict) else None


def _usd(v):
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e12:
        return f"${v/1e12:.2f}T"
    if a >= 1e9:
        return f"${v/1e9:.1f}B"
    if a >= 1e6:
        return f"${v/1e6:.0f}M"
    return f"${v:,.2f}"


def _px(v):
    if v is None:
        return "—"
    return f"${v:,.2f}" if abs(v) < 1000 else f"${v:,.0f}"


def derive_levels(pack, rating=None, existing=None):
    """Two-sided action-labeled decision levels. Prefer an explicit 56-levels.json
    (existing); else derive nearest support/resistance from SMA set + day range."""
    if existing and existing.get("downside") is not None or existing and existing.get("upside") is not None:
        return existing
    spot = _v(pack, "P1.price") or _v(pack, "P1.last")
    atr = _v(pack, "P2.atr14")
    if spot is None:
        return None
    sma = {"SMA20": _v(pack, "P2.sma20"), "SMA50": _v(pack, "P2.sma50"), "SMA200": _v(pack, "P2.sma200")}
    sma = {k: v for k, v in sma.items() if v is not None}
    below = {k: v for k, v in sma.items() if v < spot}
    above = {k: v for k, v in sma.items() if v > spot}
    dlo, dhi = _v(pack, "P1.day_low"), _v(pack, "P1.day_high")
    r = (rating or "").lower()
    dn = up = None
    # downside: nearest SMA below spot; else the day-low (intraday floor)
    if below:
        k = max(below, key=below.get)
        lvl, basis = below[k], f"close < {k}"
    elif dlo is not None and dlo < spot:
        k, lvl, basis = "day-low", dlo, "close < day-low"
    else:
        k = None
    if k is not None:
        act = "Exit" if "200" in k else ("Sell / exit" if r == "sell" else "Trim / exit")
        dn = {"level": round(lvl, 2), "action": act, "basis": basis,
              "atr_dist": round((spot - lvl) / atr, 2) if atr else None}
    # upside: nearest SMA above spot; else the day-high
    if above:
        k2 = min(above, key=above.get)
        lvl2, basis2 = above[k2], f"close > {k2}"
    elif dhi is not None and dhi > spot:
        k2, lvl2, basis2 = "day-high", dhi, "close > day-high"
    else:
        k2 = None
    if k2 is not None:
        act = "Stop trimming / re-rate" if r == "sell" else "Add / buy"
        up = {"level": round(lvl2, 2), "action": act, "basis": basis2,
              "atr_dist": round((lvl2 - spot) / atr, 2) if atr else None}
    return {"spot": round(spot, 2), "downside": dn, "upside": up, "derived": True}


def key_panel(pack, pos):
    px = _v(pack, "P1.price") or _v(pack, "P1.last")
    atr = _v(pack, "P2.atr14")

    def tiles(rows):
        return "".join(
            f'<div class="ktile"><div class="kl">{lbl}</div>'
            f'<div class="kv{(" " + kc) if kc else ""}">{val}</div>'
            f'{f"<div class=\"ks {sc}\">{sub}</div>" if sub else ""}</div>'
            for (lbl, val, sub, kc, sc) in rows if val not in (None, "—"))

    groups = []

    def sma_sub(sid):
        s = _v(pack, sid)
        if s is None or px is None or not atr:
            return None, ""
        d = (px - s) / atr
        return f"{d:+.1f}×ATR {'below' if d < 0 else 'above'}", ("neg" if d < 0 else "pos")

    chg = _v(pack, "P1.chg_pct_1d")
    chg24 = _v(pack, "P1.chg_pct_24h")
    ch = chg if chg is not None else chg24
    price_rows = [
        ("Price", _px(px), (f"{ch:+.2f}% last session" if ch is not None else None),
         None, ("neg" if (ch or 0) < 0 else "pos")),
        ("52-wk range", (f"{_px(_v(pack,'P1.low_52w'))} – {_px(_v(pack,'P1.high_52w'))}"
                         if _v(pack, "P1.high_52w") else None), None, None, ""),
        ("Mkt cap", _usd(_v(pack, "P1.mcap")), None, None, ""),
    ]
    for sid, nm in (("P2.sma20", "SMA20"), ("P2.sma50", "SMA50"), ("P2.sma200", "SMA200")):
        v = _v(pack, sid)
        if v is not None:
            sub, sc = sma_sub(sid)
            price_rows.append((nm, _px(v), sub, None, sc))
    pr = tiles(price_rows)
    if pr:
        groups.append('<div class="kgroup">Price &amp; trend</div>' + pr)

    rsi = _v(pack, "P2.rsi14")
    rsi_sub = None
    if rsi is not None:
        rsi_sub = "oversold" if rsi < 30 else ("overbought" if rsi > 70 else "neutral")
    macd, sig = _v(pack, "P2.macd"), _v(pack, "P2.macd_signal")
    mom_rows = [
        ("RSI 14", (f"{rsi:.1f}" if rsi is not None else None), rsi_sub, None, ""),
        ("MACD", (f"{macd:.2f}" if macd is not None else None),
         (f"vs signal {sig:.2f} ({'above' if macd > sig else 'below'})" if macd is not None and sig is not None else None),
         None, ("pos" if (macd is not None and sig is not None and macd > sig) else "neg")),
        ("ATR 14", (_px(atr) if atr else None),
         (f"{_v(pack,'P2.atr14_pct'):.1f}% of price" if _v(pack, "P2.atr14_pct") else None), None, ""),
        ("30-day σ", (f"{_v(pack,'P2.sigma30'):.1f}%/day" if _v(pack, "P2.sigma30") else None), None, None, ""),
    ]
    mr = tiles(mom_rows)
    if mr:
        groups.append('<div class="kgroup">Momentum &amp; volatility</div>' + mr)

    gm, om, nm2 = _v(pack, "P3.gross_margin_ttm"), _v(pack, "P3.operating_margin_ttm"), _v(pack, "P3.net_margin_ttm")
    margins = "—"
    if any(x is not None for x in (gm, om, nm2)):
        def _m(v, d):
            return f"{v:.{d}f}" if v is not None else "n/a"
        margins = f"{_m(gm, 1)} / {_m(om, 0)} / {_m(nm2, 0)}%"
    fund_rows = [
        ("P/E TTM", (f"{_v(pack,'P3.pe_ttm'):.1f}×" if _v(pack, "P3.pe_ttm") else None), None, None, ""),
        ("Revenue TTM", _usd(_v(pack, "P3.revenue_ttm")),
         (f"{_v(pack,'P3.revenue_yoy'):+.1f}% YoY" if _v(pack, "P3.revenue_yoy") is not None else None),
         None, ("pos" if (_v(pack, "P3.revenue_yoy") or 0) >= 0 else "neg")),
        ("EPS TTM", (f"${_v(pack,'P3.eps_diluted_ttm'):.2f}" if _v(pack, "P3.eps_diluted_ttm") is not None else None),
         (f"{_v(pack,'P3.eps_yoy'):+.1f}% YoY" if _v(pack, "P3.eps_yoy") is not None else None),
         None, ("pos" if (_v(pack, "P3.eps_yoy") or 0) >= 0 else "neg")),
        ("Margins G/O/N", (margins if gm is not None else None), None, None, ""),
        ("FCF TTM", _usd(_v(pack, "P3.fcf_ttm")), None, None,
         ("pos" if (_v(pack, "P3.fcf_ttm") or 0) >= 0 else "neg")),
        ("Net debt", _usd(_v(pack, "P3.net_debt")),
         ("net cash" if (_v(pack, "P3.net_debt") or 0) < 0 else None), None,
         ("pos" if (_v(pack, "P3.net_debt") or 0) < 0 else "")),
    ]
    fr = tiles(fund_rows)
    if fr:
        groups.append('<div class="kgroup">Fundamentals</div>' + fr)

    # P8 (UW dealer positioning) leads when present; P4 (Schwab IV) is the
    # fallback — under --options P4 is suppressed, so a P4-only panel would blank.
    gex = _v(pack, "P8.gex_net")
    regime = _v(pack, "P8.gex_net") is not None and _v(pack, "P8.gex_regime") or None
    ivr = _v(pack, "P8.iv_rank_1y")
    flip = _v(pack, "P8.flip_level")
    dflip = _v(pack, "P8.dist_flip")
    skew = _v(pack, "P8.rr_skew_25d")
    skew_fact = pack.get("P8.rr_skew_25d")
    skew_lbl = skew_fact.get("label") if isinstance(skew_fact, dict) else None
    iv = _v(pack, "P4.atm_iv_near")
    opt_rows = [
        ("Net GEX", (_usd(gex) if gex is not None else None), regime, None,
         ("neg" if regime == "short-gamma" else ("pos" if regime == "long-gamma" else ""))),
        ("IV rank 1y", (f"{ivr:.0f}%" if ivr is not None else None), None, None, ""),
        ("Gamma flip", (_px(flip) if flip is not None else None),
         (f"{dflip*100:+.1f}% from spot" if dflip is not None else None), None, ""),
        ("25Δ RR skew", (f"{skew*100:+.1f}%" if skew is not None else None),
         skew_lbl, None, ("neg" if (skew or 0) < 0 else "pos")),
        ("ATM IV", (f"{iv*100:.1f}%" if iv is not None else None), None, None, ""),
        ("Put/Call vol", (f"{_v(pack,'P4.put_call_volume_ratio'):.2f}" if _v(pack, "P4.put_call_volume_ratio") else None),
         None, None, ""),
        ("Put/Call OI", (f"{_v(pack,'P4.put_call_oi_ratio'):.2f}" if _v(pack, "P4.put_call_oi_ratio") else None),
         None, None, ""),
    ]
    ovr = tiles(opt_rows)
    if ovr:
        groups.append('<div class="kgroup">Options positioning</div>' + ovr)

    if pos and pos.get("H1.held", {}).get("v"):
        plp = _v(pos, "H1.unrealized_pl_pct")
        pos_rows = [
            ("Weight", (f"{_v(pos,'H1.pct_of_book'):.2f}% of book" if _v(pos, "H1.pct_of_book") else None), None, None, ""),
            ("Value", _usd(_v(pos, "H1.market_value")),
             (f"{_v(pos,'H1.shares'):g} sh" if _v(pos, "H1.shares") else None), None, ""),
            ("Avg cost", _px(_v(pos, "H1.avg_cost")), None, None, ""),
            ("Open P/L", (f"{plp:+.1f}%" if plp is not None else None), None,
             ("pos" if (plp or 0) >= 0 else "neg"), ""),
        ]
        psr = tiles(pos_rows)
        if psr:
            groups.append('<div class="kgroup">Your position</div>' + psr)

    if not groups:
        return ""
    return '<div class="kpanel">' + "".join(groups) + "</div>"


def decision_rail(pack, levels):
    if not levels:
        return ""
    spot = levels.get("spot") or _v(pack, "P1.price")
    if spot is None:
        return ""
    dn, up = levels.get("downside"), levels.get("upside")
    ctx = {"S20": _v(pack, "P2.sma20"), "S50": _v(pack, "P2.sma50"), "S200": _v(pack, "P2.sma200")}
    dlo, dhi = _v(pack, "P1.day_low"), _v(pack, "P1.day_high")
    pts = [spot] + [x for x in ctx.values() if x] + [x for x in (dlo, dhi) if x]
    if dn:
        pts.append(dn["level"])
    if up:
        pts.append(up["level"])
    lo, hi = min(pts), max(pts)
    if hi <= lo:
        hi = lo * 1.02 + 1
    span = hi - lo
    lo -= span * 0.05
    hi += span * 0.05
    W, H, padx, base = 620, 66, 46, 34

    def X(v):
        return padx + (v - lo) / (hi - lo) * (W - 2 * padx)

    svg = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="decision levels">']
    # zones
    if dn:
        svg.append(f'<rect x="{X(lo):.1f}" y="{base-9}" width="{X(dn["level"])-X(lo):.1f}" height="18" '
                   f'fill="var(--loss-soft)"/>')
    if up:
        svg.append(f'<rect x="{X(up["level"]):.1f}" y="{base-9}" width="{X(hi)-X(up["level"]):.1f}" height="18" '
                   f'fill="var(--gain-soft)"/>')
    # baseline
    svg.append(f'<line x1="{X(lo):.1f}" y1="{base}" x2="{X(hi):.1f}" y2="{base}" stroke="var(--hair)" stroke-width="2"/>')
    # SMA ticks
    for nm, v in ctx.items():
        if v is None:
            continue
        x = X(v)
        svg.append(f'<line x1="{x:.1f}" y1="{base-5}" x2="{x:.1f}" y2="{base+5}" stroke="var(--faint)" stroke-width="1.5"/>')
        svg.append(f'<text x="{x:.1f}" y="{base+18}" font-size="9" fill="var(--faint)" text-anchor="middle" '
                   f'font-family="var(--mono)">{nm}</text>')
    # downside/upside markers
    if dn:
        x = X(dn["level"])
        svg.append(f'<line x1="{x:.1f}" y1="{base-13}" x2="{x:.1f}" y2="{base+13}" stroke="var(--loss)" '
                   f'stroke-width="2" stroke-dasharray="3 2"/>')
        svg.append(f'<text x="{X(lo)+3:.1f}" y="{base-13}" font-size="10" fill="var(--loss)" font-weight="700" '
                   f'font-family="var(--mono)">▼ {dn["action"].upper()}</text>')
    if up:
        x = X(up["level"])
        svg.append(f'<line x1="{x:.1f}" y1="{base-13}" x2="{x:.1f}" y2="{base+13}" stroke="var(--gain)" '
                   f'stroke-width="2" stroke-dasharray="3 2"/>')
        svg.append(f'<text x="{X(hi)-3:.1f}" y="{base-13}" font-size="10" fill="var(--gain)" font-weight="700" '
                   f'text-anchor="end" font-family="var(--mono)">▲ {up["action"].upper()}</text>')
    # spot marker
    xs = X(spot)
    svg.append(f'<circle cx="{xs:.1f}" cy="{base}" r="5" fill="var(--ink)"/>')
    svg.append(f'<text x="{xs:.1f}" y="{base-14}" font-size="11" fill="var(--ink)" font-weight="700" '
               f'text-anchor="middle" font-family="var(--mono)">{_px(spot)}</text>')
    svg.append("</svg>")

    gauge = []
    if dn:
        d = dn.get("atr_dist")
        label = str(dn.get("action") or "Downside").upper()
        gauge.append(f'<span class="ex">▼ {html.escape(label)} {_px(dn["level"])}'
                     f'{f" · {abs(d):.1f}×ATR below" if d is not None else ""} '
                     f'<small>→ {html.escape(dn["action"])} ({html.escape(dn["basis"])})</small></span>')
    if up:
        d = up.get("atr_dist")
        label = str(up.get("action") or "Upside").upper()
        gauge.append(f'<span class="ad">▲ {html.escape(label)} {_px(up["level"])}'
                     f'{f" · {abs(d):.1f}×ATR above" if d is not None else ""} '
                     f'<small>→ {html.escape(up["action"])} ({html.escape(up["basis"])})</small></span>')
    note = ' <span style="color:var(--faint);font-weight:500">(levels derived from SMA structure — see risk box)</span>' if levels.get("derived") else ""
    return ('<div class="rail"><div class="rt">Decision levels — where the call flips' + note + '</div>'
            + "".join(svg) + '<div class="gauge">' + "".join(gauge) + "</div></div>")


def build_dashboard(pack, pos, levels):
    return '<div class="dash">' + key_panel(pack, pos) + decision_rail(pack, levels) + "</div>"


def parse_levels_marker(md, pack, rating=None):
    """Extract schema-v2 `LEVELS_JSON`, or safely upgrade a legacy `LEVELS:` line.

    Writer-emitted sides win; any side the writer omits or marks `None` is filled from
    derive_levels so the registry is ALWAYS two-sided with a named action (spec E).
    """
    jm = re.search(r"^LEVELS_JSON:\s*```(?:json)?\s*(.*?)\s*```", md, re.M | re.S)
    if jm:
        try:
            return levels_schema.normalize_level_set(json.loads(jm.group(1)), rating)
        except (ValueError, TypeError):
            pass
    m = re.search(r"^LEVELS:\s*(.+)$", md, re.M)
    spot = _v(pack, "P1.price") or _v(pack, "P1.last")
    atr = _v(pack, "P2.atr14")
    fallback = derive_levels(pack, rating) or {}
    if not m:
        return levels_schema.normalize_level_set(fallback, rating) if fallback else None
    body = m.group(1)

    def side(key):
        mm = re.search(rf"{key}=([0-9.]+)\|(.+?)(?=\s+\w+=|$)", body)
        bm = re.search(rf"basis_{('dn' if key=='downside' else 'up')}=([^\s]+)", body)
        if not mm:
            return None
        act = mm.group(2).strip()
        if not act or act.lower() == "none":
            return None
        lvl = float(mm.group(1))
        dist = round((spot - lvl) / atr, 2) if (atr and key == "downside") else (
            round((lvl - spot) / atr, 2) if atr else None)
        return {"level": lvl, "action": act,
                "basis": (bm.group(1).replace("_", " ") if bm else key), "atr_dist": dist}
    dn, up = side("downside"), side("upside")
    legacy = {"spot": round(spot, 2) if spot else fallback.get("spot"),
              "downside": dn or fallback.get("downside"),
              "upside": up or fallback.get("upside"),
              "derived": dn is None and up is None}
    return levels_schema.normalize_level_set(legacy, rating)


def _rating_from_md(md):
    m = re.search(r"Ensemble Rating:\s*\*\*(\w+)\*\*", md)
    return m.group(1) if m else None


def full_page(inner, title):
    return PAGE_TMPL.format(title=html.escape(title), css=CSS, inner=inner)


def main(argv):
    src = Path(argv[0])
    out = Path(argv[1]) if len(argv) > 1 else src.with_suffix(".html")
    md = src.read_text()
    d = src.parent
    pack = json.loads((d / "10-datapack.json").read_text()) if (d / "10-datapack.json").exists() else {}
    pos = json.loads((d / "15-position.json").read_text()) if (d / "15-position.json").exists() else {}
    rating = _rating_from_md(md)
    # Re-parse the marker on every render so 56-levels.json tracks the current report
    # (SSOT for the rail + monitor F); only fall back to on-disk when parse yields nothing.
    parsed = parse_levels_marker(md, pack, rating)
    if parsed and (parsed.get("downside") or parsed.get("upside")):
        levels = parsed
        (d / "56-levels.json").write_text(json.dumps(levels))
    elif (d / "56-levels.json").exists():
        levels = json.loads((d / "56-levels.json").read_text())
    else:
        levels = parsed
    dash = build_dashboard(pack, pos, levels) if pack else ""
    m = re.search(r"^#\s+(.+)$", md, re.M)
    title = m.group(1).strip() if m else src.stem
    out.write_text(full_page(md_to_html(md, dash), title))
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
