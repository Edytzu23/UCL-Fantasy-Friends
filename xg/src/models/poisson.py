"""
Poisson model for match probability estimation.

Used to calculate:
- P(clean sheet) = P(goals_conceded = 0)
- P(win/draw/loss) given team strengths
- Expected goals against for a team in a specific fixture
"""

import math
from scipy.stats import poisson


def poisson_pmf(k, lam):
    """P(X = k) for Poisson(lambda)."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return poisson.pmf(k, lam)


def clean_sheet_probability(avg_goals_conceded_per_match):
    """P(0 goals conceded) from Poisson distribution.
    Args:
        avg_goals_conceded_per_match: lambda for the Poisson model
    """
    return poisson_pmf(0, avg_goals_conceded_per_match)


def adjusted_cs_probability(
    team_avg_goals_conceded,
    opponent_xg_per_match,
    historical_cs_rate,
    bucket_weight=0.70,
    historical_weight=0.30,
):
    """Blended clean sheet probability.

    bucket_weight portion uses opponent xG as the Poisson lambda,
    historical_weight portion uses team's actual clean sheet rate.

    Args:
        team_avg_goals_conceded: team's season average goals conceded/match
        opponent_xg_per_match:   opponent's average xG per match (fixture factor)
        historical_cs_rate:      fraction of games team kept clean sheet (e.g. 0.35)
        bucket_weight:           weight for fixture-based probability (default 0.70)
        historical_weight:       weight for historical rate (default 0.30)
    """
    # Fixture-based: use opponent's attacking threat (xG) as the rate
    fixture_cs_prob = clean_sheet_probability(opponent_xg_per_match)

    # Historical rate is already a probability (0–1)
    blended = (bucket_weight * fixture_cs_prob) + (historical_weight * historical_cs_rate)
    return round(min(max(blended, 0.0), 1.0), 4)


def match_outcome_probabilities(lambda_home, lambda_away, max_goals=10):
    """Calculate win/draw/loss probabilities using Poisson score matrix.

    Args:
        lambda_home: expected goals for home team
        lambda_away: expected goals for away team
        max_goals:   max scoreline to consider

    Returns:
        dict with keys: p_home_win, p_draw, p_away_win
    """
    p_home_win = 0.0
    p_draw = 0.0
    p_away_win = 0.0

    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = poisson_pmf(i, lambda_home) * poisson_pmf(j, lambda_away)
            if i > j:
                p_home_win += p
            elif i == j:
                p_draw += p
            else:
                p_away_win += p

    total = p_home_win + p_draw + p_away_win
    if total > 0:
        p_home_win /= total
        p_draw /= total
        p_away_win /= total

    return {
        "p_home_win": round(p_home_win, 4),
        "p_draw":     round(p_draw, 4),
        "p_away_win": round(p_away_win, 4),
    }


def expected_goals_against(
    team_xga_per_match,
    opponent_xg_per_match,
    elo_team,
    elo_opponent,
    blend=0.5,
):
    """Estimate expected goals a team will concede in a specific fixture.

    Blends the team's defensive rate with the opponent's attacking threat,
    weighted by Elo difference.

    Args:
        team_xga_per_match:    team's season xGA per match
        opponent_xg_per_match: opponent's season xG per match
        elo_team:              Elo rating of the defending team
        elo_opponent:          Elo rating of the attacking opponent
        blend:                 0 = pure team xGA, 1 = pure opponent xG
    """
    base = (1 - blend) * team_xga_per_match + blend * opponent_xg_per_match

    # Elo adjustment: if opponent is stronger, increase expected goals against
    if elo_team and elo_opponent and elo_team > 0 and elo_opponent > 0:
        elo_diff = elo_opponent - elo_team
        # Each 100 Elo points difference ≈ 10% change
        factor = 1.0 + (elo_diff / 1000.0)
        factor = max(0.5, min(factor, 2.0))  # cap at ±50%
        base *= factor

    return round(max(base, 0.01), 4)


def minutes_probabilities(recent_minutes, total_match_minutes=90):
    """Estimate P(plays ≥ 1 min) and P(plays ≥ 60 min) from recent history.

    Uses Bayesian smoothing: starts with a prior (0.5 for each) and
    updates based on observed data.

    Args:
        recent_minutes: list of minutes played in recent matches (e.g. [90, 90, 0, 45, 90])
    Returns:
        dict with p_plays, p_plays_60
    """
    if not recent_minutes:
        return {"p_plays": 0.5, "p_plays_60": 0.3}

    n = len(recent_minutes)

    # Bayesian prior: alpha=2, beta=2 (weak prior around 0.5)
    alpha_prior = 2
    beta_prior = 2

    played = sum(1 for m in recent_minutes if m >= 1)
    played_60 = sum(1 for m in recent_minutes if m >= 60)

    p_plays = (played + alpha_prior) / (n + alpha_prior + beta_prior)
    p_plays_60 = (played_60 + alpha_prior) / (n + alpha_prior + beta_prior)

    return {
        "p_plays":    round(min(p_plays, 1.0), 4),
        "p_plays_60": round(min(p_plays_60, 1.0), 4),
    }
