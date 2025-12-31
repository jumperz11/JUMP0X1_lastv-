use super::types::{Desired, Strategy, StrategyCtx, StrategyQuote};

#[derive(Debug, Default)]
pub(crate) struct NoopStrategy;

impl Strategy for NoopStrategy {
    fn name(&self) -> &'static str {
        "noop"
    }

    fn quote(&mut self, _ctx: &StrategyCtx<'_>) -> StrategyQuote {
        StrategyQuote {
            up: Desired {
                px: None,
                q: 0.0,
                why: "noop",
            },
            down: Desired {
                px: None,
                q: 0.0,
                why: "noop",
            },
        }
    }
}

