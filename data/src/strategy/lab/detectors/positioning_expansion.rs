use crate::strategy::types::StrategyMarketContext;

/// Returns a positioning adjustment score [-0.15, +0.15] based on OI/funding/LS patterns.
/// Positive = bullish positioning context, negative = bearish.
/// Used as a scoring modifier, not as a standalone signal.
pub fn positioning_adjustment(ctx: &StrategyMarketContext) -> f64 {
    let mut adj: f64 = 0.0;

    let inst = match ctx.institutional.as_ref() {
        Some(i) => i,
        None => return 0.0,
    };

    // OI momentum aligned with price direction → reinforces the move
    if let Some(true) = ctx.flow.oi_momentum_aligned {
        adj += 0.05;
    } else if let Some(false) = ctx.flow.oi_momentum_aligned {
        adj -= 0.05;
    }

    // Funding extremes: extreme long = bearish pressure, extreme short = bullish
    use crate::institutional::FundingRegime;
    match inst.funding.regime {
        FundingRegime::ExtremeLong => adj -= 0.10,
        FundingRegime::ElevatedLong => adj -= 0.05,
        FundingRegime::ExtremeShort => adj += 0.10,
        FundingRegime::ElevatedShort => adj += 0.05,
        FundingRegime::Neutral => {}
    }

    // Smart money divergence: top traders vs retail
    use crate::institutional::DivergenceSignal;
    match inst.ls_ratio.divergence_signal {
        DivergenceSignal::SmartLongRetailShort => adj += 0.05,
        DivergenceSignal::SmartShortRetailLong => adj -= 0.05,
        _ => {}
    }

    adj.clamp(-0.15, 0.15)
}
