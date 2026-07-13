"""publish_ledger must write per-name reports to the vault's canonical
`reports/single-ticker/<TICKER>/` location — never flat at the reports/ root.

Pins the vault `reports/_index.md` taxonomy (per-name -> single-ticker/<T>/,
book-level -> portfolio/), which the replay publisher already follows via
reports/replay/<T>/. A flat write scatters a full-book batch (36 names) across
the vault root and desyncs the ledger's report_path from where the file lives.
"""
import importlib
import json
import sys
from pathlib import Path

SK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SK / "scripts" / "batch"))

TICKER = "NVDA"
ASOF = "2026-07-11"
STAMP = "1400"
RUN_ID = f"{TICKER}-{ASOF}-{STAMP}"


def _seed_run(runs_dir):
    """Minimal run dir: a decision + a report + one parseable judge vote."""
    d = runs_dir / RUN_ID
    (d / "50-votes").mkdir(parents=True)
    (d / "55-decision.json").write_text(json.dumps({
        "decision": "publish", "mode_label": "Buy", "spread": 0, "n_valid": 3,
    }))
    (d / "60-report.md").write_text(f"# {TICKER} report\nbody\n")
    (d / "50-votes" / "vote-1.md").write_text(
        "reasoning\nVERDICT: Buy | CONVICTION: 7 | WHY: because\n")
    return d


def _run_publisher(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "reports").mkdir(parents=True)
    runs = tmp_path / "runs"
    runs.mkdir()
    _seed_run(runs)
    # SK is used for BOTH the run dir (<SK>/runs/<run_id>, test-controlled) and the
    # real ledger.py the publisher shells out to — so point SK at tmp but link the
    # real scripts/ through, keeping the ledger append genuine rather than stubbed.
    (tmp_path / "scripts").symlink_to(SK / "scripts")

    pl = importlib.import_module("publish_ledger")
    importlib.reload(pl)
    monkeypatch.setattr(pl, "VAULT", str(vault))
    monkeypatch.setattr(pl, "LEDGER", str(vault / "reports" / "ledger.jsonl"))
    monkeypatch.setattr(pl, "SK", str(tmp_path))  # so run_dir = <tmp>/runs/<run_id>
    monkeypatch.setattr(sys, "argv", ["publish_ledger.py", ASOF, STAMP, TICKER])
    pl.main()
    return vault


def test_report_lands_in_single_ticker_not_flat(tmp_path, monkeypatch):
    vault = _run_publisher(tmp_path, monkeypatch)

    nested = vault / "reports" / "single-ticker" / TICKER / f"{TICKER}-{ASOF}.md"
    flat = vault / "reports" / f"{TICKER}-{ASOF}.md"

    assert nested.exists(), f"report must be written to {nested.relative_to(vault)}"
    assert not flat.exists(), "report must NOT be written flat at the reports/ root"
    assert nested.read_text() == f"# {TICKER} report\nbody\n"


def test_ledger_report_path_matches_where_the_file_actually_is(tmp_path, monkeypatch):
    vault = _run_publisher(tmp_path, monkeypatch)

    rows = [json.loads(x) for x in
            (vault / "reports" / "ledger.jsonl").read_text().splitlines() if x.strip()]
    row = next(r for r in rows if r["run_id"] == RUN_ID)

    assert row["report_path"] == f"reports/single-ticker/{TICKER}/{TICKER}-{ASOF}.md"
    # the recorded path must resolve to the file that was actually written
    assert (vault / row["report_path"]).exists()
