"""
FlowSurface calibration monitor — main entry point.

Usage:
  python monitor.py            # continuous loop (checks every 30 min)
  python monitor.py --once     # single check
  python monitor.py --force    # force recalibration now
  python monitor.py --status   # print system status table

Loop logic (every 30 min):
  1. Check if recalibration is needed (degradation, regime change, schedule)
  2. If yes → run walk-forward for current regime
  3. Validate with Deflated Sharpe gate
  4. If approved → deploy to Supabase → Rust picks it up within 5 min
  5. Log everything to MLflow + Supabase calibration_log
"""
import argparse
import os
import time
import schedule
from datetime import datetime

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from supabase import create_client

from core.db import query_signals, query_df
from core.degradation_monitor import DegradationMonitor
from calibration.walk_forward import walk_forward_optimize, get_best_robust_params, simulate_with_params
from calibration.deflated_sharpe import edge_is_real
from calibration.param_deployer import ParamDeployer
from tracking.mlflow_tracker import CalibrationTracker

load_dotenv()
console = Console()


def get_current_regime() -> str:
    try:
        result = query_df("""
            SELECT regime_combined
            FROM supabase.regime_history
            ORDER BY timestamp_ms DESC
            LIMIT 1
        """)
        return result.iloc[0]["regime_combined"] if not result.empty else "Unknown"
    except Exception:
        return "Unknown"


def run_calibration_pipeline(trigger_reason: str = "MANUAL") -> None:
    regime = get_current_regime()
    console.print(f"\n[bold]Calibration started[/bold] — regime: [cyan]{regime}[/cyan]")
    console.print(f"  Trigger: {trigger_reason}")

    # ── 1. Load signals for the current regime ──────────────────────────────
    signals = query_signals(regime=regime)
    min_trades = int(os.getenv("MIN_TRADES_FOR_CALIBRATION", 50))

    if len(signals) < min_trades:
        console.print(
            f"[yellow]Insufficient data:[/yellow] {len(signals)} trades "
            f"(need {min_trades}). Skipping."
        )
        return

    console.print(f"  Data: {len(signals)} signals for regime '{regime}'")

    # ── 2. Walk-Forward Optimization ────────────────────────────────────────
    console.print("  Running walk-forward optimization...")
    wf_results = walk_forward_optimize(
        df=signals,
        train_days=int(os.getenv("WALK_FORWARD_TRAIN_DAYS", 60)),
        test_days=int(os.getenv("WALK_FORWARD_TEST_DAYS", 20)),
        n_optuna_trials=int(os.getenv("MAX_OPTUNA_TRIALS", 200)),
    )

    if wf_results.empty:
        console.print("[red]Walk-forward returned no results. Aborting.[/red]")
        return

    best_params = get_best_robust_params(wf_results)
    if best_params is None:
        console.print(
            "[red]All walk-forward periods show high overfitting. "
            "Keeping current params.[/red]"
        )
        return

    # ── 3. Deflated Sharpe Gate ─────────────────────────────────────────────
    last_test_signals = signals.tail(100)
    test_returns = simulate_with_params(last_test_signals, best_params)

    n_trials = int(os.getenv("MAX_OPTUNA_TRIALS", 200))
    approved, dsr = edge_is_real(
        test_returns,
        n_trials=n_trials,
        threshold=float(os.getenv("DEFLATED_SHARPE_THRESHOLD", 0.95)),
    )

    # ── 4. Log to MLflow ────────────────────────────────────────────────────
    tracker = CalibrationTracker()
    run_id = tracker.log_calibration(
        regime=regime,
        trigger_reason=trigger_reason,
        wf_results=wf_results,
        best_params=best_params,
        dsr=dsr,
        approved=approved,
    )

    # ── 5. Log to Supabase calibration_log ──────────────────────────────────
    valid    = wf_results.dropna(subset=["test_expectancy"])
    avg_test = float(valid["test_expectancy"].mean())
    avg_gap  = float(valid["overfit_gap"].mean())

    sb_client = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_KEY"),
    )

    cal_row = sb_client.table("calibration_log").insert({
        "trigger_reason":      trigger_reason,
        "regime":              regime,
        "params":              best_params,
        "train_expectancy":    float(valid["train_expectancy"].mean()),
        "test_expectancy":     avg_test,
        "overfit_gap":         avg_gap,
        "n_train_trades":      int(valid["n_train_trades"].sum()),
        "n_test_trades":       int(valid["n_test_trades"].sum()),
        "deflated_sharpe":     dsr,
        "edge_is_real":        approved,
        "approved_for_deploy": approved,
        "deploy_reason":       "DSR >= 0.95" if approved else f"DSR={dsr:.3f} < 0.95",
    }).execute()

    calibration_id = cal_row.data[0]["id"]

    # ── 6. Deploy if approved ────────────────────────────────────────────────
    if approved:
        deployer = ParamDeployer()
        deployer.deploy(
            regime=regime,
            params=best_params,
            calibration_id=calibration_id,
            test_expectancy=avg_test,
            overfit_gap=avg_gap,
            dsr=dsr,
        )
        console.print(
            f"[green]Calibration approved and deployed[/green] "
            f"(DSR={dsr:.3f} test_exp={avg_test:.3f}R)"
        )
    else:
        console.print(
            f"[yellow]Calibration rejected[/yellow] — "
            f"edge not statistically significant "
            f"(DSR={dsr:.3f} < 0.95). Keeping current params."
        )

    console.print(f"  MLflow run: {run_id}\n")


def run_check() -> None:
    monitor = DegradationMonitor()
    status = monitor.check()

    if status["should_recalibrate"]:
        reason = "_".join(status["reasons"])
        console.print(f"[yellow]Recalibration triggered:[/yellow] {reason}")
        run_calibration_pipeline(trigger_reason=reason)
    else:
        console.print(
            f"[green]System healthy[/green] — "
            f"no recalibration needed "
            f"({datetime.utcnow():%H:%M UTC})"
        )


def print_status() -> None:
    table = Table(title="FlowSurface System Status")
    table.add_column("Metric",  style="cyan")
    table.add_column("Value",   style="green")

    regime = get_current_regime()
    table.add_row("Current regime", regime)

    try:
        n_24h = query_df("""
            SELECT COUNT(*) AS n
            FROM supabase.shadow_signals
            WHERE timestamp_ms > EXTRACT(EPOCH FROM NOW() - INTERVAL '24 hours') * 1000
              AND action = 'ShadowSignal'
        """).iloc[0]["n"]
        table.add_row("Signals last 24h", str(int(n_24h)))
    except Exception:
        table.add_row("Signals last 24h", "N/A")

    try:
        recent = query_df("""
            SELECT AVG(r_multiple) AS exp
            FROM supabase.v_signals_with_outcomes
            WHERE close_reason IS NOT NULL
            ORDER BY timestamp_ms DESC
            LIMIT 50
        """)
        exp = recent.iloc[0]["exp"]
        table.add_row("Recent expectancy (50t)", f"{exp:.3f}R" if exp else "N/A")
    except Exception:
        table.add_row("Recent expectancy (50t)", "N/A")

    try:
        last_cal = query_df("""
            SELECT calibrated_at, test_expectancy, overfit_gap, approved_for_deploy
            FROM supabase.calibration_log
            ORDER BY calibrated_at DESC
            LIMIT 1
        """)
        if not last_cal.empty:
            row = last_cal.iloc[0]
            table.add_row("Last calibration",   str(row["calibrated_at"])[:16])
            table.add_row("  test_expectancy",  f"{row['test_expectancy']:.3f}R")
            table.add_row("  overfit_gap",      f"{row['overfit_gap']:.3f}R")
            table.add_row("  approved",         "YES" if row["approved_for_deploy"] else "NO")
    except Exception:
        table.add_row("Last calibration", "N/A")

    console.print(table)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FlowSurface calibration monitor")
    parser.add_argument("--once",   action="store_true", help="Single check then exit")
    parser.add_argument("--force",  action="store_true", help="Force recalibration now")
    parser.add_argument("--status", action="store_true", help="Print status table then exit")
    args = parser.parse_args()

    if args.status:
        print_status()

    elif args.force:
        run_calibration_pipeline(trigger_reason="FORCED")

    elif args.once:
        run_check()

    else:
        console.print("[bold]FlowSurface calibration monitor started[/bold]")
        print_status()

        schedule.every(30).minutes.do(run_check)
        schedule.every(6).hours.do(print_status)

        while True:
            schedule.run_pending()
            time.sleep(60)
