use super::types::*;

impl StrategyMarketContext {
    pub fn price_relation(price: f64, level: Option<f64>) -> PriceRelation {
        match level {
            None => PriceRelation::Unknown,
            Some(l) => {
                let tolerance = l * 0.0001;
                if price > l + tolerance {
                    PriceRelation::Above
                } else if price < l - tolerance {
                    PriceRelation::Below
                } else {
                    PriceRelation::At
                }
            }
        }
    }

    pub fn determine_value_location(
        price: f64,
        vah: Option<f64>,
        val: Option<f64>,
    ) -> ValueLocation {
        match (vah, val) {
            (Some(vah), Some(val)) => {
                if price > vah {
                    ValueLocation::AboveVah
                } else if price < val {
                    ValueLocation::BelowVal
                } else {
                    ValueLocation::InValue
                }
            }
            _ => ValueLocation::Unknown,
        }
    }
}
