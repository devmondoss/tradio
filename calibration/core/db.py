import os
import duckdb
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

_con = None


def get_connection() -> duckdb.DuckDBPyConnection:
    """
    Returns a DuckDB connection with Supabase attached via pg extension.
    DuckDB reads PostgreSQL directly — no ORM needed.
    """
    global _con
    if _con is None:
        _con = duckdb.connect()
        _con.execute("INSTALL postgres; LOAD postgres;")
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL not set in environment")
        _con.execute(f"""
            ATTACH '{db_url}'
            AS supabase (TYPE postgres, READ_ONLY)
        """)
    return _con


def query_df(sql: str) -> pd.DataFrame:
    return get_connection().execute(sql).df()


def query_signals(
    strategy: str | None = None,
    regime: str | None = None,
    min_timestamp_ms: int | None = None,
    action: str = "ShadowSignal",
) -> pd.DataFrame:
    """
    Typed query for the main analytics view.
    Always filters by action='ShadowSignal' by default.
    """
    conditions = [f"action = '{action}'"]

    if strategy:
        conditions.append(f"strategy = '{strategy}'")
    if regime:
        conditions.append(f"regime_combined LIKE '{regime}%'")
    if min_timestamp_ms:
        conditions.append(f"timestamp_ms > {min_timestamp_ms}")

    where = " AND ".join(conditions)

    return query_df(f"""
        SELECT * FROM supabase.v_signals_with_outcomes
        WHERE {where}
        ORDER BY timestamp_ms ASC
    """)
