"""
MongoDB connection + helpers para los scripts Python.

Parte de la migración a ejecución local: la UI (Rust, `mongo_config_loader.rs`)
lee `deployed_params` desde Mongo. Este módulo es el lado *write*: se invoca
desde `seed_deployed_params.py` (seeding manual) y desde `param_deployer.py`
(salida de la calibración).

Mientras Supabase se mantiene en paralelo (Railway), estos helpers se ejecutan
*adicionalmente* al write a Supabase — nunca lo reemplazan automáticamente.

Variables de entorno:
    MONGODB_URI   default mongodb://localhost:27017
    MONGODB_DB    default flowsurface
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
except ImportError:  # pragma: no cover — solo si falta la dep
    MongoClient = None  # type: ignore[assignment]
    PyMongoError = Exception  # type: ignore[assignment]

DEFAULT_URI = "mongodb://localhost:27018"
DEFAULT_DB = "flowsurface"

_client = None
_db = None


def get_db():
    """Devuelve el handle de la base, conectando perezosamente."""
    global _client, _db
    if _db is not None:
        return _db
    if MongoClient is None:
        raise RuntimeError("pymongo no instalado: pip install pymongo")
    uri = os.getenv("MONGODB_URI", DEFAULT_URI)
    db_name = os.getenv("MONGODB_DB", DEFAULT_DB)
    # serverSelectionTimeoutMS bajo para que un mongod caído falle rápido
    # en lugar de colgar el script ~30s.
    _client = MongoClient(uri, serverSelectionTimeoutMS=2000)
    _db = _client[db_name]
    return _db


def deploy_params_to_mongo(
    regime: str,
    params: dict,
    calibration_id: Optional[str] = None,
) -> bool:
    """
    Desactiva los `deployed_params` activos previos para este régimen y
    activa uno nuevo. El esquema coincide con lo que espera el loader Rust
    en `data/src/strategy/mongo_config_loader.rs::apply_overrides`.

    Documento resultante:
        {
            regime:         str,
            strategy:       None,
            is_active:      True,
            params:         { ...campos planos de StrategyConfig... },
            calibration_id: str | None,
            updated_at:     datetime utc,
        }

    Devuelve True si tuvo éxito, False (con log) si hubo cualquier error.
    Nunca lanza — los scripts deben poder continuar aunque Mongo esté caído.
    """
    try:
        coll = get_db()["deployed_params"]
        # Desactivar el activo anterior (mismo patrón que el deployer Supabase).
        coll.update_many(
            {"regime": regime, "is_active": True},
            {"$set": {"is_active": False}},
        )
        coll.insert_one(
            {
                "regime": regime,
                "strategy": None,
                "params": params,
                "calibration_id": calibration_id,
                "is_active": True,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        return True
    except PyMongoError as e:
        print(f"[mongo-deploy] FAILED for regime={regime}: {e}")
        return False


# ────────────────────────────────────────────────────────────────────────────
#  Adaptador con la API fluida de Supabase (`.table().select().eq().gte()
#  .execute()`) implementada sobre pymongo. Permite migrar los scripts de
#  análisis con un cambio mínimo: reemplazar `create_client(...)` por
#  `mongo_client_compat()`.
# ────────────────────────────────────────────────────────────────────────────


class _MongoResponse:
    """Mimica la respuesta de supabase: tiene atributo `.data` (lista de dicts)."""

    def __init__(self, data):
        self.data = data


class _MongoQuery:
    """Encapsula un find() perezoso con la API fluida de supabase."""

    def __init__(self, coll, table_name: str):
        self._coll = coll
        self._table = table_name
        self._filter: dict = {}
        self._proj = None
        self._sort = None
        self._limit = None

    # ── filtros ────────────────────────────────────────────────────────────

    def eq(self, key, val):
        if key in self._filter and isinstance(self._filter[key], dict):
            self._filter[key]["$eq"] = val
        else:
            self._filter[key] = val
        return self

    def gte(self, key, val):
        cur = self._filter.get(key)
        if isinstance(cur, dict):
            cur["$gte"] = val
        else:
            self._filter[key] = {"$gte": val}
        return self

    def in_(self, key, vals):
        self._filter[key] = {"$in": list(vals)}
        return self

    def neq(self, key, val):
        self._filter[key] = {"$ne": val}
        return self

    # ── proyección / orden / límite ────────────────────────────────────────

    def select(self, fields: str):
        # supabase pasa "f1,f2,f3"; pymongo quiere {f1:1, f2:1, _id:1 si aplica}.
        names = [f.strip() for f in fields.split(",") if f.strip()]
        proj = {}
        for n in names:
            # supabase llama "id" al primary key — en Mongo es _id.
            proj["_id" if n == "id" else n] = 1
        self._proj = proj
        return self

    def order(self, key, desc: bool = False):
        self._sort = [(key, -1 if desc else 1)]
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    # ── ejecución ──────────────────────────────────────────────────────────

    def execute(self):
        cur = self._coll.find(self._filter, self._proj)
        if self._sort:
            cur = cur.sort(self._sort)
        if self._limit:
            cur = cur.limit(self._limit)
        out = []
        for d in cur:
            # Renombrar _id → id para que los callers (escritos para Supabase)
            # encuentren el campo donde lo esperan.
            if "_id" in d:
                d["id"] = str(d.pop("_id"))
            # signal_id (FK) también suele ser ObjectId en nuestros docs.
            if "signal_id" in d and not isinstance(d["signal_id"], str):
                d["signal_id"] = str(d["signal_id"])
            out.append(d)
        return _MongoResponse(out)


class _MongoClientCompat:
    """Reemplazo drop-in de `supabase.Client` para los scripts de análisis."""

    def __init__(self, db):
        self._db = db

    def table(self, name: str) -> _MongoQuery:
        return _MongoQuery(self._db[name], name)


def analytics_client():
    """Devuelve el cliente apropiado según `ANALYTICS_BACKEND` (default mongo).

    - `mongo` (default): cliente Mongo con API Supabase-compatible. Útil para
      los scripts de análisis que esperan `client.table(...).select(...).eq(...)`.
    - `supabase`: cliente Supabase real (requiere SUPABASE_URL/KEY).
    """
    import os
    import sys as _sys

    backend = os.environ.get("ANALYTICS_BACKEND", "mongo").lower()
    if backend == "supabase":
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            print("ERROR: ANALYTICS_BACKEND=supabase pero SUPABASE_URL/KEY no están seteadas")
            _sys.exit(1)
        from supabase import create_client  # type: ignore[import-not-found]
        return create_client(url, key)
    return mongo_client_compat()


def mongo_client_compat() -> _MongoClientCompat:
    """Cliente con la misma API que `supabase.create_client(...).` Útil para
    portar scripts de Supabase con cambios mínimos.

    Limitaciones intencionadas: soporta solo lecturas (`select`/`eq`/`gte`/
    `in_`/`neq`/`order`/`limit`/`execute`). No implementa upsert/insert/update
    — para escrituras, usa las funciones tipadas (`deploy_params_to_mongo`,
    los writers Rust, etc.).
    """
    return _MongoClientCompat(get_db())
