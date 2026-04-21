"""
Seed penalty takers from FotMob goals.json SubStatValue.

For each league, identifies the player with the most penalty goals per team
and inserts them into the `penalty_takers` table with priority=1.

Usage:
    py -3 -m scripts.seed_penalty_takers
    py -3 -m scripts.seed_penalty_takers --league "Premier League"
"""

import sys
import os
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import LEAGUES
from src.data.fotmob import fetch_penalty_takers
from src.db import connection as db


CURRENT_SEASON = "2025/26"


def _get_team_by_fotmob(conn, fotmob_id):
    row = conn.execute(
        "SELECT id FROM teams WHERE fotmob_id = ?", (fotmob_id,)
    ).fetchone()
    return row["id"] if row else None


def _find_player(conn, team_id, player_name, fotmob_player_id):
    if fotmob_player_id:
        row = conn.execute(
            "SELECT id FROM players WHERE fotmob_id = ?", (fotmob_player_id,)
        ).fetchone()
        if row:
            return row["id"]

    row = conn.execute(
        "SELECT id FROM players WHERE team_id = ? AND name = ?",
        (team_id, player_name),
    ).fetchone()
    if row:
        return row["id"]

    parts = (player_name or "").split()
    if parts:
        last_name = parts[-1]
        row = conn.execute(
            "SELECT id FROM players WHERE team_id = ? AND name LIKE ?",
            (team_id, f"%{last_name}"),
        ).fetchone()
        if row:
            return row["id"]

    return None


def seed_league(league_name):
    print(f"\n[pen] {league_name}")
    takers = fetch_penalty_takers(league_name)
    if not takers:
        print(f"  no penalty takers found")
        return 0

    conn = db.get_connection()
    inserted = 0
    skipped = 0

    for fotmob_team_id, info in takers.items():
        team_id = _get_team_by_fotmob(conn, fotmob_team_id)
        if not team_id:
            skipped += 1
            continue

        player_id = _find_player(
            conn, team_id, info["player_name"], info.get("fotmob_player_id")
        )
        if not player_id:
            print(f"  [skip] player not found: {info['player_name']} (team fotmob_id={fotmob_team_id})")
            skipped += 1
            continue

        conn.execute("""
            INSERT OR REPLACE INTO penalty_takers
            (team_id, player_id, priority, season, updated_at)
            VALUES (?, ?, 1, ?, datetime('now'))
        """, (team_id, player_id, CURRENT_SEASON))
        inserted += 1
        print(f"  [+] {info['player_name']} ({info['pen_goals']} pen goals)")

    conn.commit()
    conn.close()
    print(f"  {inserted} inserted, {skipped} skipped")
    return inserted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", help="Seed only one league")
    args = parser.parse_args()

    leagues = [args.league] if args.league else list(LEAGUES.keys())
    total = 0
    for lg in leagues:
        if lg not in LEAGUES:
            print(f"[skip] unknown league: {lg}")
            continue
        total += seed_league(lg)

    print(f"\n[done] {total} penalty takers seeded across {len(leagues)} leagues")


if __name__ == "__main__":
    main()
