"""
Escribe los parámetros calibrados a `deployed_params`.

Desde 2026-05-20 escribe **siempre a Mongo** (lo que lee la UI local) y
adicionalmente a Supabase si las credenciales están presentes (lo que lee el
monitor de Railway). Si Supabase no está configurado se omite — la calibración
puede deployarse en modo local-only.

El loader Rust recarga al detectar cambio de régimen (`request_reload`).
"""
import os
from datetime import datetime, timezone
from typing import Optional

from calibration.core.mongo_db import deploy_params_to_mongo


class ParamDeployer:

    def __init__(self):
        # Supabase es opcional; sin credenciales solo se hace deploy a Mongo.
        self.client = None
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if url and key:
            try:
                from supabase import create_client
                self.client = create_client(url, key)
            except ImportError:
                print("[deploy] supabase pkg no instalado — modo Mongo-only")

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
        Desactiva los params activos previos para este régimen y activa los
        nuevos en Mongo (siempre) y en Supabase (si está configurado).
        Devuelve True si Mongo tuvo éxito — Supabase se considera best-effort.
        """
        mongo_ok = deploy_params_to_mongo(regime, params, calibration_id=calibration_id)

        supabase_ok = self._deploy_supabase(regime, params, calibration_id)

        targets = []
        if mongo_ok:
            targets.append("mongo")
        if supabase_ok is True:
            targets.append("supabase")
        elif supabase_ok is False:
            targets.append("supabase=FAILED")

        if mongo_ok:
            print(f"[deploy] Params deployed for regime '{regime}' → {', '.join(targets) or 'none'}")
            print(f"         test_expectancy={test_expectancy:.3f}R")
            print(f"         overfit_gap={overfit_gap:.3f}R")
            print(f"         deflated_sharpe={dsr:.3f}")

        return mongo_ok

    def _deploy_supabase(
        self, regime: str, params: dict, calibration_id: str
    ) -> Optional[bool]:
        """None si Supabase está deshabilitado; True/False según resultado."""
        if self.client is None:
            return None
        try:
            self.client.table("deployed_params") \
                .update({"is_active": False}) \
                .eq("regime", regime) \
                .eq("is_active", True) \
                .execute()

            self.client.table("deployed_params").insert({
                "regime":         regime,
                "strategy":       None,
                "params":         params,
                "calibration_id": calibration_id,
                "is_active":      True,
                "updated_at":     datetime.now(timezone.utc).isoformat(),
            }).execute()
            return True
        except Exception as e:
            print(f"[deploy] Supabase write FAILED: {e}")
            return False
