//! Paper Baseline Strategy v1.0
//!
//! Simple baseline strategy for paper trading validation:
//! - EDGE_PRESENT: Mid price shows directional bias (>= 55%)
//! - dislocation >= 60s: Only trade after T+60s (tau <= 839s)
//!
//! Entry rules:
//! - Wait until 60s into session (tau_seconds <= 839)
//! - If up_mid >= 0.55, bet UP at best bid
//! - If down_mid >= 0.55, bet DOWN at best bid
//! - Order size = min_order_size (conservative)
//!
//! This captures the core insight from session_outcome_analyzer:
//! - T+60s signals have 65-75% accuracy (positive EV)
//! - Trading before T+60s has lower accuracy (negative EV)
//!

use super::types::{Desired, Strategy, StrategyCtx, StrategyQuote};

const EDGE_THRESHOLD: f64 = 0.55;  // 55% = edge present
const MIN_TAU_SECONDS: f64 = 840.0; // Session duration 899s, so tau <= 840 means we're past T+59s
const MIN_TAU_MARGIN: f64 = 120.0;  // Don't trade in last 2 minutes (tau < 120s)

#[derive(Debug, Default)]
pub(crate) struct PaperBaselineStrategy {
    _private: (),
}

impl Strategy for PaperBaselineStrategy {
    fn name(&self) -> &'static str {
        "paper_baseline"
    }

    fn quote(&mut self, ctx: &StrategyCtx<'_>) -> StrategyQuote {
        let no_trade = || StrategyQuote {
            up: Desired { px: None, q: 0.0, why: "no_trade" },
            down: Desired { px: None, q: 0.0, why: "no_trade" },
        };

        // Rule 1: Only trade after T+60s (tau_seconds <= 839)
        // tau_seconds is time UNTIL settlement, so:
        // - Session starts at tau = 899s
        // - T+60s means tau = 839s
        if ctx.tau_seconds > MIN_TAU_SECONDS {
            return StrategyQuote {
                up: Desired { px: None, q: 0.0, why: "wait_t60" },
                down: Desired { px: None, q: 0.0, why: "wait_t60" },
            };
        }

        // Rule 2: Don't trade in last 2 minutes (execution risk)
        if ctx.tau_seconds < MIN_TAU_MARGIN {
            return StrategyQuote {
                up: Desired { px: None, q: 0.0, why: "too_late" },
                down: Desired { px: None, q: 0.0, why: "too_late" },
            };
        }

        // Rule 3: Check for EDGE_PRESENT via mid price
        let up_mid = ctx.up_mid.unwrap_or(0.5);
        let down_mid = ctx.down_mid.unwrap_or(0.5);

        // Sanity: up_mid + down_mid should be ~1.0 (minus spread)
        // If up_mid >= EDGE_THRESHOLD, bet UP
        // If down_mid >= EDGE_THRESHOLD, bet DOWN
        // If both or neither, no trade (unclear signal)

        let up_edge = up_mid >= EDGE_THRESHOLD;
        let down_edge = down_mid >= EDGE_THRESHOLD;

        if up_edge && !down_edge {
            // Bet UP at best bid
            if let Some(by) = ctx.by {
                return StrategyQuote {
                    up: Desired {
                        px: Some(by),
                        q: ctx.min_order_size,
                        why: "edge_up",
                    },
                    down: Desired { px: None, q: 0.0, why: "no_edge" },
                };
            }
        }

        if down_edge && !up_edge {
            // Bet DOWN at best bid
            if let Some(bn) = ctx.bn {
                return StrategyQuote {
                    up: Desired { px: None, q: 0.0, why: "no_edge" },
                    down: Desired {
                        px: Some(bn),
                        q: ctx.min_order_size,
                        why: "edge_down",
                    },
                };
            }
        }

        // No clear edge or no bid available
        no_trade()
    }
}
