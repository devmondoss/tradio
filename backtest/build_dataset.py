"""
build_dataset.py
----------------
Une m1_trades.parquet + m1_obi.parquet en un único dataset backtest-ready.

Rango: 2025-06-15 → 2026-01-20

Salida:
    data/bybit-spot/processed/btcusdt_m1.parquet

Columnas del dataset final:
    ts_ms         : timestamp inicio de barra (ms UTC)
    open, high, low, close, volume
    buy_vol, sell_vol, delta, cvd
    obi5_mean, obi10_mean, obi20_mean
    obi10_min, obi10_max
    spread_mean   : spread medio en bps durante el minuto
    mid_open, mid_close

Uso:
    python backtest/build_dataset.py
"""

from pathlib import Path
import pandas as pd
import sys

PROC_DIR   = Path(__file__).parent.parent / "data/bybit-spot/processed"
TRADES_FILE = PROC_DIR / "m1_trades.parquet"
OBI_FILE    = PROC_DIR / "m1_obi.parquet"
OUT_FILE    = PROC_DIR / "btcusdt_m1.parquet"


def main():
    if not TRADES_FILE.exists():
        sys.exit(f"Falta {TRADES_FILE}. Ejecuta primero parse_trades.py")
    if not OBI_FILE.exists():
        sys.exit(f"Falta {OBI_FILE}. Ejecuta primero parse_orderbook.py")

    trades = pd.read_parquet(TRADES_FILE)
    obi    = pd.read_parquet(OBI_FILE)

    print(f"Trades M1 : {len(trades):,} barras")
    print(f"OBI M1    : {len(obi):,} barras")

    # Left join: conserva todas las barras de trades
    # (puede haber minutos sin datos de OB si la descarga fue parcial)
    merged = trades.merge(obi, on="ts_ms", how="left")
    merged = merged.sort_values("ts_ms").reset_index(drop=True)

    out_path = OUT_FILE
    merged.to_parquet(out_path, index=False, engine="pyarrow")

    coverage = merged["obi10_mean"].notna().mean() * 100
    print(f"\nDataset final : {len(merged):,} barras M1")
    print(f"Cobertura OBI : {coverage:.1f}% de los minutos tienen datos de OB")
    first_ts = pd.Timestamp(merged["ts_ms"].iloc[0],  unit="ms", tz="UTC")
    last_ts  = pd.Timestamp(merged["ts_ms"].iloc[-1], unit="ms", tz="UTC")
    print(f"Rango         : {first_ts}  ->  {last_ts}")
    print(f"Guardado      : {out_path}  ({out_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
