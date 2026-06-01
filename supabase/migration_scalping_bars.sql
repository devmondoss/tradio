-- ============================================================
-- scalping_bars — una fila por barra M5, siempre, sin filtrar
-- Equivalente a polyrec: registro del universo completo de barras
-- para calibración offline de thresholds S1/S2/S3
-- ============================================================

create table if not exists scalping_bars (
  id               bigserial primary key,
  created_at       timestamptz default now(),
  ts_ms            bigint      not null,
  symbol           text        not null default 'BTCUSDT',

  -- Contexto de mercado
  session          text,           -- Asia | London | LondonNyOverlap | NewYork | OffHours
  regime           text,           -- Expansion | Chop | TrendUp | etc.
  close            float,
  atr              float,
  funding_pct      float,          -- funding rate %

  -- OBI (normalizado [0,1])
  obi_fast         float,          -- EMA L10 rápida (~0.7s half-life)
  obi_slow         float,          -- EMA L10 lenta (~4s half-life)
  obi_l5           float,          -- L5 sin EMA (presión inmediata)
  l5_l10_div       float,          -- divergencia L5-L10 (>0.06 = pared o spoof)

  -- CVD
  cvd_session      float,          -- CVD acumulado desde apertura de sesión
  cvd_slope        float,          -- slope OLS 10 barras (USD/barra)

  -- Señales de activación S2
  dz               float,          -- delta z-score vs 50 barras
  vr               float,          -- volume ratio vs 50 barras
  spread_ticks     float,

  -- Score de absorción S2 (0-100, lado más fuerte)
  absorption_long  float,
  absorption_short float,

  -- Contexto institucional
  liq_ratio        float,          -- liquidaciones z-score

  -- Resultado
  signal_fired     text,           -- null | 'S1_OBI_LONG' | 'S2_ABS_SHORT' | 'S3_DIV_LONG'
  signal_score     float,          -- conviction score de la señal (null si no disparó)
  blocked_by       text            -- razón principal si no disparó (off_session | has_position | circuit_breaker | dz_low | vr_low | obi_weak | conviction)
);

create index if not exists idx_scalping_bars_ts    on scalping_bars (ts_ms desc);
create index if not exists idx_scalping_bars_sess  on scalping_bars (session, signal_fired);
create index if not exists idx_scalping_bars_sym   on scalping_bars (symbol, ts_ms desc);
