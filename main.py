from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import requests
import json
import urllib3
from datetime import datetime, timedelta
import threading
import time
import os

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=lambda: refresh_cache(10), daemon=True).start()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    threading.Thread(target=lambda: load_live_schedule(), daemon=True).start()
    yield

app = FastAPI(lifespan=lifespan)

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

# ── LIVE SCORES CACHE ─────────────────────────────────────────────────────
live_scores_cache = {}       # md -> {"data": {...}, "ts": float}
live_scores_lock = threading.Lock()
LIVE_SCORES_TTL = 30         # seconds

# ── LIVE SNAPSHOT SCHEDULE ─────────────────────────────────────────────────
CHECKPOINT_LABELS = ["HTM1", "FTM1", "HTM2", "FTM2", "FINALMD"]
live_schedule = {"matchday": 0, "checkpoints": []}
live_schedule_lock = threading.Lock()


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
            # curGDPts=live, lastGdPoints=finalized; use whichever is higher
            "curGDPts": max(p.get("curGDPts", 0) or 0, p.get("lastGdPoints", 0) or 0),
            "lastGdPts": p.get("lastGdPoints", 0) or 0,
            "goals": p.get("gS", 0) or 0,
            "assists": p.get("assist", 0) or 0,
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
    managers = []
    for uid in FRIENDS_IDS:
        url = f"https://gaming.uefa.com/en/uclfantasy/services/api/Gameplay/user/{uid}/opponent-team"
        params = {"matchdayId": matchday, "phaseId": phase_id, "opponentguid": uid}
        try:
            r = requests.get(
                url, params=params, headers=HEADERS, verify=False, timeout=10
            )
            if r.status_code == 200:
                data = r.json()["data"]["value"]
                managers.append(
                    {
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
                )
            else:
                print(f"HTTP {r.status_code} for {uid[:8]}")
        except Exception as e:
            print(f"Error {uid[:8]}: {e}")
    return managers


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
    events = {}  # pid -> {goals, assists}
    live_teams = set()  # teams currently playing (for clean sheet calc)
    conceded = set()  # teams that conceded a goal
    team_players = {}  # team_id -> set of player_ids on pitch
    for mid in match_ids:
        try:
            r = requests.get(
                f"https://match.uefa.com/v5/matches/{mid}",
                headers=MATCH_HEADERS, verify=False, timeout=8
            )
            if r.status_code != 200:
                continue
            m = r.json()
            status = m.get("status", "")
            if status not in ("LIVE", "FINISHED"):
                continue
            home_id = m.get("homeTeam", {}).get("id")
            away_id = m.get("awayTeam", {}).get("id")
            live_teams.add(home_id)
            live_teams.add(away_id)
            score = m.get("score", {}).get("total", {})
            if (score.get("away") or 0) > 0:
                conceded.add(home_id)
            if (score.get("home") or 0) > 0:
                conceded.add(away_id)
            # Collect player events (goals + assists)
            pe = m.get("playerEvents", {})
            for scorer in pe.get("scorers", []):
                goal_type = scorer.get("goalType", "")
                if goal_type == "OWN_GOAL":
                    continue
                pid = int(scorer.get("player", {}).get("id", 0))
                if pid:
                    events.setdefault(pid, {"goals": 0, "assists": 0})
                    events[pid]["goals"] += 1
                # Check for assist player
                assist_player = scorer.get("assistPlayer") or scorer.get("assist", {})
                if isinstance(assist_player, dict) and assist_player.get("id"):
                    apid = int(assist_player["id"])
                    events.setdefault(apid, {"goals": 0, "assists": 0})
                    events[apid]["assists"] += 1
            # Collect lineup player IDs per team for clean sheet
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
                        # include subs that came on
                        for p in lineups.get(side, {}).get("substitutions", {}).get("playerIn", []) if isinstance(lineups.get(side, {}).get("substitutions"), dict) else []:
                            pids.add(int(p.get("player", {}).get("id", 0)))
                        team_players[tid] = pids
            except Exception:
                pass
        except Exception as e:
            print(f"Error fetching match {mid}: {e}")
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


def build_data(matchday=10):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching MD{matchday}...")

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
    if matchday > 1:
        try:
            prev_managers_raw = fetch_team_data(matchday - 1)
            for mgr in prev_managers_raw:
                prev_team_ids[mgr["guid"]] = {int(rp["id"]) for rp in mgr["rawPlayers"]}
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

    managers_raw = fetch_team_data(matchday)

    player_ownership = {}  # pid -> list of usernames

    managers = []
    for mgr in managers_raw:
        enriched = []
        current_ids = {int(rp["id"]) for rp in mgr["rawPlayers"]}
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
            pid = int(rp["id"])
            pub = public_players.get(pid, {})
            mdpts = rp.get("overallpoints") or 0
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
                # Per-MD stats: prefer live events, fallback to diff
                "mdGoals": live_events.get(pid, {}).get("goals") or md_stat(pid, "goals", public_players, prev_players),
                "mdAssists": live_events.get(pid, {}).get("assists") or md_stat(pid, "assists", public_players, prev_players),
                "mdCleanSheet": (1 if pid in live_clean_sheet_pids else 0) or md_stat(pid, "cleanSheets", public_players, prev_players),
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

        managers.append(
            {
                "guid": mgr["guid"],
                "username": mgr["username"],
                "teamName": mgr["teamName"],
                "gdPoints": mgr["gdPoints"],
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
        all_players.append(
            {
                **p,
                "localOwnership": len(owners),
                "localPer": round(len(owners) / len(FRIENDS_IDS) * 100),
                "ownedBy": owners,
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
                pid = int(rp["id"])
                pub = public_players.get(pid, {})
                mdpts = rp.get("overallpoints") or 0
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
        "totalManagers": len(FRIENDS_IDS),
        "managers": managers,
        "allPlayers": all_players,
        "worldLeader": world_leader,
    }


def refresh_cache(matchday=10):
    try:
        public_players_cache.pop(matchday, None)  # clear stale player data
        data = build_data(matchday)
        with cache_lock:
            cache[matchday] = data
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Cache updated for MD{matchday}")
        return data
    except Exception as e:
        print(f"Error refreshing cache: {e}")
        return None


def is_match_window():
    """Check if current time falls within a live match window."""
    with live_schedule_lock:
        sched = json.loads(json.dumps(live_schedule))
    cps = sched.get("checkpoints", [])
    if not cps:
        return False
    unfired = [cp for cp in cps if not cp.get("fired")]
    if not unfired:
        return False
    now = datetime.now()
    try:
        times = []
        for cp in cps:
            h, m = map(int, cp["time"].split(":"))
            t = now.replace(hour=h, minute=m, second=0, microsecond=0)
            times.append(t)
        window_start = min(times) - timedelta(minutes=30)
        window_end = max(times) + timedelta(minutes=20)
        return window_start <= now <= window_end
    except Exception:
        return False


def scheduler_loop():
    """Run auto-refresh at 21:45, 23:15, 09:00 + live checkpoint triggers + periodic during matches."""
    last_periodic = 0
    PERIODIC_INTERVAL = 300  # 5 minutes

    while True:
        now = datetime.now()
        hm = (now.hour, now.minute)
        if hm in [(21, 45), (23, 15), (9, 0)]:
            md = max(cache.keys()) if cache else 10
            refresh_cache(md)
            last_periodic = time.time()
            time.sleep(90)  # don't double-trigger within same minute

        # Live checkpoint triggers
        with live_schedule_lock:
            sched = json.loads(json.dumps(live_schedule))
        if sched.get("checkpoints"):
            now_str = now.strftime("%H:%M")
            for cp in sched["checkpoints"]:
                if cp["time"] == now_str and not cp.get("fired"):
                    md = sched["matchday"]
                    label = cp["label"]
                    print(f"[AutoSnap] Firing '{label}' for MD{md} at {now_str}")
                    try:
                        save_live_checkpoint(md, label)
                        if label == "FINALMD":
                            advance_to_next_md()
                    except Exception as e:
                        print(f"[AutoSnap] Error: {e}")
                    last_periodic = time.time()
                    time.sleep(90)
                    break

        # Periodic refresh during match windows
        if is_match_window() and (time.time() - last_periodic) >= PERIODIC_INTERVAL:
            md = sched.get("matchday") or (max(cache.keys()) if cache else 10)
            print(f"[AutoRefresh] Periodic refresh for MD{md} (match window active)")
            refresh_cache(md)
            last_periodic = time.time()

        time.sleep(30)




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


def advance_to_next_md():
    """After FINALMD fires, advance schedule to next matchday."""
    global live_schedule
    with live_schedule_lock:
        current_md = live_schedule.get("matchday", 10)
        next_md = current_md + 1
        # Keep same times, reset fired flags
        for cp in live_schedule.get("checkpoints", []):
            cp["fired"] = False
        live_schedule["matchday"] = next_md
    save_live_schedule()
    print(f"[LiveSched] Advanced from MD{current_md} to MD{next_md}")


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
def save_snapshot(md: int = 10):
    """Save a snapshot for the given MD to GitHub."""
    if not GITHUB_TOKEN:
        return JSONResponse({"error": "GITHUB_TOKEN not set"}, status_code=500)
    # Get current data
    with cache_lock:
        data = cache.get(md)
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
def load_snapshot(md: int = 10):
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


# ── LIVE SCHEDULE / SNAPSHOT ENDPOINTS ─────────────────────────────────────

@app.get("/api/live-schedule")
def get_live_schedule():
    with live_schedule_lock:
        return JSONResponse(live_schedule)


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


@app.get("/api/data")
def get_data(md: int = 10):
    with cache_lock:
        if md in cache:
            return JSONResponse(cache[md])
    data = refresh_cache(md)
    if data:
        return JSONResponse(data)
    return JSONResponse({"error": "Failed to fetch data"}, status_code=500)


@app.get("/api/status")
def get_status():
    md = max(cache.keys()) if cache else 10
    with cache_lock:
        data = cache.get(md)
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
def get_live_scores(md: int = 10):
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
def manual_refresh(md: int = 10):
    data = refresh_cache(md)
    if data:
        return JSONResponse({"status": "ok", "lastUpdated": data["lastUpdated"]})
    return JSONResponse({"error": "Refresh failed"}, status_code=500)


@app.get("/")
def index():
    base = os.path.dirname(os.path.abspath(__file__))
    for path in [
        os.path.join(base, "templates", "index.html"),
        os.path.join(base, "index.html"),
    ]:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return HTMLResponse(f.read())
    return HTMLResponse("<h1>Eroare: templates/index.html lipseste din repo!</h1>", status_code=500)


@app.get("/new")
def mockup():
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "templates", "mockup.html")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Eroare: templates/mockup.html lipseste din repo!</h1>", status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
