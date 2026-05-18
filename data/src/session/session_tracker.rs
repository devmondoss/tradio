use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TradingSession {
    Asia,            // 00:00–08:00 UTC — acumulación, rangos, baja volatilidad
    London,          // 07:00–13:00 UTC — breakouts, liquidity grabs
    LondonNyOverlap, // 13:00–16:00 UTC — máxima liquidez
    NewYork,         // 13:00–17:00 UTC — continuación o reversión de London
    OffHours,        // 17:00–24:00 UTC — entre sesiones
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SessionPhase {
    Open,  // primeros 30 min de la sesión
    Mid,   // cuerpo central de la sesión
    Close, // últimos 30 min de la sesión
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
const NY_CLOSE: i64 = 1020;   // 17:00
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

    let phase = if minutes_since_open < 30 {
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
        // 08:00 UTC = 480 min → London open
        let ts_ms = 8 * 3600 * 1000_i64;
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
