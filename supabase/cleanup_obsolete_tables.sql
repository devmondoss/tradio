-- ============================================================
-- LIMPIEZA DE TABLAS OBSOLETAS — FlowSurface
-- Ejecutar en Supabase SQL Editor
-- Fecha: 2026-06-02
--
-- Tablas eliminadas: todas vacías (0 filas), seguro borrar.
-- Sistema actual: scalping S1/S2/S3 + RangeBreakoutFlow
-- ============================================================

-- Vistas que dependen de tablas obsoletas (borrar primero)
DROP VIEW IF EXISTS v_signals_with_outcomes;
DROP VIEW IF EXISTS v_micro_with_outcomes;

-- Tablas DRR (sistema anterior — vacías desde reset 2026-05-29)
-- signal_outcomes tiene FK hacia shadow_signals, se borra primero
DROP TABLE IF EXISTS signal_outcomes;
DROP TABLE IF EXISTS shadow_signals CASCADE;

-- Tablas que nunca recibieron datos
-- CASCADE elimina solo los FK constraints dependientes, no las tablas que los tienen
DROP TABLE IF EXISTS lab_outcomes CASCADE;
DROP TABLE IF EXISTS intrabar_outcomes CASCADE;
DROP TABLE IF EXISTS calibration_log CASCADE;
DROP TABLE IF EXISTS institutional_snapshots CASCADE;

-- ============================================================
-- TABLAS QUE SE CONSERVAN
-- ============================================================
-- scalping_bars      (1,547 filas) — datos M1 activos, escribe cada barra
-- scalping_signals   (16 filas)    — señales S1/S2/S3
-- scalping_trades    (12 filas)    — trades cerrados con PnL
-- regime_history     (416 filas)   — historial de cambios de régimen
-- micro_windows      (1,960 filas) — micro-dinámica M1 (se sigue escribiendo)
-- lab_signals        (28 filas)    — Subdimi Parallel, referencia histórica
-- deployed_params                  — hot-reload de config desde Railway
-- v_scalping_summary               — vista activa
-- v_scalping_by_entry_type         — vista activa

-- ============================================================
-- VERIFICACIÓN POST-LIMPIEZA
-- ============================================================
SELECT table_name, table_type
FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_type, table_name;
