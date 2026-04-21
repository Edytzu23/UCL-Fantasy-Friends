"""
FotMob data adapter — ported from FF Dashboard scouting.py.
Fetches xG, xA, goals, assists, minutes, team stats from FotMob.
"""

import requests
import re
import time
import threading
from src.config import (
    FOTMOB_BASE, FOTMOB_NEXT, FOTMOB_STATS, FOTMOB_HEADERS,
    LEAGUES, UCL_FOTMOB_ID, CACHE_TTL,
)

# ── Build ID management ────────────────────────────────────────────────

_build_id = None
_build_id_ts = 0
_build_id_lock = threading.Lock()


def get_build_id():
    """Fetch FotMob's Next.js buildId (changes with deployments)."""
    global _build_id, _build_id_ts
    with _build_id_lock:
        if _build_id and (time.time() - _build_id_ts) < CACHE_TTL["fotmob_build_id"]:
            return _build_id
        try:
            r = requests.get(FOTMOB_BASE + "/", headers=FOTMOB_HEADERS, timeout=10)
            m = re.search(r'"buildId":"([^"]+)"', r.text)
            if m:
                _build_id = m.group(1)
                _build_id_ts = time.time()
                return _build_id
        except Exception as e:
            print(f"[fotmob] buildId fetch error: {e}")
        return _build_id


def _get(url, timeout=12):
    """GET with FotMob headers."""
    r = requests.get(url, headers=FOTMOB_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ── Position mapping ───────────────────────────────────────────────────

_POS_MAP = {115: "FWD", 85: "MID", 55: "DEF", 25: "GK",
            125: "FWD", 95: "MID", 65: "DEF", 35: "GK"}


def _pos_label(positions):
    """Convert FotMob position IDs to labels.
    Observed ranges from live data:
      ≤25  → GK  (e.g. GK = 11)
      26-59 → DEF (e.g. CB=34/36, LB=38, RB=32)
      60-99 → MID (e.g. DM=64, CM=66/73/77, CAM/W=83-87)
      100+  → FWD (e.g. CF=105, LW=115)
    """
    if not positions:
        return "MID"
    pid = positions[0]
    if pid <= 25:  return "GK"
    if pid <= 59:  return "DEF"
    if pid <= 99:  return "MID"
    return "FWD"


# ── League table ───────────────────────────────────────────────────────

_league_cache = {}
_cache_lock = threading.Lock()


def fetch_league_table(league_name):
    """Fetch league table with all/home/away/xg/form splits, indexed by fotmob team id."""
    now = time.time()
    with _cache_lock:
        cached = _league_cache.get(league_name)
        if cached and (now - cached["ts"]) < CACHE_TTL["fotmob_stats"]:
            return cached["data"]

    league = LEAGUES[league_name]
    bid = get_build_id()
    if not bid:
        raise RuntimeError("Cannot fetch FotMob buildId")

    url = f"{FOTMOB_NEXT}/{bid}/leagues/{league['fotmob_id']}/overview/{league['slug']}.json"
    data = _get(url)
    table_raw = data["pageProps"]["table"][0]["data"]["table"]

    result = {}
    for split in ("all", "home", "away", "form", "xg"):
        rows = table_raw.get(split, [])
        result[split] = {row["id"]: row for row in rows}

    with _cache_lock:
        _league_cache[league_name] = {"data": result, "ts": now}
    return result


# ── Player stat leaderboards ──────────────────────────────────────────

def fetch_stat_leaderboard(league_name, stat_name):
    """Fetch player stat leaderboard from FotMob CDN.
    stat_name examples: expected_goals, expected_assists, clean_sheet_team
    """
    league = LEAGUES[league_name]
    url = f"{FOTMOB_STATS}/{league['fotmob_id']}/season/{league['season_id']}/{stat_name}.json"
    data = _get(url)
    entries = []
    for tl in data.get("TopLists", []):
        entries.extend(tl.get("StatList", []))
    return entries


def fetch_penalty_takers(league_name):
    """Identify primary penalty taker per team from goals.json SubStatValue.

    FotMob's goals.json includes penalty goals as SubStatValue for each player.
    For each team, the player with the most penalty goals is the primary taker.

    Returns dict keyed by fotmob_team_id:
      {team_id: {player_name, fotmob_player_id, pen_goals}}
    """
    try:
        entries = fetch_stat_leaderboard(league_name, "goals")
    except Exception as e:
        print(f"[fotmob] penalty takers fetch failed for {league_name}: {e}")
        return {}

    takers = {}
    for e in entries:
        pen_goals = e.get("SubStatValue", 0) or 0
        if pen_goals <= 0:
            continue
        tid = e.get("TeamId")
        if not tid:
            continue
        current = takers.get(tid)
        if not current or pen_goals > current["pen_goals"]:
            takers[tid] = {
                "player_name": e.get("ParticipantName"),
                "fotmob_player_id": e.get("ParticiantId") or e.get("ParticipantId"),
                "pen_goals": pen_goals,
            }
    return takers


# Penalty xG per attempt (Understat / industry-standard conversion expectation).
# Used to derive npxG from FotMob's total xG minus penalty xG.
_PENALTY_XG = 0.76


def fetch_fotmob_leaderboard_player_stats(league_name):
    """Return accurate per-player season totals from FotMob CDN stat leaderboards.

    Unlike fetch_team_players (which scrapes the league team page and hits a scope
    bug producing 56-minute rows for full-season starters), this uses the same CDN
    we rely on for saves/ball_recovery/etc. It joins three leaderboards by
    ParticiantId:
      - expected_goals.json   → xG total + actual goals + minutes + matches
      - expected_assists.json → xA total + actual assists
      - goals.json            → penalty goals (for npxG derivation)

    Returns list of dicts with: fotmob_id, name, team_fotmob_id, position_code,
    matches, minutes, goals, assists, xg, xa, npxg, penalty_goals.
    """
    try:
        xg_entries = fetch_stat_leaderboard(league_name, "expected_goals")
    except Exception as e:
        print(f"[fotmob] expected_goals fetch failed for {league_name}: {e}")
        return []

    try:
        xa_entries = fetch_stat_leaderboard(league_name, "expected_assists")
    except Exception as e:
        print(f"[fotmob] expected_assists fetch failed for {league_name}: {e}")
        xa_entries = []

    try:
        goals_entries = fetch_stat_leaderboard(league_name, "goals")
    except Exception as e:
        print(f"[fotmob] goals fetch failed for {league_name}: {e}")
        goals_entries = []

    def pid(e):
        return e.get("ParticiantId") or e.get("ParticipantId")

    xa_by_pid = {pid(e): e for e in xa_entries if pid(e)}
    pens_by_pid = {pid(e): (e.get("SubStatValue") or 0) for e in goals_entries if pid(e)}

    out = []
    for e in xg_entries:
        fm_pid = pid(e)
        if not fm_pid:
            continue
        xa_e = xa_by_pid.get(fm_pid, {})
        pen_goals = pens_by_pid.get(fm_pid, 0) or 0

        xg = float(e.get("StatValue") or 0)
        goals = int(e.get("SubStatValue") or 0)
        xa = float(xa_e.get("StatValue") or 0)
        assists = int(xa_e.get("SubStatValue") or 0)

        minutes = max(int(e.get("MinutesPlayed") or 0),
                      int(xa_e.get("MinutesPlayed") or 0))
        matches = max(int(e.get("MatchesPlayed") or 0),
                      int(xa_e.get("MatchesPlayed") or 0))

        positions = e.get("Positions") or []
        pos_code = positions[0] if positions else None

        out.append({
            "fotmob_id":       fm_pid,
            "name":            e.get("ParticipantName"),
            "team_fotmob_id":  e.get("TeamId"),
            "position_code":   pos_code,
            "matches":         matches,
            "minutes":         minutes,
            "goals":           goals,
            "assists":         assists,
            "xg":              round(xg, 4),
            "xa":              round(xa, 4),
            "npxg":            round(max(0.0, xg - pen_goals * _PENALTY_XG), 4),
            "penalty_goals":   pen_goals,
        })

    # Also include players present ONLY in xA leaderboard (rare but possible for
    # creative midfielders with zero shots)
    xg_pids = {pid(e) for e in xg_entries if pid(e)}
    for fm_pid, xa_e in xa_by_pid.items():
        if fm_pid in xg_pids:
            continue
        xa = float(xa_e.get("StatValue") or 0)
        assists = int(xa_e.get("SubStatValue") or 0)
        minutes = int(xa_e.get("MinutesPlayed") or 0)
        matches = int(xa_e.get("MatchesPlayed") or 0)
        positions = xa_e.get("Positions") or []
        out.append({
            "fotmob_id":       fm_pid,
            "name":            xa_e.get("ParticipantName"),
            "team_fotmob_id":  xa_e.get("TeamId"),
            "position_code":   positions[0] if positions else None,
            "matches":         matches,
            "minutes":         minutes,
            "goals":           0,
            "assists":         assists,
            "xg":              0.0,
            "xa":              round(xa, 4),
            "npxg":            0.0,
            "penalty_goals":   0,
        })

    return out


# ── Team stats from league table ──────────────────────────────────────

def _parse_scores(scores_str):
    parts = scores_str.split("-")
    return int(parts[0]), int(parts[1])


def fetch_team_stats(league_name, fotmob_team_id):
    """Get team stats from league table: season, home, away, form, xG."""
    table = fetch_league_table(league_name)
    all_row  = table["all"].get(fotmob_team_id, {})
    home_row = table["home"].get(fotmob_team_id, {})
    away_row = table["away"].get(fotmob_team_id, {})
    xg_row   = table["xg"].get(fotmob_team_id, {})

    gf, ga = _parse_scores(all_row.get("scoresStr", "0-0"))
    hgf, hga = _parse_scores(home_row.get("scoresStr", "0-0"))
    agf, aga = _parse_scores(away_row.get("scoresStr", "0-0"))

    # Clean sheets from CDN
    try:
        cs_data = fetch_stat_leaderboard(league_name, "clean_sheet_team")
        cs_entry = next((e for e in cs_data if e.get("TeamId") == fotmob_team_id), None)
        clean_sheets = int(cs_entry["StatValue"]) if cs_entry else 0
    except Exception:
        clean_sheets = 0

    return {
        "matches":      all_row.get("played", 0),
        "wins":         all_row.get("wins", 0),
        "draws":        all_row.get("draws", 0),
        "losses":       all_row.get("losses", 0),
        "goals_for":    gf,
        "goals_against": ga,
        "xg_for":       round(xg_row.get("xg", 0), 2),
        "xg_against":   round(xg_row.get("xgConceded", 0), 2),
        "clean_sheets": clean_sheets,
        "home_matches":      home_row.get("played", 0),
        "home_goals_for":    hgf,
        "home_goals_against": hga,
        "away_matches":      away_row.get("played", 0),
        "away_goals_for":    agf,
        "away_goals_against": aga,
    }


# ── Player stats per team ─────────────────────────────────────────────

def fetch_team_players(league_name, fotmob_team_id, top_n=None):
    """Get all players for a team from FotMob xG/xA leaderboards.
    Returns list of dicts with: name, position, fotmob_id, games, minutes,
    goals, assists, xG, xA, per90_xG, per90_xA.
    If top_n is None, returns all players found.
    """
    players = {}

    # Fetch xG leaderboard
    try:
        xg_list = fetch_stat_leaderboard(league_name, "expected_goals")
        for p in xg_list:
            if p.get("TeamId") == fotmob_team_id:
                pid = p["ParticiantId"]
                players[pid] = {
                    "name":       p["ParticipantName"],
                    "position":   _pos_label(p.get("Positions", [])),
                    "fotmob_id":  pid,
                    "team_fotmob_id": fotmob_team_id,
                    "games":      p.get("MatchesPlayed", 0),
                    "minutes":    p.get("MinutesPlayed", 0),
                    "goals":      int(p.get("SubStatValue", 0)),
                    "xg":         round(p.get("StatValue", 0), 4),
                    "assists":    0,
                    "xa":         0,
                }
    except Exception as e:
        print(f"[fotmob] xG fetch error for team {fotmob_team_id}: {e}")

    # Fetch xA leaderboard and merge
    try:
        xa_list = fetch_stat_leaderboard(league_name, "expected_assists")
        for p in xa_list:
            if p.get("TeamId") == fotmob_team_id:
                pid = p["ParticiantId"]
                if pid in players:
                    players[pid]["assists"] = int(p.get("SubStatValue", 0))
                    players[pid]["xa"]      = round(p.get("StatValue", 0), 4)
                else:
                    players[pid] = {
                        "name":       p["ParticipantName"],
                        "position":   _pos_label(p.get("Positions", [])),
                        "fotmob_id":  pid,
                        "team_fotmob_id": fotmob_team_id,
                        "games":      p.get("MatchesPlayed", 0),
                        "minutes":    p.get("MinutesPlayed", 0),
                        "goals":      0,
                        "xg":         0,
                        "assists":    int(p.get("SubStatValue", 0)),
                        "xa":         round(p.get("StatValue", 0), 4),
                    }
    except Exception as e:
        print(f"[fotmob] xA fetch error for team {fotmob_team_id}: {e}")

    # Compute per90
    result = list(players.values())
    for p in result:
        mins = p.get("minutes", 0)
        if mins and mins > 0:
            p["per90_xg"] = round(p["xg"] / (mins / 90), 4)
            p["per90_xa"] = round(p["xa"] / (mins / 90), 4)
        else:
            p["per90_xg"] = 0
            p["per90_xa"] = 0

    result.sort(key=lambda x: x["xg"] + x["xa"], reverse=True)
    return result[:top_n] if top_n else result


# ── UCL teams from bracket ─────────────────────────────────────────────

def fetch_ucl_teams():
    """Fetch all UCL knockout teams from FotMob bracket.
    Returns list of dicts with: name, code, fotmob_id.
    """
    bid = get_build_id()
    if not bid:
        raise RuntimeError("Cannot fetch FotMob buildId")

    url = f"{FOTMOB_NEXT}/{bid}/leagues/{UCL_FOTMOB_ID}/overview/champions-league.json"
    data = _get(url)

    teams = {}
    # From playoff bracket
    playoff = data.get("pageProps", {}).get("playoff")
    if playoff:
        for rnd in playoff.get("rounds", []):
            for mu in rnd.get("matchups", []):
                for side in ("homeTeam", "awayTeam"):
                    tid = mu.get(side.replace("Team", "TeamId"))
                    name = mu.get(side, "")
                    code = mu.get(side.replace("Team", "TeamShortName"), "")
                    if tid and name:
                        teams[tid] = {"name": name, "code": code, "fotmob_id": tid}

    # From league table (all 36 teams in league phase)
    try:
        table = data["pageProps"]["table"][0]["data"]["table"]
        for row in table.get("all", []):
            tid = row.get("id")
            name = row.get("name", "")
            short = row.get("shortName", "")
            if tid and tid not in teams:
                teams[tid] = {"name": name, "code": short, "fotmob_id": tid}
    except (KeyError, IndexError):
        pass

    return list(teams.values())


def fetch_ucl_fixtures():
    """Fetch UCL knockout fixtures from FotMob bracket.
    Returns list of dicts with match info.
    """
    bid = get_build_id()
    if not bid:
        raise RuntimeError("Cannot fetch FotMob buildId")

    url = f"{FOTMOB_NEXT}/{bid}/leagues/{UCL_FOTMOB_ID}/overview/champions-league.json"
    data = _get(url)
    playoff = data.get("pageProps", {}).get("playoff", {})

    fixtures = []
    for rnd in playoff.get("rounds", []):
        stage = rnd.get("stage", "")
        for mu in rnd.get("matchups", []):
            for match in mu.get("matches", []):
                status_obj = match.get("status", {})
                if isinstance(status_obj, dict):
                    finished = status_obj.get("finished", False)
                    started = status_obj.get("started", False)
                    status = "finished" if finished else ("live" if started else "scheduled")
                    utc_time = status_obj.get("utcTime")
                    score_str = status_obj.get("scoreStr")
                else:
                    status = "scheduled"
                    utc_time = None
                    score_str = None

                home_team = match.get("homeTeam", {})
                away_team = match.get("awayTeam", {})

                home_score, away_score = None, None
                if score_str and "-" in str(score_str):
                    parts = str(score_str).split("-")
                    try:
                        home_score, away_score = int(parts[0].strip()), int(parts[1].strip())
                    except ValueError:
                        pass

                fixtures.append({
                    "stage":          stage,
                    "match_id":       match.get("id"),
                    "date":           utc_time,
                    "status":         status,
                    "home_name":      home_team.get("name", mu.get("homeTeam", "")),
                    "home_fotmob_id": home_team.get("id", mu.get("homeTeamId")),
                    "away_name":      away_team.get("name", mu.get("awayTeam", "")),
                    "away_fotmob_id": away_team.get("id", mu.get("awayTeamId")),
                    "home_score":     home_score,
                    "away_score":     away_score,
                })

    return fixtures


# ── Per-player recent matches (Last-5 Form) ────────────────────────────

def fetch_player_recent_matches(fotmob_player_id, limit=8):
    """Return list of recent per-match performance dicts for a player.

    Source: FotMob Next.js `players/{id}` page → pageProps.data.recentMatches.
    Fields in each dict:
      match_id, match_date (ISO UTC), opponent_fotmob_id, is_home (0/1),
      minutes, goals, assists, yellow_cards, red_cards, rating (float or None),
      clean_sheet (0/1 — player's team conceded 0), league_id.
    """
    bid = get_build_id()
    if not bid:
        return []
    url = f"{FOTMOB_BASE}/_next/data/{bid}/en-GB/players/{fotmob_player_id}.json"
    try:
        d = _get(url, timeout=15)
    except Exception as e:
        print(f"[fotmob] playerData fetch failed for {fotmob_player_id}: {e}")
        return []

    rm = (((d or {}).get("pageProps") or {}).get("data") or {}).get("recentMatches") or []
    out = []
    for m in rm[:limit]:
        minutes = m.get("minutesPlayed") or 0
        if minutes <= 0:
            continue
        is_home = 1 if m.get("isHomeTeam") else 0
        conceded = (m.get("awayScore") if is_home else m.get("homeScore")) or 0
        rating_raw = (m.get("ratingProps") or {}).get("rating")
        try:
            rating = float(rating_raw) if rating_raw is not None else None
        except (TypeError, ValueError):
            rating = None

        out.append({
            "match_id":            m.get("id"),
            "match_date":          (m.get("matchDate") or {}).get("utcTime"),
            "opponent_fotmob_id":  m.get("opponentTeamId"),
            "is_home":             is_home,
            "minutes":             minutes,
            "goals":               m.get("goals") or 0,
            "assists":             m.get("assists") or 0,
            "yellow_cards":        m.get("yellowCards") or 0,
            "red_cards":           m.get("redCards") or 0,
            "rating":              rating,
            "clean_sheet":         1 if conceded == 0 else 0,
            "league_id":           m.get("leagueId"),
        })
    return out


# ── Identify which league a team plays in ──────────────────────────────

def find_team_league(fotmob_team_id):
    """Search all configured leagues to find which one a team belongs to.
    Returns league_name or None.
    """
    for league_name in LEAGUES:
        try:
            table = fetch_league_table(league_name)
            if fotmob_team_id in table["all"]:
                return league_name
        except Exception:
            continue
    return None
