//! Rule v1.3 Strategy (Updated 2025-12-22)
//!
//! Early OR Late only - no mid-session trades:
//! - T1 and T3 have real edge
//! - T2 bleeds money despite "okay" WR (market efficient, spread kills)
//!
//! NOTE: tau = seconds UNTIL settlement (counts DOWN from 899 to 0).
//! So T1 "869 → 809" means tau is in that range = early window (T+30s → T+90s).
//!
//! Tier structure:
//! - T1 (tau 869→809):  58% threshold → ENTER
//! - T2 (tau 809→719):  SKIP (no trades)
//! - T3 (tau 719→90):   52% threshold → ENTER
//!
//! Guard precedence (checked in this order):
//! 1. Cancel Zone: tau < 90 → NO TRADE
//! 2. Safety Cap:  best_ask >= 0.70 → SKIP session
//! 3. Tier checks: T1/T2/T3 logic
//!
//! Key insight: Middle = danger zone. Early OR very late only.

use super::types::{Desired, Strategy, StrategyCtx, StrategyQuote};

// Timing boundaries (tau = seconds until settlement, session = 899s)
const T1_START: f64 = 869.0;  // T+30s (899 - 30) - T1 begins
const T2_START: f64 = 809.0;  // T+90s (899 - 90) - T2 begins (skip zone)
const T3_START: f64 = 719.0;  // T+3m  (899 - 180) - T3 begins

// Safety cap - skip session if best_ask too high (outlier protection)
const SAFETY_ASK_CAP: f64 = 0.70;
const CANCEL_ZONE: f64 = 90.0;   // Last 90s - no trade

// Edge thresholds per tier
const T1_THRESHOLD: f64 = 0.58;  // T1: early window
// T2: SKIP - no threshold (bleeds money despite 56% WR)
const T3_THRESHOLD: f64 = 0.52;  // T3: late, market decided

// Price logging threshold (LOG ONLY - no blocking)
const PRICE_LOG_THRESHOLD: f64 = 0.58;

// Sizing multipliers
const BASE_SIZE_MULT: f64 = 1.0;
const STRONG_EDGE_MULT: f64 = 1.5;  // For edge >= 0.60
const VERY_STRONG_MULT: f64 = 2.0;  // For edge >= 0.65

#[derive(Debug, Default)]
pub(crate) struct RuleV1Strategy {
    _private: (),
}

impl RuleV1Strategy {
    fn calc_size_mult(edge: f64) -> f64 {
        if edge >= 0.65 {
            VERY_STRONG_MULT
        } else if edge >= 0.60 {
            STRONG_EDGE_MULT
        } else {
            BASE_SIZE_MULT
        }
    }

    /// Estimate best ask from mid and bid: ask = 2*mid - bid
    /// Falls back to mid + tick if bid unavailable
    fn estimate_ask(mid: Option<f64>, bid: Option<f64>, tick: f64) -> Option<f64> {
        match (mid, bid) {
            (Some(m), Some(b)) => Some((2.0 * m - b).max(m + tick)),
            (Some(m), None) => Some(m + tick),
            _ => None,
        }
    }

    /// Log entry price for analysis (no blocking)
    fn log_entry_price(ctx: &StrategyCtx<'_>, direction: &str, edge: f64, ask: f64, tier: &str) {
        let above_threshold = ask >= PRICE_LOG_THRESHOLD;
        tracing::info!(
            target: "price_log",
            direction = direction,
            edge = format!("{:.3}", edge),
            ask = format!("{:.3}", ask),
            tau = format!("{:.0}", ctx.tau_seconds),
            tier = tier,
            above_threshold = above_threshold,
            "ENTRY_PRICE"
        );
    }
}

impl Strategy for RuleV1Strategy {
    fn name(&self) -> &'static str {
        "rule_v1"
    }

    fn quote(&mut self, ctx: &StrategyCtx<'_>) -> StrategyQuote {
        let no_trade = || StrategyQuote {
            up: Desired { px: None, q: 0.0, why: "no_trade" },
            down: Desired { px: None, q: 0.0, why: "no_trade" },
        };

        let tau = ctx.tau_seconds;

        // ===== GUARD 1: Cancel zone (tau < 90) =====
        if tau < CANCEL_ZONE {
            return StrategyQuote {
                up: Desired { px: None, q: 0.0, why: "cancel_zone" },
                down: Desired { px: None, q: 0.0, why: "cancel_zone" },
            };
        }

        // ===== GUARD 2: Safety cap (best_ask >= 0.70) =====
        // Estimate asks for both directions; skip session if BOTH are too expensive
        let up_mid = ctx.up_mid.unwrap_or(0.5);
        let down_mid = ctx.down_mid.unwrap_or(0.5);
        let up_ask = Self::estimate_ask(ctx.up_mid, ctx.by, ctx.tick_size);
        let down_ask = Self::estimate_ask(ctx.down_mid, ctx.bn, ctx.tick_size);

        let up_blocked = up_ask.map(|a| a >= SAFETY_ASK_CAP).unwrap_or(false);
        let down_blocked = down_ask.map(|a| a >= SAFETY_ASK_CAP).unwrap_or(false);

        if up_blocked && down_blocked {
            return StrategyQuote {
                up: Desired { px: None, q: 0.0, why: "safety_cap" },
                down: Desired { px: None, q: 0.0, why: "safety_cap" },
            };
        }

        // ===== GUARD 3: Too early (before T1) =====
        if tau > T1_START {
            return StrategyQuote {
                up: Desired { px: None, q: 0.0, why: "too_early" },
                down: Desired { px: None, q: 0.0, why: "too_early" },
            };
        }

        // ===== TIER CHECKS =====
        let (edge_threshold, tier_label) = if tau > T2_START {
            // T1: tau 869→809 (T+30s to T+90s)
            (T1_THRESHOLD, "t1")
        } else if tau > T3_START {
            // T2: tau 809→719 (T+90s to T+3m) - SKIP
            return StrategyQuote {
                up: Desired { px: None, q: 0.0, why: "t2" },
                down: Desired { px: None, q: 0.0, why: "t2" },
            };
        } else {
            // T3: tau 719→90 (T+3m+)
            (T3_THRESHOLD, "t3")
        };

        // ===== DIRECTION SELECTION =====
        // Pick direction with edge, excluding any blocked by safety cap
        let up_valid = up_mid >= edge_threshold && !up_blocked;
        let down_valid = down_mid >= edge_threshold && !down_blocked;

        let (direction, edge, bid_px, mid_px, ask) = match (up_valid, down_valid) {
            (true, true) => {
                // Both valid - pick better edge
                if up_mid >= down_mid {
                    ("UP", up_mid, ctx.by, ctx.up_mid, up_ask)
                } else {
                    ("DOWN", down_mid, ctx.bn, ctx.down_mid, down_ask)
                }
            }
            (true, false) => ("UP", up_mid, ctx.by, ctx.up_mid, up_ask),
            (false, true) => ("DOWN", down_mid, ctx.bn, ctx.down_mid, down_ask),
            (false, false) => return no_trade(),
        };

        // Log entry price for analysis
        if let Some(ask_price) = ask {
            Self::log_entry_price(ctx, direction, edge, ask_price, tier_label);
        }

        // Calculate position size
        let size_mult = Self::calc_size_mult(edge);
        let order_size = ctx.min_order_size * size_mult;

        // Build the quote (T1 and T3 only, T2 skipped above)
        let why = match (tier_label, direction) {
            ("t1", "UP") => "t1_up",
            ("t1", "DOWN") => "t1_dn",
            ("t3", "UP") => "t3_up",
            ("t3", "DOWN") => "t3_dn",
            _ => "rule_v1",
        };

        if let Some(px) = bid_px {
            if direction == "UP" {
                return StrategyQuote {
                    up: Desired {
                        px: Some(px),
                        q: order_size,
                        why,
                    },
                    down: Desired { px: None, q: 0.0, why: "no_edge" },
                };
            } else {
                return StrategyQuote {
                    up: Desired { px: None, q: 0.0, why: "no_edge" },
                    down: Desired {
                        px: Some(px),
                        q: order_size,
                        why,
                    },
                };
            }
        }

        no_trade()
    }
}
