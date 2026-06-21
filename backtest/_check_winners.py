import json, sys, subprocess, pathlib

root = pathlib.Path(__file__).parent
script = root / "liquidity_app_backtest.py"

for sys_name in ["A", "C"]:
    result = subprocess.run(
        [sys.executable, str(script), "--days", "365", "--json", "--system", sys_name],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR {sys_name}:", result.stderr[-300:]); continue
    d = json.loads(result.stdout)
    trades = [t for t in d["trades"] if not t.get("isOpen")]
    wins   = [t for t in trades if t["resultR"] > 0]
    losses = [t for t in trades if t["resultR"] <= 0]
    small  = [t for t in wins if t["resultR"] < 1.0]
    by_reason = {}
    for t in trades:
        by_reason.setdefault(t["reason"], []).append(t["resultR"])

    net = sum(t["resultR"] for t in trades)
    print(f"Sistema {sys_name}: n={len(trades)}  WR={d['wr_pct']}%  avgR={d['avg_r']}  netR={net:.0f}R  $={d['equity']:.0f}")
    print(f"  avg_win={sum(t['resultR'] for t in wins)/len(wins):.2f}R  avg_loss={sum(t['resultR'] for t in losses)/len(losses):.2f}R  wins<1R={len(small)}")
    for reason, rs in sorted(by_reason.items()):
        print(f"  {reason:10s}  n={len(rs):3d}  avg={sum(rs)/len(rs):+.3f}R")
    print()
