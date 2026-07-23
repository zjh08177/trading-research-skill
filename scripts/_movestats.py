"""Shared history/statistics helpers for the P9 left-side-signal scripts
(stretch.py, percentile.py, volume_climax.py, move_cluster.py,
move_base_rate.py). Not a CLI itself — imported only. Stdlib only."""
import datetime


def parse_history(history_json):
    """(dates, closes, volumes) ascending by date, adjClose preferred over
    close when present. history_json is the object written by
    scripts/vendors/tiingo_history.py (11-history.json)."""
    bars = sorted(history_json.get("bars", []), key=lambda b: b["date"])
    dates = [b["date"] for b in bars]
    closes = [float(b["adjClose"] if b.get("adjClose") is not None else b["close"])
              for b in bars]
    volumes = [float(b["volume"]) for b in bars]
    return dates, closes, volumes


def daily_returns_pct(closes):
    out = [None] * len(closes)
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        out[i] = (closes[i] / prev - 1) * 100 if prev else None
    return out


def rsi14(closes):
    n = len(closes)
    out = [None] * n
    if n < 15:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]
    avg_gain = sum(gains[:14]) / 14
    avg_loss = sum(losses[:14]) / 14
    out[14] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(15, n):
        g, l = gains[i - 1], losses[i - 1]
        avg_gain = (avg_gain * 13 + g) / 14
        avg_loss = (avg_loss * 13 + l) / 14
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def percentile_rank(value, population):
    if not population:
        return None
    n = len(population)
    count_le = sum(1 for x in population if x <= value)
    return count_le / n * 100


def zscore(value, population):
    if not population:
        return None
    m = sum(population) / len(population)
    var = sum((x - m) ** 2 for x in population) / len(population)
    sd = var ** 0.5
    return 0.0 if sd == 0 else (value - m) / sd


def comparable_event_indices(returns, direction, threshold_pct):
    """Indices i (into `returns`, same length as closes) where returns[i] is
    not None, matches `direction` ('down' means <=0, 'up' means >=0), and
    abs(returns[i]) >= threshold_pct."""
    out = []
    for i, r in enumerate(returns):
        if r is None:
            continue
        if direction == "down" and r <= 0 and abs(r) >= threshold_pct:
            out.append(i)
        elif direction == "up" and r >= 0 and abs(r) >= threshold_pct:
            out.append(i)
    return out


def cluster_events(event_dates, all_dates, window_sessions=60):
    """Greedy trading-session clustering: a new cluster starts whenever the
    session gap since the previous event date exceeds window_sessions."""
    if not event_dates:
        return []
    idx = {d: i for i, d in enumerate(all_dates)}
    ordered = sorted(event_dates)
    clusters = [[ordered[0]]]
    for d in ordered[1:]:
        prev = clusters[-1][-1]
        gap = idx[d] - idx[prev]
        if gap <= window_sessions:
            clusters[-1].append(d)
        else:
            clusters.append([d])
    return clusters


def macro_cycles(clusters, gap_days=545):
    """Merge clusters whose CALENDAR gap (last date of one -> first date of
    the next) is < gap_days (~18 months). Clusters are pre-sorted (caller
    ensures event_dates were sorted before cluster_events built them)."""
    if not clusters:
        return []
    merged = [list(clusters[0])]
    for c in clusters[1:]:
        prev_last = datetime.date.fromisoformat(merged[-1][-1])
        cur_first = datetime.date.fromisoformat(c[0])
        if (cur_first - prev_last).days < gap_days:
            merged[-1].extend(c)
        else:
            merged.append(list(c))
    return merged


def forward_stats(closes, event_indices, horizons=(5, 10, 20, 40, 60)):
    out = {}
    for h in horizons:
        rets, dds = [], []
        for i in event_indices:
            j = i + h
            if j < len(closes) and closes[i]:
                rets.append((closes[j] / closes[i] - 1) * 100)
                window = closes[i:j + 1]
                dds.append((min(window) / closes[i] - 1) * 100)
        if rets:
            s = sorted(rets)
            n = len(s)
            median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
            out[h] = {
                "n": n, "mean": sum(rets) / n, "median": median,
                "winrate": sum(1 for r in rets if r > 0) / n * 100,
                "avg_dd": sum(dds) / n, "worst_dd": min(dds),
            }
        else:
            out[h] = {"n": 0, "mean": None, "median": None, "winrate": None,
                      "avg_dd": None, "worst_dd": None}
    return out
