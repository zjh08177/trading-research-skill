#!/usr/bin/env python3
"""Job B — resolve captured MT predictions against the user's OWN UW prices.

For each prediction whose 15-session horizon has settled, compute the realized
forward return from UW daily bars (first-choice vendor) and the t+1/t+5 partial
reads. No MarketTerminal access needed — resolution is pure UW.

    realized_pct = (UW close[horizon_end] - UW close[t0]) / UW close[t0]

t0 anchor = the settled UW close on/just-before capture_date (matches MT's
`percentChange`, which is vs its currentPrice ≈ the capture-day close). Predictions
still open (horizon not yet settled) are deferred and retried on a later run.

Idempotent: a (capture_date, ticker) already in the resolved sidecar is skipped.

Storage: $MT_ALPHA_DIR/predictions.jsonl (in) -> predictions-resolved.jsonl (out).

Usage:
    python3 mt_resolve.py                     # resolve everything matured
    python3 mt_resolve.py --predictions FILE  # resolve a specific input (e.g. a fixture)
"""
import argparse, datetime as dt, json, os, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts", "vendors"))
import uw_bars  # noqa: E402

DATA_DIR = os.path.expanduser(os.environ.get("MT_ALPHA_DIR", "~/trading-reports/marketterminal"))


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def _frame_closes(uw_ticker, asof):
    """date_str -> close, from UW regular-session daily bars (<= asof)."""
    df = uw_bars.fetch_frame(uw_ticker, asof)
    return {str(d): float(c) for d, c in zip(df["Date"], df["Close"])}


def _on_or_before(closes, target):
    """Close on `target` if present, else the latest bar strictly before it (holiday-safe)."""
    if target in closes:
        return target, closes[target]
    prior = [d for d in closes if d <= target]
    if not prior:
        return None, None
    d = max(prior)
    return d, closes[d]


def resolve_row(row, closes, asof):
    latest = max(closes) if closes else None
    horizon = row["horizon_end_date"]
    if latest is None or latest < horizon:
        return None, "not_settled"
    a_date, anchor = _on_or_before(closes, row["capture_date"])
    if anchor is None or anchor == 0:
        return None, "no_anchor"
    h_date, h_close = _on_or_before(closes, horizon)
    if h_close is None:
        return None, "no_horizon_bar"
    realized_pct = (h_close - anchor) / anchor * 100.0
    pred = row.get("predicted_pct")

    # trailing 15-session return up to the anchor (momentum baseline, computed here where we have the frame)
    momentum_pct = None
    sdates = sorted(closes)
    if a_date in sdates:
        ai = sdates.index(a_date)
        if ai >= 15:
            base = closes[sdates[ai - 15]]
            if base:
                momentum_pct = round((anchor - base) / base * 100.0, 4)

    # partial reads from the predicted path (t+1 = path[0], t+5 = path[4])
    partials = {}
    for label, idx in (("t1", 0), ("t5", 4)):
        path = row.get("predicted_path") or []
        if len(path) > idx:
            pd_, pc_ = _on_or_before(closes, path[idx]["date"])
            if pc_ is not None and path[idx].get("close") is not None:
                partials[f"pred_{label}"] = (path[idx]["close"] - anchor) / anchor * 100.0
                partials[f"real_{label}"] = (pc_ - anchor) / anchor * 100.0

    return {
        "capture_date": row["capture_date"], "mt_ticker": row["mt_ticker"],
        "uw_ticker": row["uw_ticker"], "sector": row.get("sector"),
        "confidence": row.get("confidence"),
        "predicted_pct": pred, "realized_pct": round(realized_pct, 4),
        "anchor_date": a_date, "anchor_close": anchor,
        "horizon_date": h_date, "horizon_close": h_close,
        "hit": None if pred is None else (pred > 0) == (realized_pct > 0),
        "abs_err_pct": None if pred is None else round(abs(pred - realized_pct), 4),
        "momentum_pct": momentum_pct,
        **{k: round(v, 4) for k, v in partials.items()},
        "resolved_at": dt.datetime.now(dt.timezone.utc).isoformat(), "src": "uw",
    }, "resolved"


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", default=os.path.join(DATA_DIR, "predictions.jsonl"))
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "predictions-resolved.jsonl"))
    ap.add_argument("--asof", default=dt.date.today().isoformat())
    args = ap.parse_args(argv)

    preds = _read_jsonl(args.predictions)
    done = {(r["capture_date"], r["mt_ticker"]) for r in _read_jsonl(args.out)}
    todo = [r for r in preds if (r["capture_date"], r["mt_ticker"]) not in done]

    counts = defaultdict(int)
    # Cheap pre-filter: a horizon after asof cannot be settled — defer WITHOUT a UW fetch.
    maybe = [r for r in todo if r["horizon_end_date"] <= args.asof]
    counts["not_settled"] += len(todo) - len(maybe)

    by_ticker = defaultdict(list)
    for r in maybe:
        by_ticker[r["uw_ticker"]].append(r)
    with open(args.out, "a") as out:
        for uw_ticker, rows in by_ticker.items():
            try:
                closes = _frame_closes(uw_ticker, args.asof)
            except Exception as e:  # noqa: BLE001 - vendor best-effort; retry next run
                counts["fetch_fail"] += len(rows)
                print(f"  ! {uw_ticker}: {type(e).__name__} {str(e)[:80]}", file=sys.stderr)
                continue
            for r in rows:
                resolved, status = resolve_row(r, closes, args.asof)
                counts[status] += 1
                if resolved:
                    out.write(json.dumps(resolved) + "\n")

    print(json.dumps({
        "input": args.predictions, "candidates": len(todo),
        "resolved": counts["resolved"], "not_settled": counts["not_settled"],
        "skipped": counts["no_anchor"] + counts["no_horizon_bar"] + counts["fetch_fail"],
        "out": args.out,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
