"""Tests for run_stats.py — deterministic disclosure-footer stats."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_stats  # noqa: E402


def _make_run(tmp_path, n_judges=3, with_prose_qa=True, with_report=True):
    (tmp_path / "20-analyst-fund.md").write_text("fund brief words here today")
    (tmp_path / "20-analyst-tech.md").write_text("tech brief words here today")
    (tmp_path / "20-analyst-sent.md").write_text("sent brief words here today")
    (tmp_path / "20-analyst-meanrev.md").write_text("meanrev brief words here today")
    (tmp_path / "30-debate.md").write_text(
        "## Bull case\n\nbull words here.\n\n## Bear case\n\nbear words here.\n")
    (tmp_path / "40-risk.md").write_text("risk narrative words here today please")
    votes_dir = tmp_path / "50-votes"
    votes_dir.mkdir()
    for i in range(1, n_judges + 1):
        (votes_dir / f"vote-{i}.md").write_text(f"vote {i} words here today")
    if with_report:
        (tmp_path / "60-report.md").write_text("# T\n\nreport words here today")
    if with_prose_qa:
        (tmp_path / "70-qa-prose.txt").write_text("PROSE QA: clean")
    return tmp_path


def test_count_agents_matches_structural_formula(tmp_path):
    run_dir = _make_run(tmp_path, n_judges=3)
    n, notes, n_judges = run_stats.count_agents(str(run_dir))
    # 4 analysts + 2 debate (bull+bear) + 1 risk + 3 judges + 1 writer + 1 prose-qa
    assert n == 12
    assert n_judges == 3


def test_count_agents_scales_with_n_judges(tmp_path):
    run_dir = _make_run(tmp_path, n_judges=5)
    n, _notes, n_judges = run_stats.count_agents(str(run_dir))
    assert n == 14
    assert n_judges == 5


def test_wall_clock_seconds_from_mtimes(tmp_path):
    run_dir = _make_run(tmp_path)
    wall = run_stats.wall_clock_seconds(str(run_dir))
    assert wall is not None and wall >= 0


def test_build_claude_code_host(tmp_path):
    run_dir = _make_run(tmp_path, n_judges=3)
    stats = run_stats.build(str(run_dir), host="claude-code")
    assert stats["agent_count"] == 12
    assert "opus" in stats["model_mix"]
    assert "sonnet" in stats["model_mix"]
    assert isinstance(stats["cost_usd"], float)
    assert stats["cost_usd"] > 0


def test_build_cursor_host_uses_gpt(tmp_path):
    run_dir = _make_run(tmp_path, n_judges=3)
    stats = run_stats.build(str(run_dir), host="cursor")
    assert "gpt-5.5-medium" in stats["model_mix"]


def test_patch_report_replaces_all_four_tokens(tmp_path):
    run_dir = _make_run(tmp_path, n_judges=3)
    report = run_dir / "60-report.md"
    report.write_text(
        "## Disclosure\n\n"
        "Actual N: 3 valid votes · Agents: {{agent_count}} · "
        "Models: {{model_mix}} · Wall clock: {{wall_s}}s · "
        "Token cost: ${{cost_usd}}.\nNot financial advice.\n"
    )
    stats = run_stats.build(str(run_dir), host="claude-code")
    found_all = run_stats.patch_report(str(report), stats)
    assert found_all is True
    patched = report.read_text()
    assert "{{" not in patched
    assert "Agents: 12" in patched


def test_patch_report_reports_missing_tokens(tmp_path):
    run_dir = _make_run(tmp_path, n_judges=3)
    report = run_dir / "60-report.md"
    report.write_text("## Disclosure\n\nNo placeholders here at all.\n")
    stats = run_stats.build(str(run_dir), host="claude-code")
    found_all = run_stats.patch_report(str(report), stats)
    assert found_all is False


def test_main_json_output(tmp_path, capsys):
    run_dir = _make_run(tmp_path, n_judges=3)
    code = run_stats.main([str(run_dir), "--json"])
    assert code == 0
    out = capsys.readouterr().out
    assert '"agent_count": 12' in out


def test_main_patch_writes_report(tmp_path):
    run_dir = _make_run(tmp_path, n_judges=3)
    report = run_dir / "60-report.md"
    report.write_text(
        "## Disclosure\n\nAgents: {{agent_count}} · Models: {{model_mix}} · "
        "Wall clock: {{wall_s}}s · Token cost: ${{cost_usd}}.\n"
    )
    code = run_stats.main([str(run_dir), "--patch", str(report)])
    assert code == 0
    assert "{{" not in report.read_text()
