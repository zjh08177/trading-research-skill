#!/usr/bin/env python3
"""Decision-level schema and execution-safety policy.

Schema v2 preserves trigger qualifiers. Legacy schema-1 levels are upgraded into
safe review-first triggers so a price cross alone cannot become an execution
instruction on a Hold-rated name.
"""

SCHEMA = 2
NEAR_PCT = 0.5


def rating_label(rating):
    if isinstance(rating, str):
        return rating
    if isinstance(rating, dict):
        for key in ("rating", "mode", "mode_rating"):
            if rating.get(key):
                return rating.get(key)
        nested = rating.get("rating")
        if isinstance(nested, dict):
            return nested.get("mode") or nested.get("mode_rating")
    return None


def action_direction(action):
    a = (action or "").strip().lower()
    if not a:
        return None
    if a.startswith("stop") or "re-rate" in a or "rerate" in a:
        return 0
    if a.startswith(("add", "buy")):
        return 1
    if a.startswith(("trim", "sell", "exit")):
        return -1
    return None


def _dir_symbol(side):
    return "▲" if side == "upside" else "▼"


def _legacy_comparison(side, basis):
    b = (basis or "").lower()
    if "close >" in b or "close above" in b:
        return "close_above"
    if "close <" in b or "close below" in b:
        return "close_below"
    return "intraday_above" if side == "upside" else "intraday_below"


def _default_strength(action, side, rating, legacy):
    r = (rating or "").lower()
    d = action_direction(action)
    a = (action or "").lower()
    if "re-rate" in a or "rerate" in a or a.startswith("stop"):
        return "review"
    if r == "hold" and d in (1, -1):
        return "review"
    if legacy and r == "hold":
        return "review"
    if r in ("sell", "strongsell") and side == "downside" and d == -1:
        return "act"
    if r in ("buy", "strongbuy") and side == "upside" and d == 1:
        return "act"
    return "review"


def _trigger_from_legacy(side, src, rating):
    if not src or src.get("level") is None:
        return None
    action = src.get("action", "")
    basis = src.get("basis", "")
    return {
        "id": side,
        "side": side,
        "level": float(src["level"]),
        "intended_action": action,
        "action": action,
        "basis": basis,
        "comparison": _legacy_comparison(side, basis),
        "atr_dist": src.get("atr_dist"),
        "action_strength": _default_strength(action, side, rating, legacy=True),
        "rating_gate": "hold_requires_review" if (rating or "").lower() == "hold" else "none",
        "conditions": [],
        "legacy": True,
    }


def _side_from_trigger(trigger):
    return {
        "level": trigger.get("level"),
        "action": trigger.get("intended_action") or trigger.get("action", ""),
        "basis": trigger.get("basis", ""),
        "atr_dist": trigger.get("atr_dist"),
        "action_strength": trigger.get("action_strength", "review"),
        "comparison": trigger.get("comparison", ""),
    }


def normalize_level_set(entry, rating=None):
    """Return a schema-v2-ish dict with triggers and downside/upside rail fields."""
    rating = rating_label(rating) or rating_label(entry.get("rating")) if isinstance(entry, dict) else None
    entry = dict(entry or {})
    if entry.get("schema") == SCHEMA and isinstance(entry.get("triggers"), list):
        triggers = []
        for raw in entry.get("triggers", []):
            t = dict(raw)
            t.setdefault("action", t.get("intended_action", ""))
            t.setdefault("intended_action", t.get("action", ""))
            t.setdefault("action_strength", _default_strength(t.get("intended_action"), t.get("side"), rating, False))
            t.setdefault("conditions", [])
            triggers.append(t)
        out = {**entry, "schema": SCHEMA, "triggers": triggers, "legacy": False}
    else:
        triggers = []
        for side in ("downside", "upside"):
            t = _trigger_from_legacy(side, entry.get(side), rating)
            if t:
                triggers.append(t)
        out = {**entry, "schema": SCHEMA, "triggers": triggers, "legacy": True}
    for side in ("downside", "upside"):
        found = next((t for t in out["triggers"] if t.get("side") == side), None)
        if found:
            out[side] = _side_from_trigger(found)
    return out


def _price_crossed(trigger, price, close=None):
    side = trigger.get("side")
    level = trigger.get("level")
    comparison = trigger.get("comparison") or ("intraday_above" if side == "upside" else "intraday_below")
    if level is None:
        return False, False
    if comparison.startswith("close_"):
        if close is None:
            observed = price
            missing_close = True
        else:
            observed = close
            missing_close = False
    else:
        observed = price
        missing_close = False
    if observed is None:
        return False, missing_close
    crossed = observed >= level if side == "upside" else observed <= level
    return crossed, missing_close


def _near(trigger, price, near_pct=NEAR_PCT):
    level = trigger.get("level")
    if price is None or level in (None, 0):
        return False
    if trigger.get("side") == "upside" and price < level:
        return 0 < (level - price) / price * 100 <= near_pct
    if trigger.get("side") == "downside" and price > level:
        return 0 < (price - level) / price * 100 <= near_pct
    return False


def _conditions_missing(trigger, market):
    conditions = trigger.get("conditions") or []
    if not conditions:
        return []
    results = (market or {}).get("condition_results") or {}
    missing = []
    for cond in conditions:
        key = cond.get("metric") or cond.get("kind")
        if not key or results.get(key) is not True:
            missing.append(key or "condition")
    return missing


def evaluate_trigger(trigger, price=None, rating=None, market=None, near_pct=NEAR_PCT):
    market = market or {}
    if price is None:
        return _event(trigger, "data_gap", price, ["price"], fired=False)
    close = market.get("close")
    crossed, missing_close = _price_crossed(trigger, price, close)
    if not crossed:
        if _near(trigger, price, near_pct):
            return _event(trigger, "near", price, [], fired=False)
        return _event(trigger, "inactive", price, [], fired=False)

    missing = []
    if missing_close:
        missing.append("close")
    missing += _conditions_missing(trigger, market)

    rating = rating_label(rating)
    strength = trigger.get("action_strength") or _default_strength(
        trigger.get("intended_action"), trigger.get("side"), rating, bool(trigger.get("legacy")))
    if (rating or "").lower() == "hold" and action_direction(trigger.get("intended_action")) in (1, -1):
        strength = "review"
    if trigger.get("legacy") and (rating or "").lower() == "hold":
        missing.append("schema-v2 confirmation")

    if missing:
        return _event(trigger, "crossed_unconfirmed", price, sorted(set(missing)), fired=True)
    if strength == "act":
        return _event(trigger, "confirmed_act", price, [], fired=True)
    return _event(trigger, "confirmed_review", price, [], fired=True)


def evaluate_level_set(entry, price=None, rating=None, market=None, near_pct=NEAR_PCT):
    level_set = normalize_level_set(entry, rating)
    events = []
    for trigger in level_set.get("triggers", []):
        event = evaluate_trigger(trigger, price=price, rating=rating, market=market, near_pct=near_pct)
        if event["state"] != "inactive":
            events.append(event)
    return events


def _event(trigger, state, price, missing, fired):
    action = trigger.get("intended_action") or trigger.get("action", "")
    event = {
        "ticker": trigger.get("ticker"),
        "dir": _dir_symbol(trigger.get("side")),
        "side": trigger.get("side"),
        "state": state,
        "fired": fired,
        "price": price,
        "level": trigger.get("level"),
        "action": action,
        "basis": trigger.get("basis", ""),
        "missing_data": missing,
    }
    event["plan"] = plan_text(event)
    return event


def plan_text(event):
    action = event.get("action", "")
    level = event.get("level")
    loc = f"{event.get('dir', '')} {level:g}" if isinstance(level, (int, float)) else event.get("dir", "")
    state = event.get("state")
    if state == "near":
        return f"WAIT — near {loc}; confirmation required"
    if state == "crossed_unconfirmed":
        miss = ", ".join(event.get("missing_data") or ["confirmation"])
        return f"REVIEW — {action} trigger crossed but unconfirmed ({loc}; needs {miss})"
    if state == "confirmed_review":
        return f"REVIEW — {action} confirmed; re-thesis before changing size ({loc})"
    if state == "confirmed_act":
        return f"ACT — {action} ({loc} crossed)"
    if state == "data_gap":
        return "WAIT_DATA — price unavailable; re-check"
    return ""
