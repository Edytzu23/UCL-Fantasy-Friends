"""
Sync UCL Fantasy positions into xg.players.fantasy_position.

The xg engine stores `players.position` from FotMob/FBref data, but the
dashboard filters by UCL Fantasy roles (skill 1-4 → GK/DEF/MID/FWD). Those
two classifications can diverge — wing-backs, dual-position players, etc.
This script fetches the public UEFA Fantasy players feed and writes the
Fantasy role into a dedicated column so the rankings API can filter
authoritatively.

Usage:
    py xg/scripts/sync_fantasy_positions.py
    py xg/scripts/sync_fantasy_positions.py --md 15
"""
import argparse
import os
import sys
import unicodedata
import urllib3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from src.db import connection as db

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

UEFA_FEED = "https://gaming.uefa.com/en/uclfantasy/services/feeds/players/players_80_en_{md}.json"
PROD_PROXY = "https://uclfriend.vercel.app/api/data?md={md}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://gaming.uefa.com/en/uclfantasy/",
}
SKILL_TO_POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def norm(s: str) -> str:
    """lowercase + strip diacritics for fuzzy name matching."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def _fetch_uefa(md: int) -> list[dict]:
    """Pull straight from the UEFA Fantasy public feed.

    Returns rows with `pFName` (full name), `cCode` (UEFA team code),
    `skill` (1-4). Times out from sandboxed/restricted networks; use the
    --via-prod fallback in that case.
    """
    url = UEFA_FEED.format(md=md)
    r = requests.get(url, headers=HEADERS, verify=False, timeout=45)
    r.raise_for_status()
    payload = r.json()
    return [
        {"name": p.get("pFName") or p.get("pDName") or "",
         "team_code": (p.get("cCode") or "").upper(),
         "skill": p.get("skill")}
        for p in payload["data"]["value"]["playerList"]
    ]


def _fetch_via_prod(md: int) -> list[dict]:
    """Pull through the deployed dashboard's /api/data proxy.

    Returns the same shape as _fetch_uefa but mapped from `allPlayers`.
    The dashboard's proxy already converts UEFA `skill` → `posCode`
    via SKILL_TO_POS, so we hand the posCode through directly.
    """
    url = PROD_PROXY.format(md=md)
    r = requests.get(url, timeout=45)
    r.raise_for_status()
    payload = r.json()
    rows = []
    for p in payload.get("allPlayers", []):
        pos = p.get("posCode")
        rows.append({
            "name": p.get("fullName") or p.get("name") or "",
            "team_code": (p.get("teamCode") or "").upper(),
            "skill": None,
            "_posCode": pos,
        })
    return rows


def fetch_fantasy_players(md: int, via_prod: bool = False) -> list[dict]:
    """Returns a normalized list of {name, team_code, skill, _posCode?}."""
    if via_prod:
        url = PROD_PROXY.format(md=md)
        print(f"[fetch] {url} (via deployed proxy)")
        return _fetch_via_prod(md)

    url = UEFA_FEED.format(md=md)
    print(f"[fetch] {url}")
    last_err = None
    for attempt in range(3):
        try:
            return _fetch_uefa(md)
        except Exception as e:
            last_err = e
            print(f"  attempt {attempt + 1} failed: {e!r}; retrying")
    print(f"  UEFA feed unreachable ({last_err!r}); falling back to prod proxy")
    return _fetch_via_prod(md)


def column_exists(conn, table: str, col: str) -> bool:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(c[1] == col for c in cols)


def ensure_column(conn) -> None:
    if not column_exists(conn, "players", "fantasy_position"):
        print("[migrate] adding players.fantasy_position column")
        conn.execute(
            "ALTER TABLE players ADD COLUMN fantasy_position TEXT "
            "CHECK(fantasy_position IN ('GK','DEF','MID','FWD'))"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_players_fpos "
            "ON players(fantasy_position)"
        )
        conn.commit()


def sync(md: int, via_prod: bool = False) -> None:
    fantasy_players = fetch_fantasy_players(md, via_prod=via_prod)
    print(f"[fetch] {len(fantasy_players)} fantasy players")

    conn = db.get_connection()
    ensure_column(conn)

    # Build norm(full_name) → fantasy_pos mapping. Last-name fallback below
    # handles xg's abbreviated forms.
    fantasy_index: dict[str, str] = {}
    for fp in fantasy_players:
        full_name = fp.get("name", "")
        pos = fp.get("_posCode") or SKILL_TO_POS.get(fp.get("skill"))
        if not pos or not full_name:
            continue
        fantasy_index[norm(full_name)] = pos

    # Also index by last-name + team for fallback, since some xg names
    # are abbreviated forms.
    db_players = conn.execute(
        "SELECT id, name, position FROM players"
    ).fetchall()
    print(f"[match] {len(db_players)} xg players")

    matched = 0
    unmatched = 0
    overrides = 0
    cur = conn.cursor()
    for row in db_players:
        pid = row["id"]
        nm = row["name"] or ""
        cur_pos = row["position"]

        # Try exact normalized full-name match
        fpos = fantasy_index.get(norm(nm))

        # Fallback: match by last-name only
        if not fpos:
            last = norm(nm.split()[-1] if nm else "")
            if last:
                hits = [v for k, v in fantasy_index.items()
                        if k.endswith(" " + last) or k == last]
                # Only use if unambiguous
                if len(set(hits)) == 1:
                    fpos = hits[0]

        if fpos:
            cur.execute(
                "UPDATE players SET fantasy_position = ? WHERE id = ?",
                (fpos, pid),
            )
            matched += 1
            if cur_pos and cur_pos != fpos:
                overrides += 1
        else:
            unmatched += 1

    conn.commit()
    conn.close()

    print(f"[done] matched={matched}, unmatched={unmatched}, "
          f"position_diffs={overrides} (fantasy != fotmob)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", type=int, default=15,
                    help="Matchday to fetch (default 15)")
    ap.add_argument("--via-prod", action="store_true",
                    help="Skip UEFA feed and pull from the deployed dashboard "
                         "proxy. Useful when the UEFA endpoint is geo-blocked "
                         "or rate-limited from the local network.")
    args = ap.parse_args()
    sync(args.md, via_prod=args.via_prod)


if __name__ == "__main__":
    main()
