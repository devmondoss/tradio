import json, sys, subprocess, pathlib

root = pathlib.Path(__file__).parent
script = root / "liquidity_app_backtest.py"

values = [1.5, 1.6, 1.7, 1.8, 1.9, 2.0, 2.1, 2.2, 2.3, 2.4, 2.5]

print(f"{'rr':>6}  {'WR%':>6}  {'avgR':>7}  {'avg_win':>8}  {'<0.5R':>6}  {'0.5-1R':>7}  {'wins<1R':>8}  {'netR':>7}")
print("-" * 75)

for rr in values:
    result = subprocess.run(
        [sys.executable, str(script), "--days", "365", "--json", "--system", "A", "--min-tp1-rr", str(rr)],
        capture_output=True, text=True
    )
    d = json.loads(result.stdout)
    trades = [t for t in d["trades"] if not t.get("isOpen")]
    wins   = [t for t in trades if t["resultR"] > 0]
    small  = [t for t in wins if t["resultR"] < 1.0]
    tiny   = [t for t in small if t["resultR"] < 0.5]
    mid    = [t for t in small if t["resultR"] >= 0.5]
    net    = sum(t["resultR"] for t in trades)
    avg_w  = sum(t["resultR"] for t in wins) / len(wins) if wins else 0
    print(f"{rr:>6.1f}  {d['wr_pct']:>6.1f}  {d['avg_r']:>7.3f}  {avg_w:>8.2f}  {len(tiny):>6}  {len(mid):>7}  {len(small):>8}  {net:>7.0f}R")
