use super::*;
use super::{emit_order_event, OrderTelemetry};

use rand::{rngs::StdRng, Rng, SeedableRng};
use std::collections::HashSet;

/// Risk caps for paper trading - enforced per-strategy
#[derive(Debug, Clone)]
pub(crate) struct RiskCaps {
    pub(crate) max_worst_total_usd: f64,
    pub(crate) max_worst_per_market_usd: f64,
    pub(crate) max_trades_per_session: u32,
    pub(crate) max_position_shares: f64,
}

impl RiskCaps {
    pub(crate) fn from_env() -> Self {
        let get_f = |k: &str, d: f64| {
            std::env::var(k)
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(d)
        };
        let get_u = |k: &str, d: u32| {
            std::env::var(k)
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(d)
        };
        Self {
            max_worst_total_usd: get_f("MAX_WORST_TOTAL_USD", 200.0),
            max_worst_per_market_usd: get_f("MAX_WORST_PER_MARKET_USD", 75.0),
            max_trades_per_session: get_u("MAX_TRADES_PER_SESSION", 1),
            max_position_shares: get_f("MAX_POSITION_SHARES", 50.0),
        }
    }
}

/// Risk state tracked per-strategy
#[derive(Debug, Clone, Default)]
pub(crate) struct RiskState {
    pub(crate) worst_total_usd: f64,
    pub(crate) worst_per_market: HashMap<String, f64>,
    pub(crate) trades_per_session: HashMap<String, u32>,
    pub(crate) position_per_market: HashMap<String, f64>,
    pub(crate) skipped_sessions: HashSet<String>,
}

impl RiskState {
    pub(crate) fn record_trade(&mut self, market: &str, notional: f64, shares: f64) {
        self.worst_total_usd += notional;
        *self.worst_per_market.entry(market.to_string()).or_insert(0.0) += notional;
        *self.trades_per_session.entry(market.to_string()).or_insert(0) += 1;
        *self.position_per_market.entry(market.to_string()).or_insert(0.0) += shares;
    }

    pub(crate) fn check_caps(&self, caps: &RiskCaps, market: &str, notional: f64, shares: f64) -> Option<String> {
        // Check total worst-case
        if self.worst_total_usd + notional > caps.max_worst_total_usd {
            return Some(format!(
                "SKIP:worst_total {:.2}+{:.2}>{:.2}",
                self.worst_total_usd, notional, caps.max_worst_total_usd
            ));
        }
        // Check per-market worst-case
        let market_worst = self.worst_per_market.get(market).copied().unwrap_or(0.0);
        if market_worst + notional > caps.max_worst_per_market_usd {
            return Some(format!(
                "SKIP:worst_market {:.2}+{:.2}>{:.2}",
                market_worst, notional, caps.max_worst_per_market_usd
            ));
        }
        // Check trades per session
        let session_trades = self.trades_per_session.get(market).copied().unwrap_or(0);
        if session_trades >= caps.max_trades_per_session {
            return Some(format!(
                "SKIP:trades_per_session {}>={}",
                session_trades, caps.max_trades_per_session
            ));
        }
        // Check position shares
        let pos = self.position_per_market.get(market).copied().unwrap_or(0.0);
        if pos + shares > caps.max_position_shares {
            return Some(format!(
                "SKIP:position {:.1}+{:.1}>{:.1}",
                pos, shares, caps.max_position_shares
            ));
        }
        None
    }
}

#[derive(Debug, Clone)]
pub(crate) struct PaperFillConfig {
    pub(crate) starting_cash_usdc: f64,
    pub(crate) post_latency_ms: i64,
    pub(crate) cancel_req_latency_ms: i64,
    pub(crate) cancel_clear_latency_ms: i64,
    pub(crate) maker_flow_base_per_sec: f64,
    pub(crate) maker_flow_depth_frac_per_sec: f64,
    pub(crate) maker_flow_noise_frac: f64,
    pub(crate) maker_flow_use_book_deltas: bool,
    pub(crate) maker_flow_fallback_mult: f64,
    pub(crate) maker_flow_require_trade_print: bool,
    pub(crate) queue_add_ahead_frac: f64,
}

impl PaperFillConfig {
    pub(crate) fn from_env() -> Self {
        let get_f = |k: &str, d: f64| {
            std::env::var(k)
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(d)
        };
        let get_i = |k: &str, d: i64| {
            std::env::var(k)
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(d)
        };
        let get_b = |k: &str, d: bool| {
            std::env::var(k)
                .ok()
                .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
                .unwrap_or(d)
        };
        Self {
            starting_cash_usdc: get_f("PM_PAPER_STARTING_CASH", 1_000.0).max(0.0),
            post_latency_ms: get_i("PM_PAPER_POST_LATENCY_MS", 250).max(0),
            cancel_req_latency_ms: get_i("PM_PAPER_CANCEL_REQ_LATENCY_MS", 80).max(0),
            cancel_clear_latency_ms: get_i("PM_PAPER_CANCEL_CLEAR_LATENCY_MS", 350).max(0),
            maker_flow_base_per_sec: get_f("PM_PAPER_FLOW_BASE", 5.0).max(0.0),
            maker_flow_depth_frac_per_sec: get_f("PM_PAPER_FLOW_DEPTH_FRAC", 0.02).max(0.0),
            maker_flow_noise_frac: get_f("PM_PAPER_FLOW_NOISE_FRAC", 0.25).clamp(0.0, 2.0),
            maker_flow_use_book_deltas: get_b("PM_PAPER_FLOW_USE_BOOK_DELTAS", true),
            maker_flow_fallback_mult: get_f("PM_PAPER_FLOW_FALLBACK_MULT", 1.0).max(0.0),
            maker_flow_require_trade_print: get_b("PM_PAPER_FLOW_REQUIRE_TRADE_PRINT", false),
            queue_add_ahead_frac: get_f("PM_PAPER_QUEUE_ADD_AHEAD_FRAC", 0.0).clamp(0.0, 1.0),
        }
    }
}

#[derive(Debug, Clone)]
struct PaperOrder {
    order_key: String,
    symbol: String,
    timeframe: Timeframe,
    outcome: String,
    market: String,      // condition_id - the real 15-min session identifier
    token_id: String,
    price: f64,
    original_size: f64,
    size_matched: f64,
    queue_ahead: f64,
    activate_at: std::time::Instant,
    opened: bool,
    cancel_req_at: Option<std::time::Instant>,
    cancel_clear_at: Option<std::time::Instant>,
    cancel_req_sent: bool,

    // Telemetry fields for orders.jsonl
    submit_ts_ms: i64,
    strategy_id: String,

    // Book-derived fill simulation state (approximate).
    ws_init: bool,
    last_ws_best_bid_key: Option<i64>,
    last_ws_best_bid_sz: f64,
    last_ws_level_sz_at_px: f64,
    last_seen_trade_ts_ms: Option<i64>,
}

impl PaperOrder {
    fn remaining(&self) -> f64 {
        (self.original_size - self.size_matched).max(0.0)
    }
}

pub(crate) struct PaperBroker {
    cfg: PaperFillConfig,
    rng: StdRng,
    id_seq: u64,
    last_step: std::time::Instant,
    open: HashMap<(String, String), PaperOrder>, // (market, token_id)
    strategy_id: String,
    pub(crate) caps: RiskCaps,
    pub(crate) risk: RiskState,
}

impl PaperBroker {
    pub(crate) fn new_from_env() -> Self {
        Self::new_with_strategy("paper")
    }

    pub(crate) fn new_with_strategy(strategy_id: &str) -> Self {
        let seed: u64 = std::env::var("PM_PAPER_SEED")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(1);
        Self {
            cfg: PaperFillConfig::from_env(),
            rng: StdRng::seed_from_u64(seed),
            id_seq: 1,
            last_step: std::time::Instant::now(),
            open: HashMap::new(),
            strategy_id: strategy_id.to_string(),
            caps: RiskCaps::from_env(),
            risk: RiskState::default(),
        }
    }

    /// Check if order can be placed within caps; returns None if OK, Some(reason) if SKIP
    pub(crate) fn check_caps(&self, market: &str, price: f64, size: f64) -> Option<String> {
        let notional = price * size;
        self.risk.check_caps(&self.caps, market, notional, size)
    }

    /// Record that a trade was placed (call after place_limit_buy succeeds)
    pub(crate) fn record_trade(&mut self, market: &str, price: f64, size: f64) {
        let notional = price * size;
        self.risk.record_trade(market, notional, size);
    }

    /// Clear session-specific counters (call on session rollover)
    pub(crate) fn clear_session(&mut self, market: &str) {
        self.risk.trades_per_session.remove(market);
        self.risk.position_per_market.remove(market);
    }

    pub(crate) fn strategy_id(&self) -> &str {
        &self.strategy_id
    }

    pub(crate) fn starting_cash_usdc(&self) -> f64 {
        self.cfg.starting_cash_usdc
    }

    pub(crate) fn is_busy(&self, market: &str, token_id: &str) -> bool {
        self.open
            .contains_key(&(market.to_string(), token_id.to_string()))
    }

    pub(crate) fn reserved_usdc(&self) -> f64 {
        self.open
            .values()
            .map(|o| o.remaining().max(0.0) * o.price.max(0.0))
            .sum::<f64>()
    }

    pub(crate) async fn cancel_market_orders(
        &mut self,
        logs: &SharedLogs,
        _orders_view: &SharedOrders,
        latencies: &SharedLatency,
        market: &str,
        token_id: &str,
    ) {
        let Some(o) = self
            .open
            .get_mut(&(market.to_string(), token_id.to_string()))
        else {
            return;
        };
        if o.cancel_clear_at.is_some() {
            return;
        }

        let now_i = std::time::Instant::now();
        let req_at = now_i + Duration::from_millis(self.cfg.cancel_req_latency_ms as u64);
        let clear_at = req_at + Duration::from_millis(self.cfg.cancel_clear_latency_ms as u64);
        o.cancel_req_at = Some(req_at);
        o.cancel_clear_at = Some(clear_at);

        {
            let mut g = latencies.write().await;
            g.last_cancel_req_ms = Some(self.cfg.cancel_req_latency_ms);
            LatencyStats::bump(&mut g.max_cancel_req_ms, self.cfg.cancel_req_latency_ms);
            let tot_clear = self
                .cfg
                .cancel_req_latency_ms
                .saturating_add(self.cfg.cancel_clear_latency_ms);
            g.last_cancel_clear_ms = Some(tot_clear);
            LatencyStats::bump(&mut g.max_cancel_clear_ms, tot_clear);
        }
        push_log(
            logs,
            format!(
                "[{}] cancel_req {} {} order_id={} (req={}ms clear={}ms)",
                self.strategy_id,
                market,
                token_id,
                o.order_key,
                self.cfg.cancel_req_latency_ms,
                self.cfg.cancel_clear_latency_ms
            ),
        )
        .await;
        emit_jsonl_event(
            "paper_cancel_req",
            serde_json::json!({
                "market": market,
                "token_id": token_id,
                "order_id": o.order_key,
                "cancel_req_latency_ms": self.cfg.cancel_req_latency_ms,
                "cancel_clear_latency_ms": self.cfg.cancel_clear_latency_ms,
            }),
        );
    }

    #[allow(dead_code)]
    pub(crate) async fn force_remove_orders(
        &mut self,
        orders_view: &SharedOrders,
        market: &str,
        token_id: &str,
        reason: &str,
    ) {
        let Some(o) = self
            .open
            .remove(&(market.to_string(), token_id.to_string()))
        else {
            return;
        };

        let ts = chrono::Utc::now();
        let mut g = orders_view.write().await;
        if let Some(v) = g.get_mut(&o.order_key) {
            v.last_event = reason.to_string();
            v.last_update_at = ts;
        }
        emit_jsonl_event(
            "paper_force_remove",
            serde_json::json!({
                "market": market,
                "token_id": token_id,
                "order_id": o.order_key,
                "reason": reason,
            }),
        );
    }

    pub(crate) async fn place_limit_buy(
        &mut self,
        logs: &SharedLogs,
        orders_view: &SharedOrders,
        latencies: &SharedLatency,
        symbol: &str,
        timeframe: Timeframe,
        outcome: &str,
        market: &str,
        token_id: &str,
        price: f64,
        size: f64,
        queue_ahead: f64,
    ) -> String {
        let order_id = format!(
            "paper-{}-{}-{}",
            symbol.to_lowercase(),
            token_id,
            self.id_seq
        );
        self.id_seq = self.id_seq.wrapping_add(1);
        let order_key = canon_id(&order_id);

        let now_i = std::time::Instant::now();
        let activate_at = now_i + Duration::from_millis(self.cfg.post_latency_ms as u64);
        let submitted_at = chrono::Utc::now();
        {
            let mut g = orders_view.write().await;
            g.insert(
                order_key.clone(),
                OrderSnapshot {
                    order_id: order_key.clone(),
                    symbol: symbol.to_string(),
                    timeframe,
                    outcome: outcome.to_string(),
                    token_id: token_id.to_string(),
                    market: market.to_string(),
                    side: "BUY".to_string(),
                    price,
                    original_size: size,
                    size_matched: 0.0,
                    last_event: "SUBMIT".to_string(),
                    last_update_at: submitted_at,
                    submitted_at: Some(submitted_at),
                    placed_at: None,
                    // Telemetry fields (paper mode)
                    submit_ts_ms: Some(submitted_at.timestamp_millis()),
                    best_bid_at_submit: None,
                    best_ask_at_submit: Some(price),
                    strategy_id: Some(self.strategy_id.clone()),
                    reason_codes: vec![],
                },
            );
        }

        {
            let mut g = latencies.write().await;
            g.last_post_ms = Some(self.cfg.post_latency_ms);
            LatencyStats::bump(&mut g.max_post_ms, self.cfg.post_latency_ms);
        }

        self.open.insert(
            (market.to_string(), token_id.to_string()),
            PaperOrder {
                order_key: order_key.clone(),
                symbol: symbol.to_string(),
                timeframe,
                outcome: outcome.to_string(),
                market: market.to_string(),
                token_id: token_id.to_string(),
                price,
                original_size: size,
                size_matched: 0.0,
                queue_ahead: queue_ahead.max(0.0),
                activate_at,
                opened: false,
                cancel_req_at: None,
                cancel_clear_at: None,
                cancel_req_sent: false,
                submit_ts_ms: submitted_at.timestamp_millis(),
                strategy_id: self.strategy_id.clone(),
                ws_init: false,
                last_ws_best_bid_key: None,
                last_ws_best_bid_sz: 0.0,
                last_ws_level_sz_at_px: 0.0,
                last_seen_trade_ts_ms: None,
            },
        );

        push_log(
            logs,
            format!(
                "[{}] submit {} {} @ {:.3} size {:.3} queue_ahead {:.3} order_id={} (post={}ms)",
                self.strategy_id,
                market,
                token_id,
                price,
                size,
                queue_ahead.max(0.0),
                order_key,
                self.cfg.post_latency_ms
            ),
        )
        .await;
        emit_jsonl_event(
            "paper_order_submit",
            serde_json::json!({
                "symbol": symbol,
                "timeframe": timeframe.label(),
                "outcome": outcome,
                "market": market,
                "token_id": token_id,
                "order_id": order_key,
                "price": price,
                "size": size,
                "queue_ahead": queue_ahead.max(0.0),
                "post_latency_ms": self.cfg.post_latency_ms,
            }),
        );

        // Emit SUBMIT to orders.jsonl (paper mode telemetry)
        emit_order_event(&OrderTelemetry {
            run_id: None,
            schema_version: None,
            session_id: market.to_string(),
            token_id: Some(token_id.to_string()),
            strategy_id: self.strategy_id.clone(),
            order_id: order_key.clone(),
            outcome: outcome.to_string(),
            side: "BUY".to_string(),
            action: "SUBMIT".to_string(),
            submit_ts_ms: submitted_at.timestamp_millis(),
            ack_ts_ms: None,
            fill_ts_ms: None,
            ack_ms: None,
            fill_ms: None,
            fill_pct: 0.0,
            avg_fill_q: None,
            best_bid_q_at_submit: None,
            best_ask_q_at_submit: Some(price),
            spread_bps_at_submit: None,
            slippage_vs_mid_bps: None,
            cancel_reason: None,
            reason_codes: vec![],
        });

        order_key
    }

    pub(crate) async fn step(
        &mut self,
        state: &SharedState,
        logs: &SharedLogs,
        positions: &SharedPositions,
        orders: &SharedOrders,
        models: &SharedModels,
        cash_usdc: &SharedCash,
    ) {
        let now_i = std::time::Instant::now();
        let dt = now_i
            .duration_since(self.last_step)
            .as_secs_f64()
            .clamp(0.1, 5.0);
        self.last_step = now_i;

        let mut to_remove = Vec::new();

        let cfg = &self.cfg;
        let rng = &mut self.rng;
        let open = &mut self.open;
        let open_keys: Vec<(String, String)> = open.keys().cloned().collect();
        for (market, token_id) in open_keys {
            let Some(o) = open.get_mut(&(market.clone(), token_id.clone())) else {
                continue;
            };
            if !o.opened && now_i >= o.activate_at {
                o.opened = true;
                let ts = chrono::Utc::now();
                let mut g = orders.write().await;
                if let Some(v) = g.get_mut(&o.order_key) {
                    v.last_event = "OPEN".to_string();
                    v.last_update_at = ts;
                    v.placed_at = Some(ts);
                }
                push_log(
                    logs,
                    format!(
                        "[{}] open {} {} order_id={}",
                        &o.strategy_id,
                        market, token_id, o.order_key
                    ),
                )
                .await;
                emit_jsonl_event(
                    "paper_order_open",
                    serde_json::json!({
                        "market": market,
                        "token_id": token_id,
                        "order_id": o.order_key,
                    }),
                );

                // Emit ACK to orders.jsonl (paper mode telemetry)
                let ack_ts_ms = ts.timestamp_millis();
                let ack_ms = (ack_ts_ms - o.submit_ts_ms).max(0);
                emit_order_event(&OrderTelemetry {
                    run_id: None,
                    schema_version: None,
                    session_id: o.market.clone(),
                    token_id: Some(o.token_id.clone()),
                    strategy_id: self.strategy_id.clone(),
                    order_id: o.order_key.clone(),
                    outcome: o.outcome.clone(),
                    side: "BUY".to_string(),
                    action: "ACK".to_string(),
                    submit_ts_ms: o.submit_ts_ms,
                    ack_ts_ms: Some(ack_ts_ms),
                    fill_ts_ms: None,
                    ack_ms: Some(ack_ms),
                    fill_ms: None,
                    fill_pct: 0.0,
                    avg_fill_q: None,
                    best_bid_q_at_submit: None,
                    best_ask_q_at_submit: Some(o.price),
                    spread_bps_at_submit: None,
                    slippage_vs_mid_bps: None,
                    cancel_reason: None,
                    reason_codes: vec![],
                });
            }

            if let (Some(req_at), false) = (o.cancel_req_at, o.cancel_req_sent) {
                if now_i >= req_at {
                    o.cancel_req_sent = true;
                    let ts = chrono::Utc::now();
                    let mut g = orders.write().await;
                    if let Some(v) = g.get_mut(&o.order_key) {
                        v.last_event = "CANCEL_REQ".to_string();
                        v.last_update_at = ts;
                    }
                    emit_jsonl_event(
                        "paper_order_cancel_req_active",
                        serde_json::json!({
                            "market": market,
                            "token_id": token_id,
                            "order_id": o.order_key,
                        }),
                    );
                }
            }

            if let Some(clear_at) = o.cancel_clear_at {
                if now_i >= clear_at {
                    let ts = chrono::Utc::now();
                    let cancel_ts_ms = ts.timestamp_millis();
                    let mut g = orders.write().await;
                    if let Some(v) = g.get_mut(&o.order_key) {
                        v.last_event = "CANCELLED".to_string();
                        v.last_update_at = ts;
                    }
                    emit_jsonl_event(
                        "paper_order_cancelled",
                        serde_json::json!({
                            "market": market,
                            "token_id": token_id,
                            "order_id": o.order_key,
                        }),
                    );

                    // Emit CANCEL to orders.jsonl (paper mode telemetry)
                    let fill_pct = if o.original_size > 0.0 {
                        o.size_matched / o.original_size * 100.0
                    } else {
                        0.0
                    };
                    emit_order_event(&OrderTelemetry {
                        run_id: None,
                        schema_version: None,
                        session_id: o.market.clone(),
                        token_id: Some(o.token_id.clone()),
                        strategy_id: self.strategy_id.clone(),
                        order_id: o.order_key.clone(),
                        outcome: o.outcome.clone(),
                        side: "BUY".to_string(),
                        action: "CANCEL".to_string(),
                        submit_ts_ms: o.submit_ts_ms,
                        ack_ts_ms: None,
                        fill_ts_ms: None,
                        ack_ms: None,
                        fill_ms: Some(cancel_ts_ms - o.submit_ts_ms),
                        fill_pct,
                        avg_fill_q: None,
                        best_bid_q_at_submit: None,
                        best_ask_q_at_submit: Some(o.price),
                        spread_bps_at_submit: None,
                        slippage_vs_mid_bps: None,
                        cancel_reason: Some("user_cancel".to_string()),
                        reason_codes: vec![],
                    });

                    to_remove.push((market.clone(), token_id.clone()));
                    continue;
                }
            }

            if now_i < o.activate_at {
                continue;
            }

            let stream = {
                let g = state.read().await;
                g.get(&(o.symbol.clone(), o.timeframe)).cloned()
            };
            let Some(stream) = stream else { continue };

            // Safety: never match/fill a resting order against a different market snapshot
            // (rollovers reuse the same symbol/timeframe but swap condition_id + token IDs).
            if stream.condition_id.as_deref() != Some(market.as_str()) {
                continue;
            }

            // Select side by token_id, not by "Up/Down" label, so stale orders can't fill
            // against a new market's order book after rollover.
            let outcome_snap = if stream.token_up.as_deref() == Some(token_id.as_str()) {
                stream.up.clone()
            } else if stream.token_down.as_deref() == Some(token_id.as_str()) {
                stream.down.clone()
            } else {
                None
            };
            let Some(outcome_snap) = outcome_snap else {
                continue;
            };

            let rem = o.remaining();
            if rem <= 1e-9 {
                to_remove.push((market.clone(), token_id.clone()));
                continue;
            }

            let ws_best_bid = outcome_snap
                .bids
                .iter()
                .next_back()
                .map(|(k, sz)| (*k, (*sz).max(0.0)));
            let ws_best_bid_px = ws_best_bid.map(|(k, _)| key_to_price(k)).unwrap_or(0.0);
            let px_key = price_to_key(o.price);
            let ws_level_sz_at_px = outcome_snap.bids.get(&px_key).copied().unwrap_or(0.0).max(0.0);

            if !o.ws_init {
                // If the queue ahead partially depleted while we were waiting for "post latency",
                // reflect it by clamping to the currently visible depth at our price level.
                o.queue_ahead = o.queue_ahead.min(ws_level_sz_at_px);
                o.last_ws_best_bid_key = ws_best_bid.map(|(k, _)| k);
                o.last_ws_best_bid_sz = ws_best_bid.map(|(_, sz)| sz).unwrap_or(0.0);
                o.last_ws_level_sz_at_px = ws_level_sz_at_px;
                o.ws_init = true;
            } else {
                // If visible depth at our level increases, assume some fraction appears ahead of us
                // (we can't observe time priority from aggregated WS levels).
                let add = (ws_level_sz_at_px - o.last_ws_level_sz_at_px)
                    .max(0.0)
                    * cfg.queue_add_ahead_frac;
                if add > 0.0 {
                    o.queue_ahead += add;
                }
                o.last_ws_level_sz_at_px = ws_level_sz_at_px;
            }

            let mut fill_qty = 0.0;
            let mut fill_vwap = 0.0;

            let mut ws_sell_cap = 0.0;
            if let Some(ts_ms) = outcome_snap.metrics.last_trade_ts_ms {
                if o.last_seen_trade_ts_ms != Some(ts_ms) {
                    o.last_seen_trade_ts_ms = Some(ts_ms);
                    let now_ms = chrono::Utc::now().timestamp_millis();
                    let age_ms = (now_ms - ts_ms).max(0);
                    if age_ms <= 10_000 {
                        if outcome_snap.metrics.last_trade_is_sell == Some(true) {
                            if let (Some(p), Some(sz)) = (
                                outcome_snap.metrics.last_trade_price,
                                outcome_snap.metrics.last_trade_size,
                            ) {
                                if p <= o.price + 1e-9 && sz.is_finite() && sz > 0.0 {
                                    ws_sell_cap = sz;
                                }
                            }
                        }
                    }
                }
            }

            // Taker: if our limit crosses the ask, consume visible ask depth up to our price.
            if let Some(best_ask) = outcome_snap.metrics.best_ask {
                if best_ask <= o.price + 1e-9 {
                    let (q, vwap) = consume_depth(&outcome_snap.asks, rem, o.price);
                    fill_qty = q;
                    fill_vwap = vwap;
                }
            }

            // Maker: if resting (not crossing), simulate sell-flow at our price once we are "top".
            if fill_qty <= 1e-9 {
                if o.price + 1e-9 >= ws_best_bid_px {
                    let depth = outcome_snap.metrics.depth_bid_top.max(0.0);
                    let base = cfg.maker_flow_base_per_sec;
                    let depth_term = cfg.maker_flow_depth_frac_per_sec * depth;
                    let noise = cfg.maker_flow_noise_frac;
                    let mult = (1.0 - noise) + (2.0 * noise) * rng.gen::<f64>();
                    let baseline_flow = (base + depth_term) * mult * dt;

                    let mut inferred_flow = 0.0;
                    if cfg.maker_flow_use_book_deltas {
                        if let Some((bb_key, bb_sz)) = ws_best_bid {
                            if o.last_ws_best_bid_key == Some(bb_key) {
                                inferred_flow = (o.last_ws_best_bid_sz - bb_sz).max(0.0);
                            }
                        }
                    }

                    let mut flow = if cfg.maker_flow_require_trade_print {
                        ws_sell_cap
                    } else if inferred_flow > 0.0 {
                        inferred_flow
                    } else {
                        baseline_flow * cfg.maker_flow_fallback_mult
                    };

                    // If the tape traded at/through us, bump flow a bit.
                    if outcome_snap
                        .metrics
                        .last_trade_price
                        .is_some_and(|p| {
                            outcome_snap.metrics.last_trade_is_sell == Some(true)
                                && p <= o.price + 1e-9
                        })
                    {
                        flow *= 1.25;
                    }

                    if !cfg.maker_flow_require_trade_print && ws_sell_cap > 0.0 {
                        flow = flow.min(ws_sell_cap);
                    }

                    if flow > 0.0 {
                        if o.queue_ahead > 0.0 {
                            let consume = flow.min(o.queue_ahead);
                            o.queue_ahead -= consume;
                            flow -= consume;
                        }
                        if flow > 0.0 {
                            fill_qty = flow.min(rem);
                            fill_vwap = o.price;
                        }
                    }
                }
            }

            // Update last WS best-bid state after we've inferred flow for this step.
            o.last_ws_best_bid_key = ws_best_bid.map(|(k, _)| k);
            o.last_ws_best_bid_sz = ws_best_bid.map(|(_, sz)| sz).unwrap_or(0.0);

            if fill_qty > 1e-9 && fill_vwap > 0.0 {
                apply_paper_fill(
                    logs, positions, orders, models, cash_usdc, o, fill_qty, fill_vwap,
                )
                .await;

                if o.remaining() <= 1e-9 {
                    to_remove.push((market.clone(), token_id.clone()));
                }
            }
        }

        for k in to_remove {
            open.remove(&k);
        }
    }
}

fn consume_depth(asks: &BTreeMap<i64, f64>, mut want: f64, max_price: f64) -> (f64, f64) {
    let max_key = price_to_key(max_price);
    let mut got = 0.0;
    let mut cost = 0.0;
    for (k, sz) in asks.iter() {
        if *k > max_key || want <= 1e-9 {
            break;
        }
        let px = key_to_price(*k);
        let take = want.min(*sz);
        got += take;
        cost += take * px;
        want -= take;
    }
    let vwap = if got > 0.0 { cost / got } else { 0.0 };
    (got, vwap)
}

async fn apply_paper_fill(
    logs: &SharedLogs,
    positions: &SharedPositions,
    orders: &SharedOrders,
    models: &SharedModels,
    cash_usdc: &SharedCash,
    order: &mut PaperOrder,
    qty: f64,
    px: f64,
) {
    let now = chrono::Utc::now();

    // Wallet cash update (strategy only uses BUY in paper mode).
    {
        let mut g = cash_usdc.write().await;
        let wallet = g.unwrap_or(0.0);
        *g = Some((wallet - qty * px).max(0.0));
    }

    // Position update.
    {
        let mut g = positions.write().await;
        let p = g.entry(order.token_id.clone()).or_insert(PositionSnapshot {
            size: 0.0,
            avg_price: 0.0,
            updated_at: now,
        });
        let new_size = p.size + qty;
        if new_size.abs() <= 1e-9 {
            p.size = 0.0;
            p.avg_price = 0.0;
        } else if p.size.abs() <= 1e-9 {
            p.size = new_size;
            p.avg_price = px;
        } else {
            let cost = p.size * p.avg_price + qty * px;
            p.size = new_size;
            p.avg_price = if p.size.abs() > 1e-9 {
                cost / p.size
            } else {
                0.0
            };
        }
        p.updated_at = now;
    }

    // Order update + model fill signals.
    order.size_matched = (order.size_matched + qty)
        .min(order.original_size)
        .max(order.size_matched);
    let remaining = order.remaining();
    {
        let mut g = orders.write().await;
        if let Some(o) = g.get_mut(&order.order_key) {
            o.size_matched = order.size_matched;
            o.last_update_at = now;
            o.last_event = if remaining <= 1e-9 {
                "FILLED".to_string()
            } else {
                "PARTIAL_FILL".to_string()
            };
        }
    }

    if qty > 1e-9 {
        let sign = match order.outcome.to_ascii_lowercase().as_str() {
            "up" => 1.0,
            "down" => -1.0,
            _ => 0.0,
        };
        if sign != 0.0 {
            let mut g = models.write().await;
            let m = g
                .entry((order.symbol.clone(), order.timeframe))
                .or_insert_with(ProbToxModel::new);
            m.on_fill(now, sign);
        }
    }

    let fill_ts_ms = now.timestamp_millis();
    let fill_ms = (fill_ts_ms - order.submit_ts_ms).max(0);
    let fill_pct = if order.original_size > 0.0 {
        order.size_matched / order.original_size * 100.0
    } else {
        0.0
    };
    let action = if remaining <= 1e-9 { "FILL" } else { "PARTIAL_FILL" };

    push_log(
        logs,
        format!(
            "[{}][fill] {} {} {} @ {:.3} size {:.3} rem {:.3}",
            &order.strategy_id,
            order.symbol, order.outcome, order.token_id, px, qty, remaining
        ),
    )
    .await;
    emit_jsonl_event(
        "paper_fill",
        serde_json::json!({
            "symbol": order.symbol,
            "timeframe": order.timeframe.label(),
            "outcome": order.outcome,
            "token_id": order.token_id,
            "order_id": order.order_key,
            "price": px,
            "qty": qty,
            "remaining": remaining,
        }),
    );

    // Emit FILL/PARTIAL_FILL to orders.jsonl (paper mode telemetry)
    emit_order_event(&OrderTelemetry {
        run_id: None,
        schema_version: None,
        session_id: order.market.clone(),  // condition_id - the real 15-min session
        token_id: Some(order.token_id.clone()),
        strategy_id: order.strategy_id.clone(),
        order_id: order.order_key.clone(),
        outcome: order.outcome.clone(),
        side: "BUY".to_string(),
        action: action.to_string(),
        submit_ts_ms: order.submit_ts_ms,
        ack_ts_ms: None,
        fill_ts_ms: Some(fill_ts_ms),
        ack_ms: None,
        fill_ms: Some(fill_ms),
        fill_pct,
        avg_fill_q: Some(px),
        best_bid_q_at_submit: None,
        best_ask_q_at_submit: Some(order.price),
        spread_bps_at_submit: None,
        slippage_vs_mid_bps: None,
        cancel_reason: None,
        reason_codes: vec![],
    });
}
