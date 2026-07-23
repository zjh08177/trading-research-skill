"""Tests for levels_schema.validate_level_set() (Invariant 19 gate)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest
import levels_schema as mod


def _trigger(side, action, strength, **extra):
    t = {
        "side": side, "intended_action": action, "action": action,
        "level": 100.0, "basis": "test", "comparison": "close_below",
        "action_strength": strength, "rating_gate": "none", "conditions": [],
    }
    t.update(extra)
    return t


def _level_set(triggers):
    return {"schema": 2, "spot": 120.0, "triggers": triggers, "legacy": False}


def test_counter_trend_buy_act_rejected_for_hold():
    ls = _level_set([_trigger("downside", "Buy", "act")])
    with pytest.raises(mod.LevelValidationError, match="counter-trend"):
        mod.validate_level_set(ls, pack={}, rating="Hold")


def test_counter_trend_buy_act_rejected_for_sell():
    # This is the gap the structural skeptic found: the runtime downgrade in
    # evaluate_trigger() only covers rating=="hold". validate_level_set must
    # reject this for EVERY rating.
    ls = _level_set([_trigger("downside", "Buy", "act")])
    with pytest.raises(mod.LevelValidationError, match="counter-trend"):
        mod.validate_level_set(ls, pack={}, rating="Sell")


def test_counter_trend_sell_act_rejected():
    ls = _level_set([_trigger("upside", "Trim", "act")])
    with pytest.raises(mod.LevelValidationError, match="counter-trend"):
        mod.validate_level_set(ls, pack={}, rating="Buy")


def test_counter_trend_review_is_fine():
    ls = _level_set([_trigger("downside", "Buy", "review")])
    mod.validate_level_set(ls, pack={}, rating="Hold")  # must not raise


def test_trend_aligned_act_is_fine():
    ls = _level_set([_trigger("upside", "Buy", "act")])
    mod.validate_level_set(ls, pack={}, rating="Buy")  # must not raise
    ls2 = _level_set([_trigger("downside", "Sell", "act")])
    mod.validate_level_set(ls2, pack={}, rating="Sell")  # must not raise


def test_leveraged_counter_trend_missing_decay_risk_rejected():
    pack = {"P0.leverage_objective": {"v": "3x daily", "unit": "text",
                                       "asof": "2026-07-17", "src": "x"}}
    ls = _level_set([_trigger("downside", "Buy", "review")])
    with pytest.raises(mod.LevelValidationError, match="decay_risk"):
        mod.validate_level_set(ls, pack=pack, rating="Hold")


def test_leveraged_counter_trend_with_decay_risk_is_fine():
    pack = {"P0.leverage_objective": {"v": "3x daily", "unit": "text",
                                       "asof": "2026-07-17", "src": "x"}}
    ls = _level_set([_trigger("downside", "Buy", "review", decay_risk=0.64)])
    mod.validate_level_set(ls, pack=pack, rating="Hold")  # must not raise


def test_non_leveraged_counter_trend_does_not_require_decay_risk():
    ls = _level_set([_trigger("downside", "Buy", "review")])
    mod.validate_level_set(ls, pack={}, rating="Hold")  # must not raise


def test_base_rate_cite_missing_n_companions_rejected():
    ls = _level_set([_trigger("downside", "Buy", "review",
                               base_rate_cite={"winrate_pct": 69.0})])
    with pytest.raises(mod.LevelValidationError, match="n_raw/n_regimes/n_macro"):
        mod.validate_level_set(ls, pack={}, rating="Hold")


def test_base_rate_cite_with_n_companions_is_fine():
    ls = _level_set([_trigger("downside", "Buy", "review",
                               base_rate_cite={"winrate_pct": 69.0, "n_raw": 63,
                                               "n_regimes": 6, "n_macro": 3})])
    mod.validate_level_set(ls, pack={}, rating="Hold")  # must not raise


def test_empty_level_set_is_fine():
    mod.validate_level_set(_level_set([]), pack={}, rating="Hold")
