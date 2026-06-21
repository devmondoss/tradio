"""
footprint.py — Acumulador de ticks publicTrade → volume profile real por barra.

Cierra el gap entre backtest (VP desde ticks) y live (VP aproximado desde OHLCV):
  - on_trade()     : llamar en cada tick del WS publicTrade
  - on_bar_close() : llamar al confirmar la vela M15 → devuelve footprint y resetea
  - bars()         : historial de barras completadas → pasa a compute_levels(fp_bars=...)

Bybit publicTrade fields usados:
  p  → price (str)
  v  → volume (str)
  S  → side "Buy" | "Sell"
  T  → timestamp ms
"""
import threading
import numpy as np
from collections import defaultdict

BIN = 5.0   # mismo bin que levels.py → $5 por nivel


class FootprintAccumulator:
    """
    Acumula ticks de publicTrade tick a tick, separando buy/sell vol por bin de precio.
    Thread-safe: el WS corre en un hilo, on_bar_close desde el hilo principal.
    """

    def __init__(self, bin_size: float = BIN, history: int = 200):
        self.bin_size = bin_size
        self.history  = history
        self.lock     = threading.Lock()
        self._cur_buy  : dict[float, float] = defaultdict(float)
        self._cur_sell : dict[float, float] = defaultdict(float)
        self._bars     : list[dict] = []

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
        Retorna None si no se acumularon ticks (no debería pasar en vivo).
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
                delta  = float((buy - sell).sum()),   # +buy dominante / −sell dominante
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
        """POC de la barra en curso (sin cerrar). Útil para logging."""
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
