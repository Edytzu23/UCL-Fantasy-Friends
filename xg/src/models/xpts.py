"""
xPts Calculator — core model.

Calculates expected fantasy points per player per fixture.
Both raw (no opponent adjustment) and adjusted (Elo-based) outputs.

Formula:
  xPts = xMinPts + xGoalPts + xAssistPts + xCSPts + xGCPts + xSavePts
       + xRecoveryPts + xBonusPts + xDiscPts + xPenPts + xOutsideBoxPts
"""

from src.config import SCORING, STAT_WEIGHTS, PENALTY_XG, CS_MODEL
from src.models.poisson import (
    adjusted_cs_probability, expected_goals_against, minutes_probabilities,
)


def _weight(last_season_val, current_val, recent_val, weights=None):
    """Weighted average of three stat snapshots."""
    w = weights or STAT_WEIGHTS
    return (
        w["last_season"]    * (last_season_val or 0) +
        w["current_season"] * (current_val or 0) +
        w["recent_form"]    * (recent_val or 0)
    )


def _per90(total_stat, total_minutes):
    """Convert a season total to per90 rate."""
    if not total_minutes or total_minutes <= 0:
        return 0.0
    return total_stat / (total_minutes / 90)


def calc_xpts(
    position,
    scoring_system,
    # Minutes history (list of minutes per recent match)
    recent_minutes,
    # Weighted per90 stats — caller pre-weights these
    npxg_per90,
    xa_per90,
    saves_per90=0,
    recoveries_per90=0,
    yellow_card_rate=0,     # cards per appearance
    red_card_rate=0,
    # Team defensive context
    team_xga_per_match=1.0,
    opponent_xg_per_match=1.0,
    team_cs_rate=0.25,
    # Penalty taker
    is_penalty_taker=False,
    team_penalty_rate=0.3,  # avg penalties won per match by team
    # Outside box shooting (UCL only)
    outside_box_goal_rate=0.0,   # fraction of goals from outside box
    # Adjustment factors (1.0 = no adjustment, i.e. raw)
    competition_factor=1.0,
    opponent_factor=1.0,
    # Whether this is for raw (no adj) or adjusted output
    apply_adjustments=True,
):
    """Calculate all xPts components for one player in one fixture.

    Returns dict with every component and totals (xpts_raw, xpts_adjusted).
    """
    sc = SCORING[scoring_system]
    pos = position  # "GK", "DEF", "MID", "FWD"

    # ── 1. Minutes probabilities ──────────────────────────────────────
    min_probs = minutes_probabilities(recent_minutes)
    p_plays   = min_probs["p_plays"]
    p_60min   = min_probs["p_plays_60"]

    x_min_pts = (
        sc["appearance"].get(pos, 1) * p_plays +
        sc["minutes_60"].get(pos, 1) * p_60min
    )

    # Effective minutes proportion (relative to 90 min) for scaling per90 stats
    # We use p_plays as the probability weight and average expected minutes
    avg_expected_mins = (p_plays * 45 + p_60min * 45)  # rough expected minutes
    min_proportion = avg_expected_mins / 90.0
    min_proportion = max(0.0, min(min_proportion, 1.0))

    # ── 2. Goals ──────────────────────────────────────────────────────
    goal_pts = sc["goal"].get(pos, 4)
    x_goal_pts = npxg_per90 * goal_pts * min_proportion

    # ── 3. Assists ────────────────────────────────────────────────────
    assist_pts = sc["assist"].get(pos, 3)
    x_assist_pts = xa_per90 * assist_pts * min_proportion

    # ── 4. Clean Sheet ────────────────────────────────────────────────
    cs_pts = sc["clean_sheet"].get(pos, 0)
    if cs_pts > 0:
        p_cs = adjusted_cs_probability(
            team_avg_goals_conceded=team_xga_per_match,
            opponent_xg_per_match=opponent_xg_per_match,
            historical_cs_rate=team_cs_rate,
            bucket_weight=CS_MODEL["fixture_bucket_weight"],
            historical_weight=CS_MODEL["historical_rate_weight"],
        )
        x_cs_pts = p_cs * p_60min * cs_pts
    else:
        p_cs = 0.0
        x_cs_pts = 0.0

    # ── 5. Goals conceded deduction (GK/DEF only) ─────────────────────
    gc_pts = sc["goals_conceded_pts"].get(pos, 0)
    if gc_pts < 0:
        exp_gc = expected_goals_against(
            team_xga_per_match=team_xga_per_match,
            opponent_xg_per_match=opponent_xg_per_match,
            elo_team=None,
            elo_opponent=None,
        )
        every = sc["goals_conceded_every"]
        x_gc_pts = (exp_gc / every) * gc_pts * p_plays
    else:
        x_gc_pts = 0.0

    # ── 6. Saves (GK only) ────────────────────────────────────────────
    saves_every = sc.get("saves_every", 3)
    saves_pts_val = sc.get("saves_pts", 1)
    if pos == "GK" and saves_every > 0:
        x_save_pts = (saves_per90 / saves_every) * saves_pts_val * min_proportion
    else:
        x_save_pts = 0.0

    # ── 7. Ball recoveries (UCL only) ─────────────────────────────────
    rec_every = sc.get("recoveries_every", 0)
    rec_pts_val = sc.get("recoveries_pts", 0)
    if rec_every and rec_every > 0:
        x_recovery_pts = (recoveries_per90 / rec_every) * rec_pts_val * min_proportion
    else:
        x_recovery_pts = 0.0

    # ── 8. Bonus / Player of the Match (UCL only) ─────────────────────
    potm_pts = sc.get("potm", 0)
    if potm_pts > 0:
        # Rough proxy: top performer in winning team
        # Use a fixed low probability (will refine with historical data)
        # GK/DEF: lower chance, MID/FWD: higher chance
        potm_base = {"GK": 0.04, "DEF": 0.05, "MID": 0.07, "FWD": 0.08}.get(pos, 0.05)
        x_bonus_pts = potm_base * potm_pts * p_plays
    else:
        x_bonus_pts = 0.0

    # ── 9. Discipline (cards) ─────────────────────────────────────────
    yellow_pts = sc.get("yellow_card", -1)
    red_pts = sc.get("red_card", -3)
    x_disc_pts = (
        yellow_card_rate * yellow_pts +
        red_card_rate * red_pts
    ) * p_plays

    # ── 10. Penalty taker bonus ───────────────────────────────────────
    if is_penalty_taker and pos != "GK":
        # Expected penalties taken by this player per 90
        exp_pens_per90 = team_penalty_rate
        pen_goal_pts = sc["goal"].get(pos, 4)
        # xPts from scoring the penalty (xG ≈ 0.76)
        x_pen_pts = exp_pens_per90 * PENALTY_XG * pen_goal_pts * min_proportion
        # Winning a penalty award (if player wins it)
        x_pen_pts += exp_pens_per90 * sc.get("penalty_won", 2) * min_proportion
    else:
        x_pen_pts = 0.0

    # ── 11. Outside box goal bonus (UCL only) ─────────────────────────
    outside_box_pts = sc.get("goal_outside_box", 0)
    if outside_box_pts > 0 and outside_box_goal_rate > 0:
        # Expected outside-box goals per 90
        exp_ob_goals = npxg_per90 * outside_box_goal_rate
        x_outside_box_pts = exp_ob_goals * outside_box_pts * min_proportion
    else:
        x_outside_box_pts = 0.0

    # ── Sum raw xPts ──────────────────────────────────────────────────
    xpts_raw = (
        x_min_pts + x_goal_pts + x_assist_pts + x_cs_pts + x_gc_pts +
        x_save_pts + x_recovery_pts + x_bonus_pts + x_disc_pts +
        x_pen_pts + x_outside_box_pts
    )
    xpts_raw = round(xpts_raw, 4)

    # ── Adjusted xPts ─────────────────────────────────────────────────
    if apply_adjustments:
        # Attacking components scaled by both competition and opponent factors
        combined_attack_factor = competition_factor * opponent_factor
        combined_attack_factor = max(0.4, min(combined_attack_factor, 1.8))

        # Defensive components scale inversely (stronger opponent = more saves/actions)
        combined_def_factor = competition_factor  # no opponent flip for defense

        adjusted_attack = (x_goal_pts + x_assist_pts + x_pen_pts + x_outside_box_pts)
        adjusted_defense = (x_save_pts + x_recovery_pts + x_cs_pts + x_gc_pts)
        unchanged = x_min_pts + x_bonus_pts + x_disc_pts

        xpts_adjusted = round(
            unchanged +
            adjusted_attack * combined_attack_factor +
            adjusted_defense * combined_def_factor,
            4
        )
    else:
        xpts_adjusted = xpts_raw
        competition_factor = 1.0
        opponent_factor = 1.0

    return {
        # Components
        "x_min_pts":          round(x_min_pts, 4),
        "x_goal_pts":         round(x_goal_pts, 4),
        "x_assist_pts":       round(x_assist_pts, 4),
        "x_cs_pts":           round(x_cs_pts, 4),
        "x_gc_pts":           round(x_gc_pts, 4),
        "x_save_pts":         round(x_save_pts, 4),
        "x_recovery_pts":     round(x_recovery_pts, 4),
        "x_bonus_pts":        round(x_bonus_pts, 4),
        "x_disc_pts":         round(x_disc_pts, 4),
        "x_pen_pts":          round(x_pen_pts, 4),
        "x_outside_box_pts":  round(x_outside_box_pts, 4),
        # Totals
        "xpts_raw":           xpts_raw,
        "xpts_adjusted":      xpts_adjusted,
        # Factors used
        "competition_factor": competition_factor,
        "opponent_factor":    opponent_factor,
        # Probabilities used
        "prob_play":          p_plays,
        "prob_60min":         p_60min,
        "prob_cs":            round(p_cs, 4),
    }


def build_player_input(stats_rows):
    """Build weighted per90 inputs from multiple stat snapshots.

    stats_rows: list of dicts with keys:
        season, npxg, xa, minutes, saves, recoveries, yellow_cards, red_cards,
        shots_outside_box, goals_outside_box, matches
    Expected order: [last_season_row, current_season_row, recent_form_row]
    Missing rows should be None or empty dict.

    Returns dict of weighted per90 values ready to pass to calc_xpts().
    """
    def safe_per90(row, stat_key):
        if not row:
            return 0.0
        mins = row.get("minutes", 0)
        return _per90(row.get(stat_key, 0) or 0, mins)

    weights = [
        STAT_WEIGHTS["last_season"],
        STAT_WEIGHTS["current_season"],
        STAT_WEIGHTS["recent_form"],
    ]
    # Pad to 3 rows
    rows = (list(stats_rows) + [None, None, None])[:3]

    def weighted(stat_key):
        return sum(
            weights[i] * safe_per90(rows[i], stat_key)
            for i in range(3)
        )

    # Card rates: per appearance (not per 90)
    def card_rate(stat_key):
        total = 0.0
        total_matches = 0
        for row in rows:
            if row:
                total_matches += row.get("matches", 0)
                total += row.get(stat_key, 0) or 0
        return total / total_matches if total_matches > 0 else 0.0

    # Outside box goal rate: fraction of goals from outside box
    ob_goals = sum((r.get("goals_outside_box", 0) or 0) for r in rows if r)
    total_goals = sum((r.get("goals", 0) or 0) for r in rows if r)
    outside_box_goal_rate = ob_goals / total_goals if total_goals > 0 else 0.0

    return {
        "npxg_per90":         weighted("npxg"),
        "xa_per90":           weighted("xa"),
        "saves_per90":        weighted("saves"),
        "recoveries_per90":   weighted("recoveries"),
        "yellow_card_rate":   card_rate("yellow_cards"),
        "red_card_rate":      card_rate("red_cards"),
        "outside_box_goal_rate": outside_box_goal_rate,
    }
