"""
Acceso a datos analíticos con dos backends:

  - **MongoDB** (default desde la migración local): leído de `flowsurface.*`.
    Cubre los helpers tipados (`query_signals`, etc.) que consumen los scripts
    de calibración / análisis.

  - **Supabase via DuckDB-Postgres**: backend legacy para `query_df(sql)` con
    SQL crudo. Sigue funcionando si `DATABASE_URL` está seteado. Útil para
    consultas que aún no fueron portadas a aggregations de Mongo (joins
    complejos con vistas, group-by, etc.).

Selección de backend:
  - `ANALYTICS_BACKEND=mongo`     → fuerza Mongo (rompe `query_df`)
  - `ANALYTICS_BACKEND=supabase`  → fuerza Supabase
  - sin variable                   → auto: Mongo si `MONGODB_URI` es resolvible,
                                    Supabase si solo `DATABASE_URL` está seteada,
                                    Mongo como último default.
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ────────────────────────────────────────────────────────────────────────────
#  Backend resolution
# ────────────────────────────────────────────────────────────────────────────


def _selected_backend() -> str:
    forced = os.getenv("ANALYTICS_BACKEND", "").strip().lower()
    if forced in ("mongo", "supabase"):
        return forced
    if os.getenv("DATABASE_URL"):
        return "supabase"
    return "mongo"


# ────────────────────────────────────────────────────────────────────────────
#  Supabase / DuckDB backend (legacy, raw SQL)
# ────────────────────────────────────────────────────────────────────────────

_con = None


def _get_supabase_connection():
    """DuckDB con la extensión postgres atachada a Supabase."""
    global _con
    if _con is None:
        import duckdb

        _con = duckdb.connect()
        _con.execute("INSTALL postgres; LOAD postgres;")
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise RuntimeError(
                "DATABASE_URL no seteada — el backend Supabase no está disponible. "
                "Setea DATABASE_URL o usa ANALYTICS_BACKEND=mongo con helpers tipados."
            )
        _con.execute(f"""
            ATTACH '{db_url}'
            AS supabase (TYPE postgres, READ_ONLY)
        """)
    return _con


def query_df(sql: str) -> pd.DataFrame:
    """Ejecuta SQL crudo contra la vista Postgres de Supabase.

    Solo funciona con backend Supabase. Si está activo el backend Mongo, lanza
    un error claro apuntando a los helpers tipados.
    """
    backend = _selected_backend()
    if backend == "mongo":
        raise RuntimeError(
            "query_df(sql) requiere backend Supabase (DATABASE_URL). "
            "Backend activo: mongo. Usa helpers tipados (query_signals, ...) o "
            "setea ANALYTICS_BACKEND=supabase si tienes la BD Postgres disponible."
        )
    return _get_supabase_connection().execute(sql).df()


# ────────────────────────────────────────────────────────────────────────────
#  Helpers tipados (Mongo-native, fallback Supabase si está forzado)
# ────────────────────────────────────────────────────────────────────────────


def query_signals(
    strategy: Optional[str] = None,
    regime: Optional[str] = None,
    min_timestamp_ms: Optional[int] = None,
    action: str = "ShadowSignal",
) -> pd.DataFrame:
    """Vista equivalente a `v_signals_with_outcomes`: une `shadow_signals` con
    `signal_outcomes` por `signal_id` y devuelve un DataFrame con los campos
    de la señal + los de su outcome (prefijo `o_` para evitar colisiones).

    Filtros opcionales — todos AND.
    """
    backend = _selected_backend()
    if backend == "supabase":
        return _query_signals_supabase(strategy, regime, min_timestamp_ms, action)
    return _query_signals_mongo(strategy, regime, min_timestamp_ms, action)


def _query_signals_mongo(
    strategy: Optional[str],
    regime: Optional[str],
    min_timestamp_ms: Optional[int],
    action: str,
) -> pd.DataFrame:
    """Pipeline aggregation: filtra señales → $lookup outcomes → flatten."""
    # Import perezoso para no exigir pymongo si solo se usa el backend Supabase.
    from .mongo_db import get_db  # type: ignore[no-redef]

    match = {"action": action}
    if strategy:
        match["strategy"] = strategy
    if regime:
        # supabase usaba LIKE 'X%'; aquí prefix-match con regex anclada
        match["regime_combined"] = {"$regex": f"^{regime}"}
    if min_timestamp_ms:
        match["timestamp_ms"] = {"$gt": min_timestamp_ms}

    pipeline = [
        {"$match": match},
        {
            "$lookup": {
                "from": "signal_outcomes",
                "localField": "_id",
                "foreignField": "signal_id",
                "as": "_outcomes",
            }
        },
        {"$sort": {"timestamp_ms": 1}},
    ]

    rows: list[dict] = []
    for d in get_db()["shadow_signals"].aggregate(pipeline):
        outcomes = d.pop("_outcomes", []) or []
        # Si hay múltiples legs, agregamos pnl/fees y elegimos la razón final
        # (mismo criterio que mongo_to_analyze.build_paper_trades).
        if outcomes:
            outcomes.sort(
                key=lambda o: (o.get("timestamp_ms") or 0) + (o.get("duration_ms") or 0)
            )
            d["o_pnl_net_usd"] = sum(o.get("pnl_net_usd", 0.0) for o in outcomes)
            d["o_pnl_gross_usd"] = sum(o.get("pnl_gross_usd", 0.0) for o in outcomes)
            d["o_duration_ms"] = sum(o.get("duration_ms", 0) for o in outcomes)
            d["o_mfe_r"] = max((o.get("mfe_r") or 0.0) for o in outcomes)
            d["o_mae_r"] = min((o.get("mae_r") or 0.0) for o in outcomes)
            d["o_close_reason"] = outcomes[-1].get("close_reason")
            d["o_r_multiple"] = outcomes[-1].get("r_multiple")
            d["o_close_price"] = outcomes[-1].get("close_price")
        d["id"] = str(d.pop("_id"))
        rows.append(d)
    return pd.DataFrame(rows)


def _query_signals_supabase(
    strategy: Optional[str],
    regime: Optional[str],
    min_timestamp_ms: Optional[int],
    action: str,
) -> pd.DataFrame:
    """Versión Supabase legacy — usa la vista `v_signals_with_outcomes`."""
    conditions = [f"action = '{action}'"]
    if strategy:
        conditions.append(f"strategy = '{strategy}'")
    if regime:
        conditions.append(f"regime_combined LIKE '{regime}%'")
    if min_timestamp_ms:
        conditions.append(f"timestamp_ms > {min_timestamp_ms}")
    where = " AND ".join(conditions)
    return _get_supabase_connection().execute(
        f"SELECT * FROM supabase.v_signals_with_outcomes WHERE {where} ORDER BY timestamp_ms ASC"
    ).df()


# Compatibilidad retroactiva — algunos callers importan `get_connection`.
def get_connection():
    """Devuelve la conexión Supabase/DuckDB. **No** intenta nada en Mongo."""
    return _get_supabase_connection()
