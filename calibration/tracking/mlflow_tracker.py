import os
import mlflow
import pandas as pd
from datetime import datetime


class CalibrationTracker:

    def __init__(self):
        mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000"))
        mlflow.set_experiment("flowsurface_calibration")

    def log_calibration(
        self,
        regime: str,
        trigger_reason: str,
        wf_results: pd.DataFrame,
        best_params: dict,
        dsr: float,
        approved: bool,
    ) -> str:
        """
        Logs a complete calibration run to MLflow.
        Returns the run_id for reference.
        """
        run_name = f"{regime}_{trigger_reason}_{datetime.utcnow():%Y%m%d_%H%M}"

        with mlflow.start_run(run_name=run_name) as run:
            mlflow.log_param("regime",         regime)
            mlflow.log_param("trigger_reason", trigger_reason)
            mlflow.log_param("timestamp",      datetime.utcnow().isoformat())

            mlflow.log_params(best_params)

            valid = wf_results.dropna(subset=["test_expectancy"])
            if not valid.empty:
                mlflow.log_metric("avg_train_expectancy", float(valid["train_expectancy"].mean()))
                mlflow.log_metric("avg_test_expectancy",  float(valid["test_expectancy"].mean()))
                mlflow.log_metric("avg_overfit_gap",      float(valid["overfit_gap"].mean()))
                mlflow.log_metric("n_wf_periods",         len(valid))

            mlflow.log_metric("deflated_sharpe", dsr)
            mlflow.log_param("approved", str(approved))

            wf_path = f"/tmp/wf_results_{regime}.csv"
            wf_results.to_csv(wf_path, index=False)
            mlflow.log_artifact(wf_path)

        return run.info.run_id
