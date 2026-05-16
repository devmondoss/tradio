"""
Degradation monitor — answers: should the system recalibrate now?

Three triggers:
  1. Market regime changed
  2. overfit_gap of last 50 trades exceeds threshold
  3. More than 14 days since last calibration
"""
import pandas as pd
from datetime import datetime
from core.db import query_df


class DegradationMonitor:

    def __init__(
        self,
        overfit_gap_threshold: float = 0.20,
        max_days_without_calibration: int = 14,
        min_trades_window: int = 50,
    ):
        self.overfit_gap_threshold = overfit_gap_threshold
        self.max_days_without_calibration = max_days_without_calibration
        self.min_trades_window = min_trades_window

    def check(self) -> dict:
        reasons = []
        details = {}

        regime_change = self._detect_regime_change()
        if regime_change["changed"]:
            reasons.append("REGIME_CHANGE")
            details["regime_change"] = regime_change

        degradation = self._detect_performance_degradation()
        if degradation["is_degraded"]:
            reasons.append("PERFORMANCE_DEGRADATION")
            details["degradation"] = degradation

        days_stale = self._days_since_last_calibration()
        if days_stale > self.max_days_without_calibration:
            reasons.append("SCHEDULED_RECALIBRATION")
            details["days_since_calibration"] = days_stale

        return {
            "should_recalibrate": len(reasons) > 0,
            "reasons": reasons,
            "details": details,
            "checked_at": datetime.utcnow().isoformat(),
        }

    def _detect_regime_change(self) -> dict:
        result = query_df("""
            SELECT regime_combined
            FROM supabase.regime_history
            ORDER BY timestamp_ms DESC
            LIMIT 2
        """)

        if len(result) < 2:
            return {"changed": False}

        current  = result.iloc[0]["regime_combined"]
        previous = result.iloc[1]["regime_combined"]

        return {
            "changed": current != previous,
            "current_regime": current,
            "previous_regime": previous,
        }

    def _detect_performance_degradation(self) -> dict:
        """
        Compares recent 50-trade expectancy against the calibration benchmark.
        Gap > overfit_gap_threshold → degraded.

        50 trades minimum: below this, variance is too high to distinguish
        signal from noise.
        """
        last_cal = query_df("""
            SELECT test_expectancy
            FROM supabase.calibration_log
            WHERE approved_for_deploy = TRUE
            ORDER BY calibrated_at DESC
            LIMIT 1
        """)

        if last_cal.empty:
            return {"is_degraded": False, "reason": "no_calibration_yet"}

        benchmark = last_cal.iloc[0]["test_expectancy"]

        recent = query_df(f"""
            SELECT r_multiple
            FROM supabase.v_signals_with_outcomes
            WHERE close_reason IS NOT NULL
            ORDER BY timestamp_ms DESC
            LIMIT {self.min_trades_window}
        """)

        if len(recent) < self.min_trades_window:
            return {
                "is_degraded": False,
                "reason": f"insufficient_trades_{len(recent)}/{self.min_trades_window}",
            }

        recent_exp = recent["r_multiple"].mean()
        gap = benchmark - recent_exp

        return {
            "is_degraded": gap > self.overfit_gap_threshold,
            "benchmark_expectancy": round(benchmark, 3),
            "recent_expectancy": round(recent_exp, 3),
            "gap": round(gap, 3),
            "threshold": self.overfit_gap_threshold,
            "n_trades": len(recent),
        }

    def _days_since_last_calibration(self) -> float:
        result = query_df("""
            SELECT calibrated_at
            FROM supabase.calibration_log
            ORDER BY calibrated_at DESC
            LIMIT 1
        """)

        if result.empty:
            return 999.0

        last = pd.to_datetime(result.iloc[0]["calibrated_at"])
        delta = datetime.utcnow() - last.replace(tzinfo=None)
        return delta.total_seconds() / 86_400
