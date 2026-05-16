"""
Walk-Forward Optimization — the correct method for trading system calibration.

Why walk-forward instead of full-sample optimization:
  Calibrating on all historical data finds parameters that are perfect for the
  past. Walk-forward simulates production: calibrate on the last 60 days, apply
  on the next 20, repeat. You never see the future when calibrating.

Key metric — overfit_gap = train_expectancy - test_expectancy:
  < 0.15R  → robust parameters
  0.15-0.30R → mild overfitting, use with caution
  > 0.30R  → severe overfitting, do not deploy

Source: López de Prado, "Advances in Financial Machine Learning" (2018)
"""
import pandas as pd
import numpy as np
import optuna
from typing import Optional
from datetime import timedelta

optuna.logging.set_verbosity(optuna.logging.WARNING)


# Search space — ranges defined by business logic:
#   liq_hunt_min_usd: $100K (noisy) to $5M (too few signals)
#   min_score: 0.50 (too many bad signals) to 0.85 (too restrictive)
PARAM_SPACE = {
    "liq_hunt_min_usd":          (100_000,  5_000_000),
    "min_divergence":            (0.08,     0.35),
    "funding_extreme_threshold": (0.0002,   0.0012),
    "funding_elevated_threshold":(0.0001,   0.0006),
    "smart_short_threshold":     (0.38,     0.52),
    "retail_long_threshold":     (0.52,     0.72),
    "min_score":                 (0.55,     0.82),
    "max_spread_bps":            (1.0,      3.5),
}


def simulate_with_params(df: pd.DataFrame, params: dict) -> pd.Series:
    """
    Applies params to a historical signals DataFrame and returns the
    r_multiple series for signals that would have passed all filters.

    Does NOT execute real trades — filters historical signals and returns
    the r_multiples of those that would have been taken.
    """
    mask = pd.Series([True] * len(df), index=df.index)

    # LiquidationHunt: minimum liquidation threshold
    liq_mask = (
        (df["strategy"] != "LIQUIDATION_HUNT") |
        (df["short_liq_usd_5m"] > params.get("liq_hunt_min_usd", 500_000)) |
        (df["long_liq_usd_5m"]  > params.get("liq_hunt_min_usd", 500_000))
    )
    mask &= liq_mask

    # SmartMoneyDivergence: minimum divergence
    div_mask = (
        (df["strategy"] != "SMART_MONEY_DIVERGENCE") |
        (df["ls_divergence"].abs() > params.get("min_divergence", 0.18))
    )
    mask &= div_mask

    # FundingExhaustionReversal: minimum funding rate
    fund_mask = (
        (df["strategy"] != "FUNDING_EXHAUSTION_REVERSAL") |
        (df["funding_current"].abs() > params.get("funding_extreme_threshold", 0.0006))
    )
    mask &= fund_mask

    # All detectors: score and spread
    mask &= df["score"] > params.get("min_score", 0.70)

    if "spread_bps" in df.columns:
        spread_mask = df["spread_bps"].isna() | (df["spread_bps"] <= params.get("max_spread_bps", 2.0))
        mask &= spread_mask

    filtered = df[mask & df["r_multiple"].notna()]
    return filtered["r_multiple"]


def walk_forward_optimize(
    df: pd.DataFrame,
    train_days: int = 60,
    test_days: int = 20,
    n_optuna_trials: int = 200,
    min_trades_per_window: int = 20,
) -> pd.DataFrame:
    """
    Walk-forward optimization over the DataFrame.

    For each test window:
      1. Take the prior train_days as training data
      2. Use Optuna to find best params on train
      3. Apply those params on the test period
      4. Record train_expectancy, test_expectancy, overfit_gap

    Returns a DataFrame with results for each period.
    """
    df = df.copy()
    df["signal_date"] = pd.to_datetime(df["timestamp_ms"], unit="ms").dt.date

    results = []
    dates = pd.date_range(
        df["signal_date"].min() + timedelta(days=train_days),
        df["signal_date"].max(),
        freq=f"{test_days}D",
    )

    for test_start in dates:
        train_start = test_start - timedelta(days=train_days)
        test_end    = test_start + timedelta(days=test_days)

        train_df = df[
            (df["signal_date"] >= train_start.date()) &
            (df["signal_date"] <  test_start.date())
        ]
        test_df = df[
            (df["signal_date"] >= test_start.date()) &
            (df["signal_date"] <  test_end.date())
        ]

        if len(train_df) < min_trades_per_window:
            continue

        def objective(trial):
            params = {
                k: trial.suggest_float(k, lo, hi)
                for k, (lo, hi) in PARAM_SPACE.items()
            }
            r = simulate_with_params(train_df, params)
            if len(r) < min_trades_per_window:
                return -1.0
            return float(r.mean())

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=n_optuna_trials, show_progress_bar=False)
        best_params = study.best_params

        train_r = simulate_with_params(train_df, best_params)
        test_r  = simulate_with_params(test_df,  best_params)

        train_exp = float(train_r.mean()) if len(train_r) > 0 else None
        test_exp  = float(test_r.mean())  if len(test_r)  > 0 else None

        results.append({
            "test_period_start": test_start,
            "best_params":       best_params,
            "train_expectancy":  round(train_exp, 3) if train_exp is not None else None,
            "test_expectancy":   round(test_exp,  3) if test_exp  is not None else None,
            "overfit_gap":       round(train_exp - test_exp, 3)
                                 if train_exp is not None and test_exp is not None else None,
            "n_train_trades":    len(train_r),
            "n_test_trades":     len(test_r),
        })

        if train_exp is not None and test_exp is not None:
            print(
                f"  Period {test_start.date()}: "
                f"train={train_exp:.3f}R test={test_exp:.3f}R "
                f"gap={train_exp-test_exp:.3f}R n_test={len(test_r)}"
            )
        else:
            print(f"  Period {test_start.date()}: insufficient data")

    return pd.DataFrame(results)


def get_best_robust_params(wf_results: pd.DataFrame) -> Optional[dict]:
    """
    From all walk-forward periods, selects params from the period with the
    best test_expectancy AND overfit_gap < 0.20R.

    If all periods show gap > 0.20R → systematic overfitting → do not deploy.
    """
    robust = wf_results[
        wf_results["overfit_gap"].notna() &
        (wf_results["overfit_gap"] < 0.20) &
        (wf_results["n_test_trades"] >= 10)
    ]

    if robust.empty:
        return None

    best_row = robust.loc[robust["test_expectancy"].idxmax()]
    return best_row["best_params"]
