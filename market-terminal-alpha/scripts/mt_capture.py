#!/usr/bin/env python3
"""Job A — daily capture of MarketTerminal AI predictions for the index universe.

For each constituent (UW∩MT universe from resolve_universe), fetch the full
`/ai-prediction/predict` record (public, no auth) and append one row per
(capture_date, ticker) to an append-only JSONL. Idempotent: a ticker already
captured today is skipped (first fixed-time snapshot wins) unless --force.

The realized ANCHOR is NOT fetched here — Job B (resolve) computes realized
returns from the user's own UW bars (UW close[t0] → close[t+15]). Capture only
records MT's forecast + horizon_end_date so scoring needs no MT access later.

Storage: $MT_ALPHA_DIR (default ~/trading-reports/marketterminal/), OUTSIDE the
public repo tree. predictions.jsonl is append-only.

Usage:
    python3 mt_capture.py                       # capture today's cohort (all ~503)
    python3 mt_capture.py --index-etf SPY --limit 5   # smoke test on 5 names
    python3 mt_capture.py --force               # re-capture even if today already logged
"""
import argparse, datetime as dt, json, os, sys, time
import urllib.request, urllib.error, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import resolve_universe as ru  # noqa: E402

PREDICT = "https://api.marketterminal.com/v1/ai-prediction/predict"
DATA_DIR = os.path.expanduser(os.environ.get("MT_ALPHA_DIR", "~/trading-reports/marketterminal"))
PRED_FILE = os.path.join(DATA_DIR, "predictions.jsonl")


def _get_json(url, params, retries=2, sleep_ms=250):
    q = urllib.parse.urlencode(params)
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(f"{url}?{q}", timeout=30) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            if attempt == retries:
                raise
            time.sleep(sleep_ms / 1000 * (attempt + 2))  # backoff
    return None


def _current_price(j):
    """MT's currentPrice lives in indicatorsMeta[].chartMeta.currentPrice; top-level fallback."""
    if isinstance(j.get("currentPrice"), (int, float)):
        return j["currentPrice"]
    for m in j.get("indicatorsMeta") or []:
        cp = (m.get("chartMeta") or {}).get("currentPrice")
        if isinstance(cp, (int, float)):
            return cp
    return None


def fetch_prediction(mt_ticker, timeframe="daily"):
    j = _get_json(PREDICT, {"ticker": mt_ticker, "timeframe": timeframe, "cache": "true"})
    candles = j.get("predicted") or []
    if not candles or j.get("percentChange") is None:
        return None  # degenerate/empty — skip, count as failure
    path = [{"date": c.get("date"), "close": c.get("close")} for c in candles]
    return {
        "predicted_pct": j.get("percentChange"),
        "confidence": j.get("confidence"),
        "mt_current_price": _current_price(j),
        "generated_at": j.get("generatedAt"),
        "cached": j.get("cached"),
        "drift_pct": j.get("driftPct"),
        "horizon_end_date": path[-1]["date"],
        "n_candles": len(path),
        "predicted_path": path,
    }


def _already_captured_today(capture_date):
    """Return set of tickers already logged for capture_date (idempotency)."""
    seen = set()
    if os.path.exists(PRED_FILE):
        with open(PRED_FILE) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("capture_date") == capture_date:
                        seen.add(r.get("mt_ticker"))
                except json.JSONDecodeError:
                    continue
    return seen


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--index-etf", default="SPY")
    ap.add_argument("--mt-min-mcap", type=float, default=2e9)
    ap.add_argument("--timeframe", default="daily")
    ap.add_argument("--limit", type=int, default=None, help="cap tickers (smoke test)")
    ap.add_argument("--sleep-ms", type=int, default=250, help="delay between predict calls")
    ap.add_argument("--force", action="store_true", help="re-capture even if today already logged")
    ap.add_argument("--capture-date", default=None, help="override capture date (YYYY-MM-DD)")
    args = ap.parse_args(argv)

    os.makedirs(DATA_DIR, exist_ok=True)
    capture_date = args.capture_date or dt.date.today().isoformat()
    captured_at = dt.datetime.now(dt.timezone.utc).isoformat()

    universe = ru.build(args.index_etf, args.mt_min_mcap, os.environ.get("MT_ACCESS_TOKEN"))
    universe = [u for u in universe if u.get("mt_ticker")]  # need an MT symbol to predict
    if args.limit:
        universe = universe[:args.limit]

    seen = set() if args.force else _already_captured_today(capture_date)
    todo = [u for u in universe if u["mt_ticker"] not in seen]
    print(f"universe={len(universe)} | already-today={len(seen)} | to-capture={len(todo)}", file=sys.stderr)

    captured = failed = 0
    with open(PRED_FILE, "a") as out:
        for i, u in enumerate(todo, 1):
            try:
                pred = fetch_prediction(u["mt_ticker"], args.timeframe)
            except Exception as e:  # noqa: BLE001 - network best-effort, log & continue
                pred = None
                print(f"  ! {u['mt_ticker']}: {type(e).__name__} {str(e)[:80]}", file=sys.stderr)
            if pred is None:
                failed += 1
            else:
                row = {
                    "capture_date": capture_date, "captured_at": captured_at,
                    "timeframe": args.timeframe,
                    "mt_ticker": u["mt_ticker"], "uw_ticker": u["uw_ticker"],
                    "sector": u.get("sector"), "sp500_weight": u.get("sp500_weight"),
                    **pred,
                }
                out.write(json.dumps(row) + "\n")
                out.flush()
                captured += 1
            if args.sleep_ms and i < len(todo):
                time.sleep(args.sleep_ms / 1000)

    with open(os.path.join(DATA_DIR, "capture.heartbeat"), "w") as hb:
        hb.write(captured_at)

    print(json.dumps({
        "capture_date": capture_date, "index": args.index_etf,
        "captured": captured, "failed": failed, "skipped_today": len(seen),
        "file": PRED_FILE,
    }, indent=2))
    return 0 if captured or not todo else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
