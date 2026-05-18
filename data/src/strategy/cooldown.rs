use std::collections::HashMap;

use super::types::StrategyId;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignalSide {
    Long,
    Short,
}

#[derive(Debug, Clone)]
pub struct CooldownEntry {
    pub last_signal_bar: u64,
    pub last_signal_side: SignalSide,
    pub last_signal_price: f64,
}

/// Estado de cooldown por estrategia — útil para logging y GUI.
#[derive(Debug)]
pub struct CooldownStatus {
    pub strategy_id: StrategyId,
    pub available: bool,
    pub bars_remaining: u64,
    pub last_price: f64,
    pub last_side: SignalSide,
}

/// Registro central de cooldowns, uno por estrategia.
/// Cooldown = N velas desde la última señal emitida, independientemente del resultado.
/// Solo se activa para la estrategia ganadora del ciclo — las demás no se penalizan.
pub struct CooldownRegistry {
    entries: HashMap<u8, CooldownEntry>,
    pub cooldown_bars: u64,
}

fn strategy_key(id: StrategyId) -> u8 {
    match id {
        StrategyId::ValueAreaFailedAuction => 0,
        StrategyId::VwapValuePullbackContinuation => 1,
        StrategyId::LvnLiquidityVacuumBreakout => 2,
        StrategyId::LiquidationHunt => 3,
        StrategyId::FundingExhaustionReversal => 4,
        StrategyId::SmartMoneyDivergence => 5,
    }
}

impl CooldownRegistry {
    pub fn new(cooldown_bars: u64) -> Self {
        Self { entries: HashMap::new(), cooldown_bars }
    }

    /// Registra que una estrategia emitió en `bar_index`.
    pub fn register_signal(
        &mut self,
        id: StrategyId,
        bar_index: u64,
        side: SignalSide,
        price: f64,
    ) {
        self.entries.insert(
            strategy_key(id),
            CooldownEntry { last_signal_bar: bar_index, last_signal_side: side, last_signal_price: price },
        );
    }

    /// True si la estrategia está disponible (no en cooldown).
    pub fn is_available(&self, id: StrategyId, current_bar: u64) -> bool {
        match self.entries.get(&strategy_key(id)) {
            None => true,
            Some(e) => current_bar >= e.last_signal_bar + self.cooldown_bars,
        }
    }

    /// Velas restantes de cooldown (0 = disponible).
    pub fn bars_remaining(&self, id: StrategyId, current_bar: u64) -> u64 {
        match self.entries.get(&strategy_key(id)) {
            None => 0,
            Some(e) => {
                let elapsed = current_bar.saturating_sub(e.last_signal_bar);
                self.cooldown_bars.saturating_sub(elapsed)
            }
        }
    }

    /// Estado de todos los cooldowns activos.
    pub fn status(&self, current_bar: u64) -> Vec<CooldownStatus> {
        use StrategyId::*;
        [
            ValueAreaFailedAuction,
            VwapValuePullbackContinuation,
            LvnLiquidityVacuumBreakout,
            LiquidationHunt,
            FundingExhaustionReversal,
            SmartMoneyDivergence,
        ]
        .iter()
        .filter_map(|&id| {
            self.entries.get(&strategy_key(id)).map(|e| CooldownStatus {
                strategy_id: id,
                available: self.is_available(id, current_bar),
                bars_remaining: self.bars_remaining(id, current_bar),
                last_price: e.last_signal_price,
                last_side: e.last_signal_side,
            })
        })
        .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use StrategyId::*;

    #[test]
    fn blocks_for_cooldown_bars() {
        let mut r = CooldownRegistry::new(5);
        r.register_signal(ValueAreaFailedAuction, 100, SignalSide::Long, 76950.0);

        assert!(!r.is_available(ValueAreaFailedAuction, 101));
        assert!(!r.is_available(ValueAreaFailedAuction, 104));
        assert!(r.is_available(ValueAreaFailedAuction, 105));
    }

    #[test]
    fn does_not_affect_other_strategies() {
        let mut r = CooldownRegistry::new(5);
        r.register_signal(ValueAreaFailedAuction, 100, SignalSide::Long, 76950.0);

        assert!(r.is_available(LvnLiquidityVacuumBreakout, 101));
        assert!(r.is_available(SmartMoneyDivergence, 101));
    }

    #[test]
    fn resets_after_new_signal() {
        let mut r = CooldownRegistry::new(5);
        r.register_signal(ValueAreaFailedAuction, 100, SignalSide::Long, 76950.0);
        assert!(r.is_available(ValueAreaFailedAuction, 105));

        r.register_signal(ValueAreaFailedAuction, 105, SignalSide::Short, 77100.0);
        assert!(!r.is_available(ValueAreaFailedAuction, 106));
        assert_eq!(r.bars_remaining(ValueAreaFailedAuction, 106), 4);
    }

    #[test]
    fn bars_remaining_counts_correctly() {
        let mut r = CooldownRegistry::new(5);
        r.register_signal(LiquidationHunt, 100, SignalSide::Short, 80000.0);

        assert_eq!(r.bars_remaining(LiquidationHunt, 100), 5);
        assert_eq!(r.bars_remaining(LiquidationHunt, 102), 3);
        assert_eq!(r.bars_remaining(LiquidationHunt, 105), 0);
    }
}
