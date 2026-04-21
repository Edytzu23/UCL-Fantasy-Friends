"""
Fixture fetcher using football-data.org free API.
Provides match schedules for UCL and World Cup.
Fallback: use FotMob bracket data for UCL.
"""

import os
import requests
import time
from src.config import FOOTBALLDATA_BASE, CACHE_TTL

_API_KEY = os.environ.get("FOOTBALLDATA_API_KEY", "")

_HEADERS = {
    "X-Auth-Token": _API_KEY,
}

# football-data.org competition codes
COMPETITION_CODES = {
    "ucl": "CL",
    "worldcup": "WC",
}

_fixture_cache = {}
_cache_lock = __import__("threading").Lock()


def fetch_fixtures(competition_type, season=None):
    """Fetch fixtures from football-data.org.
    competition_type: 'ucl' or 'worldcup'
    Returns list of match dicts.
    """
    now = time.time()
    cache_key = f"{competition_type}_{season}"
    with _cache_lock:
        cached = _fixture_cache.get(cache_key)
        if cached and (now - cached["ts"]) < CACHE_TTL["fixtures"]:
            return cached["data"]

    code = COMPETITION_CODES.get(competition_type)
    if not code:
        return []

    url = f"{FOOTBALLDATA_BASE}/competitions/{code}/matches"
    if season:
        url += f"?season={season}"

    try:
        r = requests.get(url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[fixtures] football-data.org error: {e}")
        # Return cached data if available
        with _cache_lock:
            if cached:
                return cached["data"]
        return []

    matches = []
    for m in data.get("matches", []):
        home = m.get("homeTeam", {})
        away = m.get("awayTeam", {})
        score = m.get("score", {})
        ft = score.get("fullTime", {})

        status_map = {
            "SCHEDULED": "scheduled",
            "TIMED": "scheduled",
            "IN_PLAY": "live",
            "PAUSED": "live",
            "FINISHED": "finished",
            "POSTPONED": "scheduled",
            "CANCELLED": "finished",
        }

        matches.append({
            "external_id":    str(m.get("id", "")),
            "matchday":       str(m.get("matchday", "")),
            "date":           m.get("utcDate", ""),
            "status":         status_map.get(m.get("status", ""), "scheduled"),
            "home_name":      home.get("name", ""),
            "home_code":      home.get("tla", ""),
            "away_name":      away.get("name", ""),
            "away_code":      away.get("tla", ""),
            "home_score":     ft.get("home"),
            "away_score":     ft.get("away"),
            "stage":          m.get("stage", ""),
            "group":          m.get("group"),
        })

    with _cache_lock:
        _fixture_cache[cache_key] = {"data": matches, "ts": now}

    print(f"[fixtures] Loaded {len(matches)} matches for {competition_type}")
    return matches
