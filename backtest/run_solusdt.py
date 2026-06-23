"""Entry point para backtest SOLUSDT — delega a backtest_multiasset.run_for_symbol."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from backtest_multiasset import run_for_symbol

if __name__ == "__main__":
    run_for_symbol("SOLUSDT")
