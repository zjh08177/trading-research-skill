#!/usr/bin/env python3
"""Job C — score resolved MT predictions into a signal-quality scorecard.

Per capture-day cohort AND pooled, over predictions.resolved:
  - rank IC = Spearman(predicted_pct, realized_pct) across the cohort
  - ICIR = mean(daily IC) / std(daily IC); Newey-West-free simple t-stat = mean/ (std/sqrt(n))
  - hit-rate (directional) vs 50%
  - MAE / bias of predicted vs realized
  - long-short: mean realized of top-quintile-predicted minus bottom-quintile
  - baselines it must beat: momentum IC (trailing return vs realized), and (when captured) MT rating IC

Pure local math on Job B output — no vendor calls. Honest about tiny samples:
reports n_cohorts and flags when the sample is too small to conclude.

Usage:
    python3 mt_score.py                       # score the live resolved sidecar
    python3 mt_score.py --resolved FILE       # score a specific file (e.g. a fixture)
"""
import argparse, json, math, os, sys
from collections import defaultdict

DATA_DIR = os.path.expanduser(os.environ.get("MT_ALPHA_DIR", "~/trading-reports/marketterminal"))


def _read_jsonl(path):
    out = []
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def _rank(xs):
    """Average ranks (ties shared) for Spearman."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(a, b):
    if len(a) < 3:
        return None
    ra, rb = _rank(a), _rank(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((x - mb) ** 2 for x in rb))
    if va == 0 or vb == 0:
        return None
    return cov / (va * vb)


def long_short(pred, real, q=0.2):
    """Mean realized of top-q predicted minus bottom-q predicted."""
    if len(pred) < 5:
        return None
    idx = sorted(range(len(pred)), key=lambda i: pred[i])
    k = max(1, int(len(pred) * q))
    bot = sum(real[i] for i in idx[:k]) / k
    top = sum(real[i] for i in idx[-k:]) / k
    return top - bot


def cohort_metrics(rows):
    p = [r["predicted_pct"] for r in rows if r.get("predicted_pct") is not None and r.get("realized_pct") is not None]
    y = [r["realized_pct"] for r in rows if r.get("predicted_pct") is not None and r.get("realized_pct") is not None]
    mom = [(r["momentum_pct"], r["realized_pct"]) for r in rows
           if r.get("momentum_pct") is not None and r.get("realized_pct") is not None]
    hits = [r["hit"] for r in rows if r.get("hit") is not None]
    errs = [r["abs_err_pct"] for r in rows if r.get("abs_err_pct") is not None]
    return {
        "n": len(p),
        "ic": spearman(p, y),
        "ic_momentum": spearman([m[0] for m in mom], [m[1] for m in mom]) if len(mom) >= 3 else None,
        "hit_rate": round(sum(hits) / len(hits), 4) if hits else None,
        "mae_pct": round(sum(errs) / len(errs), 4) if errs else None,
        "bias_pct": round(sum(p[i] - y[i] for i in range(len(p))) / len(p), 4) if p else None,
        "long_short_pct": long_short(p, y),
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolved", default=os.path.join(DATA_DIR, "predictions-resolved.jsonl"))
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "scorecard.json"))
    args = ap.parse_args(argv)

    rows = _read_jsonl(args.resolved)
    if not rows:
        print(json.dumps({"error": "no resolved rows yet", "resolved_file": args.resolved,
                          "note": "cohorts resolve ~15 sessions after capture"}, indent=2))
        return 0

    by_cohort = defaultdict(list)
    for r in rows:
        by_cohort[r["capture_date"]].append(r)

    cohorts = {d: cohort_metrics(rs) for d, rs in sorted(by_cohort.items())}
    ics = [m["ic"] for m in cohorts.values() if m["ic"] is not None]
    icir = t_ic = None
    if len(ics) >= 2:
        mean_ic = sum(ics) / len(ics)
        sd = math.sqrt(sum((x - mean_ic) ** 2 for x in ics) / (len(ics) - 1))
        if sd > 0:
            icir = round(mean_ic / sd, 4)
            t_ic = round(mean_ic / (sd / math.sqrt(len(ics))), 4)

    pooled = cohort_metrics(rows)
    scorecard = {
        "n_cohorts": len(cohorts), "n_resolved_total": len(rows),
        "pooled": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in pooled.items()},
        "mean_ic_across_cohorts": round(sum(ics) / len(ics), 4) if ics else None,
        "icir": icir, "ic_tstat": t_ic,
        "per_cohort": {d: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}
                       for d, m in cohorts.items()},
        "verdict_note": ("SAMPLE TOO SMALL to conclude — need many more cohorts (research floor ~6mo). "
                         "Descriptive only." if len(cohorts) < 20 else "sample meaningful; apply pre-registered decision table"),
    }
    json.dump(scorecard, open(args.out, "w"), indent=1)
    print(json.dumps(scorecard, indent=2))
    print(f"\nwrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
