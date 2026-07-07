"""Static wiring checks for v2.4a usage/evolve integration."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_skill_contract_wires_usage_and_evolve_modes():
    text = (ROOT / "SKILL.md").read_text()
    assert "scripts/usage.py start" in text
    assert "scripts/usage.py end" in text
    assert "scripts/usage.py fail" in text
    assert "--evolve" in text
    assert "scripts/evolve.py" in text
    assert "mode=evolve" in text


def test_batch_datapack_exports_usage_ids_for_children():
    text = (ROOT / "scripts" / "batch" / "build_datapack.py").read_text()
    assert "TRADING_RESEARCH_BATCH_ID" in text
    assert "usage.py" in text
    assert "TRADING_RESEARCH_INVOCATION_ID" in text
    assert '"invocation_id"' in text


def test_portfolio_workflow_terminates_usage_rows():
    text = (ROOT / "workflows" / "portfolio_pipeline.js").read_text()
    assert "usage.py end" in text
    assert "usage.py fail" in text
    assert "invocation_id" in text


def test_cursor_command_declares_host_and_mandatory_start():
    text = (ROOT / "hosts" / "cursor-command.md").read_text()
    assert "TRADING_RESEARCH_HOST=cursor" in text
    assert "/Users/bytedance/.claude/skills/trading-research/scripts/usage.py start" in text
