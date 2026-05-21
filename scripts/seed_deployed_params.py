"""
Seed deployed_params con parámetros conservadores por régimen.

Desde 2026-05-20 escribe a **MongoDB local** (lo que lee la UI) y opcionalmente
a Supabase si las credenciales están seteadas (lo que sigue leyendo el monitor
de Railway). Si solo configuraste uno de los dos, el otro se omite con un log.

Run desde la raíz del repo:
    python scripts/seed_deployed_params.py

Requiere: pip install pymongo  (supabase es opcional)

Env vars:
    MONGODB_URI   default mongodb://localhost:27017
    MONGODB_DB    default flowsurface
    SUPABASE_URL  opcional (Supabase paralelo)
    SUPABASE_KEY  opcional
"""

import os
import sys
from pathlib import Path

# Hacer importable `calibration.core.mongo_db` desde scripts/.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # env vars ya seteadas

from calibration.core.mongo_db import deploy_params_to_mongo

# Conservative params derivados del audit 2026-05-19.
# Más estrictos que los defaults (min_rr=1.5, min_score=0.60) para reducir
# ruido hasta tener ≥10 outcomes por régimen para walk-forward calibration.
CONSERVATIVE_PARAMS = {
    "min_rr": 1.8,
    "min_score": 0.70,
    "max_spread_bps": 2.0,
    "liq_hunt_min_usd": 25000.0,
    "cooldown_bars": 5,
}

REGIMES = ["TrendUp", "TrendDown", "Expansion", "Chop"]


def seed_mongo() -> int:
    """Escribe a Mongo. Devuelve nº de regímenes seedeados con éxito."""
    print(f"Seeding Mongo deployed_params ({len(REGIMES)} regimes)...")
    ok = 0
    for regime in REGIMES:
        if deploy_params_to_mongo(regime, CONSERVATIVE_PARAMS, calibration_id="seed-conservative-2026-05-20"):
            print(f"  {regime}: ok (mongo)")
            ok += 1
        else:
            print(f"  {regime}: FAILED (mongo)")
    return ok


def seed_supabase() -> int:
    """Escribe a Supabase si las credenciales están presentes. None si se omite."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("Supabase: SUPABASE_URL/KEY no seteadas — se omite (modo local-only).")
        return 0

    try:
        from supabase import create_client
    except ImportError:
        print("Supabase: paquete `supabase` no instalado — se omite.")
        return 0

    client = create_client(url, key)
    print(f"Seeding Supabase deployed_params ({len(REGIMES)} regimes)...")
    ok = 0
    for regime in REGIMES:
        try:
            client.table("deployed_params").upsert(
                {
                    "regime": regime,
                    "strategy": None,
                    "is_active": True,
                    "params": CONSERVATIVE_PARAMS,
                },
                on_conflict="regime,strategy,is_active",
            ).execute()
            print(f"  {regime}: ok (supabase)")
            ok += 1
        except Exception as e:
            print(f"  {regime}: FAILED (supabase): {e}")
    return ok


if __name__ == "__main__":
    mongo_ok = seed_mongo()
    supabase_ok = seed_supabase()
    print(f"\nDone. mongo={mongo_ok}/{len(REGIMES)}  supabase={supabase_ok}/{len(REGIMES)}")
    print("La UI recargará al detectar cambio de régimen (request_reload).")
