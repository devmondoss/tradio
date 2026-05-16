"""
Deflated Sharpe Ratio — statistical gate for edge validation.

Problem: after 200 Optuna trials, one combination will have Sharpe 1.8.
Is that real edge or did we find it by chance across 200 tries?

With 200 trials, the probability of a spurious Sharpe 1.8 is non-negligible.
The Deflated Sharpe adjusts the observed Sharpe for the number of trials.

deflated_sharpe > 0.95 → 95% confidence the edge is real.
deflated_sharpe < 0.95 → result may be statistical noise.

Source: López de Prado, "Advances in Financial Machine Learning" (2018)
"""
import numpy as np
from scipy.stats import norm
import pandas as pd


def deflated_sharpe_ratio(
    returns: pd.Series,
    n_trials: int,
    sr_benchmark: float = 0.0,
) -> float:
    """
    Computes the Deflated Sharpe Ratio.

    Args:
        returns:      r_multiple series from the test period
        n_trials:     number of parameter combinations tested (Optuna trials)
        sr_benchmark: minimum Sharpe expected by chance (default 0)

    Returns:
        Probability (0-1) that the edge is real, not statistical noise.
        >= 0.95 → approved for deploy.
        < 0.95  → rejected.
    """
    n = len(returns)
    if n < 10:
        return 0.0

    sr_observed = returns.mean() / returns.std() * np.sqrt(252)

    # Expected maximum Sharpe from pure luck across n_trials attempts.
    # The more trials, the higher the lucky maximum — so we adjust down.
    expected_max_sr = (
        (1 - np.euler_gamma) * norm.ppf(1 - 1.0 / n_trials) +
        np.euler_gamma * norm.ppf(1 - 1.0 / (n_trials * np.e))
    )

    # Adjust for non-normality of financial returns (fat tails, skew)
    skew = float(returns.skew())
    kurt = float(returns.kurtosis())

    denom = 1 - skew * sr_observed + (kurt - 1) / 4.0 * sr_observed ** 2
    if denom <= 0:
        return 0.0

    dsr = norm.cdf(
        (sr_observed - expected_max_sr) * np.sqrt(n - 1) / np.sqrt(denom)
    )

    return float(np.clip(dsr, 0.0, 1.0))


def edge_is_real(
    returns: pd.Series,
    n_trials: int,
    threshold: float = 0.95,
) -> tuple[bool, float]:
    """Simple wrapper — returns (approved, dsr_score)."""
    dsr = deflated_sharpe_ratio(returns, n_trials)
    return dsr >= threshold, dsr
