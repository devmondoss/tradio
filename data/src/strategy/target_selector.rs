use super::trade_state::StructuralLevels;
use super::types::{Side, StrategyMarketContext, StrategySignal};

pub struct TargetSelector;

impl TargetSelector {
    /// Builds StructuralLevels from a signal + market context.
    ///
    /// Primary target: taken directly from `signal.target_price`.
    /// Intermediate level: nearest HVN between entry and target (for break-even trigger).
    ///
    /// Returns None if ATR is unavailable, target is missing, or direction is invalid.
    pub fn from_signal(
        signal: &StrategySignal,
        ctx: Option<&StrategyMarketContext>,
        side: Side,
    ) -> Option<StructuralLevels> {
        let ctx = ctx?;
        let atr = ctx.atr.filter(|&a| a > 0.0)?;
        let entry = signal.entry_price?;
        let target = signal.target_price?;

        let valid_dir = match side {
            Side::Long => target > entry,
            Side::Short => target < entry,
        };
        if !valid_dir {
            return None;
        }

        let atr_distance = (target - entry).abs() / atr;

        // Find intermediate: nearest HVN strictly between entry and target.
        let intermediate = {
            let mut candidates: Vec<f64> = ctx
                .volume_profile
                .hvn_nearby
                .iter()
                .copied()
                .filter(|&hvn| {
                    let in_range = match side {
                        Side::Long => hvn > entry && hvn < target,
                        Side::Short => hvn < entry && hvn > target,
                    };
                    in_range && (hvn - entry).abs() > atr * 0.25
                })
                .collect();
            candidates.sort_by(|a, b| {
                let da = (a - entry).abs();
                let db = (b - entry).abs();
                da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
            });
            candidates.into_iter().next()
        };

        Some(StructuralLevels {
            target,
            intermediate,
            atr_distance,
        })
    }
}
