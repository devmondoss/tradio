"""
footprint.py — Acumulador de ticks publicTrade → volume profile real por barra.

Cierra el gap entre backtest (VP desde ticks) y live (VP aproximado desde OHLCV):
  - on_trade()           : llamar en cada tick del WS publicTrade
  - on_bar_close()       : llamar al confirmar la vela M15 → devuelve footprint y resetea
  - bars()               : historial de barras completadas → pasa a compute_levels(fp_bars=...)
  - save_bar_to_supa()   : persistir la barra cerrada en Supabase (sobrevive reinicios)
  - restore_from_supa()  : al arrancar, restaurar las últimas N barras desde Supabase

Bybit publicTrade fields usados:
  p  → price (str)
  v  → volume (str)
  S  → side "Buy" | "Sell"
  T  → timestamp ms

Almacenamiento Supabase: ~8-12 KB/barra × 200 barras ≈ 2-3 MB (sparse, sin total).
Auto-limpieza: mantiene solo las últimas self.history filas por symbol/tf.
"""
import threading
import numpy as np
import requests
import json
from collections import defaultdict

BIN          = 5.0   # mismo bin que levels.py → $5 por nivel
SUPA_TABLE   = "liquidity_paper_footprint"
CLEANUP_EVERY = 50   # limpiar Supabase cada N barras nuevas


class FootprintAccumulator:
    """
    Acumula ticks de publicTrade tick a tick, separando buy/sell vol por bin de precio.
    Thread-safe: el WS corre en un hilo, on_bar_close desde el hilo principal.
    """

    def __init__(self, bin_size: float = BIN, history: int = 200):
        self.bin_size   = bin_size
        self.history    = history
        self.lock       = threading.Lock()
        self._cur_buy   : dict[float, float] = defaultdict(float)
        self._cur_sell  : dict[float, float] = defaultdict(float)
        self._bars      : list[dict] = []
        self._n_saved   = 0   # contador para limpieza periódica

    # ------------------------------------------------------------------
    def _bin(self, price: float) -> float:
        return round(price / self.bin_size) * self.bin_size

    # ------------------------------------------------------------------
    def on_trade(self, price: float, volume: float, side: str) -> None:
        """Registrar un tick del feed publicTrade. side: 'Buy' | 'Sell'."""
        b = self._bin(price)
        with self.lock:
            if side == "Buy":
                self._cur_buy[b]  += volume
            else:
                self._cur_sell[b] += volume

    # ------------------------------------------------------------------
    def on_bar_close(self, ts_ms: int) -> dict | None:
        """
        Llamar cuando kline.confirm=True.
        Devuelve el footprint de la barra cerrada y resetea el acumulador.
        Retorna None si no se acumularon ticks.
        """
        with self.lock:
            all_bins = set(self._cur_buy) | set(self._cur_sell)
            if not all_bins:
                return None

            prices = np.array(sorted(all_bins), dtype=np.float64)
            buy    = np.array([self._cur_buy.get(p, 0.0)  for p in prices])
            sell   = np.array([self._cur_sell.get(p, 0.0) for p in prices])
            total  = buy + sell

            poc_idx = int(total.argmax())
            bar = dict(
                ts_ms  = ts_ms,
                prices = prices,
                buy    = buy,
                sell   = sell,
                total  = total,
                poc    = float(prices[poc_idx]),
                delta  = float((buy - sell).sum()),
                vol    = float(total.sum()),
            )
            self._bars.append(bar)
            if len(self._bars) > self.history:
                self._bars = self._bars[-self.history:]

            self._cur_buy  = defaultdict(float)
            self._cur_sell = defaultdict(float)
            return bar

    # ------------------------------------------------------------------
    def current_poc(self) -> float | None:
        """POC de la barra en curso (sin cerrar)."""
        with self.lock:
            all_bins = set(self._cur_buy) | set(self._cur_sell)
            if not all_bins:
                return None
            combined = {b: self._cur_buy.get(b, 0.0) + self._cur_sell.get(b, 0.0)
                        for b in all_bins}
            return float(max(combined, key=combined.get))

    def bars(self) -> list[dict]:
        """Copia del historial de barras completadas (más antiguo primero)."""
        with self.lock:
            return list(self._bars)

    def last_bar(self) -> dict | None:
        with self.lock:
            return self._bars[-1] if self._bars else None

    def n_bars(self) -> int:
        with self.lock:
            return len(self._bars)

    # ------------------------------------------------------------------
    # Persistencia Supabase
    # ------------------------------------------------------------------
    @staticmethod
    def _to_row(bar: dict, symbol: str, tf: str) -> dict:
        """Convierte barra a fila Supabase. No guarda 'total' (= buy+sell)."""
        return dict(
            ts_ms  = int(bar["ts_ms"]),
            symbol = symbol,
            tf     = tf,
            poc    = round(float(bar["poc"]), 2),
            delta  = round(float(bar["delta"]), 4),
            vol    = round(float(bar["vol"]), 4),
            # Redondear a 4 decimales para reducir tamaño JSON
            prices = [round(float(p), 2) for p in bar["prices"]],
            buy    = [round(float(v), 4) for v in bar["buy"]],
            sell   = [round(float(v), 4) for v in bar["sell"]],
        )

    @staticmethod
    def _from_row(row: dict) -> dict:
        """Reconstruye barra desde fila Supabase."""
        prices = np.array(row["prices"], dtype=np.float64)
        buy    = np.array(row["buy"],    dtype=np.float64)
        sell   = np.array(row["sell"],   dtype=np.float64)
        total  = buy + sell
        return dict(
            ts_ms  = int(row["ts_ms"]),
            prices = prices,
            buy    = buy,
            sell   = sell,
            total  = total,
            poc    = float(row["poc"]),
            delta  = float(row["delta"]),
            vol    = float(row["vol"]),
        )

    def save_bar_to_supa(self, bar: dict, symbol: str, tf: str,
                         supa_url: str, supa_key: str) -> None:
        """
        Guarda la barra en Supabase (upsert por ts_ms).
        Cada CLEANUP_EVERY barras, borra las filas más antiguas para mantenerse
        dentro de las últimas self.history barras (límite de almacenamiento).
        """
        if not (supa_url and supa_key):
            return
        headers = {
            "apikey": supa_key,
            "Authorization": f"Bearer {supa_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }
        row = self._to_row(bar, symbol, tf)
        try:
            requests.post(f"{supa_url}/rest/v1/{SUPA_TABLE}",
                          headers=headers, data=json.dumps(row), timeout=10)
            self._n_saved += 1
        except Exception as e:
            print(f"[FP] supa save error: {e}")
            return

        # Limpieza periódica: borra barras viejas para mantener ≤ history filas
        if self._n_saved % CLEANUP_EVERY == 0:
            try:
                # Obtener el ts_ms de la barra en la posición history+1 (la más vieja a eliminar)
                r = requests.get(
                    f"{supa_url}/rest/v1/{SUPA_TABLE}",
                    headers=headers,
                    params={"symbol": f"eq.{symbol}", "tf": f"eq.{tf}",
                            "select": "ts_ms", "order": "ts_ms.desc",
                            "limit": 1, "offset": self.history},
                    timeout=10,
                )
                rows = r.json()
                if rows:
                    cutoff = rows[0]["ts_ms"]
                    del_headers = {**headers, "Prefer": "return=minimal"}
                    requests.delete(
                        f"{supa_url}/rest/v1/{SUPA_TABLE}",
                        headers=del_headers,
                        params={"symbol": f"eq.{symbol}", "tf": f"eq.{tf}",
                                "ts_ms": f"lte.{cutoff}"},
                        timeout=10,
                    )
                    print(f"[FP] limpieza Supabase: eliminadas barras <= {cutoff}")
            except Exception as e:
                print(f"[FP] supa cleanup error: {e}")

    def restore_from_supa(self, symbol: str, tf: str,
                          supa_url: str, supa_key: str) -> int:
        """
        Al arrancar: carga las últimas self.history barras desde Supabase.
        Retorna el número de barras restauradas.
        """
        if not (supa_url and supa_key):
            return 0
        headers = {
            "apikey": supa_key,
            "Authorization": f"Bearer {supa_key}",
        }
        try:
            r = requests.get(
                f"{supa_url}/rest/v1/{SUPA_TABLE}",
                headers=headers,
                params={"symbol": f"eq.{symbol}", "tf": f"eq.{tf}",
                        "select": "*", "order": "ts_ms.desc",
                        "limit": self.history},
                timeout=15,
            )
            rows = r.json()
            if not isinstance(rows, list):
                print(f"[FP] restore error: {rows}")
                return 0
            # Llegan en orden descendente → invertir para tener más antiguo primero
            rows = list(reversed(rows))
            with self.lock:
                self._bars = [self._from_row(row) for row in rows]
            print(f"[FP] restauradas {len(self._bars)} barras desde Supabase"
                  f" (más antigua: {rows[0]['ts_ms'] if rows else '-'})")
            return len(self._bars)
        except Exception as e:
            print(f"[FP] restore error: {e}")
            return 0
