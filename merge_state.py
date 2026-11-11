"""
Merge this run's seen.json / state.json into the copies already on GitHub.

Two runs can overlap (for example a manual run and a cron-job.org run). Each
one saves its own list of alerted jobs, and a plain git rebase then fails on
a conflict. This script merges instead:

- seen.json:  union of both; for a job in both, the later timestamp wins.
- state.json: last_searched per site+term; the later timestamp wins.

Usage (from the workflow):
    python merge_state.py <our_seen.json> <our_state.json>
The repo's seen.json and state.json (already reset to origin/main) are
updated in place.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def later(a, b):
    """Later of two ISO timestamps (strings compare correctly in ISO format
    when they share the same UTC offset, which this project always writes)."""
    if not a:
        return b
    if not b:
        return a
    return max(a, b)


def merge_seen(base: dict, ours: dict) -> dict:
    merged = dict(base)
    for key, stamp in ours.items():
        merged[key] = later(merged.get(key), stamp)
    return merged


def merge_state(base: dict, ours: dict) -> dict:
    merged = dict(base)
    for k, v in ours.items():
        if k != "last_searched":
            merged.setdefault(k, v)
    last = dict(base.get("last_searched", {}))
    for key, stamp in ours.get("last_searched", {}).items():
        last[key] = later(last.get(key), stamp)
    merged["last_searched"] = last
    return merged


def main(argv) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 1
    our_seen, our_state = load(Path(argv[1])), load(Path(argv[2]))
    seen_path, state_path = ROOT / "seen.json", ROOT / "state.json"

    seen = merge_seen(load(seen_path), our_seen)
    state = merge_state(load(state_path), our_state)

    seen_path.write_text(json.dumps(seen, indent=0, sort_keys=True), encoding="utf-8")
    state_path.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    print(f"Merged: seen={len(seen)} jobs, searches tracked={len(state['last_searched'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
