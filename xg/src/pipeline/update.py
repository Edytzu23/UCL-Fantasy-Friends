"""
Main update pipeline.

Orchestrates:
1. Fetch player stats from FotMob (xG, xA, goals, assists, minutes)
2. Fetch supplementary stats from FBref (recoveries, cards, saves, etc.)
2b. Cross-validate overlapping fields between FotMob and FBref; write merged row
3. Store stats in DB
4. Compute xPts for all players with upcoming fixtures
5. Store predictions in DB

Run manually or via scheduler.
"""

import time
import datetime
from concurrent.futures import ThreadPoolExecutor

from src.config import LEAGUES
from src.data.fotmob import (
    fetch_team_stats, fetch_team_players,
    find_team_league, get_build_id,
    fetch_player_recent_matches,
    fetch_fotmob_leaderboard_player_stats,
)
from src.data.fbref import fetch_all_supplementary_stats
from src.data.elo import fetch_elo_ratings, get_team_elo
from src.db.queries import (
    get_all_teams, get_team_by_fotmob, upsert_player,
    upsert_player_stats, upsert_team_stats, get_player_stats,
    get_upcoming_fixtures, get_predictions_for_fixture,
    upsert_prediction,
)
from src.models.xpts import calc_xpts, build_player_input
from src.models.adjustments import get_all_factors
from src.db import connection as db


CURRENT_SEASON = "2025/26"


# ── Step 1: Update team & player stats from FotMob ────────────────────

def update_fotmob_stats(league_name=None):
    """Fetch xG/xA stats for all teams in a league (or all leagues) and store them."""
    leagues_to_update = [league_name] if league_name else list(LEAGUES.keys())
    updated_players = 0
    updated_teams = 0

    for lg in leagues_to_update:
        print(f"\n[pipeline] Updating FotMob stats: {lg}")
        try:
            from src.data.fotmob import fetch_league_table
            table = fetch_league_table(lg)
            fotmob_team_ids = list(table["all"].keys())

            for fotmob_tid in fotmob_team_ids:
                # Get or create team in DB
                team = get_team_by_fotmob(fotmob_tid)
                if not team:
                    continue

                # Team stats
                try:
                    ts = fetch_team_stats(lg, fotmob_tid)
                    upsert_team_stats(
                        team_id=team["id"],
                        season=CURRENT_SEASON,
                        source_league=lg,
                        **ts,
                    )
                    updated_teams += 1
                except Exception as e:
                    print(f"  [warn] Team stats {fotmob_tid}: {e}")

                # Player stats
                try:
                    players = fetch_team_players(lg, fotmob_tid)
                    for p in players:
                        player_id = upsert_player(
                            name=p["name"],
                            team_id=team["id"],
                            position=p["position"],
                            fotmob_id=p["fotmob_id"],
                        )
                        upsert_player_stats(
                            player_id=player_id,
                            season=CURRENT_SEASON,
                            source_league=lg,
                            data_source="fotmob",
                            matches=p.get("games", 0),
                            minutes=p.get("minutes", 0),
                            goals=p.get("goals", 0),
                            assists=p.get("assists", 0),
                            xg=p.get("xg", 0),
                            npxg=p.get("xg", 0),   # FotMob doesn't separate npxG, use xG
                            xa=p.get("xa", 0),
                        )
                        updated_players += 1
                except Exception as e:
                    print(f"  [warn] Player stats {fotmob_tid}: {e}")

        except Exception as e:
            print(f"  [error] {lg}: {e}")

    print(f"\n[pipeline] FotMob update done: {updated_teams} teams, {updated_players} players")
    return {"teams": updated_teams, "players": updated_players}


# ── Step 1b: Overlay FotMob CDN leaderboard totals ────────────────────

def update_fotmob_leaderboard_stats(league_name=None):
    """Overlay league-wide leaderboard totals onto the fotmob row.

    The team-scrape in update_fotmob_stats populates cards/shots/positional
    metadata but its minutes/matches/goals/xg/xa are scope-limited (only the
    team's own matches contribute). The league leaderboard gives true season
    totals. We overwrite only those numeric fields; everything else stays.
    """
    leagues_to_update = [league_name] if league_name else list(LEAGUES.keys())
    overwrite_fields = ("matches", "minutes", "goals", "assists", "xg", "xa", "npxg")
    updated = 0

    for lg in leagues_to_update:
        print(f"\n[pipeline] FotMob leaderboard overlay: {lg}")
        try:
            records = fetch_fotmob_leaderboard_player_stats(lg)
        except Exception as e:
            print(f"  [error] leaderboard fetch failed: {e}")
            continue

        if not records:
            print(f"  (no leaderboard data)")
            continue

        conn = db.get_connection()
        matched = 0
        for rec in records:
            fm_id = rec.get("fotmob_id")
            if not fm_id:
                continue
            row = conn.execute(
                "SELECT id FROM players WHERE fotmob_id = ?", (fm_id,)
            ).fetchone()
            if not row:
                continue
            pid = row["id"]
            # Only overwrite the season-total fields. Leaves cards/shots/etc
            # from the team-scrape fotmob row intact.
            sets = ", ".join(f"{f} = ?" for f in overwrite_fields)
            params = [rec.get(f, 0) for f in overwrite_fields]
            params += [pid, CURRENT_SEASON, lg]
            result = conn.execute(f"""
                UPDATE player_stats SET {sets}
                WHERE player_id = ? AND season = ? AND source_league = ?
                  AND data_source = 'fotmob'
            """, params)
            if result.rowcount > 0:
                matched += 1
                updated += 1
        conn.commit()
        conn.close()
        print(f"  {matched} fotmob rows overlaid from leaderboard")

    print(f"\n[pipeline] FotMob leaderboard overlay done: {updated} rows")
    return {"players": updated}


# ── Step 2: Update supplementary stats from FBref ─────────────────────

def update_fbref_stats(league_name=None):
    """Fetch supplementary stats from FBref and merge into player_stats."""
    leagues_to_update = [league_name] if league_name else list(LEAGUES.keys())
    updated = 0

    for lg in leagues_to_update:
        print(f"\n[pipeline] Updating FBref stats: {lg}")
        try:
            supplementary = fetch_all_supplementary_stats(lg)
            for player_name, stats in supplementary.items():
                # Find player in DB by name (approximate match)
                conn = db.get_connection()
                rows = conn.execute(
                    "SELECT p.id FROM players p WHERE LOWER(p.name) = LOWER(?)",
                    (player_name,)
                ).fetchall()
                conn.close()

                if not rows:
                    continue

                for row in rows:
                    pid = row["id"]
                    mins90 = stats.get("minutes_90s") or 0
                    minutes = int(float(mins90) * 90) if mins90 else 0

                    # Per-match rates for cards (FBref gives totals)
                    matches = stats.get("matches") or (minutes // 70 or 1)

                    upsert_player_stats(
                        player_id=pid,
                        season=CURRENT_SEASON,
                        source_league=lg,
                        data_source="fbref",
                        minutes=minutes,
                        tackles=int(stats.get("tackles") or 0),
                        interceptions=int(stats.get("interceptions") or 0),
                        blocks=int(stats.get("blocks") or 0),
                        clearances=int(stats.get("clearances") or 0),
                        recoveries=int(stats.get("recoveries") or 0),
                        yellow_cards=int(stats.get("yellow_cards") or 0),
                        red_cards=int(stats.get("red_cards") or 0),
                        penalties_won=int(stats.get("penalties_won") or 0),
                        shots=int(stats.get("shots") or 0),
                        shots_on_target=int(stats.get("shots_on_target") or 0),
                        saves=int(stats.get("saves") or 0),
                        npxg=float(stats.get("npxg") or 0),
                        shots_outside_box=0,   # derived from avg_distance if needed
                        goals_outside_box=0,
                    )
                    updated += 1

        except Exception as e:
            print(f"  [error] FBref {lg}: {e}")

    print(f"\n[pipeline] FBref update done: {updated} player stat rows")
    return {"players": updated}


# ── Step 2bb: Derived defensive stats (saves / recoveries / outside-box) ──

# Position-based fallback rates for players not present in FotMob defensive
# leaderboards. Per90 values for recoveries; pct of shots outside the box.
_RECOVERY_BASELINE_PER90 = {"DEF": 6.0, "MID": 8.0, "FWD": 3.0, "GK": 0.0}
_OUTSIDE_BOX_GOAL_RATE = {"FWD": 0.08, "MID": 0.20, "DEF": 0.15, "GK": 0.0}
_OUTSIDE_BOX_SHOT_RATE = {"FWD": 0.22, "MID": 0.45, "DEF": 0.35, "GK": 0.0}

# FotMob CDN stat keys confirmed via probe.
_DERIVED_FOTMOB_STATS = {
    "saves":       "saves",
    "recoveries":  "ball_recovery",
}


def _resolve_player_by_fotmob_or_name(conn, fotmob_pid, name):
    """Try fotmob_id first, then exact name (case-insensitive), then last name."""
    if fotmob_pid:
        row = conn.execute(
            "SELECT id, position FROM players WHERE fotmob_id = ?", (fotmob_pid,)
        ).fetchone()
        if row:
            return row
    if not name:
        return None
    row = conn.execute(
        "SELECT id, position FROM players WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    if row:
        return row
    parts = name.split()
    if parts:
        row = conn.execute(
            "SELECT id, position FROM players WHERE LOWER(name) LIKE LOWER(?)",
            (f"%{parts[-1]}%",),
        ).fetchone()
        if row:
            return row
    return None


def update_derived_defensive_stats(league_name=None):
    """Fill saves / recoveries / outside-box gaps using FotMob leaderboards + baselines.

    Writes one row per player with data_source='derived'. Cross-validate will later
    pull these fields into the merged row.

    Sources:
      - saves:      FotMob CDN 'saves.json' (GK only, ~20 entries/league)
      - recoveries: FotMob CDN 'ball_recovery.json' (~300 entries/league)
      - shots_outside_box / goals_outside_box: position-based baseline (no free source)
    """
    from src.data.fotmob import fetch_stat_leaderboard

    leagues_to_update = [league_name] if league_name else list(LEAGUES.keys())
    season = CURRENT_SEASON
    updated = 0

    for lg in leagues_to_update:
        print(f"\n[pipeline] Deriving defensive stats: {lg}")

        # 1. Fetch leaderboard entries
        leaderboards = {}
        for field, stat_key in _DERIVED_FOTMOB_STATS.items():
            try:
                leaderboards[field] = fetch_stat_leaderboard(lg, stat_key)
            except Exception as e:
                print(f"  [warn] {stat_key} leaderboard fetch failed: {e}")
                leaderboards[field] = []

        # 2. Build per-player index from leaderboards
        #    {player_id: {saves: total, recoveries: total, minutes: mins}}
        conn = db.get_connection()
        player_data = {}

        for field, entries in leaderboards.items():
            for e in entries:
                fotmob_pid = e.get("ParticiantId") or e.get("ParticipantId")
                name = e.get("ParticipantName")
                total = e.get("SubStatValue") or 0
                mins = e.get("MinutesPlayed") or 0
                if total <= 0:
                    continue
                row = _resolve_player_by_fotmob_or_name(conn, fotmob_pid, name)
                if not row:
                    continue
                pid = row["id"]
                entry = player_data.setdefault(pid, {
                    "position": row["position"],
                    "saves": 0,
                    "recoveries": 0,
                    "minutes": 0,
                })
                entry[field] = int(total)
                entry["minutes"] = max(entry["minutes"], int(mins))

        # 3. Pull season totals from existing merged/fotmob rows for baseline
        #    scaling (minutes, goals, shots) for players in this league.
        base_rows = conn.execute("""
            SELECT ps.player_id, p.position, ps.minutes, ps.goals, ps.shots
            FROM player_stats ps
            JOIN players p ON p.id = ps.player_id
            WHERE ps.season = ? AND ps.source_league = ?
              AND ps.data_source IN ('merged', 'fotmob')
              AND ps.minutes > 0
        """, (season, lg)).fetchall()

        # Prefer merged over fotmob when both exist
        base_by_pid = {}
        for r in base_rows:
            pid = r["player_id"]
            existing = base_by_pid.get(pid)
            if not existing or (r["minutes"] or 0) > (existing["minutes"] or 0):
                base_by_pid[pid] = dict(r)

        # 4. Write derived row per player
        for pid, base in base_by_pid.items():
            pos = base["position"] or "MID"
            mins = int(base["minutes"] or 0)
            total_shots = int(base["shots"] or 0)
            total_goals = int(base["goals"] or 0)

            from_lb = player_data.get(pid, {})

            # Saves: leaderboard only (GK-relevant). 0 if not a keeper in list.
            saves = from_lb.get("saves", 0)

            # Recoveries: leaderboard if present, else position baseline × minutes.
            if "recoveries" in from_lb:
                recoveries = from_lb["recoveries"]
            else:
                per90 = _RECOVERY_BASELINE_PER90.get(pos, 0.0)
                scale = min(1.0, mins / 1000.0) if mins else 0.0
                recoveries = int(round(per90 * (mins / 90.0) * scale)) if mins else 0

            # Outside-box shots/goals: pure position baseline proportional to totals.
            shots_obx = int(round(total_shots * _OUTSIDE_BOX_SHOT_RATE.get(pos, 0.0)))
            goals_obx = int(round(total_goals * _OUTSIDE_BOX_GOAL_RATE.get(pos, 0.0)))

            upsert_player_stats(
                player_id=pid,
                season=season,
                source_league=lg,
                data_source="derived",
                minutes=mins,
                saves=saves,
                recoveries=recoveries,
                shots_outside_box=shots_obx,
                goals_outside_box=goals_obx,
            )
            updated += 1

        conn.close()
        print(f"  {updated} derived rows written so far (league: {lg})")

    print(f"\n[pipeline] Derived stats done: {updated} rows")
    return {"players": updated}


# ── Step 2c: Promote fotmob row to merged, overlay derived defensive ───

def cross_validate_stats(league_name=None):
    """Promote each fotmob row to a merged row, overlaying derived defensive fields.

    Since we dropped Understat, there's no second source to cross-check against.
    The FotMob leaderboard overlay (update_fotmob_leaderboard_stats) already fixed
    the scope bug, so the fotmob row is authoritative. We copy it verbatim into a
    data_source='merged' row (data_quality=1.0) and overlay the derived row's
    defensive fields (saves / recoveries / outside-box).
    """
    season = CURRENT_SEASON
    conn = db.get_connection()

    league_filter = "AND source_league = ?" if league_name else ""
    league_params = [league_name] if league_name else []

    rows = conn.execute(f"""
        SELECT DISTINCT player_id, source_league
        FROM player_stats
        WHERE data_source = 'fotmob' AND season = ?
        {league_filter}
    """, [season] + league_params).fetchall()

    if not rows:
        print("[cross-val] No fotmob rows to promote")
        conn.close()
        return 0

    print(f"[cross-val] Promoting {len(rows)} fotmob rows to merged...")
    merged_count = 0
    now = datetime.datetime.utcnow().isoformat()
    skip = {"id", "player_id", "season", "source_league", "data_source",
            "data_quality", "fetched_at", "is_penalty_taker"}

    for r in rows:
        pid = r["player_id"]
        league = r["source_league"]

        fm_row = conn.execute(
            "SELECT * FROM player_stats WHERE player_id=? AND season=? AND source_league=? AND data_source='fotmob'",
            (pid, season, league)
        ).fetchone()
        if not fm_row:
            continue
        fm = dict(fm_row)

        dv_row = conn.execute(
            "SELECT * FROM player_stats WHERE player_id=? AND season=? AND source_league=? AND data_source='derived'",
            (pid, season, league)
        ).fetchone()
        dv = dict(dv_row) if dv_row else {}

        merged = {k: v for k, v in fm.items() if k not in skip}

        # Overlay derived defensive fields when present
        for dv_field in ("saves", "recoveries", "shots_outside_box", "goals_outside_box"):
            dv_val = dv.get(dv_field)
            if dv_val:
                merged[dv_field] = dv_val

        cols = list(merged.keys()) + ["player_id", "season", "source_league",
                                       "data_source", "data_quality", "fetched_at"]
        vals = list(merged.values()) + [pid, season, league, "merged", 1.0, now]
        placeholders = ", ".join("?" * len(cols))
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in
                            {"player_id", "season", "source_league", "data_source"})

        conn.execute(f"""
            INSERT INTO player_stats ({", ".join(cols)})
            VALUES ({placeholders})
            ON CONFLICT(player_id, season, source_league, data_source)
            DO UPDATE SET {updates}
        """, vals)
        merged_count += 1

    conn.commit()
    conn.close()
    print(f"[cross-val] Done: {merged_count} merged rows written")
    return merged_count


# ── Step 2c: Per-player recent matches (Last-5 Form) ──────────────────

def update_player_match_history(
    competition_id=None,
    limit_per_player=8,
    rate_limit_s=0.3,
    only_with_fotmob_id=True,
    skip_if_fresh_hours=24,
):
    """Fetch last-N matches for each active player into player_match_performance.

    When `competition_id` is given, limits to players on teams in that competition.
    Skips players whose most recent row was fetched within `skip_if_fresh_hours`.
    """
    conn = db.get_connection()
    where = ["p.fotmob_id IS NOT NULL"] if only_with_fotmob_id else []
    params = []
    if competition_id:
        where.append("""p.team_id IN (
            SELECT team_id FROM competition_teams WHERE competition_id = ?
        )""")
        params.append(competition_id)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(f"""
        SELECT p.id, p.fotmob_id, p.name
        FROM players p
        {where_sql}
        ORDER BY p.id
    """, params).fetchall()
    players = [dict(r) for r in rows]
    conn.close()

    print(f"[pipeline] Player match history: {len(players)} players queued "
          f"(limit {limit_per_player}, sleep {rate_limit_s}s)")

    fetched = 0
    skipped = 0
    failed = 0
    start = time.time()

    for i, p in enumerate(players):
        pid = p["id"]
        fid = p["fotmob_id"]

        conn = db.get_connection()
        fresh = conn.execute("""
            SELECT MAX(fetched_at) AS last FROM player_match_performance
            WHERE player_id = ?
        """, (pid,)).fetchone()["last"]
        if fresh:
            age_hours = conn.execute(
                "SELECT (julianday('now') - julianday(?)) * 24 AS h", (fresh,)
            ).fetchone()["h"]
            if age_hours is not None and age_hours < skip_if_fresh_hours:
                conn.close()
                skipped += 1
                continue
        conn.close()

        matches = fetch_player_recent_matches(fid, limit=limit_per_player)
        if not matches:
            failed += 1
            time.sleep(rate_limit_s)
            continue

        conn = db.get_connection()
        for m in matches:
            conn.execute("""
                INSERT INTO player_match_performance (
                    player_id, match_id, match_date, opponent_fotmob_id, is_home,
                    minutes, goals, assists, yellow_cards, red_cards,
                    clean_sheet, rating
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id, match_id) DO UPDATE SET
                    match_date=excluded.match_date,
                    opponent_fotmob_id=excluded.opponent_fotmob_id,
                    is_home=excluded.is_home,
                    minutes=excluded.minutes,
                    goals=excluded.goals, assists=excluded.assists,
                    yellow_cards=excluded.yellow_cards, red_cards=excluded.red_cards,
                    clean_sheet=excluded.clean_sheet, rating=excluded.rating,
                    fetched_at=datetime('now')
            """, (
                pid, m["match_id"], m["match_date"], m["opponent_fotmob_id"],
                m["is_home"], m["minutes"], m["goals"], m["assists"],
                m["yellow_cards"], m["red_cards"], m["clean_sheet"], m["rating"],
            ))
        conn.commit()
        conn.close()

        fetched += 1
        if fetched % 50 == 0:
            elapsed = int(time.time() - start)
            print(f"  [{fetched}/{len(players)}] fetched — {elapsed}s elapsed, "
                  f"{skipped} skipped, {failed} failed")
        time.sleep(rate_limit_s)

    print(f"[pipeline] Player match history done: {fetched} fetched, "
          f"{skipped} fresh-skipped, {failed} failed")
    return {"fetched": fetched, "skipped": skipped, "failed": failed}


def _last5_form_input(player_id, season_input, season_stats_row):
    """Build last-5 form input dict matching build_player_input() shape.

    Strategy:
      - Pull last 5 matches with minutes >= 15 from player_match_performance
      - Real fields: minutes (for recent_minutes), yellow/red card rates
      - For npxg_per90 / xa_per90 / saves_per90 / recoveries_per90:
        apply a form_multiplier derived from (actual G+A) vs (expected G+A
        based on season per90). Clamped to [0.6, 1.6].
    Returns (last5_input_dict, recent_minutes_list) or (None, None) if
    insufficient data.
    """
    conn = db.get_connection()
    matches = conn.execute("""
        SELECT minutes, goals, assists, yellow_cards, red_cards, clean_sheet
        FROM player_match_performance
        WHERE player_id = ? AND minutes >= 15
        ORDER BY match_date DESC
        LIMIT 5
    """, (player_id,)).fetchall()
    conn.close()

    if len(matches) < 2:
        return None, None

    total_mins = sum(m["minutes"] for m in matches) or 0
    total_ga   = sum((m["goals"] or 0) + (m["assists"] or 0) for m in matches)
    total_yc   = sum(m["yellow_cards"] or 0 for m in matches)
    total_rc   = sum(m["red_cards"] or 0 for m in matches)

    if total_mins <= 0:
        return None, None

    season_ga_per90 = (season_input.get("npxg_per90", 0) or 0) + (season_input.get("xa_per90", 0) or 0)
    expected_ga = season_ga_per90 * (total_mins / 90.0)
    if expected_ga > 0.1:
        form_mult = (total_ga + 0.2) / (expected_ga + 0.2)
    else:
        form_mult = 1.0 + min(total_ga * 0.15, 0.6)
    form_mult = max(0.6, min(1.6, form_mult))

    recent_minutes = [int(m["minutes"]) for m in matches]

    return {
        "npxg_per90":            (season_input.get("npxg_per90") or 0) * form_mult,
        "xa_per90":              (season_input.get("xa_per90") or 0) * form_mult,
        "saves_per90":            season_input.get("saves_per90", 0),
        "recoveries_per90":       season_input.get("recoveries_per90", 0),
        "yellow_card_rate":       total_yc / len(matches),
        "red_card_rate":          total_rc / len(matches),
        "outside_box_goal_rate":  season_input.get("outside_box_goal_rate", 0),
        "form_multiplier":        round(form_mult, 3),
    }, recent_minutes


# ── Step 3: Compute xPts for all upcoming fixtures ────────────────────

def compute_all_xpts(competition_id, scoring_system="ucl", form_filter="season"):
    """Compute xPts predictions for every player in every upcoming fixture.

    form_filter: 'season' (weighted season stats, default) or 'last5'
      (applies last-5-match form multiplier and real last-5 minutes/cards).
    """
    fixtures = get_upcoming_fixtures(competition_id)
    if not fixtures:
        print(f"[pipeline] No upcoming fixtures for competition {competition_id}")
        return 0

    elo_ratings = fetch_elo_ratings()

    # Get all team names in this competition for avg Elo calculation
    conn = db.get_connection()
    comp_teams = conn.execute("""
        SELECT t.name FROM competition_teams ct
        JOIN teams t ON ct.team_id = t.id
        WHERE ct.competition_id = ?
    """, (competition_id,)).fetchall()
    conn.close()
    comp_team_names = [r["name"] for r in comp_teams]

    total_predictions = 0
    for fixture in fixtures:
        count = _compute_fixture_xpts(
            fixture, scoring_system, elo_ratings, comp_team_names, form_filter=form_filter
        )
        total_predictions += count

    print(f"\n[pipeline] xPts computed ({form_filter}): {total_predictions} predictions for {len(fixtures)} fixtures")
    return total_predictions


def _compute_fixture_xpts(fixture, scoring_system, elo_ratings, comp_team_names, form_filter="season"):
    """Compute xPts for all players in one fixture."""
    from src.db.queries import get_players_by_team
    from src.db import connection as db_conn

    home_tid = fixture["home_team_id"]
    away_tid = fixture["away_team_id"]
    fixture_id = fixture["id"]

    # Get team stats for CS probability calculation
    def get_team_context(team_id, team_name):
        conn = db_conn.get_connection()
        ts = conn.execute("""
            SELECT * FROM team_stats
            WHERE team_id = ? AND season = ?
            ORDER BY fetched_at DESC LIMIT 1
        """, (team_id, CURRENT_SEASON)).fetchone()
        conn.close()
        if ts:
            ts = dict(ts)
            matches = ts.get("matches", 1) or 1
            return {
                "xga_per_match": (ts.get("xg_against") or ts.get("goals_against") or 0) / matches,
                "cs_rate": (ts.get("clean_sheets") or 0) / matches,
                "league": ts.get("source_league", ""),
            }
        return {"xga_per_match": 1.2, "cs_rate": 0.25, "league": ""}

    home_ctx = get_team_context(home_tid, fixture["home_name"])
    away_ctx = get_team_context(away_tid, fixture["away_name"])

    count = 0
    for team_id, team_name, opp_ctx in [
        (home_tid, fixture["home_name"], away_ctx),
        (away_tid, fixture["away_name"], home_ctx),
    ]:
        players = get_players_by_team(team_id)
        factors = get_all_factors(
            player_league_name=opp_ctx.get("league", ""),
            opponent_name=fixture["away_name"] if team_id == home_tid else fixture["home_name"],
            competition_teams=comp_team_names,
            ratings=elo_ratings,
        )

        for player in players:
            pid = player["id"]
            pos = player.get("position", "MID")

            stat_rows = get_player_stats(pid, CURRENT_SEASON)
            if not stat_rows:
                continue

            # Prefer the cross-validated merged row; fall back to manual merge
            merged_row  = next((r for r in stat_rows if r.get("data_source") == "merged"), None)
            fotmob_row  = next((r for r in stat_rows if r.get("data_source") == "fotmob"), None)
            fbref_row   = next((r for r in stat_rows if r.get("data_source") == "fbref"), None)
            derived_row = next((r for r in stat_rows if r.get("data_source") == "derived"), None)

            if merged_row:
                merged = dict(merged_row)
            elif fotmob_row:
                # Legacy fallback: manual merge (runs before cross_validate_stats)
                merged = dict(fotmob_row)
                if fbref_row:
                    for key in ("recoveries", "yellow_cards", "red_cards", "saves", "npxg",
                                "tackles", "interceptions", "blocks"):
                        if fbref_row.get(key) is not None:
                            merged[key] = fbref_row[key]
            else:
                continue

            # Overlay derived defensive fields for non-top-5 leagues where no merged row exists
            if derived_row:
                for key in ("saves", "recoveries", "shots_outside_box", "goals_outside_box"):
                    val = derived_row.get(key)
                    if val:
                        merged[key] = val

            # Build weighted inputs (only current season available; last season = 0 for now)
            player_input = build_player_input([None, merged, merged])

            # Recent minutes: approximate from games and average minutes
            games = int(merged.get("matches") or 1)
            avg_mins = (merged.get("minutes") or 0) / games
            recent_mins = [min(int(avg_mins), 90)] * min(games, 6)

            # Last-5 form override
            if form_filter == "last5":
                l5_input, l5_mins = _last5_form_input(pid, player_input, merged)
                if l5_input:
                    player_input = l5_input
                    recent_mins = l5_mins
                # If no last-5 data, silently fall back to season (player_input unchanged)

            # Check penalty taker status
            conn = db_conn.get_connection()
            pen_row = conn.execute(
                "SELECT * FROM penalty_takers WHERE player_id = ? AND season = ? AND priority = 1",
                (pid, CURRENT_SEASON)
            ).fetchone()
            conn.close()
            is_pen_taker = bool(pen_row)

            result = calc_xpts(
                position=pos,
                scoring_system=scoring_system,
                recent_minutes=recent_mins,
                npxg_per90=player_input["npxg_per90"],
                xa_per90=player_input["xa_per90"],
                saves_per90=player_input["saves_per90"],
                recoveries_per90=player_input["recoveries_per90"],
                yellow_card_rate=player_input["yellow_card_rate"],
                red_card_rate=player_input["red_card_rate"],
                outside_box_goal_rate=player_input["outside_box_goal_rate"],
                team_xga_per_match=opp_ctx["xga_per_match"],
                opponent_xg_per_match=opp_ctx["xga_per_match"],
                team_cs_rate=opp_ctx["cs_rate"],
                is_penalty_taker=is_pen_taker,
                competition_factor=factors["competition_factor"],
                opponent_factor=factors["opponent_factor"],
                apply_adjustments=True,
            )

            upsert_prediction(
                player_id=pid,
                fixture_id=fixture_id,
                scoring_system=scoring_system,
                components=result,
                xpts_raw=result["xpts_raw"],
                xpts_adjusted=result["xpts_adjusted"],
                factors={
                    "competition": factors["competition_factor"],
                    "opponent":    factors["opponent_factor"],
                },
                probs={
                    "play":  result["prob_play"],
                    "60min": result["prob_60min"],
                    "cs":    result["prob_cs"],
                },
                form_filter=form_filter,
            )
            count += 1

    return count


# ── Full pipeline run ─────────────────────────────────────────────────

def run_full_update(competition_id=None, scoring_system="ucl",
                    skip_fbref=False, league_filter=None):
    """Run the full pipeline: fetch → store → compute xPts."""
    start = time.time()
    print("\n" + "=" * 50)
    print("xPts Engine — Full Update Pipeline")
    print("=" * 50)

    # Step 1: FotMob stats (team-scrape: cards, shots, positions)
    fotmob_result = update_fotmob_stats(league_filter)

    # Step 1b: Overlay correct season totals from FotMob CDN leaderboards
    leaderboard_result = update_fotmob_leaderboard_stats(league_filter)

    # Step 2: FBref supplementary (optional, slower; 403-blocked on most leagues)
    fbref_result = {}
    if not skip_fbref:
        fbref_result = update_fbref_stats(league_filter)

    # Step 2bb: Derived defensive stats (saves / recoveries / outside-box)
    derived_result = update_derived_defensive_stats(league_filter)

    # Step 2c: Promote fotmob to merged, overlay derived defensive fields
    cross_result = cross_validate_stats(league_filter)

    # Step 3: xPts predictions (uses merged rows when available)
    xpts_count = 0
    if competition_id:
        xpts_count = compute_all_xpts(competition_id, scoring_system)

    elapsed = round(time.time() - start, 1)
    print(f"\n[pipeline] Done in {elapsed}s")
    print(f"  FotMob:      {fotmob_result.get('players', 0)} players, {fotmob_result.get('teams', 0)} teams")
    print(f"  Leaderboard: {leaderboard_result.get('players', 0)} rows overlaid")
    print(f"  FBref:       {fbref_result.get('players', 0)} stat rows")
    print(f"  Derived:     {derived_result.get('players', 0)} stat rows")
    print(f"  Merged:      {cross_result} promoted rows")
    print(f"  xPts:        {xpts_count} predictions")
    return {
        "fotmob":          fotmob_result,
        "leaderboard":     leaderboard_result,
        "fbref":           fbref_result,
        "derived":         derived_result,
        "cross_validated": cross_result,
        "xpts":            xpts_count,
        "elapsed_seconds": elapsed,
    }
