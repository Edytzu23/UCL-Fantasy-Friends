from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
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
    yield

app = FastAPI(lifespan=lifespan)

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
            "curGDPts": p.get("curGDPts", 0) or 0,
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


def build_data(matchday=10):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching MD{matchday}...")

    public_players = fetch_public_players(matchday)
    managers_raw = fetch_team_data(matchday)

    player_ownership = {}  # pid -> list of usernames

    managers = []
    for mgr in managers_raw:
        enriched = []
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
                "selPer": pub.get("selPer", 0),
                "rating": pub.get("rating", 0),
                "status": pub.get("status", "A"),
                "managerGuid": mgr["guid"],
                "managerName": mgr["username"],
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
    """Run auto-refresh at 21:45, 23:15, 09:00"""
    while True:
        now = datetime.now()
        hm = (now.hour, now.minute)
        if hm in [(21, 45), (23, 15), (9, 0)]:
            md = max(cache.keys()) if cache else 10
            refresh_cache(md)
            time.sleep(90)  # don't double-trigger within same minute
        time.sleep(30)



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
    path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)