#!/usr/bin/env python3
"""Deterministic risk officer (Feature 21 WS-A): emit the COMPLETE `40-risk.md`
— the verbatim `risk_box.py` block followed by TEMPLATED narration that is
derived ONLY by rule from the data pack (+ `15-position.json` when present).

This replaces the Stage-4b risk-officer LLM. The three narration blocks the
officer used to hand-write are all rule-derivable and are now templated:
  1. Sizing / 1R-stop implication — arithmetic on the box's own fields.
  2. Event-risk line — rule on `P5.next_earnings` vs a pinned stop horizon.
  3. Concentration — a generic sizing PRINCIPLE (position-blind, no numbers).
Anything NOT rule-derivable (valuation/margin/crowding editorializing — the
fundamental/sentiment analysts' mandate, not the risk box's) is DROPPED, never
paraphrased. Invariant 16 is preserved and hardened: `40-risk.md` is now
byte-equal to this renderer's output, so the box numbers can never drift.

PACK-ONLY BY DESIGN (invariant 12): `40-risk.md` is fed to the judge ensemble,
which must stay position-blind. This renderer therefore NEVER reads
`15-position.json` — the concentration line is a generic principle, and the
position-specific sizing lives in the writer's `## Your position` section
(which invariant 12 does license). The ERD's "concentration from
15-position.json" would have leaked position into the judge bundle; that is
deliberately not implemented here.

Stdlib only. Usage: render_risk.py <datapack.json>
Exit 0 ok; 3 on a missing required risk-box fact (fail loud, via risk_box);
2 on bad args."""
import datetime as dt
import json
import sys

import risk_box  # same dir on sys.path (subprocess) or via SCRIPTS insert (tests)

# House conventions (disclosed, NOT derived from this run's data) --------------
EVENT_HORIZON_DAYS = 14   # earnings within this many days of the fact asof = elevated event risk
CONCENTRATION_PCT = 5.0   # single-name weight above this is flagged
ABNORMAL_ATR = risk_box.ABNORMAL_ATR


def _n(x):
    return f"{x:.2f}"


def _parse_date(s):
    try:
        return dt.date.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None


def _context_band(pack):
    """Recompute the box's move/context (NORMAL|ABNORMAL|UNKNOWN) by the SAME
    rule risk_box uses, so KEY POINTS never contradicts the verbatim block."""
    atr_pct = risk_box.fval(pack, "P2.atr14_pct")
    chg = risk_box.fval(pack, "P1.chg_pct_1d")
    if chg is None or not atr_pct:
        return "UNKNOWN", None
    move_atr = abs(chg) / atr_pct
    return ("ABNORMAL" if move_atr >= ABNORMAL_ATR else "NORMAL"), move_atr


def _sizing_line(price, price_tag, sma50, atr):
    """1R implication off the long invalidation anchor (SMA50 -1x ATR14).
    Context-only geometry — never an action or a size."""
    long_stop = sma50 - atr
    if price > long_stop:
        r = price - long_stop
        pct = (r / price * 100) if price else 0.0
        mult = (r / atr) if atr else 0.0
        return (f"- Sizing (1R, long): invalidation anchor SMA50 {_n(sma50)} [P2.sma50] "
                f"-1x ATR14 [P2.atr14] = {_n(long_stop)}. A full 1R stop from {price_tag} "
                f"{_n(price)} risks {_n(r)} ({_n(pct)}%, {_n(mult)}x ATR14). Size so that "
                f"loss is tolerable -- a box implication, not a call.")
    return (f"- Sizing (1R, long): {price_tag} {_n(price)} is already at/below the long "
            f"invalidation anchor ({_n(long_stop)} = SMA50 {_n(sma50)} [P2.sma50] -1x ATR14 "
            f"[P2.atr14]); the long thesis sits at its stop.")


def _event_line(pack):
    fact = pack.get("P5.next_earnings")
    if not isinstance(fact, dict) or fact.get("v") in (None, ""):
        return "- Event risk: DATA GAP: next earnings date (no P5.next_earnings in pack)."
    v = fact.get("v")
    unit = fact.get("unit")
    asof = _parse_date(fact.get("asof"))
    edate = _parse_date(v) if unit == "date" else None
    if edate is None:
        # label-valued (e.g. ETF "N/A") — report verbatim, never guess a date.
        low = str(v).lower()
        if "n/a" in low or "etf" in low or "no " in low:
            return f"- Event risk: no scheduled corporate earnings ([P5.next_earnings]: {v})."
        return f"- Event risk: DATA GAP: next earnings date unparseable ([P5.next_earnings]: {v})."
    if asof is None:
        return (f"- Event risk: next earnings {v} [P5.next_earnings] scheduled "
                f"(no asof to horizon-check).")
    days = (edate - asof).days
    if days < 0:
        return (f"- Event risk: earnings date {v} [P5.next_earnings] precedes its asof "
                f"({asof.isoformat()}); DATA GAP: next scheduled date.")
    if days <= EVENT_HORIZON_DAYS:
        return (f"- Event risk: next earnings {v} [P5.next_earnings] in {days}d -- WITHIN the "
                f"{EVENT_HORIZON_DAYS}-day stop horizon (house convention); an earnings gap can "
                f"exceed the ATR-based stop.")
    return (f"- Event risk: next earnings {v} [P5.next_earnings] in {days}d -- beyond the "
            f"{EVENT_HORIZON_DAYS}-day stop horizon; not an immediate factor.")


def _concentration_line():
    """Position-blind (invariant 12): a generic sizing principle, no H1 facts.
    Actual position sizing is the writer's `## Your position` section."""
    return (f"- Concentration: govern size by the 1R loss above, not by conviction; "
            f">{CONCENTRATION_PCT:g}% single-name weight warrants a concentration review "
            f"(position-blind here -- see the report's position section for actual sizing).")


def build(pack):
    """Return the complete 40-risk.md text from the PACK ALONE (invariant 12 --
    never reads position). Raises KeyError(names) (via risk_box.build) when a
    required risk-box fact is missing -- fail loud."""
    block = risk_box.build(pack)  # verbatim computed box (may raise KeyError)

    price = risk_box.fval(pack, "P1.last")
    price_tag = "P1.last"
    if price is None:
        price = risk_box.fval(pack, "P1.price")
        price_tag = "P1.price"
    sma50 = risk_box.fval(pack, "P2.sma50")
    atr = risk_box.fval(pack, "P2.atr14")

    context, _ = _context_band(pack)
    long_stop = sma50 - atr
    sizing = _sizing_line(price, price_tag, sma50, atr)
    event = _event_line(pack)
    concentration = _concentration_line()

    narration = [
        "The narration below is templated deterministically from the box and pack "
        "(Feature 21 WS-A); it adds no numbers the box does not already contain.",
        "",
        sizing,
        event,
        concentration,
        "",
        "KEY POINTS:",
        f"- Adverse context: {context} (see the box's move/threshold line).",
        f"- Invalidation (long): {_n(long_stop)} = SMA50 [P2.sma50] -1x ATR14 [P2.atr14].",
        event,
    ]
    return block.rstrip("\n") + "\n\n" + "\n".join(narration) + "\n"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        sys.stderr.write("usage: render_risk.py <datapack.json>\n")
        return 2
    with open(argv[0]) as f:
        pack = json.load(f)
    try:
        out = build(pack)
    except KeyError as e:
        sys.stderr.write(f"ERROR: risk box needs missing fact(s): {e.args[0]}\n")
        return 3
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
