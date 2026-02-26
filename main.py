app = FastAPI()
MANAGER_MAPPING = {
    "Mihai": "Mike",
    "Chirila": "Pui",
    "Memedine Zidane": "Radu",
    "Pep Voicuiola": "Voiq",
    "Eduard": "Edi"
}

def remap_manager(name):
    for key, val in MANAGER_MAPPING.items():
        if key.lower() in name.lower():
            return val
    return name

def process_data(raw_dump, official_data):
    official_map = {str(p['id']): p for p in official_data.get('data', {}).get('value', {}).get('playerList', [])}
    processed_players = {}
    managers_list = []
    live_ranking = []
    overall_ranking = []

    for item in raw_dump:
        val = item.get('data', {}).get('value', {})
        mgr_name = remap_manager(val.get('username', "Unknown"))
        managers_list.append(mgr_name)

        live_ranking.append({"Antrenor": mgr_name, "Scor": val.get('gdPoints', 0)})
        overall_ranking.append({"Antrenor": mgr_name, "Total": val.get('ovPoints', 0)})

        for p in val.get('playerid', []):
            p_id = str(p.get('id'))
            is_cap = p.get('iscaptain', 0)
            total_pts = p.get('overallpoints', 0)
            # Puncte RAW (impartim la 2 daca e capitan)
            raw_pts = total_pts / 2 if is_cap == 1 else total_pts
            
            off = official_map.get(p_id, {})
            d_name = off.get('pDName') or off.get('webName') or f"Player {p_id}"
            
            if d_name not in processed_players:
                processed_players[d_name] = {
                    "name": d_name,
                    "pos": {1:"GK", 2:"DEF", 3:"MID", 4:"FWD"}.get(off.get('skill'), "OTH"),
                    "team": off.get('tName', "UNK"),
                    "price": off.get('value', 0) / 10 if off.get('value', 0) > 50 else off.get('value', 0),
                    "global_pick": off.get('selPer', 0),
                    "points": raw_pts,
                    "owners": [mgr_name],
                    "normName": d_name.lower()
                }
            else:
                processed_players[d_name]["owners"].append(mgr_name)
                if raw_pts > processed_players[d_name]["points"]:
                    processed_players[d_name]["points"] = raw_pts

    return {
        "managers": sorted(managers_list),
        "players": list(processed_players.values()),
        "live_ranking": live_ranking,
        "overall_ranking": overall_ranking
    }
