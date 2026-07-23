#!/usr/bin/env python3
"""Weekly-review surface for the MarketTerminal-alpha experiment.

Reads the append-only stores + latest scorecard and prints a human status:
capture progress, next resolution date, resolved count, latest signal metrics,
heartbeat freshness (is the launchd loop alive?), and any triggered KILL-SHOT.

It DECIDES NOTHING — it surfaces state so a human applies the pre-registered
decision table at a checkpoint. This is the pull surface behind /trading-mt-alpha.

Usage: python3 mt_report.py [--json]
"""
import argparse, datetime as dt, json, os, statistics as st, sys

DATA_DIR = os.path.expanduser(os.environ.get("MT_ALPHA_DIR", "~/trading-reports/marketterminal"))


def _read_jsonl(name):
    p = os.path.join(DATA_DIR, name)
    out = []
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def _heartbeat(name):
    p = os.path.join(DATA_DIR, name)
    if not os.path.exists(p):
        return None, None
    ts = open(p).read().strip()
    age_days = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(ts)).days
    return ts, age_days


def _killshots(preds, latest_cohort, scorecard):
    """Pre-registered stop conditions. Early ones fire from capture data alone."""
    flags = []
    cohort = [p for p in preds if p["capture_date"] == latest_cohort]
    pcts = [p["predicted_pct"] for p in cohort if p.get("predicted_pct") is not None]
    if len(pcts) >= 10:
        disp = st.pstdev(pcts)
        if disp < 1.0:
            flags.append(f"DEGENERATE DISPERSION — latest-cohort predicted std {disp:.2f}% < 1.0 "
                         "(signal can't rank; nothing to trade)")
    # resolution-based kill-shots (only once cohorts mature)
    pooled = (scorecard or {}).get("pooled") or {}
    ic, ic_mom, ls = pooled.get("ic"), pooled.get("ic_momentum"), pooled.get("long_short_pct")
    if ic is not None and ic_mom is not None and abs(ic - ic_mom) < 0.01:
        flags.append(f"INDISTINGUISHABLE FROM MOMENTUM — IC {ic:+.3f} ≈ momentum IC {ic_mom:+.3f} "
                     "(you can compute this free from UW)")
    if ls is not None and ls < 0 and (scorecard or {}).get("n_cohorts", 0) >= 5:
        flags.append(f"NEGATIVE LONG-SHORT — pooled top-minus-bottom {ls:+.2f}% over "
                     f"{scorecard['n_cohorts']} cohorts")
    return flags


def build():
    preds = _read_jsonl("predictions.jsonl")
    resolved = _read_jsonl("predictions-resolved.jsonl")
    sc_path = os.path.join(DATA_DIR, "scorecard.json")
    scorecard = json.load(open(sc_path)) if os.path.exists(sc_path) else None

    cohorts = sorted({p["capture_date"] for p in preds})
    latest = cohorts[-1] if cohorts else None
    open_horizons = sorted({p["horizon_end_date"] for p in preds
                            if (p["capture_date"], p["mt_ticker"]) not in
                            {(r["capture_date"], r["mt_ticker"]) for r in resolved}})
    cap_ts, cap_age = _heartbeat("capture.heartbeat")
    sco_ts, sco_age = _heartbeat("score.heartbeat")

    return {
        "cohorts_captured": len(cohorts),
        "capture_dates": (cohorts[:1] + ["…"] + cohorts[-1:]) if len(cohorts) > 2 else cohorts,
        "latest_cohort": latest, "predictions_total": len(preds),
        "resolved_total": len(resolved),
        "next_resolution_date": open_horizons[0] if open_horizons else None,
        "capture_heartbeat": cap_ts, "capture_stale_days": cap_age,
        "score_heartbeat": sco_ts, "score_stale_days": sco_age,
        "scorecard": (scorecard or {}).get("pooled"),
        "n_scored_cohorts": (scorecard or {}).get("n_cohorts", 0),
        "killshots": _killshots(preds, latest, scorecard) if latest else [],
        "decision_state": ("DESCRIPTIVE-ONLY — sample too small for a verdict "
                           f"({(scorecard or {}).get('n_cohorts', 0)} scored cohorts; ~20 needed)"),
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    r = build()
    if args.json:
        print(json.dumps(r, indent=2))
        return 0

    print("═══ MarketTerminal-alpha — experiment status ═══")
    print(f"Cohorts captured : {r['cohorts_captured']}  {r['capture_dates']}")
    print(f"Predictions      : {r['predictions_total']} rows | resolved: {r['resolved_total']}")
    print(f"Next resolution  : {r['next_resolution_date']}  (15 sessions after capture)")
    cap = f"{r['capture_heartbeat']} ({r['capture_stale_days']}d ago)" if r['capture_heartbeat'] else "NEVER — job may be dead"
    sco = f"{r['score_heartbeat']} ({r['score_stale_days']}d ago)" if r['score_heartbeat'] else "NEVER — job may be dead"
    print(f"Capture last-run : {cap}")
    print(f"Score last-run   : {sco}")
    if r["scorecard"]:
        s = r["scorecard"]
        print(f"Signal (pooled)  : IC {s.get('ic')} | momentum-IC {s.get('ic_momentum')} | "
              f"hit {s.get('hit_rate')} | LS {s.get('long_short_pct')}%  [{r['n_scored_cohorts']} cohorts]")
    else:
        print("Signal           : no resolved cohorts yet (first matures on the date above)")
    if r["killshots"]:
        print("\n⛔ KILL-SHOT(S) TRIGGERED:")
        for k in r["killshots"]:
            print(f"   • {k}")
    else:
        print("\n✅ No kill-shot conditions triggered.")
    print(f"\nDecision state   : {r['decision_state']}")
    print("→ Reminder: DECIDE only at pre-registered checkpoints. Never buy the yearly on trial data.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
