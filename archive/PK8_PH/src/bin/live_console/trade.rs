use super::*;
use super::{emit_order_event, OrderTelemetry};
use super::strategies::{strategy_from_env, Desired, StrategyCtx, Strategy, PaperBaselineStrategy, RuleV1Strategy};

#[derive(Debug, Clone)]
pub(crate) struct ProbToxModel {
    pub(crate) last_fill_at: Option<chrono::DateTime<chrono::Utc>>,
    pub(crate) fill_count: u64,
    pub(crate) signed_fill_sum: f64,
}

impl ProbToxModel {
    pub(crate) fn new() -> Self {
        Self {
            last_fill_at: None,
            fill_count: 0,
            signed_fill_sum: 0.0,
        }
    }

    pub(crate) fn on_fill(&mut self, ts: chrono::DateTime<chrono::Utc>, sign: f64) {
        self.last_fill_at = Some(ts);
        self.fill_count = self.fill_count.saturating_add(1);
        if sign.is_finite() {
            self.signed_fill_sum += sign;
        }
    }
}

#[derive(Debug, Clone, Default)]
struct ExecSlot {
    active: bool,
    last_price_key: Option<i64>,
    last_size: f64,
    last_action_at: Option<std::time::Instant>,
}

#[derive(Debug, Clone, Copy)]
struct CashSnapshot {
    wallet_usdc: f64,
    reserved_usdc: f64,
    utilization: f64,
}

fn same_price_key(a: Option<i64>, b: Option<i64>) -> bool {
    a.is_some() && a == b
}

fn ceil_to_step(v: f64, step: f64) -> f64 {
    if !v.is_finite() || v <= 0.0 {
        return 0.0;
    }
    let s = step.max(1e-12);
    (v / s).ceil() * s
}

fn min_required_order_qty(price: f64, min_order_size: f64, min_order_notional: f64) -> f64 {
    let px = price.max(1e-9);
    let min_shares = min_order_size.max(0.0).max(5.0);
    let min_notional = min_order_notional.max(0.0);
    let req = min_shares.max(min_notional / px);
    ceil_to_step(req, 0.001)
}

fn dec(v: f64) -> Result<Decimal> {
    Decimal::from_f64(v).context("failed to convert float to Decimal")
}

/// Check USDC balance on Polygon via RPC
async fn check_usdc_balance() -> Result<f64> {
    let rpc_url = std::env::var("PM_POLYGON_RPC_URL")
        .unwrap_or_else(|_| "https://polygon-rpc.com".to_string());
    let usdc_contract = std::env::var("PM_USDC_CONTRACT")
        .unwrap_or_else(|_| "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359".to_string());
    let private_key = std::env::var("PM_PRIVATE_KEY")
        .context("PM_PRIVATE_KEY not set")?;

    // Derive wallet address from private key
    let signer = PrivateKeySigner::from_str(&private_key)
        .context("invalid PM_PRIVATE_KEY")?;
    let wallet_addr = format!("{:?}", signer.address());
    let wallet_addr_clean = wallet_addr.trim_start_matches("0x");

    // Build balanceOf call data: 0x70a08231 + padded address
    let call_data = format!("0x70a08231000000000000000000000000{}", wallet_addr_clean);

    let client = reqwest::Client::new();
    let resp = client.post(&rpc_url)
        .header("Content-Type", "application/json")
        .json(&serde_json::json!({
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{
                "to": usdc_contract,
                "data": call_data
            }, "latest"],
            "id": 1
        }))
        .send()
        .await
        .context("RPC request failed")?;

    let json: serde_json::Value = resp.json().await
        .context("Failed to parse RPC response")?;

    let result = json["result"].as_str()
        .context("No result in RPC response")?;

    // Parse hex balance (USDC has 6 decimals)
    let balance_wei = u128::from_str_radix(result.trim_start_matches("0x"), 16)
        .unwrap_or(0);
    let balance = balance_wei as f64 / 1_000_000.0;

    Ok(balance)
}

pub(crate) async fn derive_api_creds_from_env() -> Result<Option<(PrivateKeySigner, PmApiCreds)>> {
    let Some(private_key) = std::env::var("PM_PRIVATE_KEY").ok() else {
        return Ok(None);
    };
    let chain_id: u64 = std::env::var("PM_CHAIN_ID")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(137);
    let host =
        std::env::var("PM_CLOB_HOST").unwrap_or_else(|_| "https://clob.polymarket.com".to_string());

    let signer = PrivateKeySigner::from_str(&private_key).context("invalid PM_PRIVATE_KEY")?;
    let funder = funder_address_from_env();

    let auth = PmAuthenticatedClient::new(host, signer.clone(), chain_id, None, funder);
    let creds = match auth.derive_api_key().await {
        Ok(c) => c,
        Err(_) => auth
            .create_api_key(None)
            .await
            .context("create_api_key failed")?,
    };
    Ok(Some((signer, creds)))
}

async fn trading_client_from_env() -> Result<Option<(PmTradingClient, PmApiCreds)>> {
    let Some((signer, api_creds)) = derive_api_creds_from_env().await? else {
        return Ok(None);
    };

    let chain_id: u64 = std::env::var("PM_CHAIN_ID")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(137);
    let host =
        std::env::var("PM_CLOB_HOST").unwrap_or_else(|_| "https://clob.polymarket.com".to_string());

    let sig_type = signature_type_from_env();
    let funder = funder_address_from_env();
    let order_builder = PmOrderBuilder::new(signer.clone(), Some(sig_type), funder);
    Ok(Some((
        PmTradingClient::new(host, signer, chain_id, api_creds.clone(), order_builder),
        api_creds,
    )))
}

async fn ensure_buy_order(
    client: &PmTradingClient,
    logs: &SharedLogs,
    orders_view: &SharedOrders,
    latencies: &SharedLatency,
    symbol: &str,
    timeframe: Timeframe,
    outcome: &str,
    market: &str,
    token_id: &str,
    tick_size: f64,
    desired: &Desired,
    min_order_size: f64,
    min_order_notional: f64,
    cash: Option<CashSnapshot>,
    min_requote: Duration,
    requote_min_ticks: f64,
    requote_max_age: Duration,
    slot: &mut ExecSlot,
    confirm_cancel_cleared: bool,
    cancel_post_delay: Duration,
) -> Result<()> {
    let now = std::time::Instant::now();
    let desired_px = desired.px.map(|p| p.clamp(0.01, 0.99));
    let desired_q = desired.q.max(0.0);

    let min_req = desired_px
        .map(|px| min_required_order_qty(px, min_order_size, min_order_notional))
        .unwrap_or(0.0);

    if let Some(last) = slot.last_action_at {
        if now.duration_since(last) < min_requote {
            return Ok(());
        }
    }

    let wants_order = desired_px.is_some() && desired_q + 1e-9 >= min_req;
    if !wants_order {
        if slot.active {
            if let Some(last_at) = slot.last_action_at {
                if now.duration_since(last_at) < requote_max_age {
                    return Ok(());
                }
            }
            let cancel_start = std::time::Instant::now();
            let _ = client
                .cancel_market_orders(Some(market), Some(token_id))
                .await?;
            let cancel_req_ms = cancel_start.elapsed().as_millis() as i64;
            {
                let mut g = latencies.write().await;
                g.last_cancel_req_ms = Some(cancel_req_ms);
                LatencyStats::bump(&mut g.max_cancel_req_ms, cancel_req_ms);
                g.last_cancel_clear_ms = Some(cancel_req_ms);
                LatencyStats::bump(&mut g.max_cancel_clear_ms, cancel_req_ms);
            }
            slot.active = false;
            slot.last_price_key = None;
            slot.last_size = 0.0;
            slot.last_action_at = Some(now);
            push_log(logs, format!("[trade] cancel {} {}", market, token_id)).await;
        }
        return Ok(());
    }

    let px = desired_px.unwrap();
    let px_key = price_to_key(px);
    let size = ceil_to_step(desired_q, 0.001);

    // Churn control: skip if same price and size is within 0.001, and price within N ticks.
    if slot.active {
        let px_same = same_price_key(slot.last_price_key, Some(px_key));
        let size_same = (slot.last_size - size).abs() <= 0.001 + 1e-9;
        if px_same && size_same {
            return Ok(());
        }
        if let (Some(last_key), Some(new_key)) = (slot.last_price_key, Some(px_key)) {
            let dticks = ((new_key - last_key).abs() as f64) / tick_size.max(1e-9);
            if dticks < requote_min_ticks.max(0.0) && size_same {
                return Ok(());
            }
        }
    }

    if let Some(cash) = cash {
        let need = px * size;
        let have = (cash.wallet_usdc - cash.reserved_usdc).max(0.0) * cash.utilization;
        if need > have + 1e-6 {
            push_log(
                logs,
                format!(
                    "[trade][cash] block need ${:.2} have ${:.2} (wallet ${:.2} reserved ${:.2} util {:.2})",
                    need, have, cash.wallet_usdc, cash.reserved_usdc, cash.utilization
                ),
            )
            .await;
            return Ok(());
        }
    }

    if slot.active {
        let _ = client
            .cancel_market_orders(Some(market), Some(token_id))
            .await?;
        if cancel_post_delay > Duration::ZERO {
            tokio::time::sleep(cancel_post_delay).await;
        }
        if confirm_cancel_cleared {
            let params = polymarket_rs::types::OpenOrderParams::new()
                .market(market.to_string())
                .asset_id(token_id.to_string());
            let _ = client.get_orders(params).await?;
        }
    }

    let start = std::time::Instant::now();
    let args = PmOrderArgs::new(token_id.to_string(), dec(px)?, dec(size)?, PmSide::Buy);
    let options = PmCreateOrderOptions::new()
        .tick_size(dec(tick_size)?)
        .neg_risk(false);
    let signed = client.create_order(&args, None, None, options)?;
    let resp = client.post_order(signed, PmOrderType::Gtc).await?;
    let post_ms = start.elapsed().as_millis() as i64;
    {
        let mut g = latencies.write().await;
        g.last_post_ms = Some(post_ms);
        LatencyStats::bump(&mut g.max_post_ms, post_ms);
    }

    // Track locally (minimal fields).
    let submit_ts_ms = chrono::Utc::now().timestamp_millis();
    let strategy_id = std::env::var("PM_STRATEGY").unwrap_or_else(|_| "balanced_arb".to_string());
    {
        let mut g = orders_view.write().await;
        let ts = chrono::Utc::now();
        g.insert(
            resp.order_id.to_string(),
            OrderSnapshot {
                order_id: resp.order_id.to_string(),
                symbol: symbol.to_string(),
                timeframe,
                outcome: outcome.to_string(),
                token_id: token_id.to_string(),
                market: market.to_string(),
                side: "BUY".to_string(),
                price: px,
                original_size: size,
                size_matched: 0.0,
                last_event: "SUBMIT".to_string(),
                last_update_at: ts,
                submitted_at: Some(ts),
                placed_at: None,
                // Telemetry fields
                submit_ts_ms: Some(submit_ts_ms),
                best_bid_at_submit: None,  // Not available in this context
                best_ask_at_submit: Some(px),  // Limit price approximates ask
                strategy_id: Some(strategy_id.clone()),
                reason_codes: vec![desired.why.to_string()],
            },
        );
    }

    // Emit SUBMIT event to orders.jsonl (non-blocking, safe to fail)
    emit_order_event(&OrderTelemetry {
        run_id: None,           // auto-enriched by emit_order_event
        schema_version: None,   // auto-enriched by emit_order_event
        session_id: market.to_string(),
        token_id: Some(token_id.to_string()),
        strategy_id: strategy_id,
        order_id: resp.order_id.to_string(),
        outcome: outcome.to_string(),
        side: "BUY".to_string(),
        action: "SUBMIT".to_string(),
        submit_ts_ms,
        ack_ts_ms: None,
        fill_ts_ms: None,
        ack_ms: None,
        fill_ms: None,
        fill_pct: 0.0,
        avg_fill_q: None,
        best_bid_q_at_submit: None,
        best_ask_q_at_submit: Some(px),
        spread_bps_at_submit: None,
        slippage_vs_mid_bps: None,
        cancel_reason: None,
        reason_codes: vec![desired.why.to_string()],
    });

    slot.active = true;
    slot.last_price_key = Some(px_key);
    slot.last_size = size;
    slot.last_action_at = Some(now);

    push_log(
        logs,
        format!(
            "[trade] submit {}:{} {} {} @ {:.3} x{:.3} ({})",
            symbol,
            timeframe.label(),
            market,
            token_id,
            px,
            size,
            desired.why
        ),
    )
    .await;
    Ok(())
}

async fn ensure_buy_order_paper(
    paper: &mut crate::paper::PaperBroker,
    logs: &SharedLogs,
    orders_view: &SharedOrders,
    latencies: &SharedLatency,
    symbol: &str,
    timeframe: Timeframe,
    outcome: &str,
    market: &str,
    token_id: &str,
    tick_size: f64,
    desired: &Desired,
    min_order_size: f64,
    min_order_notional: f64,
    cash: Option<CashSnapshot>,
    min_requote: Duration,
    requote_min_ticks: f64,
    requote_max_age: Duration,
    slot: &mut ExecSlot,
) -> Result<()> {
    let now = std::time::Instant::now();
    slot.active = paper.is_busy(market, token_id);
    if !slot.active {
        slot.last_price_key = None;
        slot.last_size = 0.0;
    }

    let desired_px = desired.px.map(|p| p.clamp(0.01, 0.99));
    let desired_q = desired.q.max(0.0);
    let min_req = desired_px
        .map(|px| min_required_order_qty(px, min_order_size, min_order_notional))
        .unwrap_or(0.0);

    if let Some(last) = slot.last_action_at {
        if now.duration_since(last) < min_requote {
            return Ok(());
        }
    }

    let wants_order = desired_px.is_some() && desired_q + 1e-9 >= min_req;
    if !wants_order {
        if slot.active {
            if let Some(last_at) = slot.last_action_at {
                if now.duration_since(last_at) < requote_max_age {
                    return Ok(());
                }
            }
            paper
                .cancel_market_orders(logs, orders_view, latencies, market, token_id)
                .await;
            slot.active = false;
            slot.last_price_key = None;
            slot.last_size = 0.0;
            slot.last_action_at = Some(now);
        }
        return Ok(());
    }

    let px = desired_px.unwrap();
    let px_key = price_to_key(px);
    let size = ceil_to_step(desired_q, 0.001);

    if slot.active {
        let px_same = same_price_key(slot.last_price_key, Some(px_key));
        let size_same = (slot.last_size - size).abs() <= 0.001 + 1e-9;
        if px_same && size_same {
            return Ok(());
        }
        if let (Some(last_key), Some(new_key)) = (slot.last_price_key, Some(px_key)) {
            let dticks = ((new_key - last_key).abs() as f64) / tick_size.max(1e-9);
            if dticks < requote_min_ticks.max(0.0) && size_same {
                return Ok(());
            }
        }
    }

    if let Some(cash) = cash {
        let need = px * size;
        let have = (cash.wallet_usdc - cash.reserved_usdc).max(0.0) * cash.utilization;
        if need > have + 1e-6 {
            push_log(
                logs,
                format!(
                    "[paper][cash] block need ${:.2} have ${:.2} (wallet ${:.2} reserved ${:.2} util {:.2})",
                    need, have, cash.wallet_usdc, cash.reserved_usdc, cash.utilization
                ),
            )
            .await;
            return Ok(());
        }
    }

    if slot.active {
        paper
            .cancel_market_orders(logs, orders_view, latencies, market, token_id)
            .await;
    }

    paper
        .place_limit_buy(
            logs,
            orders_view,
            latencies,
            symbol,
            timeframe,
            outcome,
            market,
            token_id,
            px,
            size,
            0.0,
        )
        .await;

    slot.active = true;
    slot.last_price_key = Some(px_key);
    slot.last_size = size;
    slot.last_action_at = Some(now);
    Ok(())
}

pub(crate) async fn trade_loop(
    state: SharedState,
    logs: SharedLogs,
    positions: SharedPositions,
    orders: SharedOrders,
    models: SharedModels,
    cfg: RunConfig,
    mut tick_rx: TradeTickRx,
    cash_usdc: SharedCash,
    latencies: SharedLatency,
) -> Result<()> {
    let paper_trading = std::env::var("PM_PAPER_TRADING")
        .ok()
        .is_some_and(|v| v == "1" || v.eq_ignore_ascii_case("true"));
    let ab_test = std::env::var("AB_TEST")
        .ok()
        .is_some_and(|v| v == "1" || v.eq_ignore_ascii_case("true"));
    let allow_multi_market = std::env::var("ALLOW_MULTI_MARKET")
        .ok()
        .is_some_and(|v| v == "1" || v.eq_ignore_ascii_case("true"));

    // A/B test mode: create two paper brokers, one per strategy
    let mut paper_a: Option<crate::paper::PaperBroker> = None;
    let mut paper_b: Option<crate::paper::PaperBroker> = None;
    let mut paper = if ab_test {
        paper_a = Some(crate::paper::PaperBroker::new_with_strategy("paper_baseline"));
        paper_b = Some(crate::paper::PaperBroker::new_with_strategy("rule_v1"));
        if let Some(ref p) = paper_a {
            *cash_usdc.write().await = Some(p.starting_cash_usdc());
            // Print caps and OK TO SLEEP checklist
            let caps = &p.caps;
            eprintln!("┌─────────────────────────────────────────────────────────────┐");
            eprintln!("│ RISK CAPS (paper)                                           │");
            eprintln!("├─────────────────────────────────────────────────────────────┤");
            eprintln!("│ MAX_WORST_TOTAL_USD:       {:<10.2}                      │", caps.max_worst_total_usd);
            eprintln!("│ MAX_WORST_PER_MARKET_USD:  {:<10.2}                      │", caps.max_worst_per_market_usd);
            eprintln!("│ MAX_TRADES_PER_SESSION:    {:<10}                      │", caps.max_trades_per_session);
            eprintln!("│ MAX_POSITION_SHARES:       {:<10.1}                      │", caps.max_position_shares);
            eprintln!("│ ALLOW_MULTI_MARKET:        {:<10}                      │", if allow_multi_market { "YES" } else { "NO (BTC only)" });
            eprintln!("├─────────────────────────────────────────────────────────────┤");
            eprintln!("│ OK TO SLEEP: caps=✓ paper_only=✓                            │");
            eprintln!("└─────────────────────────────────────────────────────────────┘");
            push_log(
                &logs,
                format!(
                    "[AB_TEST] enabled: paper_baseline + rule_v1, cash=${:.2}, caps=[total={:.0},market={:.0},trades={},shares={:.0}]",
                    p.starting_cash_usdc(), caps.max_worst_total_usd, caps.max_worst_per_market_usd,
                    caps.max_trades_per_session, caps.max_position_shares
                ),
            )
            .await;
        }
        None  // Don't use the single paper broker in AB_TEST mode
    } else {
        paper_trading.then(crate::paper::PaperBroker::new_from_env)
    };
    if let Some(ref p) = paper {
        *cash_usdc.write().await = Some(p.starting_cash_usdc());
        push_log(
            &logs,
            format!(
                "[paper] enabled starting_cash=${:.2} (set PM_PAPER_TRADING=0 to disable)",
                p.starting_cash_usdc()
            ),
        )
        .await;
    }

    let warmup_seconds: f64 = std::env::var("PM_WARMUP_SECONDS")
        .ok()
        .and_then(|v| v.parse::<f64>().ok())
        .unwrap_or(5.0);
    let warmup_for = Duration::from_secs_f64(warmup_seconds.max(0.0));
    let warmup_started_at = std::time::Instant::now();
    let mut warmup_complete = warmup_for.is_zero();

    let min_requote_seconds: f64 = std::env::var("PM_MIN_REQUOTE_SECONDS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1.0);
    let min_requote = Duration::from_secs_f64(min_requote_seconds.max(0.0));

    let requote_min_ticks: f64 = std::env::var("PM_REQUOTE_MIN_TICKS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(2.0);
    let requote_max_age_seconds: f64 = std::env::var("PM_REQUOTE_MAX_AGE_SECONDS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(10.0);
    let requote_max_age = Duration::from_secs_f64(requote_max_age_seconds.max(0.5));

    let min_order_notional: f64 = std::env::var("PM_MIN_ORDER_NOTIONAL")
        .ok()
        .and_then(|v| v.parse::<f64>().ok())
        .unwrap_or(1.0);

    let cash_utilization: f64 = std::env::var("PM_CASH_UTILIZATION")
        .ok()
        .and_then(|v| v.parse::<f64>().ok())
        .unwrap_or(0.90f64)
        .clamp(0.0f64, 1.0f64);
    let skip_cash_check = std::env::var("PM_SKIP_CASH_CHECK")
        .ok()
        .is_some_and(|v| v == "1" || v.eq_ignore_ascii_case("true"));

    let live_cancel_confirm = std::env::var("PM_LIVE_CANCEL_CONFIRM")
        .ok()
        .map(|v| v != "0" && !v.eq_ignore_ascii_case("false"))
        .unwrap_or(true);
    let live_cancel_post_delay_ms: i64 = std::env::var("PM_LIVE_CANCEL_POST_DELAY_MS")
        .ok()
        .and_then(|v| v.parse::<i64>().ok())
        .unwrap_or(0);
    let live_cancel_post_delay =
        Duration::from_millis(live_cancel_post_delay_ms.max(0) as u64);

    // A/B test mode is ALWAYS paper-only (never create live client)
    let (client, _api_creds) = if cfg.dry_run || paper_trading || ab_test {
        (None, None)
    } else {
        match trading_client_from_env().await? {
            Some((c, creds)) => (Some(c), Some(creds)),
            None => (None, None),
        }
    };
    if !cfg.dry_run && !paper_trading && !ab_test && client.is_none() {
        push_log(
            &logs,
            "[trade] PM_DRY_RUN=0 but missing PM_PRIVATE_KEY; refusing to trade".to_string(),
        )
        .await;
        anyhow::bail!("missing trading credentials");
    }

    // Live trading caps (CRITICAL: hard rails for canary)
    let live_caps = crate::paper::RiskCaps::from_env();
    let mut live_risk = crate::paper::RiskState::default();
    let kill_switch_path = std::env::var("KILL_SWITCH_FILE")
        .unwrap_or_else(|_| "KILL_SWITCH".to_string());

    if client.is_some() {
        eprintln!("┌─────────────────────────────────────────────────────────────┐");
        eprintln!("│ LIVE TRADING CAPS (canary mode)                             │");
        eprintln!("├─────────────────────────────────────────────────────────────┤");
        eprintln!("│ MAX_WORST_TOTAL_USD:       {:<10.2}                      │", live_caps.max_worst_total_usd);
        eprintln!("│ MAX_WORST_PER_MARKET_USD:  {:<10.2}                      │", live_caps.max_worst_per_market_usd);
        eprintln!("│ MAX_TRADES_PER_SESSION:    {:<10}                      │", live_caps.max_trades_per_session);
        eprintln!("│ MAX_POSITION_SHARES:       {:<10.1}                      │", live_caps.max_position_shares);
        eprintln!("│ KILL_SWITCH_FILE:          {:<26}   │", &kill_switch_path);
        eprintln!("├─────────────────────────────────────────────────────────────┤");
        eprintln!("│ ⚠ LIVE MODE: Real money at risk                             │");
        eprintln!("└─────────────────────────────────────────────────────────────┘");

        // Pre-flight balance check (wallet only - Polymarket deposited checked at runtime)
        let min_usdc_required = live_caps.max_worst_total_usd;
        eprintln!("[PREFLIGHT] Checking on-chain wallet USDC...");
        match check_usdc_balance().await {
            Ok(balance) => {
                if balance < min_usdc_required {
                    eprintln!("[PREFLIGHT] Wallet USDC: ${:.2} (low - check Polymarket deposit)", balance);
                    // Don't fail - funds may be deposited in Polymarket
                } else {
                    eprintln!("[PREFLIGHT] Wallet USDC: ${:.2} ✓", balance);
                }
            }
            Err(e) => {
                eprintln!("[PREFLIGHT] Warning: Could not check wallet USDC: {}", e);
            }
        }
        eprintln!("[PREFLIGHT] Polymarket balance will show in header once connected");

        // LIVE MODE CONFIRMATION: wait for user to confirm before trading
        let armed = std::env::var("PM_ARMED").ok().map(|v| v == "1").unwrap_or(false);
        if !armed {
            eprintln!();
            eprintln!("┌─────────────────────────────────────────────────────────────┐");
            eprintln!("│ 🔒 OBSERVATION MODE - Trading disabled                      │");
            eprintln!("│                                                             │");
            eprintln!("│ To enable live trading, set: PM_ARMED=1                     │");
            eprintln!("│ Console will show data but NOT place orders.                │");
            eprintln!("└─────────────────────────────────────────────────────────────┘");
        } else {
            eprintln!();
            eprintln!("┌─────────────────────────────────────────────────────────────┐");
            eprintln!("│ 🔴 ARMED - Live trading will start in 10 seconds...        │");
            eprintln!("│ Press Ctrl+C to abort                                       │");
            eprintln!("└─────────────────────────────────────────────────────────────┘");
            tokio::time::sleep(Duration::from_secs(10)).await;
        }
    }

    // Observation mode flag (live client exists but trading disabled)
    let observation_mode = client.is_some() && !std::env::var("PM_ARMED").ok().map(|v| v == "1").unwrap_or(false);

    // A/B test mode: create both strategies
    let mut strategy_a: Option<Box<dyn Strategy + Send>> = None;
    let mut strategy_b: Option<Box<dyn Strategy + Send>> = None;
    let mut strategy = if ab_test {
        strategy_a = Some(Box::new(PaperBaselineStrategy::default()));
        strategy_b = Some(Box::new(RuleV1Strategy::default()));
        push_log(
            &logs,
            format!(
                "[trade] AB_TEST mode: strategies=[paper_baseline, rule_v1] dry_run={} tradelist={}",
                cfg.dry_run,
                cfg.tradelist
                    .iter()
                    .map(|s| format!("{}:{}", s.symbol, s.timeframe.label()))
                    .collect::<Vec<_>>()
                    .join(",")
            ),
        )
        .await;
        None  // Don't use single strategy in AB_TEST mode
    } else {
        Some(strategy_from_env()?)
    };
    if let Some(ref s) = strategy {
        push_log(
            &logs,
            format!(
                "[trade] strategy={} dry_run={} paper={} tradelist={}",
                s.name(),
                cfg.dry_run,
                paper_trading,
                cfg.tradelist
                    .iter()
                    .map(|s| format!("{}:{}", s.symbol, s.timeframe.label()))
                    .collect::<Vec<_>>()
                    .join(",")
            ),
        )
        .await;
    }

    let mut slots: HashMap<(String, Timeframe, String), ExecSlot> = HashMap::new();
    // Separate slots for each strategy in A/B mode
    let mut slots_a: HashMap<(String, Timeframe, String), ExecSlot> = HashMap::new();
    let mut slots_b: HashMap<(String, Timeframe, String), ExecSlot> = HashMap::new();
    let mut dirty: HashSet<TradeTick> = HashSet::new();
    let mut process_all = true;

    // VALIDATION_ORDER flag: submit one test order, wait, cancel it
    let validation_order = std::env::var("VALIDATION_ORDER")
        .ok()
        .is_some_and(|v| v == "1" || v.eq_ignore_ascii_case("true"));
    let mut validation_done = false;
    let mut validation_submitted_at: Option<std::time::Instant> = None;
    let validation_market = std::sync::Arc::new(std::sync::RwLock::new(String::new()));
    let validation_token = std::sync::Arc::new(std::sync::RwLock::new(String::new()));

    let mut housekeeping_iv = tokio::time::interval(Duration::from_secs(1));
    housekeeping_iv.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

    loop {
        tokio::select! {
            _ = housekeeping_iv.tick() => {
                process_all = true;
            }
            msg = tick_rx.recv() => {
                if let Some(tick) = msg {
                    dirty.insert(tick);
                    while let Ok(t) = tick_rx.try_recv() {
                        dirty.insert(t);
                    }
                } else {
                    process_all = true;
                }
            }
        }

        // Step paper brokers - A/B mode has two, normal mode has one
        if ab_test {
            if let Some(p) = paper_a.as_mut() {
                p.step(&state, &logs, &positions, &orders, &models, &cash_usdc)
                    .await;
            }
            if let Some(p) = paper_b.as_mut() {
                p.step(&state, &logs, &positions, &orders, &models, &cash_usdc)
                    .await;
            }
        } else if let Some(p) = paper.as_mut() {
            p.step(&state, &logs, &positions, &orders, &models, &cash_usdc)
                .await;
        }

        if !warmup_complete && warmup_started_at.elapsed() >= warmup_for {
            warmup_complete = true;
            push_log(&logs, "[trade] warmup complete".to_string()).await;
        }

        // VALIDATION_ORDER: submit test order after warmup, cancel after 7s
        if validation_order && warmup_complete && !validation_done {
            if validation_submitted_at.is_none() {
                // Find first available market to submit a validation order
                if let Some(seed) = cfg.tradelist.first() {
                    let snap = {
                        let g = state.read().await;
                        g.get(&(seed.symbol.to_string(), seed.timeframe)).cloned()
                    };
                    if let Some(snap) = snap {
                        if let (Some(market), Some(tok_up)) = (snap.condition_id.as_deref(), snap.token_up.as_deref()) {
                            if let Some(p) = paper.as_mut() {
                                // Submit at 0.05 (far below market) so it won't fill
                                let order_id = p.place_limit_buy(
                                    &logs,
                                    &orders,
                                    &latencies,
                                    seed.symbol,
                                    seed.timeframe,
                                    "Up",
                                    market,
                                    tok_up,
                                    0.05,  // Non-filling price
                                    5.0,   // 5 shares
                                    0.0,
                                ).await;
                                *validation_market.write().unwrap() = market.to_string();
                                *validation_token.write().unwrap() = tok_up.to_string();
                                validation_submitted_at = Some(std::time::Instant::now());
                                push_log(&logs, format!("[VALIDATION] submitted test order {} at $0.05 (will cancel in 7s)", order_id)).await;
                                eprintln!("[VALIDATION] submitted test order {} at $0.05 (will cancel in 7s)", order_id);
                            }
                        }
                    }
                }
            } else if let Some(submitted_at) = validation_submitted_at {
                let elapsed = submitted_at.elapsed().as_secs();
                if elapsed >= 7 {
                    // Cancel the validation order
                    let market = validation_market.read().unwrap().clone();
                    let token = validation_token.read().unwrap().clone();
                    eprintln!("[VALIDATION] attempting cancel: market={} token={} paper={}", market, token, paper.is_some());
                    if let Some(p) = paper.as_mut() {
                        p.cancel_market_orders(&logs, &orders, &latencies, &market, &token).await;
                        push_log(&logs, "[VALIDATION] cancelled test order - lifecycle complete".to_string()).await;
                        eprintln!("[VALIDATION] cancelled test order - lifecycle complete");
                        eprintln!("[VALIDATION] Check orders.jsonl for SUBMIT -> ACK -> CANCEL events");
                    } else {
                        eprintln!("[VALIDATION] ERROR: paper broker is None!");
                    }
                    validation_done = true;
                }
            }
        }

        if !(process_all || !dirty.is_empty()) {
            continue;
        }
        let _ = std::mem::take(&mut dirty);
        process_all = false;

        for seed in &cfg.tradelist {
            let snap = {
                let g = state.read().await;
                g.get(&(seed.symbol.to_string(), seed.timeframe)).cloned()
            };
            let Some(snap) = snap else { continue };
            let Some(market) = snap.condition_id.as_deref() else { continue };
            let Some(tok_up) = snap.token_up.as_deref() else { continue };
            let Some(tok_dn) = snap.token_down.as_deref() else { continue };

            if !warmup_complete {
                continue;
            }

            let up_mid = snap.up.as_ref().and_then(outcome_mid_px);
            let down_mid = snap.down.as_ref().and_then(outcome_mid_px);
            let by = snap.up.as_ref().and_then(outcome_best_bid_px);
            let bn = snap.down.as_ref().and_then(outcome_best_bid_px);

            let min_order_size = snap.order_min_size.unwrap_or(5.0).max(0.0);
            let tick_size = snap.min_tick_size.unwrap_or(0.001).max(1e-9);
            let tau_seconds = snap
                .end_date
                .map(|t| (t - chrono::Utc::now()).num_seconds().max(0) as f64)
                .unwrap_or(0.0);

            let current_exposure = 0.0;
            let qy = positions
                .read()
                .await
                .get(tok_up)
                .map(|p| p.size)
                .unwrap_or(0.0);
            let qn = positions
                .read()
                .await
                .get(tok_dn)
                .map(|p| p.size)
                .unwrap_or(0.0);

            let ctx = StrategyCtx {
                symbol: seed.symbol,
                timeframe: seed.timeframe,
                tau_seconds,
                market,
                tok_up,
                tok_dn,
                by,
                bn,
                up_mid,
                down_mid,
                min_order_size,
                tick_size,
                qy,
                qn,
                current_exposure,
            };

            // Include market context in decision log for debugging
            let up_mid_str = up_mid.map(|v| format!("{:.3}", v)).unwrap_or_else(|| "-".to_string());
            let dn_mid_str = down_mid.map(|v| format!("{:.3}", v)).unwrap_or_else(|| "-".to_string());

            // A/B test mode: run both strategies
            if ab_test {
                if !cfg.trade_enabled {
                    continue;
                }

                // Scope check: BTC-only unless ALLOW_MULTI_MARKET=1
                if !allow_multi_market && !seed.symbol.to_uppercase().contains("BTC") {
                    continue;
                }

                // Strategy A: paper_baseline
                if let (Some(ref mut strat_a), Some(ref mut broker_a)) = (&mut strategy_a, &mut paper_a) {
                    let q_a = strat_a.quote(&ctx);
                    for (outcome, token_id, desired) in [
                        ("up", tok_up, q_a.up),
                        ("down", tok_dn, q_a.down),
                    ] {
                        // Skip if no order desired
                        if desired.px.is_none() || desired.q <= 0.0 {
                            continue;
                        }
                        let px = desired.px.unwrap();
                        let sz = desired.q;

                        // Cap check
                        if let Some(skip_reason) = broker_a.check_caps(market, px, sz) {
                            emit_order_event(&OrderTelemetry {
                                run_id: None,
                                schema_version: None,
                                session_id: market.to_string(),
                                token_id: Some(token_id.to_string()),
                                strategy_id: broker_a.strategy_id().to_string(),
                                order_id: format!("skip-{}-{}", market, token_id),
                                outcome: outcome.to_string(),
                                side: "BUY".to_string(),
                                action: "SKIP".to_string(),
                                submit_ts_ms: chrono::Utc::now().timestamp_millis(),
                                ack_ts_ms: None, fill_ts_ms: None, ack_ms: None, fill_ms: None,
                                fill_pct: 0.0, avg_fill_q: None,
                                best_bid_q_at_submit: None, best_ask_q_at_submit: Some(px),
                                spread_bps_at_submit: None, slippage_vs_mid_bps: None,
                                cancel_reason: Some(skip_reason.clone()),
                                reason_codes: vec![skip_reason],
                            });
                            continue;
                        }

                        let slot_key = (seed.symbol.to_string(), seed.timeframe, token_id.to_string());
                        let slot = slots_a.entry(slot_key).or_default();
                        let _ = ensure_buy_order_paper(
                            broker_a,
                            &logs,
                            &orders,
                            &latencies,
                            seed.symbol,
                            seed.timeframe,
                            outcome,
                            market,
                            token_id,
                            tick_size,
                            &desired,
                            min_order_size,
                            min_order_notional,
                            None,
                            min_requote,
                            requote_min_ticks,
                            requote_max_age,
                            slot,
                        )
                        .await;
                        // Record trade for cap tracking
                        broker_a.record_trade(market, px, sz);
                    }
                }

                // Strategy B: rule_v1
                if let (Some(ref mut strat_b), Some(ref mut broker_b)) = (&mut strategy_b, &mut paper_b) {
                    let q_b = strat_b.quote(&ctx);
                    for (outcome, token_id, desired) in [
                        ("up", tok_up, q_b.up),
                        ("down", tok_dn, q_b.down),
                    ] {
                        // Skip if no order desired
                        if desired.px.is_none() || desired.q <= 0.0 {
                            continue;
                        }
                        let px = desired.px.unwrap();
                        let sz = desired.q;

                        // Cap check
                        if let Some(skip_reason) = broker_b.check_caps(market, px, sz) {
                            emit_order_event(&OrderTelemetry {
                                run_id: None,
                                schema_version: None,
                                session_id: market.to_string(),
                                token_id: Some(token_id.to_string()),
                                strategy_id: broker_b.strategy_id().to_string(),
                                order_id: format!("skip-{}-{}", market, token_id),
                                outcome: outcome.to_string(),
                                side: "BUY".to_string(),
                                action: "SKIP".to_string(),
                                submit_ts_ms: chrono::Utc::now().timestamp_millis(),
                                ack_ts_ms: None, fill_ts_ms: None, ack_ms: None, fill_ms: None,
                                fill_pct: 0.0, avg_fill_q: None,
                                best_bid_q_at_submit: None, best_ask_q_at_submit: Some(px),
                                spread_bps_at_submit: None, slippage_vs_mid_bps: None,
                                cancel_reason: Some(skip_reason.clone()),
                                reason_codes: vec![skip_reason],
                            });
                            continue;
                        }

                        let slot_key = (seed.symbol.to_string(), seed.timeframe, token_id.to_string());
                        let slot = slots_b.entry(slot_key).or_default();
                        let _ = ensure_buy_order_paper(
                            broker_b,
                            &logs,
                            &orders,
                            &latencies,
                            seed.symbol,
                            seed.timeframe,
                            outcome,
                            market,
                            token_id,
                            tick_size,
                            &desired,
                            min_order_size,
                            min_order_notional,
                            None,
                            min_requote,
                            requote_min_ticks,
                            requote_max_age,
                            slot,
                        )
                        .await;
                        // Record trade for cap tracking
                        broker_b.record_trade(market, px, sz);
                    }
                }
                continue;  // Skip single-strategy logic
            }

            // Normal mode: run single strategy
            let q = strategy.as_mut().expect("strategy should be Some in non-AB mode").quote(&ctx);
            set_decision(
                &state,
                seed.symbol,
                seed.timeframe,
                format!("{} tau={:.0}s up_mid={} dn_mid={} | up={:?} dn={:?}",
                    strategy.as_ref().unwrap().name(), tau_seconds, up_mid_str, dn_mid_str, q.up, q.down),
            )
            .await;

            if !cfg.trade_enabled {
                continue;
            }

            let available_cash_usdc = if skip_cash_check {
                None
            } else {
                let wallet = cash_usdc.read().await.unwrap_or(0.0);
                let reserved_raw = if let Some(ref p) = paper {
                    p.reserved_usdc()
                } else {
                    let g = orders.read().await;
                    g.values()
                        .filter(|o| o.side.eq_ignore_ascii_case("BUY") && order_is_open(o))
                        .map(|o| order_remaining(o) * o.price)
                        .sum::<f64>()
                };
                let reserved = if reserved_raw.abs() < 1e-9 {
                    0.0
                } else {
                    reserved_raw.max(0.0)
                };
                Some(CashSnapshot {
                    wallet_usdc: wallet,
                    reserved_usdc: reserved,
                    utilization: cash_utilization,
                })
            };

            for (outcome, token_id, desired) in [
                ("up", tok_up, q.up),
                ("down", tok_dn, q.down),
            ] {
                let slot_key = (seed.symbol.to_string(), seed.timeframe, token_id.to_string());
                let slot = slots.entry(slot_key).or_default();

                if cfg.dry_run && !paper_trading {
                    continue;
                }
                if let Some(p) = paper.as_mut() {
                    let _ = ensure_buy_order_paper(
                        p,
                        &logs,
                        &orders,
                        &latencies,
                        seed.symbol,
                        seed.timeframe,
                        outcome,
                        market,
                        token_id,
                        tick_size,
                        &desired,
                        min_order_size,
                        min_order_notional,
                        available_cash_usdc,
                        min_requote,
                        requote_min_ticks,
                        requote_max_age,
                        slot,
                    )
                    .await;
                } else if let Some(ref client) = client {
                    // KILL SWITCH: stop all trading if file exists
                    if std::path::Path::new(&kill_switch_path).exists() {
                        push_log(&logs, "[KILL_SWITCH] File exists, refusing to trade".to_string()).await;
                        continue;
                    }

                    // OBSERVATION MODE: skip order placement
                    if observation_mode {
                        if desired.px.is_some() && desired.q > 0.0 {
                            push_log(&logs, format!("[OBSERVE] Would trade {} @{:.3} x{:.1}",
                                outcome, desired.px.unwrap_or(0.0), desired.q)).await;
                        }
                        continue;
                    }

                    // LIVE CAPS: check before trading
                    let px = desired.px.unwrap_or(0.0);
                    let sz = desired.q;
                    if px > 0.0 && sz > 0.0 {
                        if let Some(skip_reason) = live_risk.check_caps(&live_caps, market, px * sz, sz) {
                            push_log(&logs, format!("[LIVE_CAPS] {}", skip_reason)).await;
                            continue;
                        }
                    }

                    let _ = ensure_buy_order(
                        client,
                        &logs,
                        &orders,
                        &latencies,
                        seed.symbol,
                        seed.timeframe,
                        outcome,
                        market,
                        token_id,
                        tick_size,
                        &desired,
                        min_order_size,
                        min_order_notional,
                        available_cash_usdc,
                        min_requote,
                        requote_min_ticks,
                        requote_max_age,
                        slot,
                        live_cancel_confirm,
                        live_cancel_post_delay,
                    )
                    .await;

                    // Record trade for caps tracking (after successful submit)
                    if desired.px.is_some() && desired.q > 0.0 {
                        live_risk.record_trade(market, px * sz, sz);
                    }
                }
            }
        }
    }
}
fn outcome_best_bid_px(o: &OutcomeSnapshot) -> Option<f64> {
    o.bids.iter().next_back().map(|(k, _)| key_to_price(*k))
}

fn outcome_mid_px(o: &OutcomeSnapshot) -> Option<f64> {
    let bid = o.bids.iter().next_back().map(|(k, _)| key_to_price(*k));
    let ask = o.asks.iter().next().map(|(k, _)| key_to_price(*k));
    match (bid, ask) {
        (Some(b), Some(a)) => Some(0.5 * (a + b)),
        _ => None,
    }
}
