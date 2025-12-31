mod balanced_arb;
mod noop;
mod paper_baseline;
mod rule_v1;
mod types;

use anyhow::{anyhow, Result};

pub(crate) use balanced_arb::BalancedArbStrategy;
pub(crate) use noop::NoopStrategy;
pub(crate) use paper_baseline::PaperBaselineStrategy;
pub(crate) use rule_v1::RuleV1Strategy;
pub(crate) use types::{Desired, Strategy, StrategyCtx};

pub(crate) fn strategy_from_env() -> Result<Box<dyn Strategy + Send>> {
    let name = std::env::var("PM_STRATEGY")
        .unwrap_or_else(|_| "noop".to_string())
        .to_lowercase();
    match name.as_str() {
        "noop" | "none" => Ok(Box::new(NoopStrategy::default())),
        "balanced_arb" | "balanced" => Ok(Box::new(BalancedArbStrategy::default())),
        "paper_baseline" | "baseline" => Ok(Box::new(PaperBaselineStrategy::default())),
        "rule_v1" | "v1" => Ok(Box::new(RuleV1Strategy::default())),
        other => Err(anyhow!(
            "unknown PM_STRATEGY={other}; supported: noop, balanced_arb, paper_baseline, rule_v1"
        )),
    }
}
