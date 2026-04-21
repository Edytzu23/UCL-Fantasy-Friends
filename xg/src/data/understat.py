"""
Understat data fetcher.

Provides per-player season stats (xG, npxG, xA, goals, assists, minutes, cards)
for the top 5 European leagues via Understat's internal AJAX API.

League coverage: EPL, La Liga, Bundesliga, Serie A, Ligue 1.
No API key required.
"""

import requests

_LEAGUE_MAP = {
    "Premier League": ("EPL",       "2024"),
    "La Liga":        ("La_liga",   "2024"),
    "Bundesliga":     ("Bundesliga","2024"),
    "Serie A":        ("Serie_A",   "2024"),
    "Ligue 1":        ("Ligue_1",   "2024"),
}

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
})
_SESSION_PRIMED = set()


def _prime_session(league_code, season):
    key = (league_code, season)
    if key not in _SESSION_PRIMED:
        _SESSION.headers["Referer"] = f"https://understat.com/league/{league_code}/{season}"
        _SESSION.get(f"https://understat.com/league/{league_code}/{season}", timeout=20)
        _SESSION_PRIMED.add(key)


def fetch_understat_stats(league_name):
    """Return list of player stat dicts for the given league name.

    Each dict has keys:
      player_name, team_name, position,
      goals (int), assists (int), minutes (int), matches (int),
      xg (float), npxg (float), xa (float),
      shots (int), key_passes (int),
      yellow_cards (int), red_cards (int)

    Returns [] if the league is not covered or fetch fails.
    """
    if league_name not in _LEAGUE_MAP:
        return []

    league_code, season = _LEAGUE_MAP[league_name]
    try:
        _prime_session(league_code, season)
        resp = _SESSION.post(
            "https://understat.com/main/getPlayersStats/",
            data={"league": league_code, "season": season},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[understat] {league_name} fetch failed: {e}")
        return []

    raw_players = data.get("players") or []
    out = []
    for p in raw_players:
        try:
            out.append({
                "player_name":  p.get("player_name", ""),
                "team_name":    p.get("team_title", ""),
                "position":     p.get("position", ""),
                "goals":        int(p.get("goals", 0) or 0),
                "assists":      int(p.get("assists", 0) or 0),
                "minutes":      int(p.get("time", 0) or 0),
                "matches":      int(p.get("games", 0) or 0),
                "xg":           float(p.get("xG", 0) or 0),
                "npxg":         float(p.get("npxG", 0) or 0),
                "xa":           float(p.get("xA", 0) or 0),
                "shots":        int(p.get("shots", 0) or 0),
                "key_passes":   int(p.get("key_passes", 0) or 0),
                "yellow_cards": int(p.get("yellow_cards", 0) or 0),
                "red_cards":    int(p.get("red_cards", 0) or 0),
            })
        except (TypeError, ValueError):
            continue

    return out
