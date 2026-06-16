use exchange::adapter::{Exchange, MarketKind, Venue};

/// Configures which exchange/market the monitor connects to.
/// Read from the `MONITOR_EXCHANGE` env var (default: `binance_linear`).
///
/// | Value            | Exchange          | Market  |
/// |------------------|-------------------|---------|
/// | `binance_linear` | Binance USDT-M    | Futures |
/// | `bybit_spot`     | Bybit             | Spot    |
/// | `bybit_linear`   | Bybit USDT-M      | Futures |
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExchangeTarget {
    BinanceLinear,
    BybitSpot,
    BybitLinear,
}

impl ExchangeTarget {
    pub fn from_env() -> Self {
        match std::env::var("MONITOR_EXCHANGE")
            .unwrap_or_default()
            .to_lowercase()
            .replace('-', "_")
            .as_str()
        {
            "bybit_spot" | "bybitspot" => Self::BybitSpot,
            "bybit_linear" | "bybitlinear" => Self::BybitLinear,
            _ => Self::BinanceLinear,
        }
    }

    pub fn exchange(self) -> Exchange {
        match self {
            Self::BinanceLinear => Exchange::BinanceLinear,
            Self::BybitSpot => Exchange::BybitSpot,
            Self::BybitLinear => Exchange::BybitLinear,
        }
    }

    pub fn venue(self) -> Venue {
        match self {
            Self::BinanceLinear => Venue::Binance,
            Self::BybitSpot | Self::BybitLinear => Venue::Bybit,
        }
    }

    pub fn market_kind(self) -> MarketKind {
        match self {
            Self::BinanceLinear | Self::BybitLinear => MarketKind::LinearPerps,
            Self::BybitSpot => MarketKind::Spot,
        }
    }

    /// Returns true for futures-style markets that have OI, funding rate,
    /// long/short ratios, liquidation streams, etc.
    pub fn is_futures(self) -> bool {
        matches!(self, Self::BinanceLinear | Self::BybitLinear)
    }

    // ── Klines ────────────────────────────────────────────────────────────────

    /// Builds a REST URL for historical klines.
    /// `interval`: Binance format ("1m", "5m", "1h", "4h", "1d") — converted automatically.
    pub fn klines_url(self, symbol: &str, interval: &str, limit: usize) -> String {
        match self {
            Self::BinanceLinear => format!(
                "https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
            ),
            Self::BybitSpot => {
                let iv = to_bybit_interval(interval);
                format!("https://api.bybit.com/v5/market/kline?category=spot&symbol={symbol}&interval={iv}&limit={limit}")
            }
            Self::BybitLinear => {
                let iv = to_bybit_interval(interval);
                format!("https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={iv}&limit={limit}")
            }
        }
    }

    /// Extracts kline rows from a REST JSON response, normalizing exchange differences.
    ///
    /// - Binance: top-level array, oldest-first
    /// - Bybit: wrapped in `result.list`, newest-first → reversed here
    ///
    /// Row index mapping (identical for both after normalization):
    /// - `[0]` open time ms  `[1]` open  `[2]` high  `[3]` low  `[4]` close  `[5]` volume
    pub fn extract_kline_rows(self, json: &serde_json::Value) -> Vec<Vec<serde_json::Value>> {
        let raw = match self {
            Self::BinanceLinear => json.as_array(),
            Self::BybitSpot | Self::BybitLinear => json
                .get("result")
                .and_then(|r| r.get("list"))
                .and_then(|l| l.as_array()),
        };

        let Some(rows) = raw else { return vec![] };

        let mut result: Vec<Vec<serde_json::Value>> = rows
            .iter()
            .filter_map(|row| row.as_array().cloned())
            .collect();

        if matches!(self, Self::BybitSpot | Self::BybitLinear) {
            result.reverse();
        }

        result
    }

    /// Minimum expected number of fields per kline row.
    pub fn kline_min_fields(self) -> usize {
        match self {
            Self::BinanceLinear => 10, // needs index 9 (taker_buy)
            Self::BybitSpot | Self::BybitLinear => 6,
        }
    }

    /// Returns the open timestamp (ms) from a kline row.
    /// Handles both Binance (integer) and Bybit (string) formats.
    pub fn open_ms_from_row(row: &[serde_json::Value]) -> i64 {
        row.get(0)
            .and_then(|v| {
                v.as_i64()
                    .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
            })
            .unwrap_or(0)
    }

    /// Returns the taker_buy_vol from a kline row.
    /// Bybit SPOT klines don't expose taker_buy, so we assume 50/50 split.
    pub fn taker_buy_from_row(self, row: &[serde_json::Value]) -> f64 {
        match self {
            Self::BinanceLinear => row
                .get(9)
                .and_then(|v| v.as_str())
                .and_then(|s| s.parse().ok())
                .unwrap_or(0.0),
            Self::BybitSpot | Self::BybitLinear => {
                let volume: f64 = row
                    .get(5)
                    .and_then(|v| v.as_str())
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(0.0);
                volume / 2.0
            }
        }
    }

    // ── Spot price ───────────────────────────────────────────────────────────

    pub fn spot_price_url(self, symbol: &str) -> String {
        match self {
            Self::BinanceLinear => {
                format!("https://api.binance.com/api/v3/ticker/price?symbol={symbol}")
            }
            Self::BybitSpot => {
                format!("https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}")
            }
            Self::BybitLinear => {
                format!("https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}")
            }
        }
    }

    pub fn parse_spot_price(self, json: &serde_json::Value) -> Option<f64> {
        match self {
            Self::BinanceLinear => json.get("price")?.as_str()?.parse().ok(),
            Self::BybitSpot | Self::BybitLinear => json
                .get("result")?
                .get("list")?
                .as_array()?
                .first()?
                .get("lastPrice")?
                .as_str()?
                .parse()
                .ok(),
        }
    }

    // ── Futures-only endpoints ───────────────────────────────────────────────
    // All return None/empty for spot targets.

    pub fn premium_index_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
            )),
            _ => None,
        }
    }

    pub fn open_interest_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}"
            )),
            _ => None,
        }
    }

    pub fn top_trader_ls_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={symbol}&period=5m&limit=1"
            )),
            _ => None,
        }
    }

    pub fn global_ls_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=5m&limit=1"
            )),
            _ => None,
        }
    }

    pub fn taker_ratio_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1"
            )),
            _ => None,
        }
    }

    pub fn funding_history_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=21"
            )),
            _ => None,
        }
    }

    pub fn oi_history_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=30"
            )),
            _ => None,
        }
    }

    /// Returns the Binance FAPI ls-history endpoint names; empty for non-Binance-futures.
    /// Each tuple: (full URL, endpoint_name_for_source_mapping)
    pub fn ls_history_endpoint_urls(self, symbol: &str) -> Vec<(String, &'static str)> {
        match self {
            Self::BinanceLinear => vec![
                (
                    format!("https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={symbol}&period=5m&limit=6"),
                    "topLongShortPositionRatio",
                ),
                (
                    format!("https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=5m&limit=6"),
                    "globalLongShortAccountRatio",
                ),
            ],
            _ => vec![],
        }
    }
}

fn to_bybit_interval(binance_iv: &str) -> &'static str {
    match binance_iv {
        "1m" => "1",
        "3m" => "3",
        "5m" => "5",
        "15m" => "15",
        "30m" => "30",
        "1h" => "60",
        "4h" => "240",
        "1d" => "D",
        "1w" => "W",
        _ => "1",
    }
}
