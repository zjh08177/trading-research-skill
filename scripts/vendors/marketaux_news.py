"""P5 headlines CLI: dated news headlines with per-entity sentiment from Marketaux."""
import argparse
import sys
from datetime import date, datetime, timedelta

from _common import die, emit, fact

from tradingagents.dataflows import marketaux
from tradingagents.dataflows.errors import (
    NoMarketDataError, VendorNotConfiguredError, VendorRateLimitError,
)
from tradingagents.dataflows.symbol_utils import normalize_symbol


def _sentiment(article, canonical):
    for ent in article.get("entities", []):
        if (ent.get("symbol") or "").upper() == canonical.upper():
            score = ent.get("sentiment_score")
            if score is not None:
                return float(score)
    return None


def build_facts(articles, canonical, asof, limit):
    rows = [{"title": a.get("title"), "source": a.get("source"),
             "published_at": a.get("published_at"), "url": a.get("url"),
             "sentiment": _sentiment(a, canonical)} for a in articles]
    rows.sort(key=lambda r: r["published_at"] or "", reverse=True)
    return {"P5.headlines": fact(rows[:limit], "articles", asof, "marketaux")}


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--asof", default=date.today().isoformat())
    parser.add_argument("--limit", type=int, default=10)
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
    emit(build_facts(articles, canonical, args.asof, args.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
