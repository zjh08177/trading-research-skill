"""Shared bootstrap for vendor CLIs. Must be imported before any tradingagents import."""
import json
import os
import sys

UPSTREAM = "/Users/bytedance/Work/sidekicks/tradingagents-workspace/TradingAgents-upstream"
sys.path.insert(0, UPSTREAM)

from dotenv import load_dotenv

load_dotenv(os.path.join(UPSTREAM, ".env"), override=False)


def fact(v, unit, asof, src):
    return {"v": v, "unit": unit, "asof": asof, "src": src}


def emit(facts):
    print(json.dumps(facts, separators=(",", ":")))


def die(msg, code):
    print(msg, file=sys.stderr)
    sys.exit(code)
