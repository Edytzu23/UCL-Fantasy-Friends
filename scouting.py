"""
Pre-match scouting: domestic league xG stats for UCL matchup analysis.
Data sources: FotMob (primary), FBRef (fallback planned).
"""

import requests
import json
import re
import time
import threading
import os
from concurrent.futures import ThreadPoolExecutor

# ── FotMob configuration ────────────────────────────────────────────

FOTMOB_NEXT   = "https://www.fotmob.com/_next/data"
FOTMOB_STATS  = "https://data.fotmob.com/stats"
FOTMOB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
}
_build_id = None
_build_id_ts = 0
_build_id_lock = threading.Lock()

# ── League & team registry ──────────────────────────────────────────

LEAGUES = {
    "EPL":       {"id": 47, "slug": "premier-league", "season_id": 27110, "name": "Premier League"},
    "La Liga":   {"id": 87, "slug": "laliga",         "season_id": 27233, "name": "La Liga"},
    "Bundesliga":{"id": 54, "slug": "bundesliga",     "season_id": 26891, "name": "Bundesliga"},
    "Ligue 1":  {"id": 53, "slug": "ligue-1",        "season_id": 27212, "name": "Ligue 1"},
    "Liga PT":   {"id": 61, "slug": "liga-portugal",  "season_id": 27181, "name": "Liga Portugal"},
}

SCOUTING_TEAMS = {
    "LIV": {"name": "Liverpool",            "fotmob_id": 8650,  "league": "EPL"},
    "ARS": {"name": "Arsenal",              "fotmob_id": 9825,  "league": "EPL"},
    "PSG": {"name": "Paris Saint-Germain",  "fotmob_id": 9847,  "league": "Ligue 1"},
    "BAY": {"name": "Bayern Munich",        "fotmob_id": 9823,  "league": "Bundesliga"},
    "RMA": {"name": "Real Madrid",          "fotmob_id": 8633,  "league": "La Liga"},
    "BAR": {"name": "Barcelona",            "fotmob_id": 8634,  "league": "La Liga"},
    "ATM": {"name": "Atletico Madrid",      "fotmob_id": 9906,  "league": "La Liga"},
    "SPO": {"name": "Sporting CP",          "fotmob_id": 9768,  "league": "Liga PT"},
}

QF_MATCHUPS = [
    ("LIV", "PSG"),
    ("BAY", "RMA"),
    ("BAR", "ATM"),
    ("ARS", "SPO"),
]

# ── Caching ─────────────────────────────────────────────────────────

SCOUTING_TTL = 6 * 3600  # 6 hours
_scouting_cache = {}      # team_code -> {"data": dict, "ts": float}
_league_cache   = {}      # league_key -> {"data": dict, "ts": float}
_cache_lock = threading.Lock()

# ── Build ID management ─────────────────────────────────────────────

def _get_build_id():
    """Fetch FotMob's Next.js buildId (changes with deployments)."""
    global _build_id, _build_id_ts
    with _build_id_lock:
        if _build_id and (time.time() - _build_id_ts) < 3600:
            return _build_id
        try:
            r = requests.get("https://www.fotmob.com/",
                             headers=FOTMOB_HEADERS, timeout=10)
            m = re.search(r'"buildId":"([^"]+)"', r.text)
            if m:
                _build_id = m.group(1)
                _build_id_ts = time.time()
                return _build_id
        except Exception as e:
            print(f"[scouting] buildId fetch error: {e}")
        return _build_id  # return stale if available


# ── FotMob data fetching ────────────────────────────────────────────

def _fotmob_get(url, timeout=12):
    """GET with FotMob headers."""
    r = requests.get(url, headers=FOTMOB_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_league_table(league_key):
    """Fetch league table with all/home/away/xg/form splits."""
    now = time.time()
    with _cache_lock:
        cached = _league_cache.get(league_key)
        if cached and (now - cached["ts"]) < SCOUTING_TTL:
            return cached["data"]

    league = LEAGUES[league_key]
    bid = _get_build_id()
    if not bid:
        raise RuntimeError("Cannot fetch FotMob buildId")

    url = f"{FOTMOB_NEXT}/{bid}/leagues/{league['id']}/overview/{league['slug']}.json"
    data = _fotmob_get(url)
    table_raw = data["pageProps"]["table"][0]["data"]["table"]

    # Index rows by FotMob team ID for fast lookup
    result = {}
    for split in ("all", "home", "away", "form", "xg"):
        rows = table_raw.get(split, [])
        result[split] = {row["id"]: row for row in rows}

    with _cache_lock:
        _league_cache[league_key] = {"data": result, "ts": now}
    return result


def fetch_player_stats(league_key, stat_name):
    """Fetch player/team stat leaderboard from FotMob CDN."""
    league = LEAGUES[league_key]
    url = f"{FOTMOB_STATS}/{league['id']}/season/{league['season_id']}/{stat_name}.json"
    data = _fotmob_get(url)
    entries = []
    for tl in data.get("TopLists", []):
        entries.extend(tl.get("StatList", []))
    return entries


# ── Stat computation ────────────────────────────────────────────────

def _parse_scores(scores_str):
    """Parse '50-42' into (goals_for, goals_against)."""
    parts = scores_str.split("-")
    return int(parts[0]), int(parts[1])


def _build_split_stats(row, xg_row=None):
    """Build stat dict from a table row + optional xG row."""
    gf, ga = _parse_scores(row.get("scoresStr", "0-0"))
    stats = {
        "matches":     row.get("played", 0),
        "goals":       gf,
        "conceded":    ga,
        "wins":        row.get("wins", 0),
        "draws":       row.get("draws", 0),
        "losses":      row.get("losses", 0),
    }
    if xg_row:
        stats["xG"]  = round(xg_row.get("xg", 0), 1)
        stats["xGA"] = round(xg_row.get("xgConceded", 0), 1)
    return stats


def compute_team_stats(league_key, fotmob_id):
    """Compute full team stats from league table data."""
    table = fetch_league_table(league_key)
    all_row  = table["all"].get(fotmob_id, {})
    home_row = table["home"].get(fotmob_id, {})
    away_row = table["away"].get(fotmob_id, {})
    form_row = table["form"].get(fotmob_id, {})
    xg_row   = table["xg"].get(fotmob_id, {})

    # Season totals (all + xG)
    season = _build_split_stats(all_row, xg_row)

    # Clean sheets from CDN (fetched separately)
    league = LEAGUES[league_key]
    try:
        cs_data = fetch_player_stats(league_key, "clean_sheet_team")
        cs_entry = next((e for e in cs_data if e.get("TeamId") == fotmob_id), None)
        season["cleanSheets"] = int(cs_entry["StatValue"]) if cs_entry else 0
    except Exception:
        season["cleanSheets"] = 0

    # Home stats (no per-split xG from table, derive from season)
    home = _build_split_stats(home_row)
    home["cleanSheets"] = None  # not available per-split from table

    # Away stats
    away = _build_split_stats(away_row)
    away["cleanSheets"] = None

    # Last 5 form
    last5 = _build_split_stats(form_row)
    last5["cleanSheets"] = None

    return {
        "season": season,
        "home":   home,
        "away":   away,
        "last5":  last5,
    }


def compute_top_players(league_key, fotmob_id, top_n=15):
    """Get top players for a team from FotMob xG/xA leaderboards."""
    players = {}  # player_id -> dict

    # Fetch xG
    try:
        xg_list = fetch_player_stats(league_key, "expected_goals")
        for p in xg_list:
            if p.get("TeamId") == fotmob_id:
                pid = p["ParticiantId"]
                players[pid] = {
                    "name":     p["ParticipantName"],
                    "position": _pos_label(p.get("Positions", [])),
                    "teamId":   fotmob_id,
                    "games":    p.get("MatchesPlayed", 0),
                    "minutes":  p.get("MinutesPlayed", 0),
                    "goals":    int(p.get("SubStatValue", 0)),
                    "xG":       round(p.get("StatValue", 0), 2),
                    "assists":  0,
                    "xA":       0,
                }
    except Exception as e:
        print(f"[scouting] xG fetch error: {e}")

    # Fetch xA
    try:
        xa_list = fetch_player_stats(league_key, "expected_assists")
        for p in xa_list:
            if p.get("TeamId") == fotmob_id:
                pid = p["ParticiantId"]
                if pid in players:
                    players[pid]["assists"] = int(p.get("SubStatValue", 0))
                    players[pid]["xA"]      = round(p.get("StatValue", 0), 2)
                else:
                    players[pid] = {
                        "name":     p["ParticipantName"],
                        "position": _pos_label(p.get("Positions", [])),
                        "teamId":   fotmob_id,
                        "games":    p.get("MatchesPlayed", 0),
                        "minutes":  p.get("MinutesPlayed", 0),
                        "goals":    0,
                        "xG":       0,
                        "assists":  int(p.get("SubStatValue", 0)),
                        "xA":       round(p.get("StatValue", 0), 2),
                    }
    except Exception as e:
        print(f"[scouting] xA fetch error: {e}")

    # Compute per90 and sort by xG+xA
    result = list(players.values())
    for p in result:
        mins = p.get("minutes", 0)
        if mins and mins > 0:
            p["per90"] = {
                "xG": round(p["xG"] / (mins / 90), 2),
                "xA": round(p["xA"] / (mins / 90), 2),
            }
        else:
            p["per90"] = {"xG": 0, "xA": 0}

    result.sort(key=lambda x: x["xG"] + x["xA"], reverse=True)
    return result[:top_n]


def _pos_label(positions):
    """Convert FotMob position IDs to labels."""
    # FotMob position IDs: 115=FWD, 85=MID, 55=DEF, 25=GK (approximate)
    pos_map = {115: "FWD", 85: "MID", 55: "DEF", 25: "GK",
               125: "FWD", 95: "MID", 65: "DEF", 35: "GK"}
    for pid in positions:
        if pid in pos_map:
            return pos_map[pid]
        # Rough ranges
        if pid >= 100: return "FWD"
        if pid >= 70:  return "MID"
        if pid >= 40:  return "DEF"
        return "GK"
    return "?"


# ── Public API functions ────────────────────────────────────────────

def get_team_scouting(team_code):
    """Get full scouting data for one team."""
    team_code = team_code.upper()
    if team_code not in SCOUTING_TEAMS:
        return {"error": f"Unknown team code: {team_code}"}

    now = time.time()
    with _cache_lock:
        cached = _scouting_cache.get(team_code)
        if cached and (now - cached["ts"]) < SCOUTING_TTL:
            return cached["data"]

    team = SCOUTING_TEAMS[team_code]
    league_key = team["league"]
    fotmob_id  = team["fotmob_id"]
    league_info = LEAGUES[league_key]

    try:
        team_stats  = compute_team_stats(league_key, fotmob_id)
        top_players = compute_top_players(league_key, fotmob_id)
        source = "fotmob"
    except Exception as e:
        print(f"[scouting] FotMob error for {team_code}: {e}")
        # Return stale cache if available
        with _cache_lock:
            if cached:
                cached["data"]["stale"] = True
                return cached["data"]
        return {"error": f"Failed to fetch data for {team_code}: {str(e)}"}

    result = {
        "code":       team_code,
        "name":       team["name"],
        "league":     league_info["name"],
        "source":     source,
        "teamStats":  team_stats,
        "topPlayers": top_players,
        "fetchedAt":  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    with _cache_lock:
        _scouting_cache[team_code] = {"data": result, "ts": now}
    return result


def get_scouting_matchup(home_code, away_code):
    """Get scouting data for a specific matchup."""
    home = get_team_scouting(home_code)
    away = get_team_scouting(away_code)
    if "error" in home or "error" in away:
        return {"error": home.get("error") or away.get("error"),
                "home": home, "away": away}
    return {
        "home": home,
        "away": away,
        "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def get_all_matchups(round_name="QF"):
    """Get all matchups for a round."""
    matchups_def = QF_MATCHUPS  # TODO: extend for SF/Final

    # Fetch all teams in parallel
    codes = set()
    for h, a in matchups_def:
        codes.add(h)
        codes.add(a)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {code: pool.submit(get_team_scouting, code) for code in codes}
        results = {code: f.result() for code, f in futures.items()}

    matchups = []
    for h, a in matchups_def:
        matchups.append({
            "home": results.get(h, {"error": "not found"}),
            "away": results.get(a, {"error": "not found"}),
        })

    return {
        "round":     round_name,
        "matchups":  matchups,
        "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ── UCL Bracket (via FotMob) ────────────────────────────────────────

UCL_LEAGUE_ID = 42
_bracket_cache = {"data": None, "ts": 0}
BRACKET_TTL = 3600  # 1 hour

# Map FotMob stage names to display names
_STAGE_NAMES = {
    "playoff":  "Playoffs",
    "1/8":      "Round of 16",
    "1/4":      "Quarter-Finals",
    "1/2":      "Semi-Finals",
    "final":    "Final",
}


def fetch_ucl_bracket():
    """Fetch full UCL knockout bracket from FotMob."""
    now = time.time()
    if _bracket_cache["data"] and (now - _bracket_cache["ts"]) < BRACKET_TTL:
        return _bracket_cache["data"]

    try:
        bid = _get_build_id()
        if not bid:
            raise RuntimeError("Cannot fetch FotMob buildId")

        url = f"{FOTMOB_NEXT}/{bid}/leagues/{UCL_LEAGUE_ID}/overview/champions-league.json"
        data = _fotmob_get(url)
        playoff = data["pageProps"]["playoff"]

        rounds = []
        for rnd in playoff.get("rounds", []):
            stage = rnd.get("stage", "")
            display_name = _STAGE_NAMES.get(stage, stage)
            ties = []

            for mu in rnd.get("matchups", []):
                tie = _build_fotmob_tie(mu)
                ties.append(tie)

            rounds.append({
                "name": display_name,
                "stage": stage,
                "ties": ties,
            })

        result = {
            "season":    "2025/26",
            "rounds":    rounds,
            "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        _bracket_cache["data"] = result
        _bracket_cache["ts"]   = now
        return result

    except Exception as e:
        print(f"[scouting] Bracket fetch error: {e}")
        if _bracket_cache["data"]:
            return {**_bracket_cache["data"], "stale": True}
        return {"error": str(e)}


def _build_fotmob_tie(matchup):
    """Build a tie object from a FotMob playoff matchup."""
    home_name = matchup.get("homeTeam", "TBD")
    away_name = matchup.get("awayTeam", "TBD")
    home_short = matchup.get("homeTeamShortName", "")
    away_short = matchup.get("awayTeamShortName", "")

    # Build leg objects from matches array
    matches = matchup.get("matches", [])
    legs = []
    for m in matches:
        status_obj = m.get("status", {})
        if isinstance(status_obj, dict):
            finished = status_obj.get("finished", False)
            started = status_obj.get("started", False)
            reason = status_obj.get("reason", {})
            status_str = reason.get("short", "UPCOMING") if finished else ("LIVE" if started else "UPCOMING")
            utc_time = status_obj.get("utcTime")
        else:
            finished = False
            status_str = "UPCOMING"
            utc_time = None

        score_str = m.get("status", {}).get("scoreStr") if isinstance(m.get("status"), dict) else None
        home_team = m.get("homeTeam", {})
        away_team = m.get("awayTeam", {})

        legs.append({
            "matchId":  m.get("id"),
            "date":     utc_time,
            "status":   status_str,
            "score":    score_str,
            "home":     home_team.get("name", home_name),
            "homeCode": home_team.get("shortName", home_short),
            "away":     away_team.get("name", away_name),
            "awayCode": away_team.get("shortName", away_short),
        })

    # Aggregate score
    home_score = matchup.get("homeScore")
    away_score = matchup.get("awayScore")
    aggregate = f"{home_score}-{away_score}" if home_score is not None else None

    # Winner
    winner_id = matchup.get("winner")
    winner_name = None
    if winner_id == matchup.get("homeTeamId"):
        winner_name = home_name
    elif winner_id == matchup.get("awayTeamId"):
        winner_name = away_name

    return {
        "home":      {"name": home_name, "code": home_short, "id": matchup.get("homeTeamId")},
        "away":      {"name": away_name, "code": away_short, "id": matchup.get("awayTeamId")},
        "leg1":      legs[0] if len(legs) > 0 else None,
        "leg2":      legs[1] if len(legs) > 1 else None,
        "aggregate": aggregate,
        "winner":    winner_name,
    }


# ── GitHub cache persistence (reuses main.py pattern) ───────────────

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def save_scouting_cache_local():
    """Save scouting cache to local file."""
    cache_dir = os.path.join(_BASE_DIR, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "scouting.json")
    with _cache_lock:
        payload = {}
        for code, entry in _scouting_cache.items():
            payload[code] = entry["data"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_scouting_cache_local():
    """Load scouting cache from local file on startup."""
    path = os.path.join(_BASE_DIR, "cache", "scouting.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        with _cache_lock:
            for code, data in payload.items():
                _scouting_cache[code] = {"data": data, "ts": 0}  # ts=0 = stale, will refresh
        print(f"[scouting] Loaded {len(payload)} teams from local cache")
    except Exception as e:
        print(f"[scouting] Cache load error: {e}")
