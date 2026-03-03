from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import requests
import json
import urllib3
from datetime import datetime
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

SKILL_TO_POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

cache = {}
cache_lock = threading.Lock()

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
                # Per-MD stats (diff vs previous MD)
                "mdGoals": md_stat(pid, "goals", public_players, prev_players),
                "mdAssists": md_stat(pid, "assists", public_players, prev_players),
                "mdCleanSheet": md_stat(pid, "cleanSheets", public_players, prev_players),
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

    return {
        "lastUpdated": datetime.now().isoformat(),
        "matchday": matchday,
        "totalManagers": len(FRIENDS_IDS),
        "managers": managers,
        "allPlayers": all_players,
    }


def refresh_cache(matchday=10):
    try:
        data = build_data(matchday)
        with cache_lock:
            cache[matchday] = data
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Cache updated for MD{matchday}")
        return data
    except Exception as e:
        print(f"Error refreshing cache: {e}")
        return None


def scheduler_loop():
    """Run auto-refresh at 21:45, 23:15, 09:00 + live checkpoint triggers."""
    while True:
        now = datetime.now()
        hm = (now.hour, now.minute)
        if hm in [(21, 45), (23, 15), (9, 0)]:
            md = max(cache.keys()) if cache else 10
            refresh_cache(md)
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
                    time.sleep(90)
                    break

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


@app.get("/api/data")
def get_data(md: int = 10):
    with cache_lock:
        if md in cache:
            return JSONResponse(cache[md])
    data = refresh_cache(md)
    if data:
        return JSONResponse(data)
    return JSONResponse({"error": "Failed to fetch data"}, status_code=500)


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
