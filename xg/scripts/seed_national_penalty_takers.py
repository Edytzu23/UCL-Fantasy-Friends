"""
Manually seed penalty takers for national teams (WC 2026).

FotMob doesn't expose a 'goals' CDN stat for international football the same way
it does for domestic leagues, so we hardcode the current known primary penalty
takers per qualified nation based on recent international duty.

Usage:
    py -3 -m scripts.seed_national_penalty_takers
"""

import sys
import os
import io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force UTF-8 output on Windows so we can print accented names
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from src.db import connection as db


CURRENT_SEASON = "2025/26"


# (team_code, penalty_taker_last_name, note)
# Primary penalty takers as of April 2026 based on recent international matches.
NATIONAL_TAKERS = [
    # CONMEBOL
    ("ARG", "Messi",           "primary taker for Argentina"),
    ("BRA", "Neymar",          "when fit; Vinicius/Rodrygo as backup"),
    ("URU", "Valverde",        "Fede Valverde takes most recent pens"),
    ("COL", "James",           "James Rodríguez"),
    ("ECU", "Valencia",        "Enner Valencia"),
    ("PAR", "Almirón",         "Miguel Almirón"),

    # UEFA
    ("ENG", "Kane",            "Harry Kane"),
    ("FRA", "Mbappé",          "Kylian Mbappé"),
    ("GER", "Havertz",         "Kai Havertz / Wirtz backup"),
    ("ESP", "Morata",          "Álvaro Morata"),
    ("POR", "Ronaldo",         "Cristiano Ronaldo while active"),
    ("NED", "Depay",           "Memphis Depay"),
    ("BEL", "De Bruyne",       "Kevin De Bruyne"),
    ("CRO", "Modrić",          "Luka Modrić"),
    ("SUI", "Xhaka",           "Granit Xhaka"),
    ("AUT", "Arnautović",      "Marko Arnautović"),
    ("NOR", "Haaland",         "Erling Haaland"),
    ("TUR", "Çalhanoğlu",      "Hakan Çalhanoğlu"),
    ("SCO", "McGinn",          "John McGinn / Robertson secondary"),
    ("CZE", "Schick",          "Patrik Schick"),
    ("BIH", "Džeko",           "Edin Džeko"),
    ("SWE", "Gyökeres",        "Viktor Gyökeres"),

    # CONCACAF
    ("USA", "Pulisic",         "Christian Pulisic"),
    ("MEX", "Jiménez",         "Raúl Jiménez"),
    ("CAN", "David",           "Jonathan David"),
    ("PAN", "Fajardo",         "Aníbal Fajardo"),

    # AFC
    ("KOR", "Son",             "Son Heung-min"),
    ("JPN", "Mitoma",          "Kaoru Mitoma / Minamino backup"),
    ("KSA", "Al-Dawsari",      "Salem Al-Dawsari"),
    ("IRN", "Azmoun",          "Sardar Azmoun"),
    ("AUS", "Boyle",           "Martin Boyle"),
    ("UZB", "Shomurodov",      "Eldor Shomurodov"),
    ("QAT", "Afif",            "Akram Afif"),
    ("IRQ", "Resan",           "Ali Al-Hamadi / Resan"),
    ("JOR", "Al-Naimat",       "Mousa Al-Taamari / Al-Naimat"),

    # CAF
    ("MAR", "Ziyech",          "Hakim Ziyech"),
    ("SEN", "Mané",            "Sadio Mané"),
    ("EGY", "Salah",           "Mohamed Salah"),
    ("TUN", "Msakni",          "Youssef Msakni"),
    ("CIV", "Haller",           "Sébastien Haller"),
    ("ALG", "Mahrez",          "Riyad Mahrez"),
    ("RSA", "Zwane",           "Themba Zwane"),
    ("CPV", "Mendes",          "Garry Mendes"),
    ("GHA", "Kudus",           "Mohammed Kudus"),
    ("COD", "Bakambu",         "Cédric Bakambu"),

    # OFC
    ("NZL", "Wood",            "Chris Wood"),

    # Intercontinental playoffs
    ("HAI", "Pierrot",         "Duckens Nazon / Pierrot"),
    ("NCL", "Gope-Fenepej",    "Fallback"),
]


def _find_team(conn, code):
    row = conn.execute(
        "SELECT id FROM teams WHERE UPPER(code) = UPPER(?)",
        (code,),
    ).fetchone()
    return row["id"] if row else None


def _find_player_by_last_name(conn, team_id, last_name):
    row = conn.execute(
        "SELECT id, name FROM players WHERE team_id = ? AND name LIKE ?",
        (team_id, f"%{last_name}%"),
    ).fetchone()
    return (row["id"], row["name"]) if row else (None, None)


def main():
    conn = db.get_connection()

    inserted = 0
    missing_team = 0
    missing_player = 0

    for team_code, last_name, note in NATIONAL_TAKERS:
        team_id = _find_team(conn, team_code)
        if not team_id:
            print(f"  [team missing] {team_code}")
            missing_team += 1
            continue

        player_id, resolved_name = _find_player_by_last_name(conn, team_id, last_name)
        if not player_id:
            print(f"  [player missing] {team_code}: '{last_name}' not in squad")
            missing_player += 1
            continue

        conn.execute("""
            INSERT OR REPLACE INTO penalty_takers
            (team_id, player_id, priority, season, updated_at)
            VALUES (?, ?, 1, ?, datetime('now'))
        """, (team_id, player_id, CURRENT_SEASON))
        inserted += 1
        print(f"  [+] {team_code} → {resolved_name}")

    conn.commit()
    conn.close()

    print(f"\n[done] {inserted} seeded, {missing_team} teams missing, {missing_player} players missing")


if __name__ == "__main__":
    main()
