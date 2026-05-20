"""
Seed deployed_params table with conservative per-regime params.

Run from repo root:
    python scripts/seed_deployed_params.py

Requires: pip install supabase python-dotenv
Uses SUPABASE_URL and SUPABASE_KEY from .env or environment.
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # env vars already set

try:
    from supabase import create_client
except ImportError:
    print("ERROR: pip install supabase")
    sys.exit(1)

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
if not url or not key:
    print("ERROR: SUPABASE_URL and SUPABASE_KEY must be set")
    sys.exit(1)

client = create_client(url, key)

# Conservative params derived from audit 2026-05-19.
# Tighter than defaults (min_rr=1.5, min_score=0.60) to reduce noise until
# walk-forward calibration has enough samples (requires ≥10 outcomes per regime).
CONSERVATIVE_PARAMS = {
    "min_rr": 1.8,
    "min_score": 0.70,
    "max_spread_bps": 2.0,
    "liq_hunt_min_usd": 25000.0,
    "cooldown_bars": 5,
}

REGIMES = ["TrendUp", "TrendDown", "Expansion", "Chop"]

print(f"Seeding deployed_params ({len(REGIMES)} regimes)...")
for regime in REGIMES:
    result = (
        client.table("deployed_params")
        .upsert(
            {
                "regime": regime,
                "strategy": None,
                "is_active": True,
                "params": CONSERVATIVE_PARAMS,
            },
            on_conflict="regime,strategy,is_active",
        )
        .execute()
    )
    print(f"  {regime}: ok")

print("Done. Monitor will load calibrated params on next startup or regime change.")
