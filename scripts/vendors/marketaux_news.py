"""P5 headlines CLI: dated news headlines with per-entity sentiment from Marketaux."""
import argparse
import os
import sys
from datetime import date, datetime, timedelta

from _common import die, emit, fact

from tradingagents.dataflows import marketaux
from tradingagents.dataflows.errors import (
    NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError,
)
from tradingagents.dataflows.symbol_utils import normalize_symbol

# scripts/replay.py lives one directory up from scripts/vendors/; add it to
# sys.path the same way tests/test_replay.py does, so this CLI can be run
# standalone (python scripts/vendors/marketaux_news.py ...) without relying
# on the caller having already put scripts/ on the path.
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import replay  # noqa: E402


def _sentiment(article, canonical):
    for ent in article.get("entities", []):
        if (ent.get("symbol") or "").upper() == canonical.upper():
            score = ent.get("sentiment_score")
            if score is not None:
                return float(score)
    return None


def build_facts(articles, canonical, asof, limit, replay_mode=False, information_cutoff=None):
    rows = [{"title": a.get("title"), "source": a.get("source"),
             "published_at": a.get("published_at"), "url": a.get("url"),
             "sentiment": _sentiment(a, canonical)} for a in articles]
    rows.sort(key=lambda r: r["published_at"] or "", reverse=True)
    gaps = []
    if replay_mode:
        rows, gaps = replay.filter_headlines_for_replay(rows, information_cutoff)
    out = {"P5.headlines": fact(rows[:limit], "articles", asof, "marketaux")}
    if gaps:
        out["P5._gaps"] = gaps
    return out


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--asof", default=date.today().isoformat())
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--replay", action="store_true", default=False)
    args = parser.parse_args(argv)
    canonical = normalize_symbol(args.ticker)
    start = (datetime.strptime(args.asof, "%Y-%m-%d") - timedelta(days=args.days)).strftime("%Y-%m-%d")
    try:
        articles = marketaux._request(marketaux.API_URL, {
            "symbols": canonical,
            "published_after": marketaux._iso(start),
            "published_before": marketaux._iso(args.asof, end_of_day=True),
            "language": "en", "filter_entities": "true", "limit": args.limit,
        }).get("data", [])
    except VendorNotConfiguredError as e:
        die(str(e), 2)
    except NoMarketDataError as e:
        die(str(e), 3)
    except VendorRateLimitError as e:
        die(str(e), 4)
    except Exception as e:
        die(str(e), 1)
    if not articles:
        die(f"no articles for {args.ticker} ({canonical}) between {start} and {args.asof}", 3)
    emit(build_facts(
        articles, canonical, args.asof, args.limit,
        replay_mode=args.replay, information_cutoff=args.asof,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
