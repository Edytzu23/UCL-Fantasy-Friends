"""
Seed FIFA World Cup 2026 fixtures (72 group stage matches).

Also adds any missing qualified nations to the teams table.

Knockout round fixtures (R32, R16, QF, SF, 3RD, F) will be seeded when groups
conclude — team assignments depend on group results.

Usage:
    py -3 -m scripts.seed_wc_fixtures
"""

import sys
import os
import io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.db import connection as db


WC_COMPETITION_ID = 2


# Teams that may be missing from the DB seed — add if absent.
MISSING_TEAMS = [
    # code, name, league_id (null for national teams)
    ("ALG", "Algeria"),
    ("BIH", "Bosnia and Herzegovina"),
    ("COD", "DR Congo"),
    ("CPV", "Cape Verde"),
    ("CUW", "Curaçao"),
    ("CZE", "Czechia"),
    ("HAI", "Haiti"),
    ("IRQ", "Iraq"),
    ("NOR", "Norway"),
    ("SWE", "Sweden"),
]


# All 72 group stage fixtures. Times are UTC approximate (ET + 4h for EDT).
# Format: (date_iso, home_code, away_code, venue, group)
GROUP_FIXTURES = [
    # GROUP A
    ("2026-06-11 19:00", "MEX", "RSA", "Estadio Azteca, Mexico City", "A"),
    ("2026-06-12 02:00", "KOR", "CZE", "Estadio Akron, Guadalajara", "A"),
    ("2026-06-18 16:00", "CZE", "RSA", "Mercedes-Benz Stadium, Atlanta", "A"),
    ("2026-06-19 03:00", "MEX", "KOR", "Estadio Akron, Guadalajara", "A"),
    ("2026-06-25 01:00", "CZE", "MEX", "Estadio Azteca, Mexico City", "A"),
    ("2026-06-25 01:00", "RSA", "KOR", "Estadio BBVA, Monterrey", "A"),

    # GROUP B
    ("2026-06-12 19:00", "CAN", "BIH", "BMO Field, Toronto", "B"),
    ("2026-06-13 19:00", "QAT", "SUI", "Levi's Stadium, San Francisco", "B"),
    ("2026-06-18 19:00", "SUI", "BIH", "SoFi Stadium, Los Angeles", "B"),
    ("2026-06-18 22:00", "CAN", "QAT", "BC Place, Vancouver", "B"),
    ("2026-06-24 19:00", "SUI", "CAN", "BC Place, Vancouver", "B"),
    ("2026-06-24 19:00", "BIH", "QAT", "Lumen Field, Seattle", "B"),

    # GROUP C
    ("2026-06-13 22:00", "BRA", "MAR", "MetLife Stadium, New York/NJ", "C"),
    ("2026-06-14 01:00", "HAI", "SCO", "Gillette Stadium, Boston", "C"),
    ("2026-06-19 22:00", "SCO", "MAR", "Gillette Stadium, Boston", "C"),
    ("2026-06-20 01:00", "BRA", "HAI", "Lincoln Financial Field, Philadelphia", "C"),
    ("2026-06-24 22:00", "SCO", "BRA", "Hard Rock Stadium, Miami", "C"),
    ("2026-06-24 22:00", "MAR", "HAI", "Mercedes-Benz Stadium, Atlanta", "C"),

    # GROUP D
    ("2026-06-13 01:00", "USA", "PAR", "SoFi Stadium, Los Angeles", "D"),
    ("2026-06-14 04:00", "AUS", "TUR", "BC Place, Vancouver", "D"),
    ("2026-06-19 19:00", "USA", "AUS", "Lumen Field, Seattle", "D"),
    ("2026-06-20 04:00", "TUR", "PAR", "Levi's Stadium, San Francisco", "D"),
    ("2026-06-26 02:00", "TUR", "USA", "SoFi Stadium, Los Angeles", "D"),
    ("2026-06-26 02:00", "PAR", "AUS", "Levi's Stadium, San Francisco", "D"),

    # GROUP E
    ("2026-06-14 17:00", "GER", "CUW", "NRG Stadium, Houston", "E"),
    ("2026-06-14 23:00", "CIV", "ECU", "Lincoln Financial Field, Philadelphia", "E"),
    ("2026-06-20 20:00", "GER", "CIV", "BMO Field, Toronto", "E"),
    ("2026-06-21 00:00", "ECU", "CUW", "Arrowhead Stadium, Kansas City", "E"),
    ("2026-06-25 20:00", "ECU", "GER", "MetLife Stadium, New York/NJ", "E"),
    ("2026-06-25 20:00", "CUW", "CIV", "Lincoln Financial Field, Philadelphia", "E"),

    # GROUP F
    ("2026-06-14 20:00", "NED", "JPN", "AT&T Stadium, Dallas", "F"),
    ("2026-06-15 02:00", "SWE", "TUN", "Estadio BBVA, Monterrey", "F"),
    ("2026-06-20 17:00", "NED", "SWE", "NRG Stadium, Houston", "F"),
    ("2026-06-21 04:00", "TUN", "JPN", "Estadio BBVA, Monterrey", "F"),
    ("2026-06-25 23:00", "JPN", "SWE", "AT&T Stadium, Dallas", "F"),
    ("2026-06-25 23:00", "TUN", "NED", "Arrowhead Stadium, Kansas City", "F"),

    # GROUP G
    ("2026-06-15 22:00", "BEL", "EGY", "Lumen Field, Seattle", "G"),
    ("2026-06-16 04:00", "IRN", "NZL", "SoFi Stadium, Los Angeles", "G"),
    ("2026-06-21 19:00", "BEL", "IRN", "SoFi Stadium, Los Angeles", "G"),
    ("2026-06-22 01:00", "NZL", "EGY", "BC Place, Vancouver", "G"),
    ("2026-06-27 03:00", "EGY", "IRN", "Lumen Field, Seattle", "G"),
    ("2026-06-27 03:00", "NZL", "BEL", "BC Place, Vancouver", "G"),

    # GROUP H
    ("2026-06-15 16:00", "ESP", "CPV", "Mercedes-Benz Stadium, Atlanta", "H"),
    ("2026-06-15 22:00", "KSA", "URU", "Hard Rock Stadium, Miami", "H"),
    ("2026-06-21 16:00", "ESP", "KSA", "Mercedes-Benz Stadium, Atlanta", "H"),
    ("2026-06-21 22:00", "URU", "CPV", "Hard Rock Stadium, Miami", "H"),
    ("2026-06-27 00:00", "CPV", "KSA", "NRG Stadium, Houston", "H"),
    ("2026-06-27 00:00", "URU", "ESP", "Estadio Akron, Guadalajara", "H"),

    # GROUP I
    ("2026-06-16 19:00", "FRA", "SEN", "MetLife Stadium, New York/NJ", "I"),
    ("2026-06-16 22:00", "IRQ", "NOR", "Gillette Stadium, Boston", "I"),
    ("2026-06-22 21:00", "FRA", "IRQ", "Lincoln Financial Field, Philadelphia", "I"),
    ("2026-06-23 00:00", "NOR", "SEN", "MetLife Stadium, New York/NJ", "I"),
    ("2026-06-26 19:00", "NOR", "FRA", "Gillette Stadium, Boston", "I"),
    ("2026-06-26 19:00", "SEN", "IRQ", "BMO Field, Toronto", "I"),

    # GROUP J
    ("2026-06-17 01:00", "ARG", "ALG", "Arrowhead Stadium, Kansas City", "J"),
    ("2026-06-17 04:00", "AUT", "JOR", "Levi's Stadium, San Francisco", "J"),
    ("2026-06-22 17:00", "ARG", "AUT", "AT&T Stadium, Dallas", "J"),
    ("2026-06-23 03:00", "JOR", "ALG", "Levi's Stadium, San Francisco", "J"),
    ("2026-06-28 02:00", "ALG", "AUT", "Arrowhead Stadium, Kansas City", "J"),
    ("2026-06-28 02:00", "JOR", "ARG", "AT&T Stadium, Dallas", "J"),

    # GROUP K
    ("2026-06-17 17:00", "POR", "COD", "NRG Stadium, Houston", "K"),
    ("2026-06-18 02:00", "UZB", "COL", "Estadio Azteca, Mexico City", "K"),
    ("2026-06-23 17:00", "POR", "UZB", "NRG Stadium, Houston", "K"),
    ("2026-06-24 02:00", "COL", "COD", "Estadio Akron, Guadalajara", "K"),
    ("2026-06-27 23:30", "COL", "POR", "Hard Rock Stadium, Miami", "K"),
    ("2026-06-27 23:30", "COD", "UZB", "Mercedes-Benz Stadium, Atlanta", "K"),

    # GROUP L
    ("2026-06-17 20:00", "ENG", "CRO", "AT&T Stadium, Dallas", "L"),
    ("2026-06-17 23:00", "GHA", "PAN", "BMO Field, Toronto", "L"),
    ("2026-06-23 20:00", "ENG", "GHA", "Gillette Stadium, Boston", "L"),
    ("2026-06-23 23:00", "PAN", "CRO", "BMO Field, Toronto", "L"),
    ("2026-06-27 21:00", "PAN", "ENG", "MetLife Stadium, New York/NJ", "L"),
    ("2026-06-27 21:00", "CRO", "GHA", "Lincoln Financial Field, Philadelphia", "L"),
]


def ensure_missing_teams(conn):
    added = 0
    for code, name in MISSING_TEAMS:
        row = conn.execute("SELECT id FROM teams WHERE code = ?", (code,)).fetchone()
        if row:
            continue
        conn.execute("""
            INSERT INTO teams (name, code, league_id, fotmob_id, fbref_id, elo_rating)
            VALUES (?, ?, NULL, NULL, NULL, 1500)
        """, (name, code))
        added += 1
        print(f"  [+team] {code} - {name}")
    return added


def team_id_by_code(conn, code):
    row = conn.execute("SELECT id FROM teams WHERE code = ?", (code,)).fetchone()
    return row["id"] if row else None


def seed_fixtures(conn):
    inserted = 0
    skipped = 0

    for date_iso, home_code, away_code, venue, group in GROUP_FIXTURES:
        home_id = team_id_by_code(conn, home_code)
        away_id = team_id_by_code(conn, away_code)
        if not home_id or not away_id:
            print(f"  [skip] {home_code} vs {away_code}: team missing")
            skipped += 1
            continue

        existing = conn.execute("""
            SELECT id FROM fixtures
            WHERE competition_id = ? AND home_team_id = ? AND away_team_id = ? AND match_date = ?
        """, (WC_COMPETITION_ID, home_id, away_id, date_iso)).fetchone()

        if existing:
            conn.execute("""
                UPDATE fixtures SET stage = ?, group_letter = ?, venue = ?, status = 'scheduled'
                WHERE id = ?
            """, ("GROUP", group, venue, existing["id"]))
        else:
            conn.execute("""
                INSERT INTO fixtures
                (competition_id, home_team_id, away_team_id, match_date, status, stage, group_letter, venue)
                VALUES (?, ?, ?, ?, 'scheduled', 'GROUP', ?, ?)
            """, (WC_COMPETITION_ID, home_id, away_id, date_iso, group, venue))
        inserted += 1

    return inserted, skipped


def main():
    conn = db.get_connection()
    print("[wc] ensuring missing teams...")
    added = ensure_missing_teams(conn)
    print(f"  {added} teams added\n")

    print("[wc] seeding 72 group stage fixtures...")
    inserted, skipped = seed_fixtures(conn)
    conn.commit()
    conn.close()
    print(f"\n[done] {inserted} fixtures seeded, {skipped} skipped")


if __name__ == "__main__":
    main()
