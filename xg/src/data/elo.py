"""
ClubElo data fetcher.
Downloads Elo ratings for all clubs from clubelo.com (free, no API key).
Used for competition_factor and opponent_factor in xPts adjustment.
"""

import requests
import csv
import io
import time
from datetime import date
from src.config import CLUBELO_URL, CACHE_TTL

_elo_cache = {}
_elo_cache_ts = 0


def fetch_elo_ratings(target_date=None):
    """Fetch Elo ratings for all clubs on a given date.
    Returns dict: team_name -> elo_rating (float).
    ClubElo API: http://api.clubelo.com/{YYYY-MM-DD}
    """
    global _elo_cache, _elo_cache_ts
    now = time.time()
    if _elo_cache and (now - _elo_cache_ts) < CACHE_TTL["elo_ratings"]:
        return _elo_cache

    if target_date is None:
        target_date = date.today().isoformat()

    url = f"{CLUBELO_URL}/{target_date}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()

    ratings = {}
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        name = row.get("Club", "").strip()
        elo = row.get("Elo", "0")
        if name:
            try:
                ratings[name] = float(elo)
            except ValueError:
                pass

    _elo_cache = ratings
    _elo_cache_ts = now
    print(f"[elo] Loaded {len(ratings)} club ratings for {target_date}")
    return ratings


# ── Name matching helpers ──────────────────────────────────────────────

# ClubElo uses different names than FotMob. Map the most common mismatches.
_NAME_ALIASES = {
    # FotMob name -> ClubElo name
    "Paris Saint-Germain":  "Paris SG",
    "Bayern Munich":        "Bayern",
    "Atletico Madrid":      "Atlético",
    "Inter Milan":          "Inter",
    "AC Milan":             "Milan",
    "Sporting CP":          "Sporting",
    "RB Leipzig":           "Leipzig",
    "Bayer Leverkusen":     "Leverkusen",
    "Borussia Dortmund":    "Dortmund",
    "Club Brugge":          "Brugge",
    "Red Bull Salzburg":    "Salzburg",
    "Shakhtar Donetsk":     "Shakhtar",
    "Dinamo Zagreb":        "Din. Zagreb",
    "Slovan Bratislava":    "Sl. Bratislava",
    "Young Boys":           "Young Boys",
    "Red Star Belgrade":    "Red Star",
    "Sturm Graz":           "Sturm",
    "Girona FC":            "Girona",
    "Aston Villa":          "Aston Villa",
    "Newcastle United":     "Newcastle",
    "Manchester City":      "Man City",
    "Manchester United":    "Man United",
    "Tottenham Hotspur":    "Tottenham",
    "Wolverhampton Wanderers": "Wolves",
    "Nottingham Forest":    "Nottingham",
    "West Ham United":      "West Ham",
    "Sheffield United":     "Sheffield Utd",
    "Real Sociedad":        "Sociedad",
    "Athletic Club":        "Athletic",
    "Real Betis":           "Betis",
    "Rayo Vallecano":       "Vallecano",
    "Deportivo Alavés":     "Alavés",
    "Celta Vigo":           "Celta",
    "Borussia M'gladbach":  "Gladbach",
    "Eintracht Frankfurt":  "Frankfurt",
    "VfB Stuttgart":        "Stuttgart",
    "TSG Hoffenheim":       "Hoffenheim",
    "1. FC Heidenheim":     "Heidenheim",
    "1. FC Union Berlin":   "Union Berlin",
    "1. FSV Mainz 05":      "Mainz",
    "SV Darmstadt 98":      "Darmstadt",
    "FC Augsburg":          "Augsburg",
    "VfL Wolfsburg":        "Wolfsburg",
    "Werder Bremen":        "Bremen",
    "SC Freiburg":          "Freiburg",
    "VfL Bochum":           "Bochum",
    "1. FC Köln":           "Köln",
    "AS Monaco":            "Monaco",
    "Olympique Lyonnais":   "Lyon",
    "Olympique Marseille":  "Marseille",
    "LOSC Lille":           "Lille",
    "Stade Rennais":        "Rennes",
    "RC Strasbourg":        "Strasbourg",
    "OGC Nice":             "Nice",
    "FC Nantes":            "Nantes",
    "Stade Brestois":       "Brest",
    "Montpellier HSC":      "Montpellier",
    "RC Lens":              "Lens",
    "Toulouse FC":          "Toulouse",
}


def get_team_elo(team_name, ratings=None):
    """Get Elo for a team, handling name mismatches.
    Returns float or None if not found.
    """
    if ratings is None:
        ratings = fetch_elo_ratings()

    # Direct match
    if team_name in ratings:
        return ratings[team_name]

    # Alias match
    alias = _NAME_ALIASES.get(team_name)
    if alias and alias in ratings:
        return ratings[alias]

    # Fuzzy: try substring match (last resort)
    name_lower = team_name.lower()
    for elo_name, elo_val in ratings.items():
        if name_lower in elo_name.lower() or elo_name.lower() in name_lower:
            return elo_val

    return None


def compute_league_avg_elo(league_teams, ratings=None):
    """Compute average Elo for a list of team names."""
    if ratings is None:
        ratings = fetch_elo_ratings()

    elos = []
    for name in league_teams:
        elo = get_team_elo(name, ratings)
        if elo:
            elos.append(elo)

    return sum(elos) / len(elos) if elos else None
