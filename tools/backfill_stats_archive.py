"""
One-time backfill of the per-MD player stats archive.

For each MD passed in, reads the existing cache/md{XX}.json from GitHub and
writes a frozen stats/md{XX}_players.json snapshot of the current allPlayers
slice. Marks any MD whose totals look incomplete (zero assists across the
entire league) as 'unverified' so the dashboard can flag it later.

Run once to lock in current state:
    GITHUB_TOKEN=... py tools/backfill_stats_archive.py 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15

After this, the FINALMD checkpoint hook captures fresh archives going forward.
"""

import os
import sys

# Make main.py importable
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from main import (
    load_md_cache,
    save_player_stats_archive,
    mark_md_unverified,
    GITHUB_TOKEN,
)


def backfill(md):
    data = load_md_cache(md)
    if not data:
        print(f"[backfill] MD{md}: no cache — skip")
        return

    players = data.get("allPlayers", [])
    total_g = sum(p.get("mdGoals", 0) for p in players)
    total_a = sum(p.get("mdAssists", 0) for p in players)
    total_cs = sum(p.get("mdCleanSheet", 0) for p in players)
    print(f"[backfill] MD{md}: players={len(players)} g={total_g} a={total_a} cs={total_cs}")

    # Heuristic: a finished MD with > 5 goals but 0 assists is broken.
    looks_broken = (total_g > 5 and total_a == 0)
    source = "unverified" if looks_broken else "frozen_cache"

    saved = save_player_stats_archive(md, data, source=source, overwrite=False)
    print(f"  archive saved: {saved} (source={source})")

    if looks_broken:
        ok = mark_md_unverified(md, unverified=True)
        print(f"  marked unverified: {ok}")


def main():
    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN env var not set")
        sys.exit(1)

    if len(sys.argv) > 1:
        mds = [int(x) for x in sys.argv[1:]]
    else:
        # Default: backfill MDs 1-16 from current cache state
        mds = list(range(1, 17))

    print(f"Backfilling MDs: {mds}")
    for md in mds:
        backfill(md)


if __name__ == "__main__":
    main()
