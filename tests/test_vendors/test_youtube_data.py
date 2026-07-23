"""Hermetic tests for the youtube_data vendor CLI (P6 youtube_attention source)."""
import youtube_data


def _payload(n):
    return {"items": [{"id": {"kind": "youtube#video", "videoId": f"vid{i}"}}
                      for i in range(n)]}


def test_build_payload_counts_and_cites_by_id():
    out = youtube_data.build_payload("mrvl", "2026-07-11", 7, _payload(12))
    body = out["_youtube"]
    assert body["video_count"] == 12
    assert body["ticker"] == "MRVL"
    assert body["window_days"] == 7
    assert body["video_ids"] == ["vid0", "vid1", "vid2", "vid3", "vid4"]  # cite cap
    # R14 compliance: no titles/UGC text may ride along
    assert "titles" not in body and "snippet" not in body


def test_build_payload_tolerates_malformed_items():
    raw = {"items": [{"id": {"videoId": "ok1"}}, {"id": "channelmatch"}, {}]}
    out = youtube_data.build_payload("MRVL", "2026-07-11", 7, raw)
    assert out["_youtube"]["video_count"] == 1
    assert out["_youtube"]["video_ids"] == ["ok1"]


def test_missing_key_dies_not_configured(monkeypatch, capsys):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    try:
        youtube_data.main(["--ticker", "MRVL"])
    except SystemExit as e:
        assert e.code == 2  # VendorNotConfigured convention
    else:
        raise AssertionError("expected SystemExit(2) without YOUTUBE_API_KEY")
    assert "YOUTUBE_API_KEY" in capsys.readouterr().err
