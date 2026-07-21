import ast, json, os

REPO = "market-terminal-alpha"
VAULT = os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/second-brain/"
    "Projects/personal/tradingagents/market-terminal-alpha")

# 1) repo code parses
for s in ("test_screener.py", "sp500_constituents.py", "resolve_universe.py",
          "mt_capture.py", "mt_resolve.py", "mt_score.py"):
    ast.parse(open(f"{REPO}/scripts/{s}").read())
# 2) captured universe artifact well-formed with tested count
u = json.load(open(f"{REPO}/data/api-test-universe-10B.json"))
assert u["n"] == len(u["rows"]) == 2187, f"universe rows {u['n']}"
# 3) docs live in the VAULT (not the repo)
for f in ("_index.md", "design-proposal.md",
          "api-marketterminal-reference.md", "api-test-results-screener.md"):
    assert os.path.getsize(os.path.join(VAULT, f)) > 0, f"missing vault doc {f}"
# 4) docs must NOT remain in the repo
assert not os.path.exists(f"{REPO}/docs"), "docs still in repo — belong in vault"
print("VERIFY OK: code parses; universe=2187; 4 docs in vault; no docs in repo")
