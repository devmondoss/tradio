"""
Inyecta un panel `StrategyMonitor` en el layout persistido y lo vincula al
KlineChart (`link_group = "A"`). Hace backup del state previo en
`saved-state.json.bak` antes de tocar nada.

Idempotente: si ya hay un StrategyMonitor en el layout, no añade otro; solo
asegura que el link_group del candlestick y del monitor coincidan.

Uso:
    python scripts/add_strategy_monitor_pane.py
"""

import json
import os
import shutil
from pathlib import Path

LINK_GROUP = "A"

state_path = Path(os.environ["APPDATA"]) / "flowsurface" / "saved-state.json"
backup = state_path.with_suffix(".json.bak")

if not state_path.exists():
    raise SystemExit(f"No existe {state_path}")

shutil.copy2(state_path, backup)
print(f"Backup: {backup}")

state = json.loads(state_path.read_text(encoding="utf-8"))


def first_kline(node):
    """Recorrido en orden; devuelve el primer KlineChart Candles que encuentre."""
    if "Split" in node:
        return first_kline(node["Split"]["a"]) or first_kline(node["Split"]["b"])
    if "KlineChart" in node and node["KlineChart"].get("kind") == "Candles":
        return node["KlineChart"]
    return None


def has_strategy_monitor(node):
    if "Split" in node:
        return has_strategy_monitor(node["Split"]["a"]) or has_strategy_monitor(node["Split"]["b"])
    return "StrategyMonitor" in node


def replace_ladder_with_split(node):
    """Sustituye la primera ocurrencia de Ladder por un Split vertical
    [Ladder, StrategyMonitor(link_group=A)]. Devuelve True si lo encontró."""
    if "Split" in node:
        for side in ("a", "b"):
            child = node["Split"][side]
            if "Ladder" in child:
                node["Split"][side] = {
                    "Split": {
                        "axis": "Horizontal",
                        "ratio": 0.55,
                        "a": child,
                        "b": {"StrategyMonitor": {"link_group": LINK_GROUP}},
                    }
                }
                return True
            if replace_ladder_with_split(child):
                return True
    return False


layouts = state.get("layout_manager", {}).get("layouts", [])
modified = 0

for L in layouts:
    pane = L["dashboard"]["pane"]

    if has_strategy_monitor(pane):
        print(f"[{L['name']}] ya tiene StrategyMonitor; saltando")
        # aún así garantizar que el candlestick esté linked
        k = first_kline(pane)
        if k is not None:
            k["link_group"] = LINK_GROUP
            print(f"  → KlineChart link_group set a '{LINK_GROUP}'")
            modified += 1
        continue

    k = first_kline(pane)
    if k is None:
        print(f"[{L['name']}] sin KlineChart Candles; saltando")
        continue

    k["link_group"] = LINK_GROUP

    if replace_ladder_with_split(pane):
        print(f"[{L['name']}] inyectado StrategyMonitor junto al Ladder (link_group='{LINK_GROUP}')")
        modified += 1
    else:
        # No hay Ladder — splittear el KlineChart Candles para añadir el monitor al lado
        print(f"[{L['name']}] no encontré Ladder, inyectaré junto al KlineChart")

        def split_kline(node):
            if "Split" in node:
                for side in ("a", "b"):
                    child = node["Split"][side]
                    if "KlineChart" in child and child["KlineChart"].get("kind") == "Candles":
                        node["Split"][side] = {
                            "Split": {
                                "axis": "Vertical",
                                "ratio": 0.75,
                                "a": child,
                                "b": {"StrategyMonitor": {"link_group": LINK_GROUP}},
                            }
                        }
                        return True
                    if split_kline(child):
                        return True
            return False

        if split_kline(pane):
            print(f"  → split KlineChart + StrategyMonitor")
            modified += 1
        else:
            print(f"  → no pude inyectar (estructura inusual); revisa manualmente")

state_path.write_text(json.dumps(state), encoding="utf-8")
print(f"\nLayouts modificados: {modified}")
print(f"Guardado: {state_path}")
print("Reinicia la UI para ver el panel.")
