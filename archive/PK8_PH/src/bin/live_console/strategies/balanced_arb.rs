//! Balanced Early Entry Arbitrage Strategy
//!
//! Core principles from TEST1 analysis:
//! - Entry window: First 2 minutes only (edge decay)
//! - Exit window: Stop 30 seconds before settlement (no 0.99 sniping)
//! - MAX_ONE_SIDED = 6: Never hold >6 shares without balance
//! - Prioritize lagging side to maintain balance
//! - Entry prices around 0.46-0.48 work best for fills
//!
//! The insight: 84% win rate lost money because of imbalanced positions.
//! Balanced positions (4+4, 5+5) win. Unbalanced (10+0) lose big.

use super::types::{Desired, Strategy, StrategyCtx, StrategyQuote};
use tracing::{debug, info, warn};

/// Configuration loaded from environment
struct Config {
    /// Entry window - only trade in first N seconds (default: 120 = 2 min)
    tau0_seconds: f64,
    /// Exit window - stop trading last N seconds (default: 30)
    stop_new_seconds: f64,
    /// Max combined ask price for entry (default: 0.98)
    spread_threshold: f64,
    /// Max shares on one side without balance (default: 6)
    max_one_sided: f64,
    /// Base quantity per side (default: 5)
    base_qty: f64,
    /// Price buffer below mid for limit orders (default: 0.02)
    price_buffer: f64,
    /// Min price we'll bid (default: 0.40)
    min_price: f64,
    /// Max price we'll bid (default: 0.55)
    max_price: f64,
}

impl Config {
    fn from_env() -> Self {
        let get_f = |key: &str, default: f64| -> f64 {
            std::env::var(key)
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(default)
        };

        Self {
            tau0_seconds: get_f("PM_TAU0_SECONDS", 120.0),
            stop_new_seconds: get_f("PM_STOP_NEW_SECONDS", 30.0),
            spread_threshold: get_f("PM_SPREAD_THRESHOLD", 0.98),
            max_one_sided: get_f("PM_MAX_ONE_SIDED", 6.0),
            base_qty: get_f("PM_BASE_QTY", 5.0),
            price_buffer: get_f("PM_PRICE_BUFFER", 0.02),
            min_price: get_f("PM_MIN_PRICE", 0.40),
            max_price: get_f("PM_MAX_PRICE", 0.55),
        }
    }
}

pub(crate) struct BalancedArbStrategy {
    cfg: Config,
}

impl Default for BalancedArbStrategy {
    fn default() -> Self {
        Self::new()
    }
}

impl BalancedArbStrategy {
    pub(crate) fn new() -> Self {
        Self {
            cfg: Config::from_env(),
        }
    }

    /// Create a no-trade response with reason
    fn no_trade(why: &'static str) -> StrategyQuote {
        StrategyQuote {
            up: Desired { px: None, q: 0.0, why },
            down: Desired { px: None, q: 0.0, why },
        }
    }

    /// Estimate ask price from mid (since we only have bid and mid in context)
    /// ask = 2 * mid - bid, or mid + tick if bid unavailable
    fn estimate_ask(mid: Option<f64>, bid: Option<f64>, tick: f64) -> Option<f64> {
        match (mid, bid) {
            (Some(m), Some(b)) => Some((2.0 * m - b).max(m + tick)),
            (Some(m), None) => Some(m + tick),
            _ => None,
        }
    }

    /// Calculate order price: bid below mid, clamped to our range
    fn calc_order_price(&self, mid: Option<f64>, tick: f64) -> Option<f64> {
        let mid = mid?;
        let price = (mid - self.cfg.price_buffer).max(self.cfg.min_price);
        let price = price.min(self.cfg.max_price);
        // Round to tick
        let price = (price / tick).floor() * tick;
        Some(price)
    }
}

impl Strategy for BalancedArbStrategy {
    fn name(&self) -> &'static str {
        "balanced_arb"
    }

    fn quote(&mut self, ctx: &StrategyCtx<'_>) -> StrategyQuote {
        let minutes_left = ctx.tau_seconds / 60.0;

        // ===================
        // 1. TIME WINDOW CHECKS
        // ===================

        // Exit window: Last 30 seconds - no new trades
        // "Don't snipe 0.99 - one flip wipes 99 wins"
        if ctx.tau_seconds < self.cfg.stop_new_seconds {
            debug!("[{}] no_trade: exit_window ({:.1}s left)", ctx.symbol, ctx.tau_seconds);
            return Self::no_trade("exit_window");
        }

        // Entry window: Only first 2 minutes (900 - 120 = 780 seconds remaining)
        // "if you have 54% edge at open, you dont have it in 1m in"
        let entry_cutoff = 900.0 - self.cfg.tau0_seconds; // 780 for 2min window
        if ctx.tau_seconds < entry_cutoff {
            debug!("[{}] no_trade: entry_closed ({:.1}m left, need >{:.1}m)",
                ctx.symbol, minutes_left, entry_cutoff / 60.0);
            return Self::no_trade("entry_closed");
        }

        // ===================
        // 2. QUOTE AVAILABILITY
        // ===================

        // Need mid prices to determine entry levels
        let (up_mid, down_mid) = match (ctx.up_mid, ctx.down_mid) {
            (Some(u), Some(d)) => (u, d),
            (Some(_), None) => {
                warn!("[{}] no_trade: no_down_quote (up_mid={:.3})", ctx.symbol, ctx.up_mid.unwrap());
                return Self::no_trade("no_down_quote");
            },
            (None, Some(_)) => {
                warn!("[{}] no_trade: no_up_quote (down_mid={:.3})", ctx.symbol, ctx.down_mid.unwrap());
                return Self::no_trade("no_up_quote");
            },
            (None, None) => {
                warn!("[{}] no_trade: no_quotes", ctx.symbol);
                return Self::no_trade("no_quotes");
            },
        };

        // ===================
        // 3. SPREAD CHECK
        // ===================

        // Estimate ask prices from mid
        let up_ask = Self::estimate_ask(Some(up_mid), ctx.by, ctx.tick_size)
            .unwrap_or(up_mid + ctx.tick_size);
        let down_ask = Self::estimate_ask(Some(down_mid), ctx.bn, ctx.tick_size)
            .unwrap_or(down_mid + ctx.tick_size);

        // Combined cost must leave room for profit
        let spread = up_ask + down_ask;
        if spread >= self.cfg.spread_threshold {
            debug!("[{}] no_trade: spread_too_wide ({:.3} >= {:.2}) up_ask={:.3} down_ask={:.3}",
                ctx.symbol, spread, self.cfg.spread_threshold, up_ask, down_ask);
            return Self::no_trade("spread_too_wide");
        }

        info!("[{}] ENTRY WINDOW OPEN: {:.1}m left, spread={:.3}, up_ask={:.3}, down_ask={:.3}",
            ctx.symbol, minutes_left, spread, up_ask, down_ask);

        // ===================
        // 4. POSITION BALANCE CHECK
        // ===================

        let up_shares = ctx.qy;
        let down_shares = ctx.qn;
        let imbalance = (up_shares - down_shares).abs();

        // ===================
        // 5. DETERMINE QUANTITIES
        // ===================

        // MAX_ONE_SIDED = 6: Never hold more than 6 on one side without the other
        // This is THE critical fix from TEST1

        let (up_qty, down_qty, up_why, down_why) = if imbalance >= self.cfg.max_one_sided {
            // DANGER ZONE: Too imbalanced, only buy lagging side
            if up_shares > down_shares {
                // Too many UP, only buy DOWN
                (0.0, self.cfg.base_qty, "balanced_stop", "catch_up")
            } else {
                // Too many DOWN, only buy UP
                (self.cfg.base_qty, 0.0, "catch_up", "balanced_stop")
            }
        } else if imbalance > 0.0 {
            // Slightly imbalanced: buy more of lagging side
            let extra = imbalance.min(self.cfg.base_qty);
            if up_shares > down_shares {
                // UP ahead, prioritize DOWN
                (self.cfg.base_qty, self.cfg.base_qty + extra, "entry", "priority")
            } else {
                // DOWN ahead, prioritize UP
                (self.cfg.base_qty + extra, self.cfg.base_qty, "priority", "entry")
            }
        } else {
            // Perfectly balanced: buy equal amounts
            (self.cfg.base_qty, self.cfg.base_qty, "entry", "entry")
        };

        // ===================
        // 6. CALCULATE PRICES
        // ===================

        // Price strategy from TEST1: 0.46-0.48 worked well for fills
        // Bid below mid to be a maker, but not too aggressive

        let up_price = if up_qty > 0.0 {
            self.calc_order_price(Some(up_mid), ctx.tick_size)
        } else {
            None
        };

        let down_price = if down_qty > 0.0 {
            self.calc_order_price(Some(down_mid), ctx.tick_size)
        } else {
            None
        };

        // ===================
        // 7. VALIDATE MINIMUM SIZE
        // ===================

        let up_qty = if up_qty >= ctx.min_order_size { up_qty } else { 0.0 };
        let down_qty = if down_qty >= ctx.min_order_size { down_qty } else { 0.0 };

        StrategyQuote {
            up: Desired {
                px: if up_qty > 0.0 { up_price } else { None },
                q: up_qty,
                why: up_why,
            },
            down: Desired {
                px: if down_qty > 0.0 { down_price } else { None },
                q: down_qty,
                why: down_why,
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Timeframe;

    fn make_ctx<'a>(
        tau_seconds: f64,
        up_mid: Option<f64>,
        down_mid: Option<f64>,
        qy: f64,
        qn: f64,
    ) -> StrategyCtx<'a> {
        StrategyCtx {
            symbol: "BTC",
            timeframe: Timeframe::M15,
            tau_seconds,
            market: "test-market",
            tok_up: "tok-up",
            tok_dn: "tok-dn",
            by: up_mid.map(|m| m - 0.01), // bid = mid - 1 tick
            bn: down_mid.map(|m| m - 0.01),
            up_mid,
            down_mid,
            min_order_size: 5.0,
            tick_size: 0.01,
            qy,
            qn,
            current_exposure: 0.0,
        }
    }

    #[test]
    fn test_exit_window_no_trade() {
        let mut strat = BalancedArbStrategy::new();
        let ctx = make_ctx(25.0, Some(0.50), Some(0.50), 0.0, 0.0);
        let q = strat.quote(&ctx);
        assert_eq!(q.up.why, "exit_window");
        assert_eq!(q.up.q, 0.0);
    }

    #[test]
    fn test_entry_closed_no_trade() {
        let mut strat = BalancedArbStrategy::new();
        // 700 seconds left = 3.3 minutes elapsed, past 2 min window
        let ctx = make_ctx(700.0, Some(0.50), Some(0.50), 0.0, 0.0);
        let q = strat.quote(&ctx);
        assert_eq!(q.up.why, "entry_closed");
    }

    #[test]
    fn test_balanced_entry() {
        let mut strat = BalancedArbStrategy::new();
        // 850 seconds = 50 seconds elapsed, within entry window
        let ctx = make_ctx(850.0, Some(0.50), Some(0.48), 0.0, 0.0);
        let q = strat.quote(&ctx);
        assert!(q.up.q > 0.0);
        assert!(q.down.q > 0.0);
        assert_eq!(q.up.why, "entry");
        assert_eq!(q.down.why, "entry");
    }

    #[test]
    fn test_max_one_sided_stops_leading() {
        let mut strat = BalancedArbStrategy::new();
        // Already have 7 UP, 0 DOWN - past MAX_ONE_SIDED
        let ctx = make_ctx(850.0, Some(0.50), Some(0.48), 7.0, 0.0);
        let q = strat.quote(&ctx);
        assert_eq!(q.up.q, 0.0, "should stop buying UP");
        assert!(q.down.q > 0.0, "should buy DOWN to catch up");
        assert_eq!(q.up.why, "balanced_stop");
        assert_eq!(q.down.why, "catch_up");
    }

    #[test]
    fn test_prioritizes_lagging_side() {
        let mut strat = BalancedArbStrategy::new();
        // Have 3 UP, 1 DOWN - imbalanced but not at limit
        let ctx = make_ctx(850.0, Some(0.50), Some(0.48), 3.0, 1.0);
        let q = strat.quote(&ctx);
        assert!(q.down.q > q.up.q, "should prioritize DOWN");
        assert_eq!(q.down.why, "priority");
    }
}
