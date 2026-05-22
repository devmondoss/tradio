use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TradingSession {
    Asia,            // 00:00–08:00 UTC — acumulación, rangos, baja volatilidad
    London,          // 07:00–13:00 UTC — breakouts, liquidity grabs
    LondonNyOverlap, // 13:00–16:00 UTC — máxima liquidez
    NewYork,         // 13:00–21:00 UTC — continuación o reversión de London (US market close)
    OffHours,        // 21:00–24:00 UTC — entre sesiones
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

// Límites en minutos UTC desde medianoche
const ASIA_OPEN: i64 = 0;     // 00:00
const ASIA_CLOSE: i64 = 480;  // 08:00
const LONDON_OPEN: i64 = 420; // 07:00
const LONDON_CLOSE: i64 = 780; // 13:00
const NY_OPEN: i64 = 780;     // 13:00
const NY_CLOSE: i64 = 1260;   // 21:00 — US market close, covers Kaiko's active volume window
const OVERLAP_START: i64 = 780; // 13:00
const OVERLAP_END: i64 = 960;   // 16:00

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
            (TradingSession::OffHours, NY_CLOSE, 1440)
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
        // 08:00 UTC = 480 min → 60 min into London (open at 420) → Mid
        let ts_ms = 8 * 3600 * 1000_i64;
        let ctx = classify_session(ts_ms);
        assert_eq!(ctx.session, TradingSession::London);
        assert_eq!(ctx.phase, SessionPhase::Mid);
        assert_eq!(ctx.minutes_since_open, 60);
    }

    #[test]
    fn classifies_london_opening_rush() {
        // 07:05 UTC = 425 min → 5 min into London → OpeningRush
        let ts_ms = (7 * 3600 + 5 * 60) * 1000_i64;
        let ctx = classify_session(ts_ms);
        assert_eq!(ctx.session, TradingSession::London);
        assert_eq!(ctx.phase, SessionPhase::OpeningRush);
        assert_eq!(ctx.minutes_since_open, 5);
    }

    #[test]
    fn classifies_london_open_phase() {
        // 07:20 UTC = 440 min → 20 min into London → Open (>=15, <30)
        let ts_ms = (7 * 3600 + 20 * 60) * 1000_i64;
        let ctx = classify_session(ts_ms);
        assert_eq!(ctx.session, TradingSession::London);
        assert_eq!(ctx.phase, SessionPhase::Open);
    }

    #[test]
    fn classifies_overlap() {
        // 14:00 UTC = 840 min → LondonNyOverlap mid
        let ts_ms = 14 * 3600 * 1000_i64;
        let ctx = classify_session(ts_ms);
        assert_eq!(ctx.session, TradingSession::LondonNyOverlap);
    }

    #[test]
    fn classifies_asia() {
        // 03:00 UTC = 180 min → Asia mid
        let ts_ms = 3 * 3600 * 1000_i64;
        let ctx = classify_session(ts_ms);
        assert_eq!(ctx.session, TradingSession::Asia);
    }
}
