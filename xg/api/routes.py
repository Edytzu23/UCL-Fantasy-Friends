"""
API route handlers for xPts Engine.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from typing import Optional, List
from src.db import connection as db
from src.db.queries import get_upcoming_fixtures, get_predictions_for_fixture

router = APIRouter()

CURRENT_SEASON = "2025/26"


# ── Rankings ──────────────────────────────────────────────────────────────

@router.get("/rankings")
def get_rankings(
    competition_id: Optional[int] = Query(None, description="Filter by competition"),
    fixture_id: Optional[int] = Query(None, description="Filter by specific fixture"),
    scoring_system: str = Query("ucl", description="ucl or worldcup"),
    position: Optional[str] = Query(None, description="GK, DEF, MID, FWD"),
    team_id: Optional[int] = Query(None, description="Filter by team"),
    aggregate: bool = Query(False, description="Sum xPts across all upcoming fixtures"),
    form_filter: str = Query("season", description="'season' or 'last5'"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
):
    """Top players ranked by xPts_adjusted.

    By default shows each player's NEXT upcoming fixture prediction (one row per player).
    Pass `fixture_id` to get rankings for a single specific fixture.
    Pass `aggregate=true` to sum xPts across all upcoming fixtures per player.
    """
    conn = db.get_connection()
    try:
        if aggregate and competition_id and not fixture_id:
            # Aggregate: sum xPts across all upcoming fixtures for the competition
            pos_filter = " AND p.position = ?" if position else ""
            team_filter = " AND p.team_id = ?" if team_id else ""
            extra_params = []
            if position:
                extra_params.append(position.upper())
            if team_id:
                extra_params.append(team_id)

            rows = conn.execute(f"""
                SELECT
                    p.id          AS player_id,
                    p.name        AS player_name,
                    p.position,
                    t.id          AS team_id,
                    t.name        AS team_name,
                    t.code        AS team_code,
                    t.fotmob_id   AS team_fotmob_id,
                    l.name        AS league,
                    COUNT(xp.fixture_id) AS fixture_count,
                    SUM(xp.xpts_raw)      AS xpts_raw,
                    SUM(xp.xpts_adjusted) AS xpts_adjusted,
                    AVG(xp.prob_play)  AS prob_play,
                    AVG(xp.prob_60min) AS prob_60min,
                    AVG(xp.prob_cs)    AS prob_cs
                FROM xpts_predictions xp
                JOIN players p ON xp.player_id = p.id
                JOIN teams t ON p.team_id = t.id
                LEFT JOIN leagues l ON t.league_id = l.id
                JOIN fixtures f ON xp.fixture_id = f.id
                WHERE f.competition_id = ? AND xp.scoring_system = ?
                    AND xp.form_filter = ?
                    AND f.status = 'scheduled'
                    {pos_filter}{team_filter}
                GROUP BY p.id
                ORDER BY xpts_adjusted DESC
                LIMIT ? OFFSET ?
            """, [competition_id, scoring_system, form_filter] + extra_params + [limit, offset]).fetchall()

        else:
            # Default: one row per player — their NEXT fixture prediction
            wheres = ["xp.scoring_system = ?", "xp.form_filter = ?"]
            params: list = [scoring_system, form_filter]

            if fixture_id:
                wheres.append("xp.fixture_id = ?")
                params.append(fixture_id)
            elif competition_id:
                wheres.append("f.competition_id = ?")
                params.append(competition_id)
                wheres.append("f.status = 'scheduled'")

            if position:
                wheres.append("p.position = ?")
                params.append(position.upper())

            if team_id:
                wheres.append("p.team_id = ?")
                params.append(team_id)

            where_str = " AND ".join(wheres)

            rows = conn.execute(f"""
                SELECT
                    p.id          AS player_id,
                    p.name        AS player_name,
                    p.position,
                    t.id          AS team_id,
                    t.name        AS team_name,
                    t.code        AS team_code,
                    t.fotmob_id   AS team_fotmob_id,
                    l.name        AS league,
                    f.id          AS fixture_id,
                    ht.name       AS home_team,
                    ht.code       AS home_code,
                    ht.fotmob_id  AS home_fotmob_id,
                    at.name       AS away_team,
                    at.code       AS away_code,
                    at.fotmob_id  AS away_fotmob_id,
                    f.match_date,
                    xp.xpts_raw,
                    xp.xpts_adjusted,
                    xp.x_min_pts,
                    xp.x_goal_pts,
                    xp.x_assist_pts,
                    xp.x_cs_pts,
                    xp.x_gc_pts,
                    xp.x_save_pts,
                    xp.x_recovery_pts,
                    xp.x_bonus_pts,
                    xp.x_disc_pts,
                    xp.x_pen_pts,
                    xp.x_outside_box_pts,
                    xp.prob_play,
                    xp.prob_60min,
                    xp.prob_cs,
                    xp.competition_factor,
                    xp.opponent_factor
                FROM xpts_predictions xp
                JOIN players p ON xp.player_id = p.id
                JOIN teams t ON p.team_id = t.id
                LEFT JOIN leagues l ON t.league_id = l.id
                JOIN fixtures f ON xp.fixture_id = f.id
                JOIN teams ht ON f.home_team_id = ht.id
                JOIN teams at ON f.away_team_id = at.id
                WHERE {where_str}
                -- One row per player: pick their earliest upcoming fixture
                AND xp.fixture_id = (
                    SELECT xp2.fixture_id
                    FROM xpts_predictions xp2
                    JOIN fixtures f2 ON xp2.fixture_id = f2.id
                    WHERE xp2.player_id = p.id AND xp2.scoring_system = xp.scoring_system
                      AND xp2.form_filter = xp.form_filter
                      AND f2.status = 'scheduled'
                    ORDER BY f2.match_date ASC
                    LIMIT 1
                )
                ORDER BY xp.xpts_adjusted DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset]).fetchall()

        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Competitions ─────────────────────────────────────────────────────────

@router.get("/competitions")
def get_competitions():
    """List all competitions."""
    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT * FROM competitions ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Fixtures ─────────────────────────────────────────────────────────────

@router.get("/fixtures")
def get_fixtures(
    competition_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None, description="scheduled, finished, live"),
):
    """List fixtures, optionally filtered by competition and status."""
    conn = db.get_connection()
    try:
        wheres = []
        params = []
        if competition_id:
            wheres.append("f.competition_id = ?")
            params.append(competition_id)
        if status:
            wheres.append("f.status = ?")
            params.append(status)

        where_str = ("WHERE " + " AND ".join(wheres)) if wheres else ""

        rows = conn.execute(f"""
            SELECT f.*,
                   ht.name as home_name, ht.code as home_code, ht.fotmob_id as home_fotmob_id,
                   at.name as away_name, at.code as away_code, at.fotmob_id as away_fotmob_id,
                   c.name as competition_name, c.scoring_system
            FROM fixtures f
            JOIN teams ht ON f.home_team_id = ht.id
            JOIN teams at ON f.away_team_id = at.id
            JOIN competitions c ON f.competition_id = c.id
            {where_str}
            ORDER BY f.match_date
        """, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/fixtures/{fixture_id}/predictions")
def get_fixture_predictions(
    fixture_id: int,
    scoring_system: str = Query("ucl"),
):
    """Get all xPts predictions for one fixture."""
    rows = get_predictions_for_fixture(fixture_id, scoring_system)
    if not rows:
        raise HTTPException(status_code=404, detail="No predictions found for this fixture")
    return rows


# ── Players ───────────────────────────────────────────────────────────────

@router.get("/players")
def get_players(
    team_id: Optional[int] = Query(None),
    position: Optional[str] = Query(None),
    league: Optional[str] = Query(None, description="League name filter"),
    search: Optional[str] = Query(None, description="Name search"),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
):
    """List players with their latest stats."""
    conn = db.get_connection()
    try:
        wheres = []
        params = []

        if team_id:
            wheres.append("p.team_id = ?")
            params.append(team_id)
        if position:
            wheres.append("p.position = ?")
            params.append(position.upper())
        if league:
            wheres.append("l.name = ?")
            params.append(league)
        if search:
            wheres.append("p.name LIKE ?")
            params.append(f"%{search}%")

        where_str = ("WHERE " + " AND ".join(wheres)) if wheres else ""

        rows = conn.execute(f"""
            SELECT p.id, p.name, p.position, p.fotmob_id,
                   t.id as team_id, t.name as team_name, t.code as team_code,
                   l.name as league,
                   ps.matches, ps.minutes, ps.goals, ps.assists,
                   ps.xg, ps.xa, ps.npxg
            FROM players p
            JOIN teams t ON p.team_id = t.id
            LEFT JOIN leagues l ON t.league_id = l.id
            LEFT JOIN player_stats ps ON ps.player_id = p.id
                AND ps.season = '{CURRENT_SEASON}' AND ps.data_source = 'fotmob'
            {where_str}
            ORDER BY ps.xg DESC NULLS LAST
            LIMIT ? OFFSET ?
        """, params + [limit, offset]).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/players/{player_id}")
def get_player(player_id: int):
    """Player detail: stats + all xPts predictions."""
    conn = db.get_connection()
    try:
        player = conn.execute("""
            SELECT p.*, t.name as team_name, t.code as team_code, l.name as league
            FROM players p
            JOIN teams t ON p.team_id = t.id
            LEFT JOIN leagues l ON t.league_id = l.id
            WHERE p.id = ?
        """, (player_id,)).fetchone()

        if not player:
            raise HTTPException(status_code=404, detail="Player not found")

        player = dict(player)

        stats = conn.execute("""
            SELECT * FROM player_stats WHERE player_id = ? ORDER BY season DESC, data_source
        """, (player_id,)).fetchall()
        player["stats"] = [dict(r) for r in stats]

        predictions = conn.execute("""
            SELECT xp.*,
                   ht.name as home_team, at.name as away_team,
                   f.match_date, f.status as fixture_status
            FROM xpts_predictions xp
            JOIN fixtures f ON xp.fixture_id = f.id
            JOIN teams ht ON f.home_team_id = ht.id
            JOIN teams at ON f.away_team_id = at.id
            WHERE xp.player_id = ?
            ORDER BY f.match_date DESC
        """, (player_id,)).fetchall()
        player["predictions"] = [dict(r) for r in predictions]

        return player
    finally:
        conn.close()


@router.get("/players/{player_id}/breakdown")
def get_player_breakdown(
    player_id: int,
    fixture_id: int = Query(..., description="Fixture to explain the prediction for"),
    scoring_system: str = Query("ucl"),
    form_filter: str = Query("season"),
):
    """Detailed provenance for a single prediction: raw stats, per90 inputs,
    opponent context, derived fields, data sources. For live validity checks."""
    from src.models.xpts import build_player_input, _per90

    conn = db.get_connection()
    try:
        player = conn.execute("""
            SELECT p.*, t.name AS team_name, t.code AS team_code, t.fotmob_id AS team_fotmob_id
            FROM players p JOIN teams t ON p.team_id = t.id
            WHERE p.id = ?
        """, (player_id,)).fetchone()
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        player = dict(player)

        pred = conn.execute("""
            SELECT xp.*,
                   ht.name AS home_team, ht.id AS home_team_id,
                   at.name AS away_team, at.id AS away_team_id,
                   f.match_date
            FROM xpts_predictions xp
            JOIN fixtures f ON xp.fixture_id = f.id
            JOIN teams ht ON f.home_team_id = ht.id
            JOIN teams at ON f.away_team_id = at.id
            WHERE xp.player_id = ? AND xp.fixture_id = ?
              AND xp.scoring_system = ? AND xp.form_filter = ?
        """, (player_id, fixture_id, scoring_system, form_filter)).fetchone()
        if not pred:
            raise HTTPException(status_code=404, detail="Prediction not found")
        pred = dict(pred)

        stat_rows = conn.execute("""
            SELECT * FROM player_stats WHERE player_id = ? AND season = ?
            ORDER BY CASE data_source
                WHEN 'merged' THEN 1 WHEN 'fotmob' THEN 2
                WHEN 'understat' THEN 3 WHEN 'derived' THEN 4 ELSE 5 END
        """, (player_id, CURRENT_SEASON)).fetchall()
        stat_rows = [dict(r) for r in stat_rows]

        by_source = {r["data_source"]: r for r in stat_rows}
        primary = by_source.get("merged") or by_source.get("fotmob") or (stat_rows[0] if stat_rows else None)

        per90 = build_player_input([None, primary, primary]) if primary else {}

        # Opponent context (the team the player is NOT on)
        opp_team_id = pred["away_team_id"] if pred["home_team_id"] == player["team_id"] else pred["home_team_id"]
        opp_name = pred["away_team"] if pred["home_team_id"] == player["team_id"] else pred["home_team"]
        opp_ts = conn.execute("""
            SELECT * FROM team_stats WHERE team_id = ? AND season = ?
            ORDER BY fetched_at DESC LIMIT 1
        """, (opp_team_id, CURRENT_SEASON)).fetchone()
        opp_ctx = {"opponent_id": opp_team_id, "opponent_name": opp_name}
        if opp_ts:
            opp_ts = dict(opp_ts)
            m = opp_ts.get("matches") or 1
            opp_ctx.update({
                "matches":          m,
                "goals_against":    opp_ts.get("goals_against"),
                "xg_against":       opp_ts.get("xg_against"),
                "clean_sheets":     opp_ts.get("clean_sheets"),
                "xga_per_match":    round(((opp_ts.get("xg_against") or opp_ts.get("goals_against") or 0) / m), 3),
                "cs_rate":          round(((opp_ts.get("clean_sheets") or 0) / m), 3),
                "source_league":    opp_ts.get("source_league"),
            })

        # Team context (player's own team — used for CS calc)
        own_ts = conn.execute("""
            SELECT * FROM team_stats WHERE team_id = ? AND season = ?
            ORDER BY fetched_at DESC LIMIT 1
        """, (player["team_id"], CURRENT_SEASON)).fetchone()
        own_ctx = {}
        if own_ts:
            own_ts = dict(own_ts)
            m = own_ts.get("matches") or 1
            own_ctx = {
                "matches":       m,
                "clean_sheets":  own_ts.get("clean_sheets"),
                "cs_rate":       round(((own_ts.get("clean_sheets") or 0) / m), 3),
                "xg_against":    own_ts.get("xg_against"),
                "xga_per_match": round(((own_ts.get("xg_against") or 0) / m), 3),
            }

        penalty_taker = False
        pen_row = conn.execute(
            "SELECT 1 FROM penalty_takers WHERE player_id = ? AND season = ? AND priority = 1",
            (player_id, CURRENT_SEASON)
        ).fetchone()
        penalty_taker = bool(pen_row)

        components = {k: pred.get(k) for k in (
            "x_min_pts", "x_goal_pts", "x_assist_pts", "x_cs_pts", "x_gc_pts",
            "x_save_pts", "x_recovery_pts", "x_bonus_pts", "x_disc_pts",
            "x_pen_pts", "x_outside_box_pts",
        )}

        def _fmt_row(r):
            if not r:
                return None
            keys = ("data_source", "source_league", "data_quality", "matches", "minutes",
                    "goals", "assists", "xg", "npxg", "xa", "shots", "shots_on_target",
                    "saves", "recoveries", "yellow_cards", "red_cards",
                    "shots_outside_box", "goals_outside_box")
            return {k: r.get(k) for k in keys}

        return {
            "player": {
                "id": player["id"], "name": player["name"],
                "position": player["position"], "team_name": player["team_name"],
                "team_code": player["team_code"], "team_fotmob_id": player["team_fotmob_id"],
            },
            "fixture": {
                "id": pred["fixture_id"], "home_team": pred["home_team"],
                "away_team": pred["away_team"], "match_date": pred["match_date"],
            },
            "primary_source": primary["data_source"] if primary else None,
            "data_quality":   primary.get("data_quality") if primary else None,
            "season_stats":   _fmt_row(primary),
            "sources": {
                src: _fmt_row(by_source.get(src))
                for src in ("fotmob", "understat", "derived", "merged")
                if by_source.get(src)
            },
            "per90": {k: round(v, 4) for k, v in per90.items()},
            "opponent": opp_ctx,
            "own_team": own_ctx,
            "is_penalty_taker": penalty_taker,
            "components": components,
            "factors": {
                "competition_factor": pred.get("competition_factor"),
                "opponent_factor":    pred.get("opponent_factor"),
            },
            "probs": {
                "prob_play":  pred.get("prob_play"),
                "prob_60min": pred.get("prob_60min"),
                "prob_cs":    pred.get("prob_cs"),
            },
            "xpts_raw":      pred.get("xpts_raw"),
            "xpts_adjusted": pred.get("xpts_adjusted"),
        }
    finally:
        conn.close()


# ── Teams ─────────────────────────────────────────────────────────────────

@router.get("/teams")
def get_teams(
    competition_id: Optional[int] = Query(None),
    league: Optional[str] = Query(None),
):
    """List teams, optionally filtered by competition or league."""
    conn = db.get_connection()
    try:
        if competition_id:
            rows = conn.execute("""
                SELECT t.*, l.name as league_name
                FROM competition_teams ct
                JOIN teams t ON ct.team_id = t.id
                LEFT JOIN leagues l ON t.league_id = l.id
                WHERE ct.competition_id = ?
                ORDER BY t.name
            """, (competition_id,)).fetchall()
        elif league:
            rows = conn.execute("""
                SELECT t.*, l.name as league_name
                FROM teams t
                LEFT JOIN leagues l ON t.league_id = l.id
                WHERE l.name = ?
                ORDER BY t.name
            """, (league,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT t.*, l.name as league_name
                FROM teams t
                LEFT JOIN leagues l ON t.league_id = l.id
                ORDER BY l.name, t.name
            """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Pipeline ──────────────────────────────────────────────────────────────

@router.post("/pipeline/run")
def trigger_pipeline(
    background_tasks: BackgroundTasks,
    competition_id: Optional[int] = Query(None),
    scoring_system: str = Query("ucl"),
    skip_fbref: bool = Query(True),
    league: Optional[str] = Query(None),
):
    """Trigger a full pipeline update in the background.

    Disabled on Vercel — DB is read-only there. Pipeline runs in GH Actions
    and commits db/xpts.db to the repo.
    """
    if db.READ_ONLY:
        raise HTTPException(
            status_code=503,
            detail="Pipeline writes are disabled on Vercel. Trigger the GH Actions workflow instead.",
        )
    from src.pipeline.update import run_full_update

    def _run():
        run_full_update(
            competition_id=competition_id,
            scoring_system=scoring_system,
            skip_fbref=skip_fbref,
            league_filter=league,
        )

    background_tasks.add_task(_run)
    return {"status": "started", "message": "Pipeline running in background"}


@router.get("/pipeline/status")
def pipeline_status():
    """Quick DB health check: player/stat/prediction counts."""
    conn = db.get_connection()
    try:
        return {
            "players":      conn.execute("SELECT COUNT(*) FROM players").fetchone()[0],
            "player_stats": conn.execute("SELECT COUNT(*) FROM player_stats").fetchone()[0],
            "predictions":  conn.execute("SELECT COUNT(*) FROM xpts_predictions").fetchone()[0],
            "fixtures":     conn.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0],
            "last_stats_update": conn.execute(
                "SELECT MAX(fetched_at) FROM player_stats"
            ).fetchone()[0],
            "last_prediction_update": conn.execute(
                "SELECT MAX(computed_at) FROM xpts_predictions"
            ).fetchone()[0],
        }
    finally:
        conn.close()
