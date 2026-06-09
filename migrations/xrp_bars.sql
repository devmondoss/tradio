-- Tabla de barras M1 para XRPUSDT (misma estructura que btc_bars/sol_bars/bnb_bars/eth_bars)
CREATE TABLE IF NOT EXISTS xrp_bars (LIKE btc_bars INCLUDING ALL);

-- Mover filas XRP que cayeron en btc_bars por el fallback wildcard
INSERT INTO xrp_bars
SELECT * FROM btc_bars WHERE symbol = 'XRPUSDT'
ON CONFLICT DO NOTHING;

DELETE FROM btc_bars WHERE symbol = 'XRPUSDT';
