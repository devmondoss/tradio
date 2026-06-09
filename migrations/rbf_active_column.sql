-- Persistencia del paper trader RBF entre reinicios de Railway.
-- Una señal con is_active=TRUE indica que hay una posición abierta rastreando esa señal.
-- El monitor la consulta al arrancar y restaura el estado del paper trader.

ALTER TABLE rbf_signals ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT FALSE;

-- Índice para que la query de startup sea rápida
CREATE INDEX IF NOT EXISTS idx_rbf_signals_is_active
    ON rbf_signals (symbol, is_active)
    WHERE is_active = TRUE;
