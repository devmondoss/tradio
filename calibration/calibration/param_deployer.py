"""
Writes calibrated parameters to Supabase deployed_params table.
The Rust system reads them automatically on the next config check (every 5 min).
"""
import os
from datetime import datetime, timezone
from supabase import create_client


class ParamDeployer:

    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")
        self.client = create_client(url, key)

    def deploy(
        self,
        regime: str,
        params: dict,
        calibration_id: str,
        test_expectancy: float,
        overfit_gap: float,
        dsr: float,
    ) -> bool:
        """
        Deactivates previous params for this regime and activates the new ones.
        Returns True on success.
        """
        try:
            # Deactivate current active params for this regime
            self.client.table("deployed_params") \
                .update({"is_active": False}) \
                .eq("regime", regime) \
                .eq("is_active", True) \
                .execute()

            # Insert new active params
            self.client.table("deployed_params").insert({
                "regime":         regime,
                "strategy":       None,
                "params":         params,
                "calibration_id": calibration_id,
                "is_active":      True,
                "updated_at":     datetime.now(timezone.utc).isoformat(),
            }).execute()

            print(f"[deploy] Params deployed for regime '{regime}'")
            print(f"         test_expectancy={test_expectancy:.3f}R")
            print(f"         overfit_gap={overfit_gap:.3f}R")
            print(f"         deflated_sharpe={dsr:.3f}")
            return True

        except Exception as e:
            print(f"[deploy] FAILED: {e}")
            return False
