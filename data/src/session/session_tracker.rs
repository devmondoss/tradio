use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TradingSession {
    Asia,            // 00:00–09:00 UTC — Tokyo/Asia, acumulación, rangos
    London,          // 08:00–17:00 UTC — Frankfurt open + London, breakouts
    LondonNyOverlap, // 13:00–17:00 UTC — máxima liquidez (London + NYSE activos)
    NewYork,         // 13:00–22:00 UTC — NYSE open 13:30, cierre 20:00
    OffHours,        // 22:00–24:00 UTC — entre sesiones
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SessionPhase {
    /// Primeros 15 min de London o NY open — mayor edge para LiquidationHunt.
    OpeningRush,
    /// Minutos 16–30 desde apertura.
    Open,
    /// Cuerpo central de la sesión.
    Mid,
    /// Últimos 30 min de la sesión.
    Close,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionContext {
    pub session: TradingSession,
    pub phase: SessionPhase,
    /// Minutos transcurridos desde la apertura de la sesión actual.
    pub minutes_since_open: u32,
    /// Minutos restantes hasta el cierre de la sesión actual.
    pub minutes_until_close: u32,
}

// Límites en minutos UTC desde medianoche — horarios reales de mercado
const ASIA_OPEN: i64 = 0;       // 00:00
const ASIA_CLOSE: i64 = 540;    // 09:00 — Tokyo close
const LONDON_OPEN: i64 = 480;   // 08:00 — Frankfurt/London open
const LONDON_CLOSE: i64 = 1020; // 17:00 — London close
const NY_OPEN: i64 = 780;       // 13:00 — US traders active (NYSE opens 13:30)
const NY_CLOSE: i64 = 1320;     // 22:00 — post-NYSE close activity ends
const OVERLAP_START: i64 = 780; // 13:00 — London + NY ambos activos
const OVERLAP_END: i64 = 1020;  // 17:00 — London close = overlap termina

/// Clasifica la sesión activa dado un timestamp UTC en milisegundos.
pub fn classify_session(timestamp_ms: i64) -> SessionContext {
    let secs_in_day = (timestamp_ms / 1000) % (24 * 3600);
    let day_minutes = secs_in_day / 60;

    let (session, open_min, close_min) =
        if day_minutes >= OVERLAP_START && day_minutes < OVERLAP_END {
            (TradingSession::LondonNyOverlap, OVERLAP_START, OVERLAP_END)
        } else if day_minutes >= NY_OPEN && day_minutes < NY_CLOSE {
            (TradingSession::NewYork, NY_OPEN, NY_CLOSE)
        } else if day_minutes >= LONDON_OPEN && day_minutes < LONDON_CLOSE {
            (TradingSession::London, LONDON_OPEN, LONDON_CLOSE)
        } else if day_minutes >= ASIA_OPEN && day_minutes < ASIA_CLOSE {
            (TradingSession::Asia, ASIA_OPEN, ASIA_CLOSE)
        } else {
            (TradingSession::OffHours, NY_CLOSE, 1440) // 22:00–24:00
        };

    let minutes_since_open = (day_minutes - open_min).max(0) as u32;
    let minutes_until_close = (close_min - day_minutes).max(0) as u32;

    // OpeningRush only applies to high-liquidity sessions (London, NY, Overlap).
    let is_high_liq_open = matches!(
        session,
        TradingSession::London | TradingSession::NewYork | TradingSession::LondonNyOverlap
    );
    let phase = if is_high_liq_open && minutes_since_open < 15 {
        SessionPhase::OpeningRush
    } else if minutes_since_open < 30 {
        SessionPhase::Open
    } else if minutes_until_close < 30 {
        SessionPhase::Close
    } else {
        SessionPhase::Mid
    };

    SessionContext {
        session,
        phase,
        minutes_since_open,
        minutes_until_close,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classifies_london_session() {
        // 09:00 UTC = 540 min → 60 min into London (open at 480) → Mid
        let ts_ms = 9 * 3600 * 1000_i64;
        let ctx = classify_session(ts_ms);
        assert_eq!(ctx.session, TradingSession::London);
        assert_eq!(ctx.phase, SessionPhase::Mid);
        assert_eq!(ctx.minutes_since_open, 60);
    }

    #[test]
    fn classifies_london_opening_rush() {
        // 08:05 UTC = 485 min → 5 min into London → OpeningRush
        let ts_ms = (8 * 3600 + 5 * 60) * 1000_i64;
        let ctx = classify_session(ts_ms);
        assert_eq!(ctx.session, TradingSession::London);
        assert_eq!(ctx.phase, SessionPhase::OpeningRush);
        assert_eq!(ctx.minutes_since_open, 5);
    }

    #[test]
    fn classifies_london_open_phase() {
        // 08:20 UTC = 500 min → 20 min into London → Open (>=15, <30)
        let ts_ms = (8 * 3600 + 20 * 60) * 1000_i64;
        let ctx = classify_session(ts_ms);
        assert_eq!(ctx.session, TradingSession::London);
        assert_eq!(ctx.phase, SessionPhase::Open);
    }

    #[test]
    fn classifies_overlap() {
        // 14:00 UTC = 840 min → LondonNyOverlap (13:00–17:00)
        let ts_ms = 14 * 3600 * 1000_i64;
        let ctx = classify_session(ts_ms);
        assert_eq!(ctx.session, TradingSession::LondonNyOverlap);
    }

    #[test]
    fn classifies_newyork_pure() {
        // 18:00 UTC = 1080 min → NewYork (after London close at 17:00)
        let ts_ms = 18 * 3600 * 1000_i64;
        let ctx = classify_session(ts_ms);
        assert_eq!(ctx.session, TradingSession::NewYork);
    }

    #[test]
    fn classifies_asia() {
        // 03:00 UTC = 180 min → Asia mid
        let ts_ms = 3 * 3600 * 1000_i64;
        let ctx = classify_session(ts_ms);
        assert_eq!(ctx.session, TradingSession::Asia);
    }

    #[test]
    fn classifies_offhours() {
        // 23:00 UTC = 1380 min → OffHours (after 22:00)
        let ts_ms = 23 * 3600 * 1000_i64;
        let ctx = classify_session(ts_ms);
        assert_eq!(ctx.session, TradingSession::OffHours);
    }
}
