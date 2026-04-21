"""
Elo-based opponent and competition adjustment factors.

Three-layer system:
  1. Baseline: raw per90 stats from domestic league
  2. competition_factor: scales stats from player's domestic league to UCL/WC level
  3. opponent_factor: adjusts for specific opponent strength in the fixture
"""

from src.data.elo import fetch_elo_ratings, get_team_elo, compute_league_avg_elo
from src.config import FIXTURE_ADJUSTMENT_MAX


def competition_factor(player_league_elo_avg, competition_elo_avg):
    """Factor to scale domestic stats to competition level.

    If a player's league is weaker than the competition on average,
    their stats are slightly deflated (opponents are harder).

    Args:
        player_league_elo_avg:  average Elo of teams in player's domestic league
        competition_elo_avg:    average Elo of teams in the target competition (UCL/WC)

    Returns:
        float factor (e.g. 0.92 for a weaker league player, 1.05 for a stronger league)
    """
    if not player_league_elo_avg or not competition_elo_avg:
        return 1.0

    ratio = player_league_elo_avg / competition_elo_avg
    # Cap adjustment: max ±FIXTURE_ADJUSTMENT_MAX (default 30%)
    capped = 1.0 + max(
        -FIXTURE_ADJUSTMENT_MAX,
        min(FIXTURE_ADJUSTMENT_MAX, ratio - 1.0)
    )
    return round(capped, 4)


def opponent_factor(opponent_elo, competition_elo_avg):
    """Factor to adjust for a specific opponent's strength.

    A stronger opponent reduces expected attacking output (goals/assists)
    and increases expected defensive pressure.

    Args:
        opponent_elo:          Elo rating of the specific opponent in the fixture
        competition_elo_avg:   average Elo in the competition (used as baseline)

    Returns:
        float factor (e.g. 0.85 vs Real Madrid, 1.10 vs weaker side)
    """
    if not opponent_elo or not competition_elo_avg:
        return 1.0

    # If opponent is above average → attacking stats decrease
    # If opponent is below average → attacking stats increase
    ratio = competition_elo_avg / opponent_elo  # flipped: weaker opponent → >1.0
    capped = 1.0 + max(
        -FIXTURE_ADJUSTMENT_MAX,
        min(FIXTURE_ADJUSTMENT_MAX, ratio - 1.0)
    )
    return round(capped, 4)


def defensive_opponent_factor(opponent_elo, competition_elo_avg):
    """Factor for defensive stats (GK saves, tackles, etc.) vs a specific opponent.

    Stronger opponent → more saves/defensive actions needed → factor > 1.
    Weaker opponent → fewer saves needed → factor < 1.
    """
    if not opponent_elo or not competition_elo_avg:
        return 1.0

    ratio = opponent_elo / competition_elo_avg
    capped = 1.0 + max(
        -FIXTURE_ADJUSTMENT_MAX,
        min(FIXTURE_ADJUSTMENT_MAX, ratio - 1.0)
    )
    return round(capped, 4)


def cs_opponent_factor(opponent_xg_per_match, league_avg_xg_per_match):
    """Factor for clean sheet probability based on opponent's attacking threat.

    This is applied to the baseline CS probability:
    - Stronger attacker → CS less likely → factor < 1
    - Weaker attacker → CS more likely → factor > 1

    Args:
        opponent_xg_per_match:     opponent's average xG/match
        league_avg_xg_per_match:   league average xG/match (baseline)
    """
    if not opponent_xg_per_match or not league_avg_xg_per_match:
        return 1.0

    # Higher opponent xG → more dangerous → reduce CS probability
    ratio = league_avg_xg_per_match / opponent_xg_per_match
    capped = 1.0 + max(
        -FIXTURE_ADJUSTMENT_MAX,
        min(FIXTURE_ADJUSTMENT_MAX, ratio - 1.0)
    )
    return round(capped, 4)


def get_all_factors(
    player_league_name,
    opponent_name,
    competition_teams,
    ratings=None,
):
    """Compute all adjustment factors for a player in a fixture.

    Args:
        player_league_name:  name of the player's domestic league (e.g. "La Liga")
        opponent_name:       name of the specific opponent in the fixture
        competition_teams:   list of team names in the competition (for avg Elo)
        ratings:             pre-fetched Elo dict (optional, fetches if None)

    Returns:
        dict with: competition_factor, opponent_factor, defensive_factor
    """
    if ratings is None:
        ratings = fetch_elo_ratings()

    # Average Elo of the competition
    comp_elo_avg = compute_league_avg_elo(competition_teams, ratings)

    # Average Elo of the player's domestic league
    from src.db.queries import get_all_teams
    # Get teams in the player's league by name lookup (approximation using all registered teams)
    # For now, use a lookup table of approximate league Elo averages
    LEAGUE_ELO_APPROX = {
        "Premier League": 1750,
        "La Liga":        1720,
        "Bundesliga":     1690,
        "Serie A":        1670,
        "Ligue 1":        1640,
        "Liga Portugal":  1580,
        "Eredivisie":     1590,
        "Scottish PL":    1530,
        "Super Lig":      1550,
        "Belgian Pro":    1520,
    }
    league_elo = LEAGUE_ELO_APPROX.get(player_league_name, comp_elo_avg or 1650)

    # Opponent Elo
    opp_elo = get_team_elo(opponent_name, ratings)

    comp_f = competition_factor(league_elo, comp_elo_avg) if comp_elo_avg else 1.0
    opp_f = opponent_factor(opp_elo, comp_elo_avg) if comp_elo_avg else 1.0
    def_f = defensive_opponent_factor(opp_elo, comp_elo_avg) if comp_elo_avg else 1.0

    return {
        "competition_factor": comp_f,
        "opponent_factor":    opp_f,
        "defensive_factor":   def_f,
        "league_elo":         league_elo,
        "opponent_elo":       opp_elo,
        "competition_elo_avg": comp_elo_avg,
    }
