"""tests for the P6 social-sentiment slice (ERD social-sentiment v2.7):
reddit_tone / reddit_crowding / youtube_attention / social_risk distillers +
their signal_registry entries (R1-R6, R9 replay split, R12 key gate)."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from distillers import DistillCtx  # noqa: E402
from distillers import reddit_crowding, reddit_tone, social_risk, youtube_attention  # noqa: E402
import signal_registry  # noqa: E402


def _ctx(facts=None, mode="live", asof="2026-07-11"):
    return DistillCtx(ticker="MRVL", kind="equity", asof=asof, mode=mode,
                      facts=facts or {}, spot=100.0, atr=2.0,
                      max_rows=1, max_tokens=None, entry=None)


def _by_id(signals, sid):
    return next((s for s in signals if s["id"] == sid), None)


# -- reddit_tone (tradestie) --

def test_tone_ranked_row_is_low_trust_crowding_direction():
    raw = {"date": "2026-07-11", "row": {"sentiment_score": 0.21, "no_of_comments": 14}}
    (s,) = reddit_tone.distill(raw, _ctx())
    assert s["v"] == 0.21 and s["label"] == "bull"
    assert s["low_trust"] is True          # R6: never a thesis/quality input
    assert s["n_comments"] == 14


def test_tone_labels_bear_and_neutral():
    (s,) = reddit_tone.distill({"date": "d", "row": {"sentiment_score": -0.3}}, _ctx())
    assert s["label"] == "bear"
    (s,) = reddit_tone.distill({"date": "d", "row": {"sentiment_score": 0.01}}, _ctx())
    assert s["label"] == "neutral"


def test_tone_not_ranked_is_quiet_reading_not_gap():
    (s,) = reddit_tone.distill({"date": "d", "row": None, "n_ranked": 50}, _ctx())
    assert s["label"] == "not-ranked" and s["notable"] is False
    assert "quiet" in s["v"] and s.get("gap") is None


def test_tone_malformed_score_degrades_to_named_gap():
    (s,) = reddit_tone.distill({"date": "d", "row": {"sentiment_score": "hot"}}, _ctx())
    assert s["v"] is None and "sentiment_score" in s["gap"]


def test_tone_replay_guard_rejects_cutoff_day_data():
    raw = {"date": "2026-07-11", "row": {"sentiment_score": 0.5}}
    (s,) = reddit_tone.distill(raw, _ctx(mode="replay", asof="2026-07-11"))
    assert s["v"] is None and "replay guard" in s["gap"]


def test_tone_replay_accepts_strictly_prior_day():
    raw = {"date": "2026-07-10", "row": {"sentiment_score": 0.5}}
    (s,) = reddit_tone.distill(raw, _ctx(mode="replay", asof="2026-07-11"))
    assert s["v"] == 0.5


# -- reddit_crowding (apewisdom) --

def test_crowding_mention_spike_is_notable():
    raw = {"asof": "d", "row": {"rank": 32, "mentions": 19, "mentions_24h_ago": 10,
                                "rank_24h_ago": 62}}
    (s,) = reddit_crowding.distill(raw, _ctx())
    assert s["v"] == 19 and s["notable"] is True and s["rank"] == 32
    assert s["mentions_24h_ago"] == 10 and s["low_trust"] is True


def test_crowding_flat_mentions_not_notable():
    raw = {"asof": "d", "row": {"rank": 40, "mentions": 11, "mentions_24h_ago": 10,
                                "rank_24h_ago": 42}}
    (s,) = reddit_crowding.distill(raw, _ctx())
    assert s["notable"] is False


def test_crowding_rank_jump_alone_is_notable():
    raw = {"asof": "d", "row": {"rank": 30, "mentions": 11, "mentions_24h_ago": 10,
                                "rank_24h_ago": 55}}
    (s,) = reddit_crowding.distill(raw, _ctx())
    assert s["notable"] is True


def test_crowding_not_ranked_is_quiet_reading():
    (s,) = reddit_crowding.distill({"asof": "d", "row": None, "scanned_ranks": 300}, _ctx())
    assert s["notable"] is False and "quiet" in s["v"]


def test_crowding_malformed_mentions_degrades_to_named_gap():
    (s,) = reddit_crowding.distill({"asof": "d", "row": {"mentions": None}}, _ctx())
    assert s["v"] is None and "mentions" in s["gap"]


# -- youtube_attention (+ R4 tone gate) --

def test_attention_count_and_gated_tone():
    raw = {"asof": "d", "video_count": 12, "window_days": 7, "capped_at": 50,
           "video_ids": ["a", "b"]}
    out = youtube_attention.distill(raw, _ctx())
    att = _by_id(out, "P6.youtube_attention")
    assert att["v"] == 12 and att["notable"] is False and att["video_ids"] == ["a", "b"]
    tone = _by_id(out, "P6.youtube_tone")
    assert tone["v"] is None and "classifier-validation" in tone["gap"]  # R4 gate


def test_attention_saturation_names_the_cap():
    raw = {"asof": "d", "video_count": 50, "capped_at": 50}
    att = _by_id(youtube_attention.distill(raw, _ctx()), "P6.youtube_attention")
    assert att["notable"] is True and "saturated" in att["gap"]


def test_attention_missing_count_degrades_to_named_gap():
    att = _by_id(youtube_attention.distill({"asof": "d"}, _ctx()), "P6.youtube_attention")
    assert att["v"] is None and att["gap"]


# -- social_risk (R6 composite) --

def _risk_facts(tone=None, crowd_notable=False, yt_notable=False, with_yt=True):
    facts = {
        "P6.reddit_crowding": {"v": 19, "notable": crowd_notable},
    }
    if tone is not None:
        facts["P6.reddit_tone"] = {"v": tone, "low_trust": True}
    if with_yt:
        facts["P6.youtube_attention"] = {"v": 50, "notable": yt_notable}
    return facts


def test_risk_squeeze_on_hot_crowding_bearish_tone():
    (s,) = social_risk.distill(None, _ctx(_risk_facts(tone=-0.3, crowd_notable=True)))
    assert s["v"] == "squeeze-risk" and s["notable"] is True


def test_risk_reversal_on_hot_crowding_euphoric_tone():
    (s,) = social_risk.distill(None, _ctx(_risk_facts(tone=0.4, yt_notable=True)))
    assert s["v"] == "reversal-risk"


def test_risk_none_when_crowding_cold_even_with_strong_tone():
    (s,) = social_risk.distill(None, _ctx(_risk_facts(tone=0.9)))
    assert s["v"] == "none" and s["notable"] is False


def test_risk_weak_tone_never_conjoins():
    (s,) = social_risk.distill(None, _ctx(_risk_facts(tone=0.05, crowd_notable=True)))
    assert s["v"] == "none"


def test_risk_agreement_counts_independent_substrates_only():
    # tradestie+apewisdom = ONE reddit substrate; youtube is the second (R6)
    (s,) = social_risk.distill(None, _ctx(_risk_facts(tone=0.4, crowd_notable=True)))
    assert s["agreement"] == 2
    (s,) = social_risk.distill(None, _ctx(_risk_facts(tone=0.4, crowd_notable=True,
                                                      with_yt=False)))
    assert s["agreement"] == 1


def test_risk_hot_crowding_missing_tone_names_the_floor():
    (s,) = social_risk.distill(None, _ctx(_risk_facts(crowd_notable=True)))
    assert s["v"] == "none" and "tone unavailable" in s["gap"]


def test_risk_no_atomics_degrades_to_named_gap():
    (s,) = social_risk.distill(None, _ctx({}))
    assert s["v"] is None and s["gap"]


# -- registry wiring (R9 replay split, R12 key gate) --

def _p6(entries):
    return {e.feed_id for e in entries if e.section == "P6"}


def test_registry_p6_replay_split():
    live = signal_registry.load_registry(mode="live",
                                         registry=signal_registry.REGISTRY)
    replay = signal_registry.load_registry(mode="replay",
                                           registry=signal_registry.REGISTRY)
    live_p6 = _p6(live)
    assert {"reddit.tradestie", "reddit.apewisdom", "social.risk"} <= live_p6
    assert _p6(replay) <= {"reddit.tradestie"}  # only the strictly-prior-day feed survives


def test_registry_social_risk_ordered_after_p6_atomics():
    ids = [e.feed_id for e in signal_registry.REGISTRY]
    for atomic in ("reddit.tradestie", "reddit.apewisdom", "youtube.attention"):
        assert ids.index(atomic) < ids.index("social.risk")


def test_registry_tradestie_replay_fetch_args_carry_replay_flag():
    entry = next(e for e in signal_registry.REGISTRY if e.feed_id == "reddit.tradestie")
    args = entry.fetch_args(_ctx(mode="replay", asof="2026-07-08"))
    assert "--replay" in args and "2026-07-08" in args
    args_live = entry.fetch_args(_ctx(mode="live", asof="2026-07-11"))
    assert "--replay" not in args_live


def test_youtube_key_gate(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    creds = tmp_path / "vendors.env"
    creds.write_text("OTHER=1\n")
    assert signal_registry._youtube_key_present(creds_path=str(creds)) is False
    creds.write_text("OTHER=1\nYOUTUBE_API_KEY=abc123\n")
    assert signal_registry._youtube_key_present(creds_path=str(creds)) is True
    assert signal_registry._youtube_key_present(creds_path=str(tmp_path / "nope")) is False
    monkeypatch.setenv("YOUTUBE_API_KEY", "zzz")
    assert signal_registry._youtube_key_present(creds_path=str(tmp_path / "nope")) is True
