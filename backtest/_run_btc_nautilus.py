"""BTC-only Nautilus run — lanzar desde repo root."""
import sys
sys.path.insert(0, "backtest")
import _scalp_nautilus_variants as NV
NV.SYMS = ["BTCUSDT"]
NV.main()
