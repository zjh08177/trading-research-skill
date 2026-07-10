"""Schwab T1 fundamentals distiller (R7, tech-solution §4.2), section P3,
tier 1, replay_safe False.

`raw_rows` is the raw `_schwab_fundamental` dict emitted by
scripts/vendors/schwab_fundamental.py -- a documented-subset passthrough of
the Schwab quotes `fundamental` block (NOT a pack fact, NOT the P8-style
fct()-shaped dict other distillers see). This distiller maps a DOCUMENTED
subset of that raw dict to cited P3 signals, omitting (never fabricating)
any absent key.
"""
from ._base import signal

_SRC = "schwab(fundamental)"

# Each entry: (schwab fundamental key, signal id, unit, notable predicate)
_FIELD_MAP = [
    ("beta", "P3.beta", "index", lambda v: v > 1.5),
    ("shortIntToFloat", "P3.short_int_to_float", "pct", lambda v: v >= 20),
    ("shortIntDayToCover", "P3.short_int_days_to_cover", "days", lambda v: v >= 5),
    ("peRatio", "P3.pe_vendor", "ratio", None),
    ("pegRatio", "P3.peg_ratio", "ratio", None),
    ("pbRatio", "P3.pb_ratio", "ratio", None),
    ("divYield", "P3.div_yield", "pct", None),
    ("divAmount", "P3.div_amount", "USD", None),
    ("bookValuePerShare", "P3.book_value_ps", "USD", None),
    ("marketCap", "P3.market_cap_vendor", "USD", None),
]


def distill(raw_rows, ctx) -> list:
    raw_rows = raw_rows or {}

    if not raw_rows:
        return [signal(
            "P3.fundamental_quiet", "quiet: no fundamental data available",
            "none", ctx.asof, _SRC, notable=False,
        )]

    signals = []
    for key, fid, unit, notable_fn in _FIELD_MAP:
        v = raw_rows.get(key)
        if v is None:
            continue
        # notable predicates are numeric comparisons; a non-numeric vendor value
        # must not raise (would otherwise abort the batch) — treat as not-notable.
        notable = (notable_fn(v) if (notable_fn is not None and isinstance(v, (int, float)))
                   else None)
        signals.append(signal(fid, v, unit, ctx.asof, _SRC, notable=notable))

    if not signals:
        return [signal(
            "P3.fundamental_quiet", "quiet: no fundamental data available",
            "none", ctx.asof, _SRC, notable=False,
        )]

    max_rows = ctx.max_rows
    if max_rows is not None and len(signals) > max_rows:
        # R3: never truncate silently — keep top-K and name the omission as a gap.
        total = len(signals)
        signals = signals[:max_rows]
        signals.append(signal(
            "P3.fundamental_omitted", None, "none", ctx.asof, _SRC,
            gap=(f"schwab.fundamental: kept {max_rows} of {total} fields "
                 f"({total - max_rows} omitted for salience cap)"),
        ))

    return signals
