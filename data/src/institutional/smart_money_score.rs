use crate::institutional::types::{FundingContext, FundingRegime, LiquidationSnapshot, LsRatioContext, OiTrend, OiTrendDir};

/// Calcula el score institucional unificado en rango −1.0 a +1.0.
///
/// +1.0 → institucionales fuertemente largos, retail fuertemente corto.
/// −1.0 → institucionales fuertemente cortos, retail fuertemente largo.
///  0.0 → sin divergencia clara o señales contradictorias.
///
/// Este score se computa una vez y se reutiliza en FER, SMD, LiqHunt y VWAP,
/// eliminando la lógica duplicada que existía en cada detector.
pub fn compute(
    ls_ratio: &LsRatioContext,
    oi_trend: &OiTrend,
    funding: &FundingContext,
    liquidations: &LiquidationSnapshot,
) -> f32 {
    // Si hay cascada activa las señales institucionales son poco fiables
    if liquidations.cascade_detected {
        return 0.0;
    }

    // Componente 1 (50%): divergencia top traders vs retail
    // Positivo cuando top traders son más largos que retail → señal alcista institucional
    let divergence = (ls_ratio.top_traders_long_pct - ls_ratio.retail_long_pct) as f32;
    // Normalizar: divergencia máxima real ~30% → clampear a ±1.0
    let ls_score = (divergence / 0.30).clamp(-1.0, 1.0);

    // Componente 2 (20%): momentum de OI
    // OI acumulándose en tendencia = institucionales entrando = sesgo en esa dirección
    let oi_score: f32 = match oi_trend.trend {
        OiTrendDir::AccumulatingFast => 0.5,
        OiTrendDir::Accumulating => 0.25,
        OiTrendDir::Flat => 0.0,
        OiTrendDir::Decreasing => -0.25,
        OiTrendDir::DecreasingFast => -0.5,
    };

    // Componente 3 (30%): régimen de funding
    // Funding extremo positivo → mercado sobrecargado de largos → institucionales se ponen cortos
    // Funding extremo negativo → mercado sobrecargado de cortos → institucionales se ponen largos
    let funding_score: f32 = match funding.regime {
        FundingRegime::ExtremeShort => 0.5,
        FundingRegime::ElevatedShort => 0.25,
        FundingRegime::Neutral => 0.0,
        FundingRegime::ElevatedLong => -0.25,
        FundingRegime::ExtremeLong => -0.5,
    };

    let score = ls_score * 0.50 + oi_score * 0.20 + funding_score * 0.30;
    score.clamp(-1.0, 1.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::institutional::types::*;

    fn default_liqs() -> LiquidationSnapshot {
        LiquidationSnapshot::default()
    }

    #[test]
    fn returns_zero_on_cascade() {
        let liqs = LiquidationSnapshot {
            cascade_detected: true,
            ..Default::default()
        };
        let score = compute(
            &LsRatioContext::default(),
            &OiTrend::default(),
            &FundingContext::default(),
            &liqs,
        );
        assert_eq!(score, 0.0);
    }

    #[test]
    fn strong_institutional_long() {
        // Top traders muy largos, retail corto, funding deprimido, OI acumulando
        let ls = LsRatioContext {
            top_traders_long_pct: 0.70,
            retail_long_pct: 0.35,
            divergence_signal: crate::institutional::types::DivergenceSignal::SmartLongRetailShort,
        };
        let oi = OiTrend { trend: OiTrendDir::AccumulatingFast, ..OiTrend::default() };
        let funding = FundingContext { regime: FundingRegime::ExtremeShort, ..FundingContext::default() };
        let score = compute(&ls, &oi, &funding, &default_liqs());
        assert!(score > 0.5, "debería ser fuertemente positivo, got {score}");
    }

    #[test]
    fn strong_institutional_short() {
        let ls = LsRatioContext {
            top_traders_long_pct: 0.30,
            retail_long_pct: 0.65,
            divergence_signal: crate::institutional::types::DivergenceSignal::SmartShortRetailLong,
        };
        let oi = OiTrend { trend: OiTrendDir::Decreasing, ..OiTrend::default() };
        let funding = FundingContext { regime: FundingRegime::ExtremeLong, ..FundingContext::default() };
        let score = compute(&ls, &oi, &funding, &default_liqs());
        assert!(score < -0.3, "debería ser negativo, got {score}");
    }
}
