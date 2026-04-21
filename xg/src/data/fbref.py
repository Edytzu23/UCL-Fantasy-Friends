"""
FBref scraper for supplementary stats that FotMob doesn't provide:
- Ball recoveries, tackles, interceptions, blocks
- Saves (GK), penalty saves
- Yellow/red card rates
- Shots from outside box
- Penalty taker identification

Uses HTML scraping with respectful rate limiting (4s between requests).
"""

import requests
import time
import re
from bs4 import BeautifulSoup
from src.config import FBREF_BASE, FBREF_DELAY, LEAGUES

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

_last_request_ts = 0
_request_lock = __import__("threading").Lock()


def _rate_limited_get(url, timeout=15):
    """GET with rate limiting to respect FBref's limits."""
    global _last_request_ts
    with _request_lock:
        elapsed = time.time() - _last_request_ts
        if elapsed < FBREF_DELAY:
            time.sleep(FBREF_DELAY - elapsed)
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        _last_request_ts = time.time()
        r.raise_for_status()
        return r


def _parse_table(html, table_id):
    """Extract a stats table by its HTML id. Returns list of row dicts."""
    soup = BeautifulSoup(html, "lxml")

    # FBref wraps some tables in comments
    table = soup.find("table", id=table_id)
    if not table:
        for comment in soup.find_all(string=lambda t: isinstance(t, __import__("bs4").Comment)):
            if table_id in comment:
                inner = BeautifulSoup(comment, "lxml")
                table = inner.find("table", id=table_id)
                if table:
                    break

    if not table:
        return []

    # Parse headers
    thead = table.find("thead")
    header_rows = thead.find_all("tr") if thead else []
    headers = []
    if header_rows:
        last_row = header_rows[-1]
        for th in last_row.find_all(["th", "td"]):
            stat = th.get("data-stat", th.get_text(strip=True))
            headers.append(stat)

    # Parse body rows
    tbody = table.find("tbody")
    rows = []
    for tr in tbody.find_all("tr"):
        if tr.get("class") and "thead" in tr.get("class", []):
            continue
        row = {}
        for i, td in enumerate(tr.find_all(["th", "td"])):
            stat = td.get("data-stat", headers[i] if i < len(headers) else f"col_{i}")
            val = td.get_text(strip=True)
            # Try to parse numbers
            if val == "":
                row[stat] = None
            else:
                try:
                    row[stat] = int(val)
                except ValueError:
                    try:
                        row[stat] = float(val)
                    except ValueError:
                        row[stat] = val
        if row.get("player"):
            rows.append(row)

    return rows


def fetch_league_defense_stats(league_name):
    """Fetch defensive stats for all players in a league.
    Returns list of dicts with: player, team, tackles, interceptions, blocks, clearances, recoveries.
    """
    league = LEAGUES.get(league_name)
    if not league or "fbref_path" not in league:
        return []

    url = f"{FBREF_BASE}/{league['fbref_path']}"
    # Defense page
    defense_url = url.replace("-Stats", "-Stats").rstrip("/")
    # FBref URLs: /en/comps/9/defense/Premier-League-Stats
    parts = league["fbref_path"].split("/")
    comp_id = parts[0]
    slug = parts[1]
    defense_url = f"{FBREF_BASE}/{comp_id}/defense/{slug.replace('-Stats', '')}-Stats"

    r = _rate_limited_get(defense_url)
    rows = _parse_table(r.text, "stats_defense")

    results = []
    for row in rows:
        results.append({
            "player":         row.get("player"),
            "team":           row.get("team"),
            "minutes_90s":    row.get("minutes_90s"),
            "tackles":        row.get("tackles", 0),
            "tackles_won":    row.get("tackles_won", 0),
            "interceptions":  row.get("interceptions", 0),
            "blocks":         row.get("blocks", 0),
            "clearances":     row.get("clearances", 0),
        })
    return results


def fetch_league_misc_stats(league_name):
    """Fetch miscellaneous stats: cards, recoveries, penalties won/conceded.
    Returns list of dicts.
    """
    league = LEAGUES.get(league_name)
    if not league or "fbref_path" not in league:
        return []

    parts = league["fbref_path"].split("/")
    comp_id = parts[0]
    slug = parts[1]
    misc_url = f"{FBREF_BASE}/{comp_id}/misc/{slug.replace('-Stats', '')}-Stats"

    r = _rate_limited_get(misc_url)
    rows = _parse_table(r.text, "stats_misc")

    results = []
    for row in rows:
        results.append({
            "player":           row.get("player"),
            "team":             row.get("team"),
            "minutes_90s":      row.get("minutes_90s"),
            "yellow_cards":     row.get("cards_yellow", 0),
            "red_cards":        row.get("cards_red", 0),
            "recoveries":       row.get("ball_recoveries", 0),
            "penalties_won":    row.get("pens_won", 0),
            "penalties_conceded": row.get("pens_conceded", 0),
        })
    return results


def fetch_league_shooting_stats(league_name):
    """Fetch shooting stats: shots, shots on target, distance, outside box.
    Returns list of dicts.
    """
    league = LEAGUES.get(league_name)
    if not league or "fbref_path" not in league:
        return []

    parts = league["fbref_path"].split("/")
    comp_id = parts[0]
    slug = parts[1]
    shooting_url = f"{FBREF_BASE}/{comp_id}/shooting/{slug.replace('-Stats', '')}-Stats"

    r = _rate_limited_get(shooting_url)
    rows = _parse_table(r.text, "stats_shooting")

    results = []
    for row in rows:
        results.append({
            "player":             row.get("player"),
            "team":               row.get("team"),
            "minutes_90s":        row.get("minutes_90s"),
            "shots":              row.get("shots", 0),
            "shots_on_target":    row.get("shots_on_target", 0),
            "goals":              row.get("goals", 0),
            "npxg":               row.get("npxg", 0),
            "xg":                 row.get("xg", 0),
            # average distance is in yards — useful for outside box classification
            "avg_distance":       row.get("average_shot_distance", 0),
        })
    return results


def fetch_league_keeper_stats(league_name):
    """Fetch goalkeeper stats: saves, clean sheets, penalty saves.
    Returns list of dicts.
    """
    league = LEAGUES.get(league_name)
    if not league or "fbref_path" not in league:
        return []

    parts = league["fbref_path"].split("/")
    comp_id = parts[0]
    slug = parts[1]
    keeper_url = f"{FBREF_BASE}/{comp_id}/keepers/{slug.replace('-Stats', '')}-Stats"

    r = _rate_limited_get(keeper_url)
    rows = _parse_table(r.text, "stats_keeper")

    results = []
    for row in rows:
        results.append({
            "player":           row.get("player"),
            "team":             row.get("team"),
            "minutes_90s":      row.get("minutes_90s"),
            "saves":            row.get("gk_saves", 0),
            "clean_sheets":     row.get("gk_clean_sheets", 0),
            "penalty_saves":    row.get("gk_pens_saved", 0),
            "penalties_faced":  row.get("gk_pens_att", 0),
        })
    return results


def fetch_all_supplementary_stats(league_name):
    """Fetch all supplementary stats for a league and merge by player name.
    Returns dict: player_name -> merged stats dict.
    """
    merged = {}

    for fetch_fn, stat_key in [
        (fetch_league_defense_stats, "defense"),
        (fetch_league_misc_stats, "misc"),
        (fetch_league_shooting_stats, "shooting"),
        (fetch_league_keeper_stats, "keeper"),
    ]:
        try:
            rows = fetch_fn(league_name)
            for row in rows:
                name = row.get("player")
                if not name:
                    continue
                if name not in merged:
                    merged[name] = {"player": name, "team": row.get("team")}
                # Merge all keys except player/team
                for k, v in row.items():
                    if k not in ("player", "team"):
                        merged[name][k] = v
            print(f"[fbref] {league_name} {stat_key}: {len(rows)} rows")
        except Exception as e:
            print(f"[fbref] {league_name} {stat_key} error: {e}")

    return merged
