"""tests for scripts/signal_registry.py (Step 2 of the distilled-signal-pack
v1 slice): registry schema + load_registry filters (D2/AC5/AC-F,
tech-solution §2).
"""
import dataclasses
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import signal_registry  # noqa: E402
from signal_registry import FeedEntry, load_registry  # noqa: E402


REQUIRED_FIELDS = {
    "feed_id", "section", "tier", "vendor", "endpoint", "cost", "cadence",
    "replay_safe", "default_on", "max_rows", "max_tokens", "source",
    "distiller", "cite_src", "fetch_args", "relevance_gate",
}


def test_schema_version_present():
    assert signal_registry.SCHEMA_VERSION == 1


def test_registry_entries_have_required_fields():
    field_names = {f.name for f in dataclasses.fields(FeedEntry)}
    assert REQUIRED_FIELDS <= field_names
    assert len(signal_registry.REGISTRY) > 0
    for entry in signal_registry.REGISTRY:
        assert isinstance(entry, FeedEntry)
        assert entry.feed_id
        assert entry.section
        assert entry.tier in (1, 2, 3)
        assert entry.vendor
        assert entry.cite_src


def test_load_registry_replay_drops_replay_unsafe():
    entries = load_registry(mode="replay")
    assert entries  # sanity: replay-safe feeds still survive
    assert all(e.replay_safe for e in entries)

    live_only = [e for e in signal_registry.REGISTRY if not e.replay_safe]
    assert live_only  # sanity: the full registry actually has live-only feeds
    kept_ids = {e.feed_id for e in entries}
    assert not any(e.feed_id in kept_ids for e in live_only)


def test_load_registry_lean_drops_tier_above_1():
    entries = load_registry(profile="lean")
    assert entries
    assert all(e.tier <= 1 for e in entries)

    tier_gt1 = [e for e in signal_registry.REGISTRY if e.tier > 1]
    assert tier_gt1  # sanity: the full registry has tier>1 feeds
    kept_ids = {e.feed_id for e in entries}
    assert not any(e.feed_id in kept_ids for e in tier_gt1)


def test_load_registry_options_false_drops_p8():
    entries = load_registry(options=False)
    assert entries
    assert all(e.section != "P8" for e in entries)

    p8 = [e for e in signal_registry.REGISTRY if e.section == "P8"]
    assert p8  # sanity: the full registry has P8 feeds
    kept_ids = {e.feed_id for e in entries}
    assert not any(e.feed_id in kept_ids for e in p8)


def test_load_registry_options_true_keeps_default_on_p8():
    entries = load_registry(options=True, profile="full", mode="live")
    p8_default_on_ids = {
        e.feed_id for e in signal_registry.REGISTRY
        if e.section == "P8" and e.default_on
    }
    kept_ids = {e.feed_id for e in entries}
    assert p8_default_on_ids <= kept_ids


def test_load_registry_honors_stub_registry_ac_f():
    """AC-F hook: a caller-supplied stub registry is honored so a feed can be
    added with zero build_datapack control-flow edits, proven by loading a
    registry that contains only a stub feed."""
    stub = FeedEntry(
        feed_id="stub.feed", section="P9", tier=1, vendor="derived",
        endpoint="stub", cost="free", cadence="per-run", replay_safe=True,
        default_on=True, max_rows=1, max_tokens=None, source="derive",
        distiller=None, cite_src="derived",
    )
    entries = load_registry(registry=[stub])
    assert entries == [stub]


def test_load_registry_stub_registry_still_filtered():
    stub_off = FeedEntry(
        feed_id="stub.off", section="P8", tier=2, vendor="derived",
        endpoint="stub", cost="free", cadence="per-run", replay_safe=False,
        default_on=True, max_rows=1, max_tokens=None, source="derive",
        distiller=None, cite_src="derived",
    )
    assert load_registry(registry=[stub_off], options=False) == []
    assert load_registry(registry=[stub_off], mode="replay") == []


def test_load_registry_does_not_mutate_module_registry():
    before = list(signal_registry.REGISTRY)
    load_registry(mode="replay")
    load_registry(profile="lean")
    load_registry(options=False)
    assert signal_registry.REGISTRY == before
