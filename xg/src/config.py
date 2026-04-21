"""
Scoring systems, league configurations, and model constants.
All tunable parameters live here — never bury them in code.
"""

# ── Scoring Systems ────────────────────────────────────────────────────

SCORING = {
    "ucl": {
        "appearance":           {"GK": 1, "DEF": 1, "MID": 1, "FWD": 1},
        "minutes_60":           {"GK": 1, "DEF": 1, "MID": 1, "FWD": 1},
        "goal":                 {"GK": 6, "DEF": 6, "MID": 5, "FWD": 4},
        "assist":               {"GK": 3, "DEF": 3, "MID": 3, "FWD": 3},
        "clean_sheet":          {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0},
        "goals_conceded_every": 2,
        "goals_conceded_pts":   {"GK": -1, "DEF": -1, "MID": 0, "FWD": 0},
        "saves_every":          3,
        "saves_pts":            1,
        "penalty_save":         5,
        "penalty_won":          2,
        "penalty_miss":         -2,
        "penalty_conceded":     -1,
        "goal_outside_box":     1,
        "recoveries_every":     3,
        "recoveries_pts":       1,
        "potm":                 3,
        "yellow_card":          -1,
        "red_card":             -3,
        "own_goal":             -2,
        "captain_multiplier":   2,
    },
    "worldcup": {
        "appearance":           {"GK": 1, "DEF": 1, "MID": 1, "FWD": 1},
        "minutes_60":           {"GK": 2, "DEF": 2, "MID": 2, "FWD": 2},
        "goal":                 {"GK": 6, "DEF": 6, "MID": 5, "FWD": 4},
        "assist":               {"GK": 3, "DEF": 3, "MID": 3, "FWD": 3},
        "clean_sheet":          {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0},
        "goals_conceded_every": 2,
        "goals_conceded_pts":   {"GK": -1, "DEF": -1, "MID": 0, "FWD": 0},
        "saves_every":          3,
        "saves_pts":            1,
        "penalty_save":         5,
        "penalty_won":          2,
        "penalty_miss":         -2,
        "penalty_conceded":     -1,
        "goal_outside_box":     0,   # not in WC Fantasy
        "recoveries_every":     0,   # not in WC Fantasy (0 = disabled)
        "recoveries_pts":       0,
        "potm":                 0,   # not in WC Fantasy
        "yellow_card":          -1,
        "red_card":             -3,
        "own_goal":             -2,
        "captain_multiplier":   2,
    },
}

# ── FotMob Configuration ──────────────────────────────────────────────

FOTMOB_BASE = "https://www.fotmob.com"
FOTMOB_NEXT = "https://www.fotmob.com/_next/data"
FOTMOB_STATS = "https://data.fotmob.com/stats"
FOTMOB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# ── League Registry ───────────────────────────────────────────────────
# fotmob_id and season_id change each season — update before each campaign.

LEAGUES = {
    "Premier League":   {"fotmob_id": 47,  "slug": "premier-league",  "season_id": 27110, "country": "England",     "fbref_path": "9/Premier-League-Stats"},
    "La Liga":          {"fotmob_id": 87,  "slug": "laliga",          "season_id": 27233, "country": "Spain",       "fbref_path": "12/La-Liga-Stats"},
    "Bundesliga":       {"fotmob_id": 54,  "slug": "bundesliga",      "season_id": 26891, "country": "Germany",     "fbref_path": "20/Bundesliga-Stats"},
    "Serie A":          {"fotmob_id": 55,  "slug": "serie-a",         "season_id": 27044, "country": "Italy",       "fbref_path": "11/Serie-A-Stats"},
    "Ligue 1":          {"fotmob_id": 53,  "slug": "ligue-1",         "season_id": 27212, "country": "France",      "fbref_path": "13/Ligue-1-Stats"},
    "Liga Portugal":    {"fotmob_id": 61,  "slug": "liga-portugal",   "season_id": 27181, "country": "Portugal",    "fbref_path": "32/Primeira-Liga-Stats"},
    "Eredivisie":       {"fotmob_id": 57,  "slug": "eredivisie",      "season_id": 27131, "country": "Netherlands", "fbref_path": "23/Eredivisie-Stats"},
    "Scottish PL":      {"fotmob_id": 62,  "slug": "scottish-pl",     "season_id": 27095, "country": "Scotland",    "fbref_path": "40/Scottish-Premiership-Stats"},
    "Super Lig":        {"fotmob_id": 71,  "slug": "super-lig",       "season_id": 27164, "country": "Turkey",      "fbref_path": "26/Super-Lig-Stats"},
    "Belgian Pro":      {"fotmob_id": 65,  "slug": "belgian-pro",     "season_id": 27069, "country": "Belgium",     "fbref_path": "37/Belgian-Pro-League-Stats"},
}

UCL_FOTMOB_ID = 42

# ── FBref Configuration ───────────────────────────────────────────────

FBREF_BASE = "https://fbref.com/en/comps"
FBREF_DELAY = 4  # seconds between requests (respect rate limits)

# Stat pages to scrape per league
FBREF_STAT_PAGES = [
    "stats",        # standard stats (goals, assists, xG, xA, npxG)
    "shooting",     # shots, shots on target, distance, free kicks
    "passing",      # key passes, assists
    "defense",      # tackles, interceptions, blocks
    "misc",         # cards, recoveries, penalties won/conceded
    "keepers",      # saves, clean sheets, penalty saves
]

# ── Model Parameters ──────────────────────────────────────────────────

# Weighting for rolling stats
STAT_WEIGHTS = {
    "last_season":      0.30,
    "current_season":   0.30,
    "recent_form":      0.40,  # last 6 matches
}

# Poisson clean sheet model blend
CS_MODEL = {
    "fixture_bucket_weight":  0.70,
    "historical_rate_weight": 0.30,
}

# Fixture adjustment range (opponent factor can boost/reduce by this %)
FIXTURE_ADJUSTMENT_MAX = 0.30

# Penalty xG (average conversion probability)
PENALTY_XG = 0.76

# ── Elo Configuration ─────────────────────────────────────────────────

CLUBELO_URL = "http://api.clubelo.com"

# ── football-data.org ──────────────────────────────────────────────────

FOOTBALLDATA_BASE = "https://api.football-data.org/v4"
# Set FOOTBALLDATA_API_KEY env var or it will be empty (limited access)

# ── Cache TTLs ─────────────────────────────────────────────────────────

CACHE_TTL = {
    "fotmob_build_id":  3600,      # 1 hour
    "fotmob_stats":     6 * 3600,  # 6 hours
    "fbref_stats":      24 * 3600, # 24 hours (slow source, cache longer)
    "elo_ratings":      7 * 86400, # 7 days
    "fixtures":         12 * 3600, # 12 hours
}

# ── Database ───────────────────────────────────────────────────────────

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "db", "xpts.db")
