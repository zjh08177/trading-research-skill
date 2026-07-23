"""P0 'what-changed' catcher distiller (R6, tech-solution §4.1).

Pure derive-only: `raw_rows` is ignored; every signal is derived exclusively
from `ctx.facts`. Null-tolerant by contract (B1): a missing or None input
degrades to a named gap instead of a fabricated number or a raw index/divide
crash — never use bare `facts["..."]` or an unguarded `/`.
"""
from datetime import date

from ._base import signal

CATALYST_WINDOW_DAYS = 7
CATALYST_NOTABLE_DAYS = 2
EARNINGS_NOTABLE_DAYS = 7
MOVE_NOTABLE_PCT = 3.0
ATR_NOTABLE = 1.0
REL_VOLUME_NOTABLE = 1.5


def _parse_date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def distill(raw_rows, ctx) -> list:
    facts = ctx.facts or {}
    asof = ctx.asof
    cutoff = _parse_date(asof)

    signals = []
    move_notable = False

    move_fact = facts.get("P1.chg_pct_1d") or {}
    move_pct = move_fact.get("v")
    bar_date = move_fact.get("asof") or asof

    atr_fact = facts.get("P2.atr14_pct") or {}
    atr14_pct = atr_fact.get("v")

    if move_pct is not None:
        move_notable_flag = abs(move_pct) >= MOVE_NOTABLE_PCT
        signals.append(signal(
            "P0.move_pct", move_pct, "pct", bar_date,
            "derived(uw_bars.chg_pct_1d)", notable=move_notable_flag,
        ))
        move_notable = move_notable or move_notable_flag

    if move_pct is not None and atr14_pct:
        move_vs_atr = round(move_pct / atr14_pct, 3)
        atr_notable_flag = abs(move_vs_atr) >= ATR_NOTABLE
        signals.append(signal(
            "P0.move_vs_atr", move_vs_atr, "ATRs", bar_date,
            "derived(uw_bars/atr14)", notable=atr_notable_flag,
        ))
        move_notable = move_notable or atr_notable_flag

    day_volume = (facts.get("P1.day_volume") or {}).get("v")
    avg_vol_20d = (facts.get("P1.avg_vol_20d") or {}).get("v")
    if ctx.mode == "replay":
        signals.append(signal(
            "P0.rel_volume", None, "x_avg", asof,
            "derived(uw_quote/uw_bars)",
            gap="omitted in replay (live day_volume unavailable)",
        ))
    elif day_volume is not None and avg_vol_20d:
        rel_volume = round(day_volume / avg_vol_20d, 3)
        signals.append(signal(
            "P0.rel_volume", rel_volume, "x_avg", asof,
            "derived(uw_quote/uw_bars)",
            notable=rel_volume >= REL_VOLUME_NOTABLE,
        ))
    else:
        signals.append(signal(
            "P0.rel_volume", None, "x_avg", asof,
            "derived(uw_quote/uw_bars)",
            gap="day_volume or avg_vol_20d unavailable",
        ))

    headlines = (facts.get("P5.headlines") or {}).get("v") or []
    fresh = []
    if cutoff is not None:
        for h in headlines:
            if not isinstance(h, dict):
                continue
            h_date = _parse_date(h.get("published_at"))
            if h_date is None:
                continue
            if h_date <= cutoff and (cutoff - h_date).days <= CATALYST_WINDOW_DAYS:
                fresh.append(h)
    fresh.sort(key=lambda h: h.get("published_at") or "", reverse=True)
    catalyst_present = bool(fresh)

    if fresh:
        newest = fresh[0].get("published_at") or asof
        catalyst_notable = any(
            (cutoff - _parse_date(h.get("published_at"))).days <= CATALYST_NOTABLE_DAYS
            for h in fresh
            if _parse_date(h.get("published_at")) is not None
        )
        kept = fresh[: ctx.max_rows]
        cap_gap = None
        if len(fresh) > ctx.max_rows:
            cap_gap = (
                f"kept top {ctx.max_rows} of {len(fresh)} fresh headlines; "
                "count in P0.catalyst_count"
            )
        signals.append(signal(
            "P0.catalysts", kept, "articles", newest, "marketaux",
            notable=catalyst_notable, gap=cap_gap,
        ))
        if cap_gap:
            signals.append(signal(
                "P0.catalyst_count", len(fresh), "count", newest, "marketaux",
            ))

    earnings_fact = facts.get("P5.next_earnings") or {}
    if earnings_fact.get("v") is not None:
        known_at = earnings_fact.get("known_at")
        known_ok = ctx.mode != "replay" or (
            _parse_date(known_at) is not None
            and cutoff is not None
            and _parse_date(known_at) <= cutoff
        )
        if known_ok:
            earn_date = _parse_date(earnings_fact.get("v"))
            if earn_date is not None and cutoff is not None:
                days = (earn_date - cutoff).days
                signals.append(signal(
                    "P0.days_to_earnings", days, "days", asof,
                    "derived(P5.next_earnings)",
                    notable=(0 <= days <= EARNINGS_NOTABLE_DAYS),
                ))
        else:
            signals.append(signal(
                "P0.days_to_earnings", None, "days", asof,
                "derived(P5.next_earnings)",
                gap="next earnings date known live-only in replay",
            ))

    signals.append(signal(
        "P0.gap_pct", None, "pct", asof, "derived",
        gap=(
            "deferred: needs today's open, not exposed by uw_bars "
            "(v1 uses move_pct/move_vs_atr)"
        ),
    ))
    signals.append(signal(
        "P0.catalyst_8k", None, "none", asof, "derived",
        gap="deferred: needs EDGAR submissions items endpoint (next slice)",
    ))

    if not move_notable and not catalyst_present:
        return [signal(
            "P0.quiet", "quiet: no notable tape move or fresh catalyst",
            "none", asof, "derived", notable=False,
        )]

    return signals
