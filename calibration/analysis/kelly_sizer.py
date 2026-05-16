"""
Kelly Criterion sizing — optimal position size per detector.

Why Kelly instead of fixed sizing:
  Fixed sizing (e.g. 1 BTC per trade) doesn't protect capital in drawdown.
  A streak of 5 losses costs the same as a streak of 5 wins gains.
  Kelly allocates more capital to signals with higher expected edge.

Quarter Kelly (0.25x full Kelly) is the algorithmic trading standard.
Full Kelly is mathematically optimal but maximizes variance — in practice
leads to unacceptable drawdowns. Quarter Kelly gives 75% of full Kelly's
growth rate with much lower volatility.
"""
import pandas as pd
from core.db import query_df


def calculate_kelly_by_detector(
    min_trades: int = 100,
    kelly_fraction: float = 0.25,
) -> dict:
    """
    Computes Quarter Kelly per detector using historical data.
    Cap: never more than 10% of equity per trade.
    """
    results = {}

    strategies = query_df("""
        SELECT DISTINCT strategy
        FROM supabase.v_signals_with_outcomes
        WHERE close_reason IS NOT NULL
    """)["strategy"].tolist()

    for strategy in strategies:
        data = query_df(f"""
            SELECT r_multiple
            FROM supabase.v_signals_with_outcomes
            WHERE strategy = '{strategy}'
              AND close_reason IS NOT NULL
        """)

        if len(data) < min_trades:
            results[strategy] = {
                "kelly": None,
                "reason": f"insufficient_trades_{len(data)}/{min_trades}",
            }
            continue

        r = data["r_multiple"]
        win_rate = float((r > 0).mean())
        avg_win  = float(r[r > 0].mean()) if (r > 0).any() else 0.0
        avg_loss = float(abs(r[r < 0].mean())) if (r < 0).any() else 0.0

        if avg_loss == 0:
            results[strategy] = {"kelly": None, "reason": "no_losing_trades"}
            continue

        b = avg_win / avg_loss
        full_kelly    = (b * win_rate - (1 - win_rate)) / b
        quarter_kelly = full_kelly * kelly_fraction
        max_size_pct  = min(quarter_kelly, 0.10)  # hard cap: 10% per trade

        results[strategy] = {
            "win_rate":      round(win_rate,      3),
            "avg_win_r":     round(avg_win,       3),
            "avg_loss_r":    round(avg_loss,      3),
            "full_kelly":    round(full_kelly,    4),
            "quarter_kelly": round(quarter_kelly, 4),
            "max_size_pct":  round(max_size_pct,  4),
            "n_trades":      len(data),
        }

    return results


def get_position_size(
    signal_score: float,
    strategy: str,
    equity_btc: float,
    kelly_table: dict,
) -> float:
    """
    Scales position size by signal score within the detector's quarter Kelly.

    score 0.90 → 100% of quarter kelly
    score 0.70 → 78% of quarter kelly
    score 0.60 → 67% of quarter kelly
    """
    entry = kelly_table.get(strategy, {})
    max_fraction = entry.get("max_size_pct", 0.02) if isinstance(entry, dict) else 0.02

    if not max_fraction or max_fraction <= 0:
        return equity_btc * 0.01

    # Non-linear score scaling — penalizes marginal signals
    score_multiplier = (signal_score / 0.90) ** 1.5
    final_fraction = min(max_fraction * score_multiplier, 0.10)

    return equity_btc * final_fraction
