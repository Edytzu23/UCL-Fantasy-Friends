"""
Insert/update/select helpers for all tables.
Each function handles upsert logic to avoid duplicates.
"""

from src.db.connection import get_connection


# ── Leagues ────────────────────────────────────────────────────────────

def upsert_league(name, country=None, fotmob_id=None, fbref_id=None, elo_avg=None):
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO leagues (name, country, fotmob_id, fbref_id, elo_avg)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                country = COALESCE(excluded.country, country),
                fotmob_id = COALESCE(excluded.fotmob_id, fotmob_id),
                fbref_id = COALESCE(excluded.fbref_id, fbref_id),
                elo_avg = COALESCE(excluded.elo_avg, elo_avg),
                updated_at = datetime('now')
        """, (name, country, fotmob_id, fbref_id, elo_avg))
        conn.commit()
        row = conn.execute("SELECT id FROM leagues WHERE name = ?", (name,)).fetchone()
        return row["id"]
    finally:
        conn.close()


# ── Teams ──────────────────────────────────────────────────────────────

def upsert_team(name, code, league_id, fotmob_id=None, fbref_id=None, elo_rating=None):
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO teams (name, code, league_id, fotmob_id, fbref_id, elo_rating)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name, league_id) DO UPDATE SET
                code = excluded.code,
                fotmob_id = COALESCE(excluded.fotmob_id, fotmob_id),
                fbref_id = COALESCE(excluded.fbref_id, fbref_id),
                elo_rating = COALESCE(excluded.elo_rating, elo_rating),
                updated_at = datetime('now')
        """, (name, code, league_id, fotmob_id, fbref_id, elo_rating))
        conn.commit()
        row = conn.execute(
            "SELECT id FROM teams WHERE name = ? AND league_id = ?",
            (name, league_id)
        ).fetchone()
        return row["id"]
    finally:
        conn.close()


def get_team_by_fotmob(fotmob_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM teams WHERE fotmob_id = ?", (fotmob_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_team_by_code(code):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM teams WHERE code = ?", (code,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_teams():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM teams ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Players ────────────────────────────────────────────────────────────

def upsert_player(name, team_id, position, fotmob_id=None, fbref_id=None):
    conn = get_connection()
    try:
        # Try to find by fotmob_id first (most reliable)
        existing = None
        if fotmob_id:
            existing = conn.execute(
                "SELECT id FROM players WHERE fotmob_id = ?", (fotmob_id,)
            ).fetchone()

        if existing:
            conn.execute("""
                UPDATE players SET name=?, team_id=?, position=?,
                    fbref_id=COALESCE(?, fbref_id), updated_at=datetime('now')
                WHERE id=?
            """, (name, team_id, position, fbref_id, existing["id"]))
            conn.commit()
            return existing["id"]

        conn.execute("""
            INSERT INTO players (name, team_id, position, fotmob_id, fbref_id)
            VALUES (?, ?, ?, ?, ?)
        """, (name, team_id, position, fotmob_id, fbref_id))
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def get_players_by_team(team_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM players WHERE team_id = ? ORDER BY position, name",
            (team_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Player Stats ───────────────────────────────────────────────────────

def upsert_player_stats(player_id, season, source_league, data_source="fotmob", **stats):
    conn = get_connection()
    try:
        # Build SET clause dynamically from provided stats
        columns = ["player_id", "season", "source_league", "data_source"]
        values = [player_id, season, source_league, data_source]
        update_parts = []

        for key, val in stats.items():
            columns.append(key)
            values.append(val)
            update_parts.append(f"{key} = excluded.{key}")

        cols_str = ", ".join(columns)
        placeholders = ", ".join(["?"] * len(values))
        update_str = ", ".join(update_parts) if update_parts else "fetched_at = datetime('now')"

        conn.execute(f"""
            INSERT INTO player_stats ({cols_str})
            VALUES ({placeholders})
            ON CONFLICT(player_id, season, source_league, data_source) DO UPDATE SET
                {update_str},
                fetched_at = datetime('now')
        """, values)
        conn.commit()
    finally:
        conn.close()


def get_player_stats(player_id, season=None):
    conn = get_connection()
    try:
        if season:
            rows = conn.execute(
                "SELECT * FROM player_stats WHERE player_id = ? AND season = ?",
                (player_id, season)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM player_stats WHERE player_id = ? ORDER BY season DESC",
                (player_id,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Team Stats ─────────────────────────────────────────────────────────

def upsert_team_stats(team_id, season, source_league, **stats):
    conn = get_connection()
    try:
        columns = ["team_id", "season", "source_league"]
        values = [team_id, season, source_league]
        update_parts = []

        for key, val in stats.items():
            columns.append(key)
            values.append(val)
            update_parts.append(f"{key} = excluded.{key}")

        cols_str = ", ".join(columns)
        placeholders = ", ".join(["?"] * len(values))
        update_str = ", ".join(update_parts) if update_parts else "fetched_at = datetime('now')"

        conn.execute(f"""
            INSERT INTO team_stats ({cols_str})
            VALUES ({placeholders})
            ON CONFLICT(team_id, season, source_league) DO UPDATE SET
                {update_str},
                fetched_at = datetime('now')
        """, values)
        conn.commit()
    finally:
        conn.close()


# ── Competitions ───────────────────────────────────────────────────────

def upsert_competition(name, comp_type, season, scoring_system, fotmob_id=None):
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO competitions (name, type, season, scoring_system, fotmob_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name, season) DO UPDATE SET
                fotmob_id = COALESCE(excluded.fotmob_id, fotmob_id)
        """, (name, comp_type, season, scoring_system, fotmob_id))
        conn.commit()
        row = conn.execute(
            "SELECT id FROM competitions WHERE name = ? AND season = ?",
            (name, season)
        ).fetchone()
        return row["id"]
    finally:
        conn.close()


# ── Fixtures ───────────────────────────────────────────────────────────

def upsert_fixture(competition_id, home_team_id, away_team_id, match_date,
                   matchday=None, status="scheduled", home_score=None,
                   away_score=None, external_id=None):
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO fixtures (competition_id, home_team_id, away_team_id,
                match_date, matchday, status, home_score, away_score, external_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(competition_id, home_team_id, away_team_id, match_date)
            DO UPDATE SET
                matchday = COALESCE(excluded.matchday, matchday),
                status = excluded.status,
                home_score = excluded.home_score,
                away_score = excluded.away_score,
                external_id = COALESCE(excluded.external_id, external_id)
        """, (competition_id, home_team_id, away_team_id, match_date,
              matchday, status, home_score, away_score, external_id))
        conn.commit()
        row = conn.execute(
            "SELECT id FROM fixtures WHERE competition_id=? AND home_team_id=? AND away_team_id=? AND match_date=?",
            (competition_id, home_team_id, away_team_id, match_date)
        ).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def get_upcoming_fixtures(competition_id):
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT f.*, ht.name as home_name, ht.code as home_code,
                   at.name as away_name, at.code as away_code
            FROM fixtures f
            JOIN teams ht ON f.home_team_id = ht.id
            JOIN teams at ON f.away_team_id = at.id
            WHERE f.competition_id = ? AND f.status = 'scheduled'
            ORDER BY f.match_date
        """, (competition_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── xPts Predictions ──────────────────────────────────────────────────

def upsert_prediction(player_id, fixture_id, scoring_system, components: dict,
                      xpts_raw, xpts_adjusted, factors: dict, probs: dict,
                      model_version="1.0", form_filter="season"):
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO xpts_predictions (
                player_id, fixture_id, scoring_system,
                x_min_pts, x_goal_pts, x_assist_pts, x_cs_pts, x_gc_pts,
                x_save_pts, x_recovery_pts, x_bonus_pts, x_disc_pts,
                x_pen_pts, x_outside_box_pts,
                xpts_raw, xpts_adjusted,
                competition_factor, opponent_factor,
                prob_play, prob_60min, prob_cs,
                model_version, form_filter
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(player_id, fixture_id, scoring_system, model_version, form_filter)
            DO UPDATE SET
                x_min_pts=excluded.x_min_pts, x_goal_pts=excluded.x_goal_pts,
                x_assist_pts=excluded.x_assist_pts, x_cs_pts=excluded.x_cs_pts,
                x_gc_pts=excluded.x_gc_pts, x_save_pts=excluded.x_save_pts,
                x_recovery_pts=excluded.x_recovery_pts, x_bonus_pts=excluded.x_bonus_pts,
                x_disc_pts=excluded.x_disc_pts, x_pen_pts=excluded.x_pen_pts,
                x_outside_box_pts=excluded.x_outside_box_pts,
                xpts_raw=excluded.xpts_raw, xpts_adjusted=excluded.xpts_adjusted,
                competition_factor=excluded.competition_factor,
                opponent_factor=excluded.opponent_factor,
                prob_play=excluded.prob_play, prob_60min=excluded.prob_60min,
                prob_cs=excluded.prob_cs,
                computed_at=datetime('now')
        """, (
            player_id, fixture_id, scoring_system,
            components.get("x_min_pts", 0),
            components.get("x_goal_pts", 0),
            components.get("x_assist_pts", 0),
            components.get("x_cs_pts", 0),
            components.get("x_gc_pts", 0),
            components.get("x_save_pts", 0),
            components.get("x_recovery_pts", 0),
            components.get("x_bonus_pts", 0),
            components.get("x_disc_pts", 0),
            components.get("x_pen_pts", 0),
            components.get("x_outside_box_pts", 0),
            xpts_raw, xpts_adjusted,
            factors.get("competition", 1.0),
            factors.get("opponent", 1.0),
            probs.get("play", 0),
            probs.get("60min", 0),
            probs.get("cs", 0),
            model_version, form_filter,
        ))
        conn.commit()
    finally:
        conn.close()


def get_predictions_for_fixture(fixture_id, scoring_system="ucl"):
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT xp.*, p.name as player_name, p.position, t.name as team_name, t.code as team_code
            FROM xpts_predictions xp
            JOIN players p ON xp.player_id = p.id
            JOIN teams t ON p.team_id = t.id
            WHERE xp.fixture_id = ? AND xp.scoring_system = ?
            ORDER BY xp.xpts_adjusted DESC
        """, (fixture_id, scoring_system)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
