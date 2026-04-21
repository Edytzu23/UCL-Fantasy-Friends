"""
Seed script — populates teams, leagues, competitions, and fixtures.
Run once to bootstrap the database, then again to refresh.

Usage:
    python -m scripts.seed_teams
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.connection import init_db
from src.db.queries import (
    upsert_league, upsert_team, upsert_competition,
    get_team_by_fotmob, upsert_fixture,
)
from src.data.fotmob import fetch_ucl_teams, fetch_ucl_fixtures, fetch_league_table, get_build_id
from src.data.elo import fetch_elo_ratings, get_team_elo
from src.config import LEAGUES

# ── Static: World Cup 2026 national teams ─────────────────────────────
# All 48 qualified nations. Code = FIFA 3-letter code.
WC_TEAMS = [
    # Group A–P (48 teams, all qualified nations as of April 2026)
    # Host nations
    {"name": "United States",   "code": "USA", "elo_name": "USA"},
    {"name": "Canada",          "code": "CAN", "elo_name": "Canada"},
    {"name": "Mexico",          "code": "MEX", "elo_name": "Mexico"},
    # South America (6 teams)
    {"name": "Argentina",       "code": "ARG", "elo_name": "Argentina"},
    {"name": "Brazil",          "code": "BRA", "elo_name": "Brazil"},
    {"name": "Colombia",        "code": "COL", "elo_name": "Colombia"},
    {"name": "Uruguay",         "code": "URU", "elo_name": "Uruguay"},
    {"name": "Ecuador",         "code": "ECU", "elo_name": "Ecuador"},
    {"name": "Venezuela",       "code": "VEN", "elo_name": "Venezuela"},
    # CONCACAF (additional, 6 from region total)
    {"name": "Panama",          "code": "PAN", "elo_name": "Panama"},
    {"name": "Costa Rica",      "code": "CRC", "elo_name": "Costa Rica"},
    {"name": "Honduras",        "code": "HON", "elo_name": "Honduras"},
    # Europe (16 teams)
    {"name": "France",          "code": "FRA", "elo_name": "France"},
    {"name": "England",         "code": "ENG", "elo_name": "England"},
    {"name": "Spain",           "code": "ESP", "elo_name": "Spain"},
    {"name": "Germany",         "code": "GER", "elo_name": "Germany"},
    {"name": "Portugal",        "code": "POR", "elo_name": "Portugal"},
    {"name": "Netherlands",     "code": "NED", "elo_name": "Netherlands"},
    {"name": "Belgium",         "code": "BEL", "elo_name": "Belgium"},
    {"name": "Italy",           "code": "ITA", "elo_name": "Italy"},
    {"name": "Croatia",         "code": "CRO", "elo_name": "Croatia"},
    {"name": "Switzerland",     "code": "SUI", "elo_name": "Switzerland"},
    {"name": "Serbia",          "code": "SRB", "elo_name": "Serbia"},
    {"name": "Austria",         "code": "AUT", "elo_name": "Austria"},
    {"name": "Denmark",         "code": "DEN", "elo_name": "Denmark"},
    {"name": "Scotland",        "code": "SCO", "elo_name": "Scotland"},
    {"name": "Turkey",          "code": "TUR", "elo_name": "Turkey"},
    {"name": "Ukraine",         "code": "UKR", "elo_name": "Ukraine"},
    # Africa (9 teams)
    {"name": "Morocco",         "code": "MAR", "elo_name": "Morocco"},
    {"name": "Senegal",         "code": "SEN", "elo_name": "Senegal"},
    {"name": "Nigeria",         "code": "NGA", "elo_name": "Nigeria"},
    {"name": "Egypt",           "code": "EGY", "elo_name": "Egypt"},
    {"name": "South Africa",    "code": "RSA", "elo_name": "South Africa"},
    {"name": "Côte d'Ivoire",   "code": "CIV", "elo_name": "Ivory Coast"},
    {"name": "Ghana",           "code": "GHA", "elo_name": "Ghana"},
    {"name": "Cameroon",        "code": "CMR", "elo_name": "Cameroon"},
    {"name": "Tunisia",         "code": "TUN", "elo_name": "Tunisia"},
    # Asia (8 teams)
    {"name": "Japan",           "code": "JPN", "elo_name": "Japan"},
    {"name": "South Korea",     "code": "KOR", "elo_name": "Korea Republic"},
    {"name": "Iran",            "code": "IRN", "elo_name": "Iran"},
    {"name": "Australia",       "code": "AUS", "elo_name": "Australia"},
    {"name": "Saudi Arabia",    "code": "KSA", "elo_name": "Saudi Arabia"},
    {"name": "Qatar",           "code": "QAT", "elo_name": "Qatar"},
    {"name": "Uzbekistan",      "code": "UZB", "elo_name": "Uzbekistan"},
    {"name": "Jordan",          "code": "JOR", "elo_name": "Jordan"},
    # Oceania (1)
    {"name": "New Zealand",     "code": "NZL", "elo_name": "New Zealand"},
    # CONCACAF additional
    {"name": "Jamaica",         "code": "JAM", "elo_name": "Jamaica"},
    {"name": "Cuba",            "code": "CUB", "elo_name": "Cuba"},
    # Remaining playoff/qualified
    {"name": "Paraguay",        "code": "PAR", "elo_name": "Paraguay"},
    {"name": "Bolivia",         "code": "BOL", "elo_name": "Bolivia"},
]


def seed_leagues():
    """Seed all domestic leagues used as stat sources."""
    print("\n[seed] Seeding leagues...")
    league_ids = {}
    for name, cfg in LEAGUES.items():
        lid = upsert_league(
            name=name,
            country=cfg.get("country"),
            fotmob_id=cfg.get("fotmob_id"),
        )
        league_ids[name] = lid
        print(f"  League: {name} (id={lid})")
    return league_ids


def seed_ucl_teams(league_ids, elo_ratings):
    """Fetch all UCL teams from FotMob and seed into DB."""
    print("\n[seed] Fetching UCL teams from FotMob...")

    # Create a virtual "UCL" league for the competition
    ucl_league_id = upsert_league(
        name="Champions League",
        country="Europe",
        fotmob_id=42,
    )

    try:
        ucl_teams = fetch_ucl_teams()
        print(f"  Found {len(ucl_teams)} UCL teams from FotMob")
    except Exception as e:
        print(f"  [warn] FotMob UCL teams fetch failed: {e}")
        ucl_teams = []

    team_ids = {}
    for t in ucl_teams:
        elo = get_team_elo(t["name"], elo_ratings)
        team_id = upsert_team(
            name=t["name"],
            code=t.get("code", t["name"][:3].upper()),
            league_id=ucl_league_id,
            fotmob_id=t["fotmob_id"],
            elo_rating=elo,
        )
        team_ids[t["fotmob_id"]] = team_id
        print(f"  UCL Team: {t['name']} (fotmob={t['fotmob_id']}, elo={elo})")

    return team_ids, ucl_league_id


def seed_domestic_teams(league_ids, elo_ratings):
    """Seed all teams from domestic leagues into DB for stat lookup."""
    print("\n[seed] Seeding domestic league teams...")
    for league_name, league_id in league_ids.items():
        try:
            table = fetch_league_table(league_name)
            rows = list(table["all"].values())
            for row in rows:
                fotmob_id = row.get("id")
                name = row.get("name", "")
                short = row.get("shortName", name[:3].upper())
                if not name:
                    continue
                elo = get_team_elo(name, elo_ratings)
                upsert_team(
                    name=name,
                    code=short[:6],
                    league_id=league_id,
                    fotmob_id=fotmob_id,
                    elo_rating=elo,
                )
            print(f"  {league_name}: {len(rows)} teams")
        except Exception as e:
            print(f"  [warn] {league_name} failed: {e}")


def seed_competitions(league_ids):
    """Seed UCL and World Cup as competitions."""
    print("\n[seed] Seeding competitions...")
    ucl_id = upsert_competition(
        name="UEFA Champions League",
        comp_type="ucl",
        season="2025/26",
        scoring_system="ucl",
        fotmob_id=42,
    )
    print(f"  UCL competition id={ucl_id}")

    wc_id = upsert_competition(
        name="FIFA World Cup",
        comp_type="worldcup",
        season="2026",
        scoring_system="worldcup",
        fotmob_id=None,
    )
    print(f"  World Cup competition id={wc_id}")
    return ucl_id, wc_id


def seed_world_cup_teams(wc_competition_id, elo_ratings):
    """Seed World Cup national teams."""
    print("\n[seed] Seeding World Cup national teams...")

    # Create an "International" league for national teams
    intl_league_id = upsert_league(
        name="International",
        country="World",
        fotmob_id=None,
    )

    for t in WC_TEAMS:
        elo = get_team_elo(t["elo_name"], elo_ratings)
        upsert_team(
            name=t["name"],
            code=t["code"],
            league_id=intl_league_id,
            elo_rating=elo,
        )

    print(f"  Seeded {len(WC_TEAMS)} national teams")


def seed_ucl_fixtures(ucl_competition_id):
    """Fetch UCL knockout fixtures from FotMob and seed into DB."""
    print("\n[seed] Fetching UCL fixtures from FotMob...")
    try:
        fixtures = fetch_ucl_fixtures()
        print(f"  Found {len(fixtures)} UCL fixtures")
        seeded = 0
        for f in fixtures:
            home = get_team_by_fotmob(f["home_fotmob_id"])
            away = get_team_by_fotmob(f["away_fotmob_id"])
            if not home or not away:
                continue
            upsert_fixture(
                competition_id=ucl_competition_id,
                home_team_id=home["id"],
                away_team_id=away["id"],
                match_date=f.get("date", ""),
                matchday=f.get("stage", ""),
                status=f.get("status", "scheduled"),
                home_score=f.get("home_score"),
                away_score=f.get("away_score"),
                external_id=str(f.get("match_id", "")),
            )
            seeded += 1
        print(f"  Seeded {seeded} UCL fixtures")
    except Exception as e:
        print(f"  [warn] UCL fixtures failed: {e}")


def main():
    print("=" * 50)
    print("xPts Engine — Database Seeder")
    print("=" * 50)

    # Init DB schema
    init_db()

    # Fetch Elo ratings once (reused across all teams)
    print("\n[seed] Fetching Elo ratings from ClubElo...")
    try:
        elo_ratings = fetch_elo_ratings()
        print(f"  Loaded {len(elo_ratings)} Elo ratings")
    except Exception as e:
        print(f"  [warn] Elo fetch failed: {e}. Continuing without Elo.")
        elo_ratings = {}

    # Seed leagues
    league_ids = seed_leagues()

    # Seed competitions
    ucl_id, wc_id = seed_competitions(league_ids)

    # Seed UCL teams (from FotMob bracket + table)
    team_ids, ucl_league_id = seed_ucl_teams(league_ids, elo_ratings)

    # Seed domestic teams (for stat lookup)
    seed_domestic_teams(league_ids, elo_ratings)

    # Seed World Cup national teams
    seed_world_cup_teams(wc_id, elo_ratings)

    # Seed UCL fixtures
    seed_ucl_fixtures(ucl_id)

    print("\n" + "=" * 50)
    print("Seeding complete.")
    print("=" * 50)


if __name__ == "__main__":
    main()
