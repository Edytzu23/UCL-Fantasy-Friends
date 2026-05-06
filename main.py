from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import requests
import json
import urllib3
from datetime import datetime, timedelta
import threading
import time
import os
import sys
import concurrent.futures
from scouting import (
    get_team_scouting, get_scouting_matchup, get_all_matchups,
    fetch_ucl_bracket, load_scouting_cache_local,
)

# Mount xPts Engine (XG DATABASE) as a subapp under /api/xg.
# xg/ is a self-contained project with its own src/ tree; adding it to
# sys.path lets `src.db.connection` etc. resolve without package renames.
_XG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xg")
if _XG_DIR not in sys.path:
    sys.path.insert(0, _XG_DIR)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

@asynccontextmanager
async def lifespan(app):
    # Load schedule first so get_current_matchday() knows which MD is live
    load_live_schedule()
    # Load old matchdays from GitHub cache (no UEFA API calls)
    load_all_cached_mds()
    # Load scouting cache if available
    load_scouting_cache_local()
    # No background threads — Vercel freezes the lambda after each request.
    # Fresh data is driven by /api/cron/tick (external cron, minutely) which
    # writes to GitHub; /api/data reads the GitHub blob. All lambda instances
    # see the same data within the memo TTL.
    yield

app = FastAPI(lifespan=lifespan)

# Mount xPts Engine router under /api/xg. Import happens here (after sys.path
# is set at the top of the module) so the xg package's `src.*` imports resolve.
from api.routes import router as xg_router  # noqa: E402 — xg/api/routes.py
app.include_router(xg_router, prefix="/api/xg")

# Serve static files (logos etc.) — absolute path works on Render
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(_BASE_DIR, "static")), name="static")

FRIENDS_IDS = [
    "c346c242-889e-11f0-8a99-3fabd3074e1f",
    "fd1a0736-7db8-11f0-aeb7-c7e93fdf190e",
    "a284802e-92c8-11f0-ab76-b78040df534f",
    "e8efc0bc-7dbc-11f0-9ce5-21af25004814",
    "abc10086-916a-11f0-8d1a-6dbc146ea53d",
    "5c5169da-8db6-11f0-8f13-5bb5a7bfec1a",
    "32193db2-81a1-11f0-a065-e1558753dd0a",
    "e43e985a-9260-11f0-9895-517cf2cbfca4",
    "e888e9d2-7db7-11f0-a1c0-df1a0de05bf5",
    "abd0968c-81a1-11f0-bb57-abab47f5742d",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "entity": "ed0t4n$3!",
}

# Session cookie for leaderboard API — needs periodic refresh when it expires
UCL_CLASSIC_007 = "4B0044004E00650067006100770045005500700049004A004400760063004A004B0075006C00670065005A00350046004D00570035006A00690059004900330031005A006A004A005000660031006F005A0076006E0072005100610047003400750075004F00760061006700360052004C0072005900560031004A0077004E0066007A002B00760045006800520072004B00570054004D0069007A007000350063006E00560075006200360073006F0043006D0059007800550078003500420070006400360044004400570030006D0045003500770074004E004D0053006F00590064004E002F0059006900760051006E00440048004E0059006D00630075004400750070004B00720048006A006B0071006C002B002F004700560048004E004C006F0037003600630041003D003D00"
LEADERBOARD_HEADERS = {**HEADERS, "Cookie": f"UCL_CLASSIC_007={UCL_CLASSIC_007}"}

SKILL_TO_POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

cache = {}
cache_lock = threading.Lock()

# ── GITHUB-BACKED MATCHDAY CACHE ─────────────────────────────────────────
# Old matchdays are saved to GitHub as cache/md{XX}.json so Render can
# load them on startup without hitting UEFA APIs.
GH_CACHE_DIR = "cache"


def save_md_cache(md, data):
    """Persist full matchday build_data result to GitHub."""
    if not GITHUB_TOKEN:
        return
    try:
        path = f"{GH_CACHE_DIR}/md{md:02d}.json"
        content_str = json.dumps(data, ensure_ascii=False)
        _, sha = github_get_file(path)
        ok = github_put_file(path, content_str, sha=sha,
                             message=f"cache MD{md} — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        if ok:
            print(f"[GHCache] Saved MD{md} to GitHub")
    except Exception as e:
        print(f"[GHCache] Error saving MD{md}: {e}")


def load_md_cache(md):
    """Load a single matchday from GitHub cache. Returns dict or None."""
    if not GITHUB_TOKEN:
        return None
    try:
        path = f"{GH_CACHE_DIR}/md{md:02d}.json"
        content, _ = github_get_file(path)
        if content:
            return json.loads(content)
    except Exception as e:
        print(f"[GHCache] Error loading MD{md}: {e}")
    return None


# ── PER-MD PLAYER STATS ARCHIVE ──────────────────────────────────────────
# Immutable per-MD per-player snapshots captured while UEFA feeds are warm.
# Once a stats/md{XX}_players.json file exists for a given MD, it is the
# authoritative source of mdGoals/mdAssists/mdCleanSheet for that MD —
# refresh_cache will not overwrite it. Solves the past-assists bug where
# UEFA's gA field returns 0 outside the live match window.
GH_STATS_DIR = "stats"

_stats_archive_memo = {}  # md -> {pid: {...}} or None
_unverified_memo = {"data": None, "ts": 0}
_UNVERIFIED_TTL = 300  # seconds


def _stats_archive_payload(md, players, source="live_window"):
    """Shape the per-player slice we want to freeze for a given MD."""
    rows = []
    for p in players:
        rows.append({
            "id": int(p["id"]),
            "name": p.get("name", ""),
            "teamCode": p.get("teamCode", ""),
            "posCode": p.get("posCode", "MID"),
            "mdGoals": int(p.get("mdGoals", 0) or 0),
            "mdAssists": int(p.get("mdAssists", 0) or 0),
            "mdCleanSheet": int(p.get("mdCleanSheet", 0) or 0),
            "mdMins": int(p.get("mdMins", 0) or 0),
            "mdSaves": int(p.get("mdSaves", 0) or 0),
            "mdYellowCards": int(p.get("yellowCards", 0) or 0),
            "mdRedCards": int(p.get("redCards", 0) or 0),
            "mdMomFlag": bool(p.get("momFlag", False)),
            "mdPoints": int(p.get("curGDPts", 0) or 0),
        })
    return {
        "matchday": md,
        "savedAt": datetime.now().isoformat(),
        "source": source,
        "playerCount": len(rows),
        "players": rows,
    }


def save_player_stats_archive(md, data, source="live_window", overwrite=False):
    """Persist per-MD per-player stats to GitHub. Idempotent — does not
    overwrite an existing archive unless overwrite=True (admin endpoint)."""
    if not GITHUB_TOKEN:
        return False
    try:
        path = f"{GH_STATS_DIR}/md{md:02d}_players.json"
        existing, sha = github_get_file(path)
        if existing and not overwrite:
            print(f"[StatsArchive] MD{md} already saved, skipping")
            return False
        payload = _stats_archive_payload(md, data.get("allPlayers", []), source=source)
        content_str = json.dumps(payload, ensure_ascii=False)
        ok = github_put_file(
            path, content_str, sha=sha,
            message=f"stats archive MD{md} — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )
        if ok:
            _stats_archive_memo[md] = {p["id"]: p for p in payload["players"]}
            print(f"[StatsArchive] Saved MD{md} ({payload['playerCount']} players, source={source})")
        return bool(ok)
    except Exception as e:
        print(f"[StatsArchive] Error saving MD{md}: {e}")
        return False


def load_player_stats_archive(md):
    """Return {pid_int: {...}} for the MD's archive, or None if missing.
    Memoized in-process — archives are immutable so we never re-fetch."""
    if md in _stats_archive_memo:
        return _stats_archive_memo[md]
    if not GITHUB_TOKEN:
        return None
    try:
        path = f"{GH_STATS_DIR}/md{md:02d}_players.json"
        content, _ = github_get_file(path)
        if not content:
            _stats_archive_memo[md] = None
            return None
        payload = json.loads(content)
        idx = {int(p["id"]): p for p in payload.get("players", [])}
        _stats_archive_memo[md] = idx
        return idx
    except Exception as e:
        print(f"[StatsArchive] Error loading MD{md}: {e}")
        return None


def _load_unverified():
    """Load list of MDs marked as unverified (cached for 5min)."""
    now = time.time()
    if _unverified_memo["data"] is not None and (now - _unverified_memo["ts"]) < _UNVERIFIED_TTL:
        return _unverified_memo["data"]
    if not GITHUB_TOKEN:
        _unverified_memo.update({"data": set(), "ts": now})
        return set()
    try:
        content, _ = github_get_file(f"{GH_STATS_DIR}/_unverified.json")
        mds = set(json.loads(content)) if content else set()
    except Exception:
        mds = set()
    _unverified_memo.update({"data": mds, "ts": now})
    return mds


def is_unverified(md):
    return md in _load_unverified()


def mark_md_unverified(md, unverified=True):
    """Add or remove a matchday from the unverified list on GitHub."""
    if not GITHUB_TOKEN:
        return False
    try:
        path = f"{GH_STATS_DIR}/_unverified.json"
        content, sha = github_get_file(path)
        mds = set(json.loads(content)) if content else set()
        if unverified:
            mds.add(md)
        else:
            mds.discard(md)
        new_content = json.dumps(sorted(mds))
        ok = github_put_file(
            path, new_content, sha=sha,
            message=f"{'mark' if unverified else 'unmark'} MD{md} unverified",
        )
        if ok:
            _unverified_memo.update({"data": mds, "ts": time.time()})
        return bool(ok)
    except Exception as e:
        print(f"[StatsArchive] Error updating unverified list: {e}")
        return False


# ── SECONDARY BACKUP (FotMob) ────────────────────────────────────────────
# Independent backup source for goals/assists/MOTM/clean-sheets. UEFA's
# feeds are the primary; FotMob is captured in parallel at FINALMD so we
# never depend on a single vendor for the matchday's truth.
#
# As of 2026-05, FotMob's public CDN returns 403 and matchDetails requires
# Turnstile. The fetch is wrapped defensively — if the source is down, we
# log and skip without breaking the primary capture. The framework stays
# in place so a future source (or a working FotMob path) plugs in cleanly.

def fetch_fotmob_md_stats(matchday):
    """Try to fetch per-player stats for the given UCL matchday from FotMob.
    Returns {uefa_id: {mdGoals, mdAssists, mdCleanSheet, mdMomFlag, mdMins}}
    or {} if the source is unavailable.

    Mapping FotMob → UEFA is done by (normalized last name, teamCode).
    """
    try:
        # FotMob's matchDetails is currently behind Turnstile and the player
        # _next/data endpoint returns null for `data`. We attempt nothing
        # and return empty until a working source is wired up.
        # When re-enabling: refer to xg/src/data/fotmob.py for fetch_ucl_fixtures
        # and existing helpers (build_id, headers).
        print(f"[StatsFotmob] Source unavailable (FotMob blocked), skipping MD{matchday}")
        return {}
    except Exception as e:
        print(f"[StatsFotmob] fetch failed for MD{matchday}: {e}")
        return {}


def save_fotmob_backup_archive(md, overwrite=False):
    """Write FotMob secondary backup to stats/md{XX}_fotmob.json.
    Idempotent like the primary archive. No-op if FotMob returned nothing."""
    if not GITHUB_TOKEN:
        return False
    fm_data = fetch_fotmob_md_stats(md)
    if not fm_data:
        return False
    try:
        path = f"{GH_STATS_DIR}/md{md:02d}_fotmob.json"
        existing, sha = github_get_file(path)
        if existing and not overwrite:
            print(f"[StatsFotmob] MD{md} backup already saved, skipping")
            return False
        payload = {
            "matchday": md,
            "savedAt": datetime.now().isoformat(),
            "source": "fotmob_matchDetails",
            "playerCount": len(fm_data),
            "players": list(fm_data.values()),
        }
        ok = github_put_file(
            path, json.dumps(payload, ensure_ascii=False), sha=sha,
            message=f"fotmob backup MD{md} — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )
        if ok:
            print(f"[StatsFotmob] Saved MD{md} backup ({len(fm_data)} players)")
        return bool(ok)
    except Exception as e:
        print(f"[StatsFotmob] Error saving MD{md}: {e}")
        return False


def load_fotmob_backup_archive(md):
    """Return {pid: {...}} for the FotMob backup, or None if missing."""
    if not GITHUB_TOKEN:
        return None
    try:
        path = f"{GH_STATS_DIR}/md{md:02d}_fotmob.json"
        content, _ = github_get_file(path)
        if not content:
            return None
        payload = json.loads(content)
        return {int(p.get("uefaId", p.get("id", 0))): p for p in payload.get("players", [])}
    except Exception as e:
        print(f"[StatsFotmob] Error loading MD{md}: {e}")
        return None


def compare_archives(md):
    """Return diff between primary (UEFA) and FotMob backup archives for MD."""
    primary = load_player_stats_archive(md) or {}
    backup = load_fotmob_backup_archive(md) or {}
    diffs = []
    for pid, p in primary.items():
        b = backup.get(pid)
        if not b:
            continue
        for field in ("mdGoals", "mdAssists", "mdCleanSheet", "mdMomFlag"):
            pv = p.get(field, 0)
            bv = b.get(field, 0)
            if pv != bv:
                diffs.append({
                    "id": pid, "name": p.get("name", ""), "field": field,
                    "primary": pv, "fotmob": bv,
                })
    return {
        "matchday": md,
        "primaryCount": len(primary),
        "fotmobCount": len(backup),
        "discrepancies": diffs,
    }


def load_all_cached_mds():
    """Load all cached matchdays from GitHub into memory on startup."""
    if not GITHUB_TOKEN:
        print("[GHCache] No GITHUB_TOKEN — skipping cache load")
        return
    try:
        url = f"{GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GH_CACHE_DIR}"
        r = requests.get(url, headers=_gh_headers(), params={"ref": GITHUB_BRANCH})
        if r.status_code != 200:
            print(f"[GHCache] No cache dir on GitHub (status {r.status_code})")
            return
        files = [f["name"] for f in r.json() if f["name"].startswith("md") and f["name"].endswith(".json")]
        loaded = 0
        now = time.time()
        for fname in files:
            try:
                md = int(fname.replace("md", "").replace(".json", ""))
                data = load_md_cache(md)
                if data:
                    with cache_lock:
                        cache[md] = {"ts": now, "data": data}
                    loaded += 1
            except (ValueError, Exception):
                pass
        if loaded:
            print(f"[GHCache] Loaded {loaded} matchdays from GitHub")
    except Exception as e:
        print(f"[GHCache] Error listing cache: {e}")

# ── LIVE SCORES CACHE ─────────────────────────────────────────────────────
live_scores_cache = {}       # md -> {"data": {...}, "ts": float}
live_scores_lock = threading.Lock()
LIVE_SCORES_TTL = 30         # seconds

# ── LIVE SNAPSHOT SCHEDULE ─────────────────────────────────────────────────
CHECKPOINT_LABELS = ["HTM1", "FTM1", "HTM2", "FTM2", "FINALMD"]
live_schedule = {"matchday": 0, "checkpoints": []}
live_schedule_lock = threading.Lock()

# ── KNOCKOUT MATCHDAY PLAN ─────────────────────────────────────────────────
# Pre-seeded schedule for the remaining UCL matchdays so the cron can auto-
# advance without any manual input. All kickoffs in Romanian local time.
# - 2-day MDs get 5 checkpoints (HTM/FTM per day + FINALMD the morning after).
# - 1-day MDs (final) get 3 checkpoints (HTM1, FTM1, FINALMD).
UPCOMING_MATCHDAYS = {
    14: {"dates": ["2026-04-14", "2026-04-15"], "kickoff": "22:00"},  # QF leg 2
    15: {"dates": ["2026-04-28", "2026-04-29"], "kickoff": "22:00"},  # SF leg 1
    16: {"dates": ["2026-05-05", "2026-05-06"], "kickoff": "22:00"},  # SF leg 2
    17: {"dates": ["2026-05-30"],               "kickoff": "19:00"},  # Final
}
FINALMD_TIME = "09:00"


def _add_minutes(hhmm, mins):
    """Return HH:MM string for kickoff + mins (wraps past midnight if needed)."""
    h, m = map(int, hhmm.split(":"))
    base = datetime(2000, 1, 1, h, m) + timedelta(minutes=mins)
    return base.strftime("%H:%M")


def build_checkpoints_from_plan(plan, today_str=None):
    """Derive checkpoint list from a plan entry {dates, kickoff}.

    Past-date checkpoints are marked fired so the cron won't retroactively
    snapshot them. `today_str` is overridable for tests; defaults to now.
    """
    ht = _add_minutes(plan["kickoff"], 45)
    ft = _add_minutes(plan["kickoff"], 105)
    dates = plan["dates"]
    today = today_str or datetime.now().strftime("%Y-%m-%d")
    day1_past = len(dates) >= 1 and dates[0] < today
    day2_past = len(dates) >= 2 and dates[1] < today
    if len(dates) >= 2:
        return [
            {"time": ht, "label": "HTM1", "fired": day1_past},
            {"time": ft, "label": "FTM1", "fired": day1_past},
            {"time": ht, "label": "HTM2", "fired": day2_past},
            {"time": ft, "label": "FTM2", "fired": day2_past},
            {"time": FINALMD_TIME, "label": "FINALMD", "fired": False},
        ]
    # Single-day MD (the final)
    return [
        {"time": ht, "label": "HTM1", "fired": day1_past},
        {"time": ft, "label": "FTM1", "fired": day1_past},
        {"time": FINALMD_TIME, "label": "FINALMD", "fired": False},
    ]


PUBLIC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://gaming.uefa.com/en/uclfantasy/",
    "Origin": "https://gaming.uefa.com",
}


def fetch_public_players(matchday):
    url = f"https://gaming.uefa.com/en/uclfantasy/services/feeds/players/players_80_en_{matchday}.json"
    r = requests.get(url, headers=PUBLIC_HEADERS, verify=False, timeout=15)
    r.raise_for_status()
    players_raw = r.json()["data"]["value"]["playerList"]
    players = {}
    for p in players_raw:
        players[int(p["id"])] = {
            "id": int(p["id"]),
            "name": p["pDName"],
            "fullName": p["pFName"],
            "team": p["tName"],
            "teamCode": p["cCode"],
            "posCode": SKILL_TO_POS.get(p["skill"], "MID"),
            "totPts": p.get("totPts", 0) or 0,
            # curGDPts is the live score for the current MD. Do NOT fall back
            # to lastGdPoints (previous MD's final) — at MD boundary that leaks
            # last MD's points for players who haven't kicked off yet.
            "curGDPts": p.get("curGDPts", 0) or 0,
            "lastGdPts": p.get("lastGdPoints", 0) or 0,
            "goals": p.get("gS", 0) or 0,
            "assists": p.get("gA", 0) or 0,
            "cleanSheets": p.get("cS", 0) or 0,
            "selPer": p.get("selPer", 0) or 0,
            "value": p.get("value", 0) or 0,
            "rating": p.get("rating", 0) or 0,
            "status": p.get("pStatus", "A"),
            "momCount": p.get("mOM", 0) or 0,
            "yellowCards": p.get("yC", 0) or 0,
            "redCards": p.get("rC", 0) or 0,
        }
    return players


def fetch_team_data(matchday, phase_id=2):
    def _fetch_one(uid):
        url = f"https://gaming.uefa.com/en/uclfantasy/services/api/Gameplay/user/{uid}/opponent-team"
        params = {"matchdayId": matchday, "phaseId": phase_id, "opponentguid": uid}
        try:
            r = requests.get(url, params=params, headers=HEADERS, verify=False, timeout=10)
            if r.status_code == 200:
                data = r.json()["data"]["value"]
                return {
                    "guid": uid,
                    "username": data.get("username", "?"),
                    "teamName": data.get("teamName", "?"),
                    "gdPoints": data.get("gdPoints", 0) or 0,
                    "gdRank": data.get("gdRank", 0) or 0,
                    "ovPoints": data.get("ovPoints", 0) or 0,
                    "ovRank": data.get("ovRank", 0) or 0,
                    "captainId": data.get("captplayerid"),
                    "rawPlayers": data.get("playerid", []),
                }
            else:
                print(f"HTTP {r.status_code} for {uid[:8]}")
        except Exception as e:
            print(f"Error {uid[:8]}: {e}")
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(_fetch_one, FRIENDS_IDS))
    return [m for m in results if m is not None]


public_players_cache = {}  # md -> players dict, cached in memory

def fetch_public_players_cached(matchday):
    if matchday in public_players_cache:
        return public_players_cache[matchday]
    data = fetch_public_players(matchday)
    public_players_cache[matchday] = data
    return data


MATCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0",
    "Accept": "application/json",
}

fixtures_cache = {}  # md -> list of match IDs

def fetch_match_ids(matchday):
    """Get match IDs for a matchday from the fixtures feed."""
    if matchday in fixtures_cache:
        return fixtures_cache[matchday]
    try:
        url = "https://gaming.uefa.com/en/uclfantasy/services/feeds/fixtures/fixtures_80_en.json"
        r = requests.get(url, headers=PUBLIC_HEADERS, verify=False, timeout=15)
        r.raise_for_status()
        for fx in r.json()["data"]["value"]:
            md = fx.get("mdId")
            ids = [m["mId"] for m in fx.get("match", []) if m.get("mId")]
            fixtures_cache[md] = ids
        return fixtures_cache.get(matchday, [])
    except Exception as e:
        print(f"Error fetching fixtures: {e}")
        return []


def fetch_live_events(matchday):
    """Fetch live goal/assist events from match.uefa.com for all matches in a matchday.
    Returns dict: { player_id: { 'goals': int, 'assists': int } }
    """
    match_ids = fetch_match_ids(matchday)
    if not match_ids:
        return {}

    def _process_match(mid):
        """Process a single match: fetch events + lineups."""
        local_events = {}
        local_live_teams = set()
        local_conceded = set()
        local_team_players = {}
        try:
            r = requests.get(
                f"https://match.uefa.com/v5/matches/{mid}",
                headers=MATCH_HEADERS, verify=False, timeout=8
            )
            if r.status_code != 200:
                return local_events, local_live_teams, local_conceded, local_team_players
            m = r.json()
            status = m.get("status", "")
            if status not in ("LIVE", "FINISHED"):
                return local_events, local_live_teams, local_conceded, local_team_players
            home_id = m.get("homeTeam", {}).get("id")
            away_id = m.get("awayTeam", {}).get("id")
            local_live_teams.add(home_id)
            local_live_teams.add(away_id)
            score = m.get("score", {}).get("total", {})
            if (score.get("away") or 0) > 0:
                local_conceded.add(home_id)
            if (score.get("home") or 0) > 0:
                local_conceded.add(away_id)
            pe = m.get("playerEvents", {})
            for scorer in pe.get("scorers", []):
                goal_type = scorer.get("goalType", "")
                if goal_type == "OWN_GOAL":
                    continue
                pid = int(scorer.get("player", {}).get("id", 0))
                if pid:
                    local_events.setdefault(pid, {"goals": 0, "assists": 0})
                    local_events[pid]["goals"] += 1
                assist_player = scorer.get("assistPlayer") or scorer.get("assist", {})
                if isinstance(assist_player, dict) and assist_player.get("id"):
                    apid = int(assist_player["id"])
                    local_events.setdefault(apid, {"goals": 0, "assists": 0})
                    local_events[apid]["assists"] += 1
            try:
                r2 = requests.get(
                    f"https://match.uefa.com/v5/matches/{mid}/lineups",
                    headers=MATCH_HEADERS, verify=False, timeout=8
                )
                if r2.status_code == 200:
                    lineups = r2.json()
                    for side, tid in [("homeTeam", home_id), ("awayTeam", away_id)]:
                        pids = set()
                        for p in lineups.get(side, {}).get("field", []):
                            pids.add(int(p.get("player", {}).get("id", 0)))
                        for p in lineups.get(side, {}).get("substitutions", {}).get("playerIn", []) if isinstance(lineups.get(side, {}).get("substitutions"), dict) else []:
                            pids.add(int(p.get("player", {}).get("id", 0)))
                        local_team_players[tid] = pids
            except Exception:
                pass
        except Exception as e:
            print(f"Error fetching match {mid}: {e}")
        return local_events, local_live_teams, local_conceded, local_team_players

    # Fetch all matches in parallel
    events = {}
    live_teams = set()
    conceded = set()
    team_players = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(match_ids)) as ex:
        results = list(ex.map(_process_match, match_ids))

    for local_events, local_live_teams, local_conceded, local_team_players in results:
        for pid, ev in local_events.items():
            events.setdefault(pid, {"goals": 0, "assists": 0})
            events[pid]["goals"] += ev["goals"]
            events[pid]["assists"] += ev["assists"]
        live_teams |= local_live_teams
        conceded |= local_conceded
        team_players.update(local_team_players)

    # Build clean sheet set: players on teams that haven't conceded AND are live/finished
    clean_sheet_pids = set()
    for tid in live_teams - conceded:
        for pid in team_players.get(tid, set()):
            if pid:
                clean_sheet_pids.add(pid)
    return events, clean_sheet_pids


def fetch_live_scores(matchday):
    """Fetch live fantasy scores from UEFA scoring feed for all matches in a matchday.
    Returns: {
        "matches": [{"mId", "home", "away", "homeScore", "awayScore", "status", "minute"}],
        "players": {pid_str: {"pts", "goals", "assists", "cs", "yc", "rc", "saves", "mins"}}
    }

    UEFA API structure (verified):
    - data.value.pPoints: list of {pId, tPoints, gS, gA, cS, yC, rC, oF, ...}
    - data.value.pStats:  list of {pId, gS, gA, cS, yC, rC, saves, oF, ...}
    - data.value.scoreLine: [{tName, gS}, {tName, gS}]  (home=index 0, away=index 1)
    - data.value.status: 3=live, 1=finished
    - data.value.liveMinute: current match minute
    """
    match_ids = fetch_match_ids(matchday)
    if not match_ids:
        return {"matches": [], "players": {}}

    matches = []
    players = {}

    for mid in match_ids:
        try:
            url = f"https://gaming.uefa.com/en/uclfantasy/services/feeds/scoring/live-scores_80_{mid}.json"
            r = requests.get(url, headers=PUBLIC_HEADERS, verify=False, timeout=10)
            if r.status_code != 200:
                continue
            raw = r.json()
            data = raw.get("data", raw)
            if isinstance(data, dict) and "value" in data:
                data = data["value"]

            # Match score comes from scoreLine array
            score_line = data.get("scoreLine", [])
            home_score = int(score_line[0].get("gS", 0)) if len(score_line) > 0 else 0
            away_score = int(score_line[1].get("gS", 0)) if len(score_line) > 1 else 0
            home_name = score_line[0].get("tName", "") if len(score_line) > 0 else ""
            away_name = score_line[1].get("tName", "") if len(score_line) > 1 else ""

            match_info = {
                "mId": mid,
                "home": home_name,
                "away": away_name,
                "homeScore": home_score,
                "awayScore": away_score,
                "status": data.get("status", 0),
                "minute": data.get("liveMinute", data.get("matchMinute", 0)) or 0,
            }
            matches.append(match_info)

            # pPoints is a list of player objects with tPoints field
            p_points = data.get("pPoints", [])
            pp_dict = {}
            if isinstance(p_points, list):
                for pp in p_points:
                    if isinstance(pp, dict):
                        pid = str(pp.get("pId", ""))
                        if pid:
                            pp_dict[pid] = pp.get("tPoints", 0) or 0
            elif isinstance(p_points, dict):
                # fallback: old dict format {pid: pts}
                pp_dict = {str(k): v for k, v in p_points.items()}

            # pStats has player stat details
            p_stats = data.get("pStats", [])
            for ps in p_stats:
                if not isinstance(ps, dict):
                    continue
                pid = str(ps.get("pId", ps.get("id", "")))
                if not pid:
                    continue
                players[pid] = {
                    "pts": pp_dict.get(pid, 0),
                    "goals": ps.get("gS", 0) or 0,
                    "assists": ps.get("gA", 0) or 0,
                    "cs": 1 if ps.get("cS", 0) else 0,
                    "yc": ps.get("yC", 0) or 0,
                    "rc": ps.get("rC", 0) or 0,
                    "saves": ps.get("saves", ps.get("sV", ps.get("sv", 0))) or 0,
                    "mins": ps.get("oF", ps.get("mP", 0)) or 0,
                }
        except Exception as e:
            print(f"Error fetching live-scores for match {mid}: {e}")

    return {"matches": matches, "players": players}


def fetch_world_leader_team(matchday, phase_id=2):
    """Fetch the #1 global player's team from the World Leaderboard."""
    try:
        # Step 1: Get #1's GUID from leaderboard
        lb_url = "https://gaming.uefa.com/en/uclfantasy/services/api//Leaderboard/leaders"
        lb_params = {
            "optType": 2, "phaseId": 0, "matchdayId": matchday,
            "vPageChunk": 1, "vPageNo": 1, "vPageOneChunk": 1,
        }
        r = requests.get(lb_url, params=lb_params, headers=LEADERBOARD_HEADERS, verify=False, timeout=15)
        if r.status_code != 200:
            print(f"Leaderboard API returned {r.status_code}")
            return None
        leaders = r.json()["data"]["value"]["userInfo"]
        if not leaders:
            return None
        leader = leaders[0]
        guid = leader["guid"]

        # Step 2: Fetch their team using existing opponent-team endpoint
        team_url = f"https://gaming.uefa.com/en/uclfantasy/services/api/Gameplay/user/{guid}/opponent-team"
        team_params = {"matchdayId": matchday, "phaseId": phase_id, "opponentguid": guid}
        r2 = requests.get(team_url, params=team_params, headers=HEADERS, verify=False, timeout=10)
        if r2.status_code != 200:
            print(f"World leader team API returned {r2.status_code}")
            return None
        data = r2.json()["data"]["value"]

        return {
            "guid": guid,
            "fullName": leader.get("fullName", "?"),
            "teamName": leader.get("teamName", "?"),
            "rank": leader.get("rank", 1),
            "matchdayPoints": leader.get("overallPoints", 0),  # optType=2 puts MD pts here
            "gdPoints": data.get("gdPoints", 0) or 0,
            "ovPoints": data.get("ovPoints", 0) or 0,
            "captainId": data.get("captplayerid"),
            "rawPlayers": data.get("playerid", []),
        }
    except Exception as e:
        print(f"Error fetching world leader: {e}")
        return None


def build_data(matchday=11):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching MD{matchday}...")

    # If this is a past MD with an immutable stats archive, that archive is
    # the source of truth for mdGoals/mdAssists/mdCleanSheet/mdMins/mdSaves —
    # UEFA's live feeds decay outside the match window, but the archive was
    # captured while they were warm.
    try:
        current_md = get_current_matchday()
    except Exception:
        current_md = matchday
    is_past_md = matchday < current_md
    archive = load_player_stats_archive(matchday) if is_past_md else None
    if archive:
        print(f"  Stats archive: {len(archive)} players (past MD)")

    public_players = fetch_public_players_cached(matchday)

    # Fetch previous MD to compute per-MD stats via diff (uses cache if already loaded)
    prev_players = {}
    if matchday > 1:
        try:
            prev_players = fetch_public_players_cached(matchday - 1)
        except Exception as e:
            print(f"Could not fetch MD{matchday-1} for diff: {e}")

    # Fetch previous MD team rosters to detect transfers
    prev_team_ids = {}  # guid -> set of player IDs
    prev_managers_raw = []
    if matchday > 1:
        try:
            prev_managers_raw = fetch_team_data(matchday - 1)
            for mgr in prev_managers_raw:
                prev_team_ids[mgr["guid"]] = {int(rp["id"]) for rp in mgr["rawPlayers"] if rp.get("id") is not None}
        except Exception as e:
            print(f"Could not fetch MD{matchday-1} teams for transfer diff: {e}")

    def md_stat(pid, field, current_players, previous_players):
        """Goals/assists/CS for this MD only = current cumulative - previous cumulative"""
        cur = current_players.get(pid, {}).get(field, 0) or 0
        prv = previous_players.get(pid, {}).get(field, 0) or 0
        return max(0, cur - prv)

    # Fetch live match events (goals/assists/clean sheets) from match.uefa.com
    live_events = {}
    live_clean_sheet_pids = set()
    try:
        result = fetch_live_events(matchday)
        if result:
            live_events, live_clean_sheet_pids = result
            if live_events or live_clean_sheet_pids:
                print(f"  Live events: {sum(e['goals'] for e in live_events.values())} goals, "
                      f"{sum(e['assists'] for e in live_events.values())} assists, "
                      f"{len(live_clean_sheet_pids)} CS players")
    except Exception as e:
        print(f"Live events fetch failed (non-fatal): {e}")

    # Fetch live fantasy scores (tPoints per player) from UEFA scoring feed
    live_pts = {}  # pid (int) -> tPoints
    live_scoring_stats = {}  # pid (int) -> {"goals": int, "assists": int, "cs": int}
    try:
        live_data = fetch_live_scores(matchday)
        if live_data and live_data.get("players"):
            for pid_str, pdata in live_data["players"].items():
                pid_int = int(pid_str)
                pts = pdata.get("pts", 0)
                if pts:  # only override if live feed has non-zero points
                    live_pts[pid_int] = pts
                # Record every player from pStats (even 0/0/0) so `pid in live_scoring_stats`
                # distinguishes "played, scored 0" from "no live data" for MD1 fallback.
                g = pdata.get("goals", 0) or 0
                a = pdata.get("assists", 0) or 0
                cs = pdata.get("cs", 0) or 0
                live_scoring_stats[pid_int] = {"goals": g, "assists": a, "cs": cs}
            if live_pts:
                print(f"  Live scores: {len(live_pts)} players with points")
            if live_scoring_stats:
                print(f"  Live scoring stats: {len(live_scoring_stats)} players with goals/assists/cs")
    except Exception as e:
        print(f"Live scores fetch failed (non-fatal): {e}")

    def md_g(pid):
        if archive and pid in archive:
            return archive[pid].get("mdGoals", 0)
        if pid in live_events:
            return live_events[pid].get("goals", 0)
        if pid in live_scoring_stats:
            return live_scoring_stats[pid].get("goals", 0)
        # MD1 has no prev baseline — md_stat would return season cumulative.
        if matchday == 1:
            return 0
        return md_stat(pid, "goals", public_players, prev_players)

    def md_a(pid):
        if archive and pid in archive:
            return archive[pid].get("mdAssists", 0)
        if pid in live_events:
            return live_events[pid].get("assists", 0)
        if pid in live_scoring_stats:
            return live_scoring_stats[pid].get("assists", 0)
        if matchday == 1:
            return 0
        return md_stat(pid, "assists", public_players, prev_players)

    def md_cs(pid):
        if archive and pid in archive:
            return archive[pid].get("mdCleanSheet", 0)
        if pid in live_clean_sheet_pids:
            return 1
        if pid in live_scoring_stats:
            return 1 if live_scoring_stats[pid].get("cs", 0) else 0
        if matchday == 1:
            return 0
        return md_stat(pid, "cleanSheets", public_players, prev_players)

    def md_mins(pid, lss_default):
        if archive and pid in archive:
            return archive[pid].get("mdMins", 0)
        return lss_default

    def md_saves(pid, lss_default):
        if archive and pid in archive:
            return archive[pid].get("mdSaves", 0)
        return lss_default

    managers_raw = fetch_team_data(matchday)

    # "MD started" = UEFA has locked in real rosters (at deadline). While
    # the MD is still upcoming, every manager's rawPlayers is a single
    # null-id placeholder. We capture this BEFORE the fallback below rewrites
    # rawPlayers so /api/data can still distinguish "not started" from "in
    # progress with zero points".
    md_started = any(
        any(rp.get("id") is not None for rp in mgr.get("rawPlayers", []))
        for mgr in managers_raw
    )

    # Pre-deadline fallback: UEFA returns a single null-id placeholder until
    # the MD deadline locks teams in. Substitute MD-1 rosters so clasament
    # still shows team composition; MD-level points stay 0 from the live feed.
    if prev_managers_raw:
        prev_by_guid = {m["guid"]: m for m in prev_managers_raw}
        for mgr in managers_raw:
            valid = [rp for rp in mgr.get("rawPlayers", []) if rp.get("id") is not None]
            if not valid:
                prev = prev_by_guid.get(mgr["guid"])
                if prev and prev.get("rawPlayers"):
                    mgr["rawPlayers"] = prev["rawPlayers"]
                    if not mgr.get("captainId"):
                        mgr["captainId"] = prev.get("captainId")

    player_ownership = {}  # pid -> list of usernames

    managers = []
    for mgr in managers_raw:
        enriched = []
        current_ids = {int(rp["id"]) for rp in mgr["rawPlayers"] if rp.get("id") is not None}
        prev_ids = prev_team_ids.get(mgr["guid"], set())
        transferred_in = current_ids - prev_ids if prev_ids else set()
        transferred_out_ids = prev_ids - current_ids if prev_ids else set()
        transfers_out = []
        for out_pid in transferred_out_ids:
            pub = public_players.get(out_pid, {})
            if pub:
                transfers_out.append({
                    "id": out_pid,
                    "name": pub.get("name", f"#{out_pid}"),
                    "teamCode": pub.get("teamCode", ""),
                    "team": pub.get("team", ""),
                    "posCode": pub.get("posCode", "MID"),
                    "mdPoints": pub.get("curGDPts", 0),
                })
        for rp in mgr["rawPlayers"]:
            if rp.get("id") is None:
                continue
            pid = int(rp["id"])
            pub = public_players.get(pid, {})
            mdpts = live_pts.get(pid) or pub.get("curGDPts", 0)
            is_captain = rp.get("iscaptain", 0) == 1
            is_starter = rp.get("benchposition", 0) == 0

            player = {
                "id": pid,
                "name": pub.get("name", f"#{pid}"),
                "fullName": pub.get("fullName", f"#{pid}"),
                "team": pub.get("team", ""),
                "teamCode": pub.get("teamCode", ""),
                "posCode": pub.get("posCode", SKILL_TO_POS.get(rp.get("skill", 3), "MID")),
                "mdPoints": mdpts,
                "isCaptain": is_captain,
                "isStarter": is_starter,
                "benchPosition": rp.get("benchposition", 0),
                "value": pub.get("value", rp.get("value", 0)),
                "momFlag": rp.get("momflag", 0) == 1,
                "minutesPlayed": rp.get("minutesingame"),
                "totPts": pub.get("totPts", 0),
                "goals": pub.get("goals", 0),
                "assists": pub.get("assists", 0),
                "cleanSheets": pub.get("cleanSheets", 0),
                # Per-MD stats: prefer live events → live scoring → cumulative diff (MD>1 only)
                "mdGoals": md_g(pid),
                "mdAssists": md_a(pid),
                "mdCleanSheet": md_cs(pid),
                "selPer": pub.get("selPer", 0),
                "rating": pub.get("rating", 0),
                "status": pub.get("status", "A"),
                "managerGuid": mgr["guid"],
                "managerName": mgr["username"],
                "isTransfer": pid in transferred_in,
            }
            enriched.append(player)

            if pid not in player_ownership:
                player_ownership[pid] = []
            player_ownership[pid].append(mgr["username"])

        # Calculate dynamic gdPoints from enriched player data
        computed_gd = sum(
            p["mdPoints"] * (2 if p["isCaptain"] else 1)
            for p in enriched if p["isStarter"]
        )
        managers.append(
            {
                "guid": mgr["guid"],
                "username": mgr["username"],
                "teamName": mgr["teamName"],
                "gdPoints": computed_gd if live_pts else mgr["gdPoints"],
                "gdRank": mgr["gdRank"],
                "ovPoints": mgr["ovPoints"],
                "ovRank": mgr["ovRank"],
                "captainId": mgr["captainId"],
                "players": enriched,
                "transfersOut": transfers_out,
            }
        )

    # Sort managers by ovPoints desc
    managers.sort(key=lambda x: x["ovPoints"], reverse=True)

    # Build all players enriched
    all_players = []
    for pid, p in public_players.items():
        owners = player_ownership.get(pid, [])
        enriched_p = {**p}
        # Override curGDPts with live scores if available
        if pid in live_pts:
            enriched_p["curGDPts"] = live_pts[pid]
        lss = live_scoring_stats.get(pid, {})
        all_players.append(
            {
                **enriched_p,
                "localOwnership": len(owners),
                "localPer": round(len(owners) / len(FRIENDS_IDS) * 100),
                "ownedBy": owners,
                "mdGoals": md_g(pid),
                "mdAssists": md_a(pid),
                "mdCleanSheet": md_cs(pid),
                "mdMins": md_mins(pid, lss.get("mins", 0)),
                "mdSaves": md_saves(pid, lss.get("saves", 0)),
            }
        )
    all_players.sort(key=lambda x: x["totPts"], reverse=True)

    # Fetch World #1's team
    world_leader = None
    try:
        wl_raw = fetch_world_leader_team(matchday)
        if wl_raw:
            wl_players = []
            for rp in wl_raw["rawPlayers"]:
                if rp.get("id") is None:
                    continue
                pid = int(rp["id"])
                pub = public_players.get(pid, {})
                mdpts = live_pts.get(pid) or pub.get("curGDPts", 0)
                is_captain = rp.get("iscaptain", 0) == 1
                is_starter = rp.get("benchposition", 0) == 0
                wl_players.append({
                    "id": pid,
                    "name": pub.get("name", f"#{pid}"),
                    "team": pub.get("team", ""),
                    "teamCode": pub.get("teamCode", ""),
                    "posCode": pub.get("posCode", SKILL_TO_POS.get(rp.get("skill", 3), "MID")),
                    "mdPoints": mdpts,
                    "isCaptain": is_captain,
                    "isStarter": is_starter,
                    "benchPosition": rp.get("benchposition", 0),
                })
            world_leader = {
                "guid": wl_raw["guid"],
                "fullName": wl_raw["fullName"],
                "teamName": wl_raw["teamName"],
                "rank": wl_raw["rank"],
                "matchdayPoints": wl_raw["matchdayPoints"],
                "gdPoints": wl_raw["gdPoints"],
                "ovPoints": wl_raw["ovPoints"],
                "players": wl_players,
            }
    except Exception as e:
        print(f"World leader enrichment failed: {e}")

    return {
        "lastUpdated": datetime.now().isoformat(),
        "matchday": matchday,
        "mdStarted": md_started,
        "totalManagers": len(FRIENDS_IDS),
        "managers": managers,
        "allPlayers": all_players,
        "worldLeader": world_leader,
        "statsArchived": archive is not None,
        "unverified": is_unverified(matchday),
    }


def get_current_matchday():
    """Return the current live matchday from schedule, or fallback to max cached."""
    with live_schedule_lock:
        sched_md = live_schedule.get("matchday", 0)
    if sched_md > 0:
        return sched_md
    return max(cache.keys()) if cache else 11


def refresh_cache(matchday=11):
    """Build fresh MD data from UEFA APIs and persist it to GitHub.

    On Vercel this is the ONLY writer of cache/md{XX}.json. /api/data reads
    from GitHub (with a short per-lambda memo) so every lambda instance sees
    the same data. The GitHub write is synchronous — daemon threads don't
    survive the lambda freeze.
    """
    try:
        public_players_cache.pop(matchday, None)  # clear stale player data
        data = build_data(matchday)
        # Synchronously persist to GitHub — this is the shared source of truth
        save_md_cache(matchday, data)
        # Update local memo so same-lambda follow-ups skip the GitHub round-trip
        with cache_lock:
            cache[matchday] = {"ts": time.time(), "data": data}
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Cache updated for MD{matchday}")
        return data
    except Exception as e:
        print(f"Error refreshing cache: {e}")
        return None


def is_match_window():
    """Check if current time falls within a live match window.

    Only HTM/FTM checkpoints define the window — FINALMD is a morning-after
    trigger (09:00) that would otherwise stretch the window across the whole
    day. Falls back to True if there are any unfired non-FINALMD checkpoints
    and no match-typed checkpoints (shouldn't happen in practice).
    """
    with live_schedule_lock:
        sched = json.loads(json.dumps(live_schedule))
    cps = sched.get("checkpoints", [])
    if not cps:
        return False
    unfired = [cp for cp in cps if not cp.get("fired")]
    if not unfired:
        return False
    match_cps = [cp for cp in cps if cp.get("label", "").startswith(("HTM", "FTM"))]
    if not match_cps:
        return False
    now = datetime.now()
    try:
        times = []
        for cp in match_cps:
            h, m = map(int, cp["time"].split(":"))
            t = now.replace(hour=h, minute=m, second=0, microsecond=0)
            times.append(t)
        window_start = min(times) - timedelta(minutes=30)
        window_end = max(times) + timedelta(minutes=20)
        # Handle the FT-past-midnight case: if FT wraps (e.g. 00:05), the raw
        # window may be inverted for part of the day. Accept either side.
        if window_end < window_start:
            return now >= window_start or now <= window_end
        return window_start <= now <= window_end
    except Exception:
        return False


def is_md_active():
    """True when a matchday is in progress — at least one HTM/FTM has fired
    (or is scheduled for today/past) and FINALMD hasn't fired yet. Used to
    drive periodic refreshes across multi-day knockout rounds where there's
    a gap between leg 1 and leg 2 but the MD is still "live"."""
    with live_schedule_lock:
        sched = json.loads(json.dumps(live_schedule))
    cps = sched.get("checkpoints", [])
    if not cps:
        return False
    finalmd = next((cp for cp in cps if cp.get("label") == "FINALMD"), None)
    if finalmd and finalmd.get("fired"):
        return False
    match_cps = [cp for cp in cps if cp.get("label", "").startswith(("HTM", "FTM"))]
    if not match_cps:
        return False
    # Active if any match checkpoint has already fired (MD has started)
    return any(cp.get("fired") for cp in match_cps)


# scheduler_loop() was removed — Vercel lambdas can't keep background threads
# alive. Its responsibilities moved to /api/cron/tick (invoked minutely by an
# external cron), which handles checkpoint firing and match-window refreshes.


# ── GITHUB SNAPSHOT CONFIG ──────────────────────────────────────────────────
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER  = "Edytzu23"
GITHUB_REPO   = "UCL-Fantasy-Friends"
GITHUB_BRANCH = "main"
SNAPSHOT_DIR  = "snapshots"
GH_API        = "https://api.github.com"

def _gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }

def github_get_file(path):
    """Returns (content_str, sha) or (None, None) if not found."""
    url = f"{GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    r = requests.get(url, headers=_gh_headers(), params={"ref": GITHUB_BRANCH})
    if r.status_code == 200:
        data = r.json()
        import base64
        content = base64.b64decode(data["content"]).decode("utf-8")
        return content, data["sha"]
    return None, None

_last_gh_error = {"code": None, "text": None}

def github_put_file(path, content_str, sha=None, message=None):
    """Create or update a file on GitHub. Returns True on success."""
    import base64
    url = f"{GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    payload = {
        "message": message or f"snapshot: {path}",
        "content": base64.b64encode(content_str.encode("utf-8")).decode("utf-8"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=_gh_headers(), json=payload)
    if r.status_code not in (200, 201):
        print(f"[GitHub] PUT failed {r.status_code}: {r.text[:500]}")
        _last_gh_error["code"] = r.status_code
        _last_gh_error["text"] = r.text[:500]
    else:
        _last_gh_error["code"] = None
        _last_gh_error["text"] = None
    return r.status_code in (200, 201)


# ── LIVE SCHEDULE HELPERS ──────────────────────────────────────────────────

def load_live_schedule():
    """Load schedule.json from GitHub into memory."""
    global live_schedule
    try:
        content, _ = github_get_file(f"{SNAPSHOT_DIR}/schedule.json")
        if content:
            with live_schedule_lock:
                live_schedule = json.loads(content)
            print(f"[LiveSched] Loaded schedule for MD{live_schedule.get('matchday', '?')}")
        else:
            print("[LiveSched] No schedule.json found on GitHub")
    except Exception as e:
        print(f"[LiveSched] Error loading schedule: {e}")


def save_live_schedule():
    """Write current live_schedule to GitHub."""
    with live_schedule_lock:
        data = json.loads(json.dumps(live_schedule))
    content_str = json.dumps(data, ensure_ascii=False, indent=2)
    path = f"{SNAPSHOT_DIR}/schedule.json"
    _, sha = github_get_file(path)
    ok = github_put_file(path, content_str, sha=sha, message=f"schedule MD{data.get('matchday', '?')}")
    if ok:
        print(f"[LiveSched] Saved schedule for MD{data.get('matchday', '?')}")
    return ok


def save_live_checkpoint(md, label):
    """Take a snapshot and append it as a checkpoint to md{XX}_live.json."""
    # Refresh cache to get latest data
    data = refresh_cache(md)
    if not data:
        raise Exception(f"Could not refresh data for MD{md}")

    snapshot = build_snapshot(md, data)

    # Load existing live file or create new
    live_path = f"{SNAPSHOT_DIR}/md{md:02d}_live.json"
    content, sha = github_get_file(live_path)
    if content:
        live_data = json.loads(content)
    else:
        live_data = {"matchday": md, "checkpoints": []}

    # Append checkpoint
    live_data["checkpoints"].append({
        "label": label,
        "savedAt": datetime.now().isoformat(),
        "managers": snapshot["managers"],
        "players": snapshot["players"],
    })

    # Save to GitHub
    content_str = json.dumps(live_data, ensure_ascii=False, indent=2)
    ok = github_put_file(live_path, content_str, sha=sha,
                         message=f"live checkpoint {label} MD{md} — {datetime.now().strftime('%H:%M')}")
    if not ok:
        raise Exception(f"Failed to save live checkpoint to GitHub")

    # Mark checkpoint as fired in schedule
    with live_schedule_lock:
        for cp in live_schedule.get("checkpoints", []):
            if cp["label"] == label and not cp.get("fired"):
                cp["fired"] = True
                break
    save_live_schedule()
    print(f"[LiveSnap] Saved checkpoint '{label}' for MD{md}")

    # Capture immutable per-MD player stats archive while UEFA feeds are warm.
    # FINALMD (09:00 day after) is the canonical write; HTM2/FTM2 act as
    # backups in case FINALMD fails. save_player_stats_archive is idempotent.
    if label in ("HTM2", "FTM2", "FINALMD"):
        try:
            save_player_stats_archive(md, data, source="live_window")
        except Exception as e:
            print(f"[StatsArchive] checkpoint write failed: {e}")
        # Secondary backup from FotMob — independent source for goals/assists/MOTM/CS
        if label == "FINALMD":
            try:
                save_fotmob_backup_archive(md)
            except Exception as e:
                print(f"[StatsFotmob] checkpoint write failed: {e}")


def advance_to_next_md():
    """After FINALMD fires, advance schedule to the next matchday.

    If UPCOMING_MATCHDAYS has an entry for the next MD, build fresh checkpoints
    from the plan (correct kickoff times for that specific matchday). Otherwise
    fall back to reusing the previous times with fired flags reset.
    """
    global live_schedule
    with live_schedule_lock:
        current_md = live_schedule.get("matchday", 0)
        next_md = current_md + 1

    plan = UPCOMING_MATCHDAYS.get(next_md)
    if plan:
        cps = build_checkpoints_from_plan(plan)
        with live_schedule_lock:
            live_schedule = {"matchday": next_md, "checkpoints": cps}
        print(f"[LiveSched] Advanced MD{current_md} → MD{next_md} (from plan)")
    else:
        with live_schedule_lock:
            for cp in live_schedule.get("checkpoints", []):
                cp["fired"] = False
            live_schedule["matchday"] = next_md
        print(f"[LiveSched] Advanced MD{current_md} → MD{next_md} (legacy, no plan)")
    save_live_schedule()


def build_snapshot(md, data):
    """Extract per-player stats snapshot from build_data result."""
    snapshot = {
        "matchday": md,
        "savedAt": datetime.now().isoformat(),
        "managers": [],
        "players": [],
    }
    # Manager clasament
    for m in data["managers"]:
        snapshot["managers"].append({
            "guid": m["guid"],
            "username": m["username"],
            "teamName": m["teamName"],
            "gdPoints": m["gdPoints"],
            "gdRank": m["gdRank"],
            "ovPoints": m["ovPoints"],
            "ovRank": m["ovRank"],
        })
    # All players stats (only those owned by someone in the group, to keep it lean)
    owned_ids = set()
    for m in data["managers"]:
        for p in m["players"]:
            owned_ids.add(p["id"])

    for p in data["allPlayers"]:
        if p["id"] not in owned_ids:
            continue
        snapshot["players"].append({
            "id": p["id"],
            "name": p["name"],
            "fullName": p["fullName"],
            "team": p["team"],
            "teamCode": p["teamCode"],
            "posCode": p["posCode"],
            "totPts": p["totPts"],
            "curGDPts": p.get("curGDPts", 0),
            "goals": p.get("goals", 0),
            "assists": p.get("assists", 0),
            "cleanSheets": p.get("cleanSheets", 0),
            "momCount": p.get("momCount", 0),
            "yellowCards": p.get("yellowCards", 0),
            "redCards": p.get("redCards", 0),
            "value": p.get("value", 0),
            "selPer": p.get("selPer", 0),
        })
    return snapshot


@app.post("/api/snapshot/save")
def save_snapshot(md: int = 11):
    """Save a snapshot for the given MD to GitHub."""
    if not GITHUB_TOKEN:
        return JSONResponse({"error": "GITHUB_TOKEN not set"}, status_code=500)
    # Get current data — memo first, then fresh build (which also writes GitHub)
    with cache_lock:
        entry = cache.get(md)
    data = entry["data"] if (entry and isinstance(entry, dict) and "data" in entry) else None
    if not data:
        data = refresh_cache(md)
    if not data:
        return JSONResponse({"error": "No data available"}, status_code=500)

    snapshot = build_snapshot(md, data)
    content_str = json.dumps(snapshot, ensure_ascii=False, indent=2)
    path = f"{SNAPSHOT_DIR}/md{md:02d}.json"

    # Check if file already exists (need SHA to update)
    _, sha = github_get_file(path)

    import base64
    url = f"{GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{path}"
    payload = {
        "message": f"snapshot MD{md} — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "content": base64.b64encode(content_str.encode("utf-8")).decode("utf-8"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=_gh_headers(), json=payload)
    if r.status_code in (200, 201):
        return JSONResponse({"status": "saved", "path": path, "matchday": md})
    # Return exact GitHub error for debugging
    try:
        gh_err = r.json()
    except:
        gh_err = r.text
    print(f"[GitHub] save failed {r.status_code}: {gh_err}")
    return JSONResponse({"error": f"GitHub {r.status_code}: {gh_err}"}, status_code=500)


@app.get("/api/snapshot/load")
def load_snapshot(md: int = 11):
    """Load a snapshot for the given MD from GitHub."""
    if not GITHUB_TOKEN:
        return JSONResponse({"error": "GITHUB_TOKEN not set"}, status_code=500)
    path = f"{SNAPSHOT_DIR}/md{md:02d}.json"
    content, _ = github_get_file(path)
    if content:
        return JSONResponse(json.loads(content))
    return JSONResponse({"error": f"No snapshot for MD{md}"}, status_code=404)


@app.get("/api/snapshot/list")
def list_snapshots():
    """List all available MD snapshots."""
    if not GITHUB_TOKEN:
        return JSONResponse({"error": "GITHUB_TOKEN not set"}, status_code=500)
    url = f"{GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{SNAPSHOT_DIR}"
    r = requests.get(url, headers=_gh_headers(), params={"ref": GITHUB_BRANCH})
    if r.status_code == 200:
        files = [f["name"] for f in r.json() if f["name"].endswith(".json")]
        mds = []
        live_mds = []
        for f in files:
            if f == "schedule.json":
                continue
            if "_live" in f:
                try:
                    live_mds.append(int(f.replace("md", "").replace("_live.json", "")))
                except:
                    pass
            else:
                try:
                    mds.append(int(f.replace("md", "").replace(".json", "")))
                except:
                    pass
        return JSONResponse({"snapshots": sorted(mds), "liveSnapshots": sorted(live_mds)})
    return JSONResponse({"snapshots": [], "liveSnapshots": []})


# ── PER-MD STATS ARCHIVE ENDPOINTS ─────────────────────────────────────────

@app.post("/api/stats/archive/save")
def admin_save_stats_archive(md: int, overwrite: bool = False):
    """Force-capture the immutable per-MD player stats archive for the given MD.
    Used to lock in data before it can decay from UEFA's feeds. Idempotent
    unless overwrite=true (admin-only safety valve)."""
    if not GITHUB_TOKEN:
        return JSONResponse({"error": "GITHUB_TOKEN not set"}, status_code=500)
    # Prefer fresh data — refresh forces a UEFA pull and writes md cache too.
    data = refresh_cache(md)
    if not data:
        return JSONResponse({"error": "Could not build data for MD"}, status_code=500)
    saved = save_player_stats_archive(md, data, source="manual", overwrite=overwrite)
    return JSONResponse({
        "matchday": md,
        "saved": saved,
        "overwrite": overwrite,
        "playerCount": len(data.get("allPlayers", [])),
    })


@app.post("/api/stats/backup/fotmob/save")
def admin_save_fotmob_backup(md: int, overwrite: bool = False):
    """Force-capture FotMob secondary backup for the given MD.
    Currently no-ops gracefully when FotMob is blocked."""
    if not GITHUB_TOKEN:
        return JSONResponse({"error": "GITHUB_TOKEN not set"}, status_code=500)
    saved = save_fotmob_backup_archive(md, overwrite=overwrite)
    return JSONResponse({
        "matchday": md,
        "saved": saved,
        "overwrite": overwrite,
        "note": "FotMob currently blocked (Cloudflare/Turnstile). Saves nothing until source is re-enabled.",
    })


@app.get("/api/stats/compare/{md}")
def compare_stats_sources(md: int):
    """Return diff between primary (UEFA) and FotMob backup archives for MD.
    Useful for auditing data quality once both sources are populated."""
    return JSONResponse(compare_archives(md))


@app.get("/api/stats/list")
def list_stats_archives():
    """List which MDs have a primary stats archive and/or FotMob backup."""
    if not GITHUB_TOKEN:
        return JSONResponse({"error": "GITHUB_TOKEN not set"}, status_code=500)
    url = f"{GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GH_STATS_DIR}"
    r = requests.get(url, headers=_gh_headers(), params={"ref": GITHUB_BRANCH})
    primary = []
    fotmob = []
    if r.status_code == 200:
        for f in r.json():
            n = f["name"]
            if n.endswith("_players.json"):
                try: primary.append(int(n.replace("md", "").replace("_players.json", "")))
                except: pass
            elif n.endswith("_fotmob.json"):
                try: fotmob.append(int(n.replace("md", "").replace("_fotmob.json", "")))
                except: pass
    return JSONResponse({
        "primary": sorted(primary),
        "fotmob": sorted(fotmob),
        "unverified": sorted(_load_unverified()),
    })


@app.post("/api/stats/unverified/mark")
def admin_mark_unverified(md: int, unverified: bool = True):
    """Mark or unmark a matchday as having unverified/incomplete stats."""
    if not GITHUB_TOKEN:
        return JSONResponse({"error": "GITHUB_TOKEN not set"}, status_code=500)
    ok = mark_md_unverified(md, unverified=unverified)
    return JSONResponse({"matchday": md, "unverified": unverified, "saved": ok})


# ── LIVE SCHEDULE / SNAPSHOT ENDPOINTS ─────────────────────────────────────

@app.get("/api/live-schedule")
def get_live_schedule():
    with live_schedule_lock:
        return JSONResponse(live_schedule)


@app.get("/api/cron/tick")
async def cron_tick(request: Request):
    """Called every minute by an external cron (e.g. cron-job.org). Replaces scheduler_loop() for Vercel serverless."""
    cron_secret = os.environ.get("CRON_SECRET", "")
    if cron_secret:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {cron_secret}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)

    now = datetime.now()
    hm = (now.hour, now.minute)
    actions = []

    # Fixed-time cache refreshes
    if hm in [(21, 45), (23, 15), (9, 0)]:
        md = get_current_matchday()
        refresh_cache(md)
        actions.append(f"refresh_cache MD{md}")

    # Checkpoint firing
    with live_schedule_lock:
        sched = json.loads(json.dumps(live_schedule))

    now_str = now.strftime("%H:%M")
    cps = sched.get("checkpoints", [])
    # FINALMD is a morning-after checkpoint — only let it fire once every
    # HTM/FTM checkpoint has already fired. Otherwise a daily 09:00 tick on
    # day 2 of a 2-day MD would advance the schedule before kickoff.
    match_cps_all_fired = all(
        cp.get("fired") for cp in cps
        if cp.get("label", "").startswith(("HTM", "FTM"))
    ) and any(cp.get("label", "").startswith(("HTM", "FTM")) for cp in cps)
    for cp in cps:
        if cp["time"] == now_str and not cp.get("fired"):
            md = sched["matchday"]
            label = cp["label"]
            if label == "FINALMD" and not match_cps_all_fired:
                actions.append(f"skipped FINALMD for MD{md}: HTM/FTM not all fired")
                break
            try:
                save_live_checkpoint(md, label)
                if label == "FINALMD":
                    advance_to_next_md()
                    actions.append(f"advanced to MD{md + 1}")
                actions.append(f"fired {label} for MD{md}")
            except Exception as e:
                actions.append(f"error firing {label}: {e}")
            break

    # Periodic refresh during active match windows (every minute)
    if is_match_window():
        md = get_current_matchday()
        refresh_cache(md)
        actions.append(f"periodic_refresh MD{md}")
    # Otherwise, if the MD is in progress but outside a live window
    # (e.g. between leg 1 and leg 2 of a knockout round), refresh every
    # 30 minutes so stats stay warm without hammering UEFA.
    elif is_md_active() and now.minute in (0, 30):
        md = get_current_matchday()
        refresh_cache(md)
        actions.append(f"md_active_refresh MD{md}")

    return JSONResponse({"ok": True, "time": now_str, "actions": actions})


@app.post("/api/live-schedule")
def set_live_schedule(req: dict):
    global live_schedule
    md = req.get("matchday")
    checkpoints = req.get("checkpoints", [])
    # Ensure all 5 labels are valid and have fired flag
    for cp in checkpoints:
        cp["fired"] = cp.get("fired", False)
    new_sched = {"matchday": md, "checkpoints": checkpoints}
    with live_schedule_lock:
        live_schedule = new_sched
    save_live_schedule()
    return JSONResponse({"status": "ok", "schedule": new_sched})


@app.post("/api/live-schedule/apply")
def apply_live_schedule(md: int):
    """Apply the hardcoded UPCOMING_MATCHDAYS plan for a specific MD.

    Writes fresh checkpoints derived from the plan's kickoff times, marks
    past-date checkpoints as already fired, and persists to GitHub. Used to
    (re)seed a matchday on demand — auto-advance at FINALMD does this
    automatically for subsequent MDs.
    """
    global live_schedule
    plan = UPCOMING_MATCHDAYS.get(md)
    if not plan:
        return JSONResponse(
            {"error": f"No plan for MD{md}", "known": sorted(UPCOMING_MATCHDAYS.keys())},
            status_code=404,
        )
    cps = build_checkpoints_from_plan(plan)
    new_sched = {"matchday": md, "checkpoints": cps}
    with live_schedule_lock:
        live_schedule = new_sched
    saved = save_live_schedule()
    return JSONResponse({
        "status": "ok" if saved else "save_failed",
        "saved": bool(saved),
        "schedule": new_sched,
        "plan": plan,
        "gh_token_present": bool(GITHUB_TOKEN),
        "gh_token_len": len(GITHUB_TOKEN) if GITHUB_TOKEN else 0,
        "last_gh_error": _last_gh_error,
    })


@app.get("/api/live-snapshot/load")
def load_live_snapshot(md: int):
    if not GITHUB_TOKEN:
        return JSONResponse({"error": "GITHUB_TOKEN not set"}, status_code=500)
    path = f"{SNAPSHOT_DIR}/md{md:02d}_live.json"
    content, _ = github_get_file(path)
    if content:
        return JSONResponse(json.loads(content))
    return JSONResponse({"error": f"No live snapshot for MD{md}"}, status_code=404)


@app.post("/api/live-snapshot/fire")
def fire_live_snapshot(md: int, label: str = "Manual"):
    if not GITHUB_TOKEN:
        return JSONResponse({"error": "GITHUB_TOKEN not set"}, status_code=500)
    try:
        save_live_checkpoint(md, label)
        if label == "FINALMD":
            advance_to_next_md()
        return JSONResponse({"status": "saved", "matchday": md, "label": label})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/ucl-fixtures")
def ucl_fixtures():
    """Proxy UEFA fixtures feed (avoids CORS)."""
    try:
        r = requests.get(
            "https://gaming.uefa.com/en/uclfantasy/services/feeds/fixtures/fixtures_80_en.json",
            headers=PUBLIC_HEADERS, verify=False, timeout=20)
        return JSONResponse(content=r.json())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/ucl-match/{match_id}")
def ucl_match(match_id: int):
    """Proxy individual UEFA match detail (avoids CORS)."""
    try:
        r = requests.get(
            f"https://match.uefa.com/v5/matches/{match_id}",
            headers={"User-Agent": PUBLIC_HEADERS["User-Agent"]}, verify=False, timeout=20)
        return JSONResponse(content=r.json())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/match-detail/{match_id}")
def match_detail(match_id: int):
    """Returns combined match data: events, lineups, and fantasy scoring for a single match."""
    import concurrent.futures

    def fetch_match():
        try:
            r = requests.get(
                f"https://match.uefa.com/v5/matches/{match_id}",
                headers=MATCH_HEADERS, verify=False, timeout=10
            )
            return r.json() if r.status_code == 200 else {}
        except Exception:
            return {}

    def fetch_lineups():
        try:
            r = requests.get(
                f"https://match.uefa.com/v5/matches/{match_id}/lineups",
                headers=MATCH_HEADERS, verify=False, timeout=10
            )
            return r.json() if r.status_code == 200 else {}
        except Exception:
            return {}

    def fetch_fantasy():
        try:
            url = f"https://gaming.uefa.com/en/uclfantasy/services/feeds/scoring/live-scores_80_{match_id}.json"
            r = requests.get(url, headers=PUBLIC_HEADERS, verify=False, timeout=10)
            if r.status_code != 200:
                return {}
            raw = r.json()
            data = raw.get("data", raw)
            if isinstance(data, dict) and "value" in data:
                data = data["value"]
            # Build player scoring dict
            p_points = data.get("pPoints", [])
            pp_dict = {}
            if isinstance(p_points, list):
                for pp in p_points:
                    if isinstance(pp, dict):
                        pid = str(pp.get("pId", ""))
                        if pid:
                            pp_dict[pid] = pp.get("tPoints", 0) or 0
            players = {}
            for ps in data.get("pStats", []):
                if not isinstance(ps, dict): continue
                pid = str(ps.get("pId", ps.get("id", "")))
                if not pid: continue
                players[pid] = {
                    "pts": pp_dict.get(pid, 0),
                    "goals": ps.get("gS", 0) or 0,
                    "assists": ps.get("gA", 0) or 0,
                    "cs": 1 if ps.get("cS", 0) else 0,
                    "yc": ps.get("yC", 0) or 0,
                    "rc": ps.get("rC", 0) or 0,
                    "saves": ps.get("saves", ps.get("sV", ps.get("sv", 0))) or 0,
                    "mins": ps.get("oF", ps.get("mP", 0)) or 0,
                }
            return players
        except Exception:
            return {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        fut_match = ex.submit(fetch_match)
        fut_lineups = ex.submit(fetch_lineups)
        fut_fantasy = ex.submit(fetch_fantasy)
        match_data = fut_match.result()
        lineups_data = fut_lineups.result()
        fantasy_data = fut_fantasy.result()

    status = (match_data or {}).get("status", "")
    cc = "public, max-age=86400, immutable" if status == "FINISHED" else "public, max-age=10"
    return JSONResponse(
        content={"match": match_data, "lineups": lineups_data, "fantasy": fantasy_data},
        headers={"Cache-Control": cc},
    )


DATA_MEMO_TTL = 20          # seconds — only applies to the current MD
GITHUB_CACHE_TTL = 4 * 3600  # 4 hours — stale GitHub cache triggers rebuild for current MD


@app.get("/api/data")
def get_data(md: int = None):
    # When no md is passed, default to the live MD — unless UEFA hasn't
    # locked rosters yet (pre-deadline placeholder). In that window we keep
    # the previous, finalized MD on screen so clasament shows real standings
    # instead of a zeroed-out snapshot of the upcoming MD.
    if md is None:
        md = get_current_matchday()
        if md > 1:
            with cache_lock:
                live_entry = cache.get(md)
            live_data = live_entry.get("data") if isinstance(live_entry, dict) else None
            if live_data is None:
                live_data = load_md_cache(md)
            if live_data is None or (isinstance(live_data, dict) and live_data.get("mdStarted") is False):
                md = md - 1
    current_md = get_current_matchday()
    is_current = (md == current_md)
    # Previous MD shown as stand-in while current MD hasn't started yet —
    # needs TTL treatment so second-leg results aren't missed.
    is_recent_previous = (not is_current and md == current_md - 1)
    now = time.time()

    # Past MDs never change → long browser cache. Live/recent-previous → short TTL.
    if is_current or is_recent_previous:
        cc = "public, max-age=20"
    else:
        cc = "public, max-age=86400, immutable"
    cache_headers = {"Cache-Control": cc}

    # Local memo check: historical MDs cached forever, current MD for DATA_MEMO_TTL.
    # recent_previous bypasses memo so the GitHub TTL check below can run.
    with cache_lock:
        entry = cache.get(md)
    if entry and isinstance(entry, dict) and "data" in entry and not is_recent_previous:
        if not is_current or (now - entry.get("ts", 0) < DATA_MEMO_TTL):
            return JSONResponse(entry["data"], headers=cache_headers)

    # GitHub is the shared source of truth — any lambda that hits /api/cron/tick
    # during a match window writes fresh data there. Read it back.
    data = load_md_cache(md)
    if data:
        # Discard stale GitHub cache for current MD and recent previous MD
        if is_current or is_recent_previous:
            try:
                lu = datetime.fromisoformat(data["lastUpdated"])
                if (datetime.now() - lu).total_seconds() > GITHUB_CACHE_TTL:
                    data = None
            except Exception:
                pass
    if data:
        with cache_lock:
            cache[md] = {"ts": now, "data": data}
        return JSONResponse(data, headers=cache_headers)

    # GitHub has nothing for this MD yet (e.g. brand-new MD before first cron
    # tick). Build it now, which also persists to GitHub for everyone else.
    data = refresh_cache(md)
    if data:
        return JSONResponse(data, headers=cache_headers)
    return JSONResponse({"error": "Failed to fetch data"}, status_code=500)


@app.get("/api/status")
def get_status():
    md = get_current_matchday()
    with cache_lock:
        entry = cache.get(md)
    data = entry["data"] if (entry and isinstance(entry, dict) and "data" in entry) else None
    last_updated = data["lastUpdated"] if data else None
    live_window = is_match_window()

    # Also check cached live-scores for current and next MD
    # (avoid slow probe — let /api/live-scores do the fetching)
    if not live_window:
        now = time.time()
        with live_scores_lock:
            for check_md in (md, md + 1):
                cached_live = live_scores_cache.get(check_md)
                if cached_live and (now - cached_live["ts"]) < 300 and cached_live["data"].get("live"):
                    live_window = True
                    md = check_md
                    break

    return JSONResponse({
        "matchday": md,
        "lastUpdated": last_updated,
        "liveWindow": live_window,
    })


@app.get("/api/live-scores")
def get_live_scores(md: int = None):
    if md is None:
        md = get_current_matchday()
    """Return live fantasy scores. Cached for 30s."""
    now = time.time()
    with live_scores_lock:
        cached = live_scores_cache.get(md)
        if cached and (now - cached["ts"]) < LIVE_SCORES_TTL:
            return JSONResponse(cached["data"])

    data = fetch_live_scores(md)
    has_live = any(m["status"] in (3, "3", "LIVE") for m in data.get("matches", []))
    data["live"] = has_live
    data["fetchedAt"] = datetime.now().isoformat()

    with live_scores_lock:
        live_scores_cache[md] = {"data": data, "ts": now}

    return JSONResponse(data)


@app.post("/api/refresh")
def manual_refresh(md: int = 11):
    data = refresh_cache(md)
    if data:
        return JSONResponse({"status": "ok", "lastUpdated": data["lastUpdated"]})
    return JSONResponse({"error": "Refresh failed"}, status_code=500)


# ── Scouting endpoints ──────────────────────────────────────────────

@app.get("/api/scouting/matchup")
def scouting_matchup(home: str, away: str):
    """Pre-match scouting stats for a specific matchup."""
    data = get_scouting_matchup(home.upper(), away.upper())
    if "error" in data:
        return JSONResponse(data, status_code=404)
    return JSONResponse(data)


@app.get("/api/scouting/all")
def scouting_all(round: str = "QF"):
    """All matchup scouting data for a round."""
    return JSONResponse(get_all_matchups(round))


@app.get("/api/scouting/team/{team_code}")
def scouting_team(team_code: str):
    """Single team scouting data."""
    data = get_team_scouting(team_code.upper())
    if "error" in data:
        return JSONResponse(data, status_code=404)
    return JSONResponse(data)


@app.get("/api/scouting/bracket")
def scouting_bracket():
    """Full UCL knockout bracket with dates and scores."""
    data = fetch_ucl_bracket()
    if "error" in data:
        return JSONResponse(data, status_code=500)
    return JSONResponse(data)


def _is_mobile(request: Request) -> bool:
    ua = request.headers.get("user-agent", "")
    return bool(any(kw in ua for kw in ["Mobile", "Android", "iPhone", "iPad", "iPod", "Windows Phone"]))


@app.get("/")
def landing(request: Request):
    if _is_mobile(request):
        return RedirectResponse("/mobile", status_code=302)
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "templates", "landing.html")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Eroare: templates/landing.html lipseste din repo!</h1>", status_code=500)


@app.get("/mobile")
def mobile_dashboard():
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "templates", "mockup_mobile.html")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Eroare: templates/mockup_mobile.html lipseste din repo!</h1>", status_code=500)


@app.get("/dashboard")
def dashboard():
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "templates", "mockup.html")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Eroare: templates/mockup.html lipseste din repo!</h1>", status_code=500)


@app.get("/xpts")
def xpts_page():
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "xpts.html")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Eroare: xpts.html lipseste din repo!</h1>", status_code=500)


@app.get("/old")
def index_old():
    base = os.path.dirname(os.path.abspath(__file__))
    for path in [
        os.path.join(base, "templates", "index.html"),
        os.path.join(base, "index.html"),
    ]:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return HTMLResponse(f.read())
    return HTMLResponse("<h1>Eroare: templates/index.html lipseste din repo!</h1>", status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
