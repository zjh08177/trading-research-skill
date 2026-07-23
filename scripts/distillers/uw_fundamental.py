"""UW company-info distiller (section P3, tier 1, replay_safe False).

Restores the ONE schwab.fundamental field UW covers like-for-like after the
Schwab sunset: `beta` (from `/api/stock/{ticker}/info`). The other former
schwab.fundamental fields are deliberately NOT emitted — UW has no PEG, its
short data is daily short-VOLUME ratio (a different metric from Schwab's
short-interest-to-float, so surfacing it as the same signal would mislabel it),
and dividends require UW's Advanced tier. EDGAR still supplies core P3.

`raw_rows` is the raw `_uw_info` dict emitted by scripts/vendors/uw_info.py.
"""
from ._base import signal

_SRC = "uw(info)"


def distill(raw_rows, ctx) -> list:
    raw_rows = raw_rows or {}
    beta = raw_rows.get("beta")
    try:
        beta = float(beta)
    except (TypeError, ValueError):
        beta = None
    if beta is None:
        return [signal(
            "P3.fundamental_quiet", "quiet: no UW beta available",
            "none", ctx.asof, _SRC, notable=False,
        )]
    # Notable at the same threshold schwab.fundamental used (>1.5 = high beta).
    return [signal("P3.beta", beta, "index", ctx.asof, _SRC, notable=beta > 1.5)]
