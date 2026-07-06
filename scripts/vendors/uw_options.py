#!/usr/bin/env python3
"""P8 options-analytics pack from Unusual Whales — dealer GEX, IV rank/skew,
max pain, live flow. Deterministic: net GEX, nearest-spot gamma flip and a
sign-driven regime are computed here (agents cite, never recompute). Live
snapshot; no --asof. Contract: tech-solution-options-analysis.md.

Fetches ~17 UW endpoints through the get_json seam (NOT data_or_die, which is
process-fatal); a dead endpoint becomes a P8._gaps entry, never a fabricated
number. Emits scalar single-unit citable facts + context-only lists + P8._gaps.
Exit 0 when >=1 group returns, 3 when nothing, 2 on 401, 4 on sustained 429."""
import argparse
import math
import sys
import time
from datetime import datetime, timezone

import _uw_common as uw

PACE_S = 0.75  # ~80/min inter-call, under UW's ~120/min ceiling (tests set 0)
SESSION_MAP = {"pm": "pre-open", "r": "mid", "po": "post"}


def f(x):
    """UW numbers arrive as strings; null / '' / None -> 0.0."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _pct(vals, p):
    """p-th percentile (0-100) of a numeric list, linear interp. [] -> 0.0."""
    s = sorted(vals)
    if not s:
        return 0.0
    if len(s) == 1:
        return s[0]
    r = (p / 100) * (len(s) - 1)
    lo = int(math.floor(r))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (r - lo)


class Fetcher:
    """Paced, non-fatal UW fetch. Records per-endpoint gaps; flags 401/429."""

    def __init__(self):
        self._last = 0.0
        self.gaps = []
        self.auth_failed = False
        self.rate_limited = False

    def get(self, path, fact_name, params=None):
        gap = PACE_S - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        status, body = uw.get_json(path, params)
        self._last = time.monotonic()
        if status == 200:
            return body.get("data", body) if isinstance(body, dict) else body
        if status == 401:
            self.auth_failed = True
        if status == 429:
            self.rate_limited = True
        reason = str(body)[:80] if not isinstance(body, dict) else (
            body.get("message") or body.get("code") or str(body)[:80])
        self.gaps.append(f"MISSING({fact_name}: HTTP {status} {reason})")
        return None


def fct(v, unit, history, derived=False, src="uw", asof=None):
    return {"v": v, "unit": unit, "asof": asof, "src": src,
            "history": history, "derived": derived}


def _latest(rows):
    """UW daily series are oldest-first; newest = last."""
    return rows[-1] if rows else None


def build(ticker, spot, atr, earnings, fetch):
    """Return (facts_dict, gaps_list). facts keyed P8.*; P8._gaps carries the
    endpoint-level MISSING() strings so a partial failure is named, not silent."""
    F = {}
    run_asof = datetime.now(timezone.utc).date().isoformat()

    # ---- Dealer positioning ----
    ge = fetch.get(f"/api/stock/{ticker}/greek-exposure", "gex_net")
    gex_series, gex_net, ge_asof = [], None, run_asof
    if ge:
        gex_series = [[r.get("date"), f(r.get("call_gamma")) + f(r.get("put_gamma"))] for r in ge]
        last = _latest(ge)
        gex_net = f(last.get("call_gamma")) + f(last.get("put_gamma"))
        ge_asof = last.get("date") or run_asof
        F["P8.gex_net"] = fct(round(gex_net, 2), "usd", "daily", True, asof=ge_asof)
        F["P8.gex_series"] = fct(gex_series, "list", "daily", asof=ge_asof)

    gs = fetch.get(f"/api/stock/{ticker}/greek-exposure/strike", "flip_level")
    flip, gs_asof = None, run_asof
    if gs:
        gs_asof = (gs[0].get("date") if gs else None) or run_asof
        pts = sorted(
            (f(r.get("strike")), f(r.get("call_gex")) + f(r.get("put_gex")))
            for r in gs if r.get("strike") not in (None, ""))
        cum, cums = 0.0, []
        for k, ng in pts:
            cum += ng
            cums.append((k, cum))
        crossings = []
        for i in range(1, len(cums)):
            k0, c0 = cums[i - 1]
            k1, c1 = cums[i]
            if (c0 <= 0 <= c1) or (c0 >= 0 >= c1):
                crossings.append(k0 + (0 - c0) * (k1 - k0) / (c1 - c0) if c1 != c0 else k1)
        if crossings:
            flip = round(min(crossings, key=lambda x: abs(x - spot)), 2)
            F["P8.flip_level"] = fct(flip, "price", "snapshot", True, asof=gs_asof)
            F["P8.dist_flip"] = fct(round((spot - flip) / spot, 4), "pct", "snapshot", True, asof=gs_asof)
        walls = sorted(pts, key=lambda p: abs(p[1]), reverse=True)[:8]
        F["P8.gex_by_strike"] = fct([[k, round(v, 2)] for k, v in walls], "list", "snapshot", asof=gs_asof)

    gexp = fetch.get(f"/api/stock/{ticker}/greek-exposure/expiry", "gex_front_dte")
    if gexp:
        front = [r for r in gexp if f(r.get("dte", r.get("expiry", 999))) <= 2] if isinstance(gexp, list) else []
        # dte may be absent; fall back to the two nearest expiries
        near = front or (sorted(gexp, key=lambda r: str(r.get("expiry", "")))[:2] if gexp else [])
        fdte = sum(f(r.get("call_gex", r.get("call_gamma"))) + f(r.get("put_gex", r.get("put_gamma"))) for r in near)
        F["P8.gex_front_dte"] = fct(round(fdte, 2), "usd", "snapshot", True, asof=gs_asof)

    # ---- Vol surface ----
    ivr = fetch.get(f"/api/stock/{ticker}/iv-rank", "iv_rank_1y")
    if ivr:
        last = _latest(ivr)
        F["P8.iv_rank_1y"] = fct(round(f(last.get("iv_rank_1y")), 2), "pct", "snapshot", asof=last.get("date"))

    vr = fetch.get(f"/api/stock/{ticker}/volatility/realized", "iv_now")
    rv_now = None
    if vr:
        last = _latest(vr)
        rv_now = f(last.get("realized_volatility"))
        F["P8.iv_now"] = fct(round(f(last.get("implied_volatility")), 4), "ratio", "daily", asof=last.get("date"))
        F["P8.rv_now"] = fct(round(rv_now, 4), "ratio", "daily", asof=last.get("date"))

    ts = fetch.get(f"/api/stock/{ticker}/volatility/term-structure", "implied_move_front")
    if ts:
        front = sorted(ts, key=lambda r: f(r.get("dte", 9999)))
        fr = front[0] if front else None
        if fr:
            spans = bool(earnings and str(fr.get("expiry", "")) >= str(earnings) >= run_asof)
            note = "event-inclusive" if spans else ("event-status-unknown" if not earnings else "no-event")
            fac = fct(round(f(fr.get("implied_move_perc")), 4), "pct", "snapshot", asof=fr.get("date"))
            fac["event"] = note
            F["P8.implied_move_front"] = fac
        F["P8.iv_term"] = fct([[r.get("expiry"), round(f(r.get("volatility")), 4)] for r in ts], "list", "snapshot")

    cmt = fetch.get(f"/api/stock/{ticker}/interpolated-iv", "iv_cmt_30d")
    if cmt:
        near30 = min(cmt, key=lambda r: abs(f(r.get("days")) - 30))
        F["P8.iv_cmt_30d"] = fct(round(f(near30.get("volatility")), 4), "ratio", "snapshot", asof=near30.get("date"))

    rr = fetch.get(f"/api/stock/{ticker}/historical-risk-reversal-skew", "rr_skew_25d")
    if rr:
        last = _latest(rr)
        rrv = f(last.get("risk_reversal"))
        fac = fct(round(rrv, 4), "ratio", "daily", asof=last.get("date"))
        fac["label"] = "put-skewed" if rrv < 0 else "call-skewed" if rrv > 0 else "flat"
        F["P8.rr_skew_25d"] = fac

    # ---- Levels ----
    mp = fetch.get(f"/api/stock/{ticker}/max-pain", "max_pain_front")
    if mp:
        rows = sorted(mp, key=lambda r: str(r.get("expiry", "")))
        if rows:
            F["P8.max_pain_front"] = fct(round(f(rows[0].get("max_pain")), 2), "price", "snapshot", asof=rows[0].get("expiry"))
        F["P8.max_pain_by_expiry"] = fct([[r.get("expiry"), round(f(r.get("max_pain")), 2)] for r in rows], "list", "snapshot")

    oc = fetch.get(f"/api/stock/{ticker}/option-contracts", "call_wall")
    if oc:
        calls = [r for r in oc if str(r.get("option_symbol", "")).find("C") > 0 or r.get("type") == "call"]
        puts = [r for r in oc if str(r.get("option_symbol", "")).find("P") > 0 or r.get("type") == "put"]
        def _wall(rows):
            if not rows:
                return None
            top = max(rows, key=lambda r: f(r.get("open_interest")))
            return top
        cw, pw = _wall(calls), _wall(puts)
        if cw is not None:
            k = _strike_of(cw)
            if k:
                F["P8.call_wall"] = fct(round(k, 2), "price", "snapshot", asof=run_asof)
                F["P8.dist_call_wall"] = fct(round((k - spot) / spot, 4), "pct", "snapshot", True)
        if pw is not None:
            k = _strike_of(pw)
            if k:
                F["P8.put_wall"] = fct(round(k, 2), "price", "snapshot", asof=run_asof)
                F["P8.dist_put_wall"] = fct(round((k - spot) / spot, 4), "pct", "snapshot", True)

    eb = fetch.get(f"/api/stock/{ticker}/expiry-breakdown", "oi_walls")
    if eb:
        F["P8.oi_walls"] = fct([[r.get("expires"), int(f(r.get("open_interest"))), int(f(r.get("volume")))] for r in eb], "list", "snapshot")

    ov = fetch.get(f"/api/stock/{ticker}/options-volume", "net_prem_day")
    if ov:
        last = _latest(ov)
        F["P8.net_prem_day"] = fct(round(f(last.get("net_call_premium")), 2), "usd", "daily", asof=last.get("date"))
        cv, pv = f(last.get("call_volume")), f(last.get("put_volume"))
        if cv:
            F["P8.pc_ratio_vol"] = fct(round(pv / cv, 4), "ratio", "daily", asof=last.get("date"))

    # ---- Live flow (session-stamped) ----
    ss = fetch.get(f"/api/stock/{ticker}/stock-state", "session_state")
    session = "none"
    if isinstance(ss, dict):
        session = _session_state(ss, run_asof)
        F["P8.session_state"] = fct(session, "label", "live", True)

    npt = fetch.get(f"/api/stock/{ticker}/net-prem-ticks", "net_prem_ticks")
    if npt:
        last = _latest(npt)
        _live(F, "P8.net_prem_ticks", round(f(last.get("net_call_premium")), 2), "usd", session)
    nope = fetch.get(f"/api/stock/{ticker}/nope", "nope")
    if nope:
        last = _latest(nope)
        _live(F, "P8.nope", round(f(last.get("nope")), 4), "ratio", session)
    sx = fetch.get(f"/api/stock/{ticker}/spot-exposures", "spot_gex")
    if sx:
        last = _latest(sx)
        _live(F, "P8.spot_gex", round(f(last.get("gamma_per_one_percent_move_oi", last.get("charm_per_one_percent_move_oi", 0))), 2), "usd", session)
    fa = fetch.get(f"/api/stock/{ticker}/flow-alerts", "flow_alerts")
    if fa:
        rows = [[a.get("type"), a.get("strike"), a.get("expiry"), a.get("volume")] for a in fa[:15]]
        fac = fct(rows, "list", "live")
        fac["session_state"] = session
        F["P8.flow_alerts"] = fac

    _data_floor(F, fetch, gex_net, atr, spot, rv_now, flip, earnings)
    return F, fetch.gaps


def _strike_of(row):
    """Strike from an option-contracts row (explicit or parsed from OCC symbol)."""
    if row.get("strike") not in (None, ""):
        return f(row.get("strike"))
    sym = str(row.get("option_symbol", ""))
    # OCC: ...YYMMDD[C/P]00000000 (strike*1000, 8 digits)
    for i, ch in enumerate(sym):
        if ch in "CP" and sym[i + 1:i + 9].isdigit():
            return int(sym[i + 1:i + 9]) / 1000.0
    return None


def _live(F, key, v, unit, session):
    fac = fct(v, unit, "live")
    fac["session_state"] = session
    F[key] = fac


def _session_state(ss, run_asof):
    tape = str(ss.get("tape_time", ""))[:10]
    if tape and tape < run_asof:
        return "none"  # stale: market closed since last tape (weekend/holiday)
    mt = ss.get("market_time")
    base = SESSION_MAP.get(mt, "none")
    if base == "mid":
        clock = str(ss.get("tape_time", ""))[11:16]
        if clock and clock < "10:30":
            return "early"
        if clock and clock > "15:30":
            return "close"
    return base


def _data_floor(F, fetch, gex_net, atr, spot, rv_now, flip, earnings):
    """Regime + band/floor. Omit regime on a degenerate dealer payload (O7)."""
    series = F.get("P8.gex_series", {}).get("v") or []
    absgex = [abs(g) for _, g in series if g is not None]
    if gex_net is None or not absgex:
        fetch.gaps.append("DATA-THIN(dealer): no greek-exposure — regime/flip withheld")
        return
    floor = _pct(absgex, 25)
    daily_sigma = atr if atr else (spot * (rv_now or 0.0) / math.sqrt(252))
    band = 1.5 * daily_sigma
    inconsistent = False
    if flip is not None:
        if (gex_net >= 0) != (spot >= flip):
            inconsistent = True
    if abs(gex_net) < floor:
        regime = "transitional"
    elif flip is not None and not inconsistent and abs(spot - flip) <= band:
        regime = "near-flip"
    elif gex_net >= 0:
        regime = "long-gamma"
    else:
        regime = "short-gamma"
    F["P8.gex_regime"] = fct(regime, "label", "snapshot", True)
    F["P8.gex_data_inconsistent"] = fct(inconsistent, "bool", "snapshot", True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="UW P8 options-analytics pack")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--spot", type=float, required=True)
    ap.add_argument("--atr", type=float)
    ap.add_argument("--earnings")
    ap.add_argument("--outdir")
    args = ap.parse_args(argv)

    fetch = Fetcher()
    try:
        F, gaps = build(args.ticker.upper(), args.spot, args.atr, args.earnings, fetch)
    except SystemExit:
        raise
    except Exception as exc:  # noqa
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    facts = {k: v for k, v in F.items() if k.startswith("P8.")}
    if gaps:
        facts["P8._gaps"] = gaps
    uw.emit(facts)
    if args.outdir:
        import json
        import os
        uw.write_atomic(os.path.join(args.outdir, "P8-options.json"),
                        json.dumps(facts, separators=(",", ":")))
    scalar_groups = [k for k in facts if k != "P8._gaps" and not facts[k].get("unit") == "list"]
    if not scalar_groups:
        if fetch.auth_failed:
            return 2
        if fetch.rate_limited:
            return 4
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
