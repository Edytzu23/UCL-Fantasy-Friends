-- xPts Engine — SQLite Schema
-- All tables for player stats, team data, fixtures, and xPts predictions.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Leagues ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS leagues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    country     TEXT,
    fotmob_id   INTEGER,
    fbref_id    TEXT,
    elo_avg     REAL,
    updated_at  TEXT    DEFAULT (datetime('now'))
);

-- ── Teams ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS teams (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    code        TEXT    NOT NULL,
    league_id   INTEGER REFERENCES leagues(id),
    fotmob_id   INTEGER,
    fbref_id    TEXT,
    elo_rating  REAL,
    updated_at  TEXT    DEFAULT (datetime('now')),
    UNIQUE(name, league_id)
);

-- ── Competitions ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS competitions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    type            TEXT    NOT NULL CHECK(type IN ('ucl', 'worldcup', 'league')),
    season          TEXT    NOT NULL,
    scoring_system  TEXT    NOT NULL CHECK(scoring_system IN ('ucl', 'worldcup')),
    fotmob_id       INTEGER,
    UNIQUE(name, season)
);

-- ── Competition Teams (many-to-many) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS competition_teams (
    competition_id  INTEGER NOT NULL REFERENCES competitions(id),
    team_id         INTEGER NOT NULL REFERENCES teams(id),
    PRIMARY KEY (competition_id, team_id)
);

-- ── Players ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS players (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    team_id     INTEGER REFERENCES teams(id),
    position    TEXT    CHECK(position IN ('GK', 'DEF', 'MID', 'FWD')),
    fotmob_id   INTEGER,
    fbref_id    TEXT,
    updated_at  TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_players_team ON players(team_id);
CREATE INDEX IF NOT EXISTS idx_players_fotmob ON players(fotmob_id);

-- ── Player Stats (per season per competition source) ──────────────────
-- One row per player per season per source league.
-- Stores raw per90 and totals for xPts calculation.
CREATE TABLE IF NOT EXISTS player_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       INTEGER NOT NULL REFERENCES players(id),
    season          TEXT    NOT NULL,
    source_league   TEXT    NOT NULL,          -- e.g. "Premier League", "La Liga"
    -- Appearance
    matches         INTEGER DEFAULT 0,
    minutes         INTEGER DEFAULT 0,
    starts          INTEGER DEFAULT 0,
    -- Attacking
    goals           INTEGER DEFAULT 0,
    assists         INTEGER DEFAULT 0,
    xg              REAL    DEFAULT 0,         -- total xG
    npxg            REAL    DEFAULT 0,         -- non-penalty xG
    xa              REAL    DEFAULT 0,         -- total xA
    shots           INTEGER DEFAULT 0,
    shots_on_target INTEGER DEFAULT 0,
    -- Defensive
    tackles         INTEGER DEFAULT 0,
    interceptions   INTEGER DEFAULT 0,
    blocks          INTEGER DEFAULT 0,
    clearances      INTEGER DEFAULT 0,
    recoveries      INTEGER DEFAULT 0,
    -- Goalkeeping
    saves           INTEGER DEFAULT 0,
    -- Discipline
    yellow_cards    INTEGER DEFAULT 0,
    red_cards       INTEGER DEFAULT 0,
    -- Penalties
    penalties_taken INTEGER DEFAULT 0,
    penalties_scored INTEGER DEFAULT 0,
    penalties_won   INTEGER DEFAULT 0,
    -- Shooting detail
    shots_outside_box   INTEGER DEFAULT 0,
    goals_outside_box   INTEGER DEFAULT 0,
    -- Metadata
    is_penalty_taker    INTEGER DEFAULT 0,     -- 1 if primary penalty taker
    data_source         TEXT    DEFAULT 'fotmob',
    data_quality        REAL    DEFAULT NULL,  -- 0.0-1.0 agreement score (merged rows only)
    fetched_at          TEXT    DEFAULT (datetime('now')),
    UNIQUE(player_id, season, source_league, data_source)
);
CREATE INDEX IF NOT EXISTS idx_pstats_player ON player_stats(player_id);
CREATE INDEX IF NOT EXISTS idx_pstats_season ON player_stats(season);

-- ── Stat Validation Log ───────────────────────────────────────────────
-- Logs per-field discrepancies between FotMob and FBref for auditing.
CREATE TABLE IF NOT EXISTS stat_validation_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id   INTEGER NOT NULL REFERENCES players(id),
    season      TEXT    NOT NULL,
    source_league TEXT  NOT NULL,
    field       TEXT    NOT NULL,
    fotmob_val  REAL,
    fbref_val   REAL,
    delta_pct   REAL,   -- abs((a-b)/max(a,b)) * 100
    flagged     INTEGER DEFAULT 0,  -- 1 if delta_pct > threshold
    logged_at   TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_vallog_player ON stat_validation_log(player_id, season);

-- ── Team Stats (per season per league) ────────────────────────────────
CREATE TABLE IF NOT EXISTS team_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id         INTEGER NOT NULL REFERENCES teams(id),
    season          TEXT    NOT NULL,
    source_league   TEXT    NOT NULL,
    -- Overall
    matches         INTEGER DEFAULT 0,
    wins            INTEGER DEFAULT 0,
    draws           INTEGER DEFAULT 0,
    losses          INTEGER DEFAULT 0,
    goals_for       INTEGER DEFAULT 0,
    goals_against   INTEGER DEFAULT 0,
    xg_for          REAL    DEFAULT 0,
    xg_against      REAL    DEFAULT 0,
    clean_sheets    INTEGER DEFAULT 0,
    -- Home
    home_matches    INTEGER DEFAULT 0,
    home_goals_for  INTEGER DEFAULT 0,
    home_goals_against INTEGER DEFAULT 0,
    -- Away
    away_matches    INTEGER DEFAULT 0,
    away_goals_for  INTEGER DEFAULT 0,
    away_goals_against INTEGER DEFAULT 0,
    -- Metadata
    fetched_at      TEXT    DEFAULT (datetime('now')),
    UNIQUE(team_id, season, source_league)
);

-- ── Fixtures ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fixtures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    competition_id  INTEGER NOT NULL REFERENCES competitions(id),
    home_team_id    INTEGER NOT NULL REFERENCES teams(id),
    away_team_id    INTEGER NOT NULL REFERENCES teams(id),
    matchday        TEXT,
    match_date      TEXT,
    status          TEXT    DEFAULT 'scheduled' CHECK(status IN ('scheduled', 'live', 'finished')),
    home_score      INTEGER,
    away_score      INTEGER,
    external_id     TEXT,
    stage           TEXT,                          -- GROUP, R32, R16, QF, SF, 3RD, F
    group_letter    TEXT,                          -- A-L for group stage
    venue           TEXT,                          -- Stadium name
    UNIQUE(competition_id, home_team_id, away_team_id, match_date)
);
CREATE INDEX IF NOT EXISTS idx_fixtures_comp ON fixtures(competition_id);
CREATE INDEX IF NOT EXISTS idx_fixtures_date ON fixtures(match_date);

-- ── Player Match Performance (per-match stats for Last-5 Form) ────────
CREATE TABLE IF NOT EXISTS player_match_performance (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id           INTEGER NOT NULL REFERENCES players(id),
    match_id            INTEGER,                     -- FotMob match id
    match_date          TEXT    NOT NULL,
    opponent_fotmob_id  INTEGER,
    is_home             INTEGER DEFAULT 0,
    minutes             INTEGER DEFAULT 0,
    goals               INTEGER DEFAULT 0,
    assists             INTEGER DEFAULT 0,
    xg                  REAL    DEFAULT 0,
    xa                  REAL    DEFAULT 0,
    shots               INTEGER DEFAULT 0,
    shots_on_target     INTEGER DEFAULT 0,
    yellow_cards        INTEGER DEFAULT 0,
    red_cards           INTEGER DEFAULT 0,
    saves               INTEGER DEFAULT 0,
    clean_sheet         INTEGER DEFAULT 0,
    rating              REAL,
    fetched_at          TEXT    DEFAULT (datetime('now')),
    UNIQUE(player_id, match_id)
);
CREATE INDEX IF NOT EXISTS idx_pmp_player_date ON player_match_performance(player_id, match_date DESC);

-- ── Penalty Takers ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS penalty_takers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id     INTEGER NOT NULL REFERENCES teams(id),
    player_id   INTEGER NOT NULL REFERENCES players(id),
    priority    INTEGER NOT NULL DEFAULT 1,    -- 1 = first choice
    season      TEXT    NOT NULL,
    updated_at  TEXT    DEFAULT (datetime('now')),
    UNIQUE(team_id, player_id, season)
);

-- ── xPts Predictions ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS xpts_predictions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id           INTEGER NOT NULL REFERENCES players(id),
    fixture_id          INTEGER NOT NULL REFERENCES fixtures(id),
    scoring_system      TEXT    NOT NULL CHECK(scoring_system IN ('ucl', 'worldcup')),
    -- Component values
    x_min_pts           REAL    DEFAULT 0,
    x_goal_pts          REAL    DEFAULT 0,
    x_assist_pts        REAL    DEFAULT 0,
    x_cs_pts            REAL    DEFAULT 0,
    x_gc_pts            REAL    DEFAULT 0,
    x_save_pts          REAL    DEFAULT 0,
    x_recovery_pts      REAL    DEFAULT 0,
    x_bonus_pts         REAL    DEFAULT 0,
    x_disc_pts          REAL    DEFAULT 0,
    x_pen_pts           REAL    DEFAULT 0,
    x_outside_box_pts   REAL    DEFAULT 0,
    -- Totals
    xpts_raw            REAL    DEFAULT 0,
    xpts_adjusted       REAL    DEFAULT 0,
    -- Adjustment factors used
    competition_factor  REAL    DEFAULT 1.0,
    opponent_factor     REAL    DEFAULT 1.0,
    -- Probabilities used
    prob_play           REAL    DEFAULT 0,
    prob_60min          REAL    DEFAULT 0,
    prob_cs             REAL    DEFAULT 0,
    -- Metadata
    model_version       TEXT    DEFAULT '1.0',
    form_filter         TEXT    DEFAULT 'season',   -- 'season' or 'last5'
    computed_at         TEXT    DEFAULT (datetime('now')),
    UNIQUE(player_id, fixture_id, scoring_system, model_version, form_filter)
);
CREATE INDEX IF NOT EXISTS idx_xpts_fixture ON xpts_predictions(fixture_id);
CREATE INDEX IF NOT EXISTS idx_xpts_player ON xpts_predictions(player_id);
CREATE INDEX IF NOT EXISTS idx_xpts_raw ON xpts_predictions(xpts_raw DESC);
CREATE INDEX IF NOT EXISTS idx_xpts_adj ON xpts_predictions(xpts_adjusted DESC);
