"""Feed registry (D2, tech-solution §2): the single source of truth for
"which feeds, at what cost, in which mode" (§6). Adding/removing a feed is a
`FeedEntry(...)` literal edit here plus (for distiller feeds) a module under
`scripts/distillers/` -- never surgery across `build_datapack.py` (AC5).

Distiller callables are resolved LAZILY (via `_resolve_distillers()`, called
once when `REGISTRY` is built below) so this module imports `distillers`,
never the reverse.
"""
import os
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

SCHEMA_VERSION = 1

# vendors.env path mirror of vendors/_common.py CREDS_PATH (this module must
# stay import-light: no dotenv dependency, and vendor CLIs run as subprocesses
# so the parent process never loads vendors.env itself).
_CREDS_PATH = (
    os.environ.get("TRADING_RESEARCH_VENDORS_ENV")
    or os.environ.get("TRADINGAGENTS_VENDORS_ENV")
    or os.path.join(os.path.expanduser("~"), ".config", "tradingagents", "vendors.env")
)


def _youtube_key_present(creds_path=None):
    """ERD R12: YouTube ships OFF-by-default for distributed installs; a BYO
    `YOUTUBE_API_KEY` (env or vendors.env) is what turns the feed on."""
    if os.environ.get("YOUTUBE_API_KEY"):
        return True
    try:
        with open(creds_path or _CREDS_PATH) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("YOUTUBE_API_KEY=") and line.split("=", 1)[1].strip():
                    return True
    except OSError:
        pass
    return False


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
    from distillers import reddit_crowding
    from distillers import reddit_tone
    from distillers import schwab_fundamental as schwab_fundamental_distiller
    from distillers import social_risk
    from distillers import uw_options_depth
    from distillers import youtube_attention

    return {
        "p0.catcher": p0_catcher.distill,
        "schwab.fundamental": schwab_fundamental_distiller.distill,
        "uw.options_depth": uw_options_depth.distill,
        "reddit.tradestie": reddit_tone.distill,
        "reddit.apewisdom": reddit_crowding.distill,
        "youtube.attention": youtube_attention.distill,
        "social.risk": social_risk.distill,
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
        # -- P6 social sentiment (ERD social-sentiment v2.7, R1-R6/R9/R12) --
        # Ordering contract: social.risk derives from the P6 atomics above it,
        # so these four entries stay contiguous and social.risk stays LAST.
        # v1 cadence is per-run (single-ticker); the R12 once-per-session
        # leaderboard cache is a batch-path follow-up.
        FeedEntry(
            feed_id="reddit.tradestie", section="P6", tier=2, vendor="tradestie",
            endpoint="tradestie", cost="free", cadence="per-run",
            replay_safe=True,  # ONLY with the strictly-prior-day guard in the CLI/distiller (R9)
            default_on=True, max_rows=1, max_tokens=None,
            source="cli:tradestie", distiller=d["reddit.tradestie"], cite_src="tradestie",
            fetch_args=lambda ctx: (["--ticker", ctx.ticker, "--asof", ctx.asof]
                                    + (["--replay"] if ctx.mode == "replay" else [])),
        ),
        FeedEntry(
            feed_id="reddit.apewisdom", section="P6", tier=2, vendor="apewisdom",
            endpoint="apewisdom", cost="free", cadence="per-run",
            replay_safe=False,  # rolling 24h snapshots only -> no PIT (R9)
            default_on=True, max_rows=1, max_tokens=None,
            source="cli:apewisdom", distiller=d["reddit.apewisdom"], cite_src="apewisdom",
            fetch_args=lambda ctx: ["--ticker", ctx.ticker, "--asof", ctx.asof],
        ),
        FeedEntry(
            feed_id="youtube.attention", section="P6", tier=2, vendor="youtube",
            endpoint="youtube_data", cost="free", cadence="per-run",
            replay_safe=False,  # hard: comment/publish-time look-ahead (R9)
            default_on=_youtube_key_present(),  # BYO key = on (R12)
            max_rows=1, max_tokens=None,
            source="cli:youtube_data", distiller=d["youtube.attention"],
            cite_src="youtube(search.list)",
            fetch_args=lambda ctx: ["--ticker", ctx.ticker, "--asof", ctx.asof],
        ),
        FeedEntry(
            feed_id="social.risk", section="P6", tier=2, vendor="derived",
            endpoint="derived", cost="free", cadence="per-run",
            replay_safe=False,  # composite of non-PIT atomics (R9)
            default_on=True, max_rows=1, max_tokens=None,
            source="derive", distiller=d["social.risk"], cite_src="derived(P6)",
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
