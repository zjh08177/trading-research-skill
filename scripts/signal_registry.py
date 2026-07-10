"""Feed registry (D2, tech-solution §2): the single source of truth for
"which feeds, at what cost, in which mode" (§6). Adding/removing a feed is a
`FeedEntry(...)` literal edit here plus (for distiller feeds) a module under
`scripts/distillers/` -- never surgery across `build_datapack.py` (AC5).

Distiller callables are resolved LAZILY (via `_resolve_distillers()`, called
once when `REGISTRY` is built below) so this module imports `distillers`,
never the reverse.
"""
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class FeedEntry:
    feed_id: str                 # unique, e.g. "p0.catcher", "schwab.fundamental"
    section: str                 # pack section id: "P0" | "P1" | "P3" | "P8" | ...
    tier: int                    # 1 | 2 | 3
    vendor: str                  # "schwab" | "uw" | "edgar" | "marketaux" | "derived"
    endpoint: str                # citation-resolvable endpoint/CLI id
    cost: str                    # "free" | "on-tier" | "paid:$49"
    cadence: str                 # "per-run" | "daily-cache" | "relevance-gated"
    replay_safe: bool            # False => omitted (and named) in replay
    default_on: bool             # tier default: T1 True, T2 True(measured), T3 False
    max_rows: int                # salience cap (R3)
    max_tokens: Optional[int]    # optional token cap (R3); None = rows-only
    source: str                  # "native" | "derive" | "wrap:P8" | "cli:schwab_fundamental"
    distiller: Optional[Callable]  # (raw_rows, ctx) -> list[Signal]; None for "native"
    cite_src: str                 # default citation stamped when a signal omits src (R4)
    fetch_args: Optional[Callable] = None      # (ctx) -> list[str]; only for "cli:*"
    relevance_gate: Optional[Callable] = None  # (ctx) -> bool; None = always run


def _resolve_distillers():
    """Lazy import: pulls the three v1 distiller `distill` callables. Called
    once, from `_build_registry()`, so `distillers` is only ever imported
    FROM here -- never the other way around (no import cycle)."""
    from distillers import p0_catcher
    from distillers import schwab_fundamental as schwab_fundamental_distiller
    from distillers import uw_options_depth

    return {
        "p0.catcher": p0_catcher.distill,
        "schwab.fundamental": schwab_fundamental_distiller.distill,
        "uw.options_depth": uw_options_depth.distill,
    }


def _build_registry() -> List[FeedEntry]:
    d = _resolve_distillers()
    return [
        # -- distiller feeds (source in {"derive", "cli:*", "wrap:P8"}) --
        FeedEntry(
            feed_id="p0.catcher", section="P0", tier=1, vendor="derived",
            endpoint="derived", cost="free", cadence="per-run",
            replay_safe=True, default_on=True, max_rows=3, max_tokens=None,
            source="derive", distiller=d["p0.catcher"], cite_src="derived",
        ),
        FeedEntry(
            feed_id="schwab.fundamental", section="P3", tier=1, vendor="schwab",
            endpoint="schwab_fundamental", cost="free", cadence="per-run",
            replay_safe=False, default_on=True, max_rows=12, max_tokens=None,
            source="cli:schwab_fundamental", distiller=d["schwab.fundamental"],
            cite_src="schwab(fundamental)",
            fetch_args=lambda ctx: ["--ticker", ctx.ticker],
        ),
        FeedEntry(
            feed_id="uw.options_depth", section="P8", tier=2, vendor="uw",
            endpoint="uw_options", cost="on-tier", cadence="per-run",
            replay_safe=False, default_on=True, max_rows=8, max_tokens=None,
            source="wrap:P8", distiller=d["uw.options_depth"], cite_src="uw",
        ),
        # -- native mirror entries (distiller=None; document full pack +
        #    drive replay/section filtering for feeds not yet distilled) --
        FeedEntry(
            feed_id="schwab.bars", section="P1", tier=1, vendor="schwab",
            endpoint="schwab_bars", cost="free", cadence="per-run",
            replay_safe=True, default_on=True, max_rows=0, max_tokens=None,
            source="native", distiller=None, cite_src="schwab(bars)",
        ),
        FeedEntry(
            feed_id="schwab.quote", section="P1", tier=1, vendor="schwab",
            endpoint="schwab_quote", cost="free", cadence="per-run",
            replay_safe=False, default_on=True, max_rows=0, max_tokens=None,
            source="native", distiller=None, cite_src="schwab(quote)",
        ),
        FeedEntry(
            feed_id="edgar.fundamentals", section="P3", tier=1, vendor="edgar",
            endpoint="edgar_fundamentals", cost="free", cadence="per-run",
            replay_safe=True, default_on=True, max_rows=0, max_tokens=None,
            source="native", distiller=None, cite_src="edgar(fundamentals)",
        ),
        FeedEntry(
            feed_id="marketaux.news", section="P5", tier=1, vendor="marketaux",
            endpoint="marketaux_news", cost="free", cadence="per-run",
            replay_safe=True, default_on=True, max_rows=0, max_tokens=None,
            source="native", distiller=None, cite_src="marketaux",
        ),
        FeedEntry(
            feed_id="uw.options", section="P8", tier=2, vendor="uw",
            endpoint="uw_options", cost="on-tier", cadence="per-run",
            replay_safe=False, default_on=True, max_rows=0, max_tokens=None,
            source="native", distiller=None, cite_src="uw",
        ),
    ]


REGISTRY: List[FeedEntry] = _build_registry()


def load_registry(profile="full", options=False, mode="live", registry=None):
    """Return the active FeedEntry list for this run.

    Filters, in order:
      - mode=="replay"   -> drop replay_safe is False
      - profile=="lean"  -> keep tier <= 1
      - options is False -> drop feeds whose section == "P8"
      - default_on is False (no override plumbed yet) -> drop (T3 stays off)

    `registry` defaults to module `REGISTRY`; tests (and callers) may pass a
    stub list -- honored verbatim modulo the same filters (AC-F).
    """
    entries = list(REGISTRY if registry is None else registry)

    if mode == "replay":
        entries = [e for e in entries if e.replay_safe]

    if profile == "lean":
        entries = [e for e in entries if e.tier <= 1]

    if not options:
        entries = [e for e in entries if e.section != "P8"]

    entries = [e for e in entries if e.default_on]

    return entries
