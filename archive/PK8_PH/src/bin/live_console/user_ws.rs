use super::*;

pub(crate) fn signature_type_from_env() -> PmSignatureType {
    let raw: u8 = std::env::var("PM_SIGNATURE_TYPE")
        .ok()
        .and_then(|v| v.parse::<u8>().ok())
        .unwrap_or(1);
    PmSignatureType::from_u8(raw).unwrap_or(PmSignatureType::PolyProxy)
}

pub(crate) fn funder_address_from_env() -> Option<PmAddress> {
    let v = std::env::var("PM_LIVE_WALLET_ADDRESS")
        .or_else(|_| std::env::var("PM_FUNDER_ADDRESS"))
        .ok()?;
    PmAddress::from_str(v.trim()).ok()
}

#[derive(Debug, serde::Deserialize)]
struct UserOrderMsg {
    id: String,
    market: String,
    asset_id: String,
    price: String,
    side: String,
    original_size: String,
    size_matched: String,
    timestamp: String,
    #[serde(rename = "type")]
    kind: String,
    outcome: Option<String>,
}

#[derive(Debug, serde::Deserialize)]
struct UserTradeMsg {
    id: String,
    asset_id: String,
    taker_order_id: Option<String>,
    maker_orders: Option<Vec<UserTradeMakerOrder>>,
    price: String,
    side: String,
    size: String,
    status: Option<String>,
    timestamp: String,
}

#[derive(Debug, Clone, serde::Deserialize)]
struct UserTradeMakerOrder {
    asset_id: Option<String>,
    matched_amount: String,
    order_id: String,
    price: String,
}

pub(crate) async fn user_ws_loop(
    logs: SharedLogs,
    orders: SharedOrders,
    positions: SharedPositions,
    models: SharedModels,
    seen_trades: SharedSeenTrades,
    seen_trade_fills: SharedSeenTradeFills,
    latencies: SharedLatency,
    auth: UserWsAuth,
) -> Result<()> {
    let url = std::env::var("PM_USER_WS_URL")
        .unwrap_or_else(|_| "wss://ws-subscriptions-clob.polymarket.com/ws/user".to_string());
    let ws = UserWs::new(&url)?;
    push_log(&logs, format!("[user-ws] connecting {url}")).await;
    let log_raw = std::env::var("PM_USER_WS_LOG")
        .ok()
        .is_some_and(|v| v == "1" || v.eq_ignore_ascii_case("true"));
    let log_raw_max: usize = std::env::var("PM_USER_WS_LOG_MAX")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(400);

    let (_cancel_tx, cancel_rx) = tokio::sync::watch::channel(false);

    ws.stream_user(
        vec![],
        auth,
        cancel_rx,
        |msg| {
            if log_raw {
                let line = if msg.len() > log_raw_max {
                    format!("{}...", &msg[..log_raw_max.saturating_sub(3)])
                } else {
                    msg.clone()
                };
                let logs = Arc::clone(&logs);
                tokio::spawn(async move {
                    push_log(&logs, format!("[user-ws] raw={line}")).await;
                });
            }

            let Ok(v) = serde_json::from_str::<serde_json::Value>(&msg) else {
                return;
            };
            let items: Vec<serde_json::Value> = match v {
                serde_json::Value::Array(arr) => arr,
                serde_json::Value::Object(_) => vec![v],
                _ => return,
            };

            for item in items {
                let Some(event_type) = item.get("event_type").and_then(|v| v.as_str()) else {
                    continue;
                };
                match event_type {
                    "order" => {
                        if let Ok(o) = serde_json::from_value::<UserOrderMsg>(item) {
                            let orders = Arc::clone(&orders);
                            let logs = Arc::clone(&logs);
                            let models = Arc::clone(&models);
                            let latencies = Arc::clone(&latencies);
                            let positions = Arc::clone(&positions);
                            tokio::spawn(async move {
                                apply_user_order(orders, logs, positions, models, latencies, o)
                                    .await;
                            });
                        }
                    }
                    "trade" => {
                        if let Ok(t) = serde_json::from_value::<UserTradeMsg>(item) {
                            let orders = Arc::clone(&orders);
                            let logs = Arc::clone(&logs);
                            let positions = Arc::clone(&positions);
                            let models = Arc::clone(&models);
                            let seen_trades = Arc::clone(&seen_trades);
                            let seen_trade_fills = Arc::clone(&seen_trade_fills);
                            let latencies = Arc::clone(&latencies);
                            tokio::spawn(async move {
                                apply_user_trade(
                                    orders,
                                    logs,
                                    positions,
                                    models,
                                    latencies,
                                    seen_trades,
                                    seen_trade_fills,
                                    t,
                                )
                                .await;
                            });
                        }
                    }
                    _ => {}
                }
            }
        },
        |rtt_ms| {
            let logs = Arc::clone(&logs);
            tokio::spawn(async move {
                push_log(&logs, format!("[user-ws] rtt={rtt_ms}ms")).await;
            });
        },
    )
    .await
}

async fn apply_user_order(
    orders: SharedOrders,
    logs: SharedLogs,
    positions: SharedPositions,
    models: SharedModels,
    latencies: SharedLatency,
    msg: UserOrderMsg,
) {
    let now = chrono::Utc::now();
    let event_ts = parse_ts_utc(&msg.timestamp).unwrap_or(now);
    let price = msg.price.parse::<f64>().unwrap_or(0.0);
    let original_size = msg.original_size.parse::<f64>().unwrap_or(0.0);
    let size_matched = msg.size_matched.parse::<f64>().unwrap_or(0.0);

    let mut to_log: Vec<String> = Vec::new();
    let mut maybe_place_ms: Option<i64> = None;
    {
        let mut guard = orders.write().await;
        let key = canon_id(&msg.id);
        let entry = guard.entry(key.clone()).or_insert_with(|| OrderSnapshot {
            order_id: key.clone(),
            symbol: "".to_string(),
            timeframe: Timeframe::M15,
            outcome: msg.outcome.clone().unwrap_or_default(),
            token_id: msg.asset_id.clone(),
            market: msg.market.clone(),
            side: msg.side.clone(),
            price,
            original_size,
            size_matched: 0.0,
            last_event: msg.kind.clone(),
            last_update_at: now,
            submitted_at: None,
            placed_at: None,
            // Telemetry fields (unknown for externally-created orders)
            submit_ts_ms: None,
            best_bid_at_submit: None,
            best_ask_at_submit: None,
            strategy_id: None,
            reason_codes: vec![],
        });

        let prev_matched = entry.size_matched;
        entry.token_id = msg.asset_id;
        entry.market = msg.market;
        entry.side = msg.side;
        entry.price = price;
        entry.original_size = original_size;
        entry.size_matched = entry.size_matched.max(size_matched);
        entry.last_event = msg.kind.clone();
        entry.last_update_at = now;

        if entry.size_matched > prev_matched + 1e-9 {
            let delta = entry.size_matched - prev_matched;
            to_log.push(format!(
                "[fill] {} matched +{:.3} => {:.3}/{:.3}",
                entry.order_id, delta, entry.size_matched, original_size
            ));
        }

        if msg.kind.eq_ignore_ascii_case("PLACEMENT") && entry.placed_at.is_none() {
            entry.placed_at = Some(now);
            let ack_ts_ms = now.timestamp_millis();
            let ack_ms = entry.submit_ts_ms.map(|sub| (ack_ts_ms - sub).max(0));

            if let Some(sub) = entry.submitted_at {
                let ms = (now - sub).num_milliseconds();
                maybe_place_ms = Some(ms.max(0));
                to_log.push(format!(
                    "[order] {} placed in {}ms",
                    entry.order_id,
                    ms.max(0)
                ));
            } else {
                to_log.push(format!(
                    "[order] {} placed (matched={:.3}/{:.3})",
                    entry.order_id, entry.size_matched, entry.original_size
                ));
            }

            // Emit ACK event to orders.jsonl
            emit_order_event(&OrderTelemetry {
                run_id: None,
                schema_version: None,
                session_id: entry.market.clone(),
                token_id: Some(entry.token_id.clone()),
                strategy_id: entry.strategy_id.clone().unwrap_or_default(),
                order_id: entry.order_id.clone(),
                outcome: entry.outcome.clone(),
                side: entry.side.clone(),
                action: "ACK".to_string(),
                submit_ts_ms: entry.submit_ts_ms.unwrap_or(0),
                ack_ts_ms: Some(ack_ts_ms),
                fill_ts_ms: None,
                ack_ms,
                fill_ms: None,
                fill_pct: if entry.original_size > 0.0 { entry.size_matched / entry.original_size * 100.0 } else { 0.0 },
                avg_fill_q: None,
                best_bid_q_at_submit: entry.best_bid_at_submit,
                best_ask_q_at_submit: entry.best_ask_at_submit,
                spread_bps_at_submit: None,
                slippage_vs_mid_bps: None,
                cancel_reason: None,
                reason_codes: entry.reason_codes.clone(),
            });
        }

        // Emit CANCEL event on CANCELED/CANCELLED
        let kind_upper = msg.kind.to_uppercase();
        if kind_upper == "CANCELED" || kind_upper == "CANCELLED" {
            let cancel_ts_ms = now.timestamp_millis();
            let fill_pct = if entry.original_size > 0.0 { entry.size_matched / entry.original_size * 100.0 } else { 0.0 };
            emit_order_event(&OrderTelemetry {
                run_id: None,
                schema_version: None,
                session_id: entry.market.clone(),
                token_id: Some(entry.token_id.clone()),
                strategy_id: entry.strategy_id.clone().unwrap_or_default(),
                order_id: entry.order_id.clone(),
                outcome: entry.outcome.clone(),
                side: entry.side.clone(),
                action: "CANCEL".to_string(),
                submit_ts_ms: entry.submit_ts_ms.unwrap_or(0),
                ack_ts_ms: None,
                fill_ts_ms: Some(cancel_ts_ms),
                ack_ms: None,
                fill_ms: entry.submit_ts_ms.map(|sub| (cancel_ts_ms - sub).max(0)),
                fill_pct,
                avg_fill_q: None,
                best_bid_q_at_submit: entry.best_bid_at_submit,
                best_ask_q_at_submit: entry.best_ask_at_submit,
                spread_bps_at_submit: None,
                slippage_vs_mid_bps: None,
                cancel_reason: Some("USER_CANCEL".to_string()),
                reason_codes: entry.reason_codes.clone(),
            });
            to_log.push(format!(
                "[order] {} canceled (filled {:.1}%)",
                entry.order_id, fill_pct
            ));
        }
    }

    for line in to_log {
        push_log(&logs, line).await;
    }

    if let Some(ms) = maybe_place_ms {
        let mut g = latencies.write().await;
        g.last_place_ms = Some(ms);
        LatencyStats::bump(&mut g.max_place_ms, ms);
    }
    // Positions and toxicity updates are derived from trade messages to match actual executions.
    let _ = (positions, models, event_ts);
}

fn outcome_sign(outcome: &str) -> f64 {
    match outcome.trim().to_lowercase().as_str() {
        "up" | "yes" => 1.0,
        "down" | "no" => -1.0,
        _ => 0.0,
    }
}

async fn apply_position_fill(
    positions: &SharedPositions,
    token_id: String,
    side: String,
    qty: f64,
    px: f64,
) {
    if qty <= 1e-9 || px <= 0.0 {
        return;
    }
    let signed = if side.eq_ignore_ascii_case("BUY") {
        qty
    } else {
        -qty
    };
    if signed.abs() <= 1e-9 {
        return;
    }
    let now = chrono::Utc::now();
    let mut guard = positions.write().await;
    let entry = guard.entry(token_id).or_insert_with(|| PositionSnapshot {
        size: 0.0,
        avg_price: 0.0,
        updated_at: now,
    });
    let prev_size = entry.size;
    let new_size = prev_size + signed;
    if prev_size.abs() < 1e-9 {
        entry.avg_price = px;
    } else if prev_size.signum() == signed.signum() {
        let prev_cost = entry.avg_price * prev_size.abs();
        let add_cost = px * signed.abs();
        entry.avg_price = (prev_cost + add_cost) / (prev_size.abs() + signed.abs());
    } else if new_size.abs() < 1e-9 {
        entry.avg_price = 0.0;
    }
    entry.size = new_size;
    entry.updated_at = now;
}

async fn apply_user_trade(
    orders: SharedOrders,
    logs: SharedLogs,
    positions: SharedPositions,
    models: SharedModels,
    latencies: SharedLatency,
    seen_trades: SharedSeenTrades,
    seen_trade_fills: SharedSeenTradeFills,
    msg: UserTradeMsg,
) {
    let now = chrono::Utc::now();
    let event_ts = parse_ts_utc(&msg.timestamp).unwrap_or(now);
    let include_history = std::env::var("PM_POSITION_INCLUDE_HISTORY")
        .ok()
        .is_some_and(|v| v == "1" || v.eq_ignore_ascii_case("true"));
    if !include_history && event_ts < run_start_utc() {
        return;
    }

    let price_msg = msg.price.parse::<f64>().unwrap_or(0.0);
    let size_msg = msg.size.parse::<f64>().unwrap_or(0.0);
    if size_msg <= 0.0 {
        return;
    }

    let status_key = msg.status.clone().unwrap_or_default();
    let status_upper = status_key.to_uppercase();
    let latency_kind: Option<&'static str> = match status_upper.as_str() {
        "MINED" => Some("MINED"),
        "CONFIRMED" => Some("CONFIRMED"),
        _ => None,
    };
    {
        let dedupe_key = format!("{}:{status_key}", msg.id);
        let mut g = seen_trades.write().await;
        if g.contains(&dedupe_key) {
            return;
        }
        g.insert(dedupe_key);
        if g.len() > 50_000 {
            g.clear();
        }
    }

    let log_all = std::env::var("PM_USER_WS_TRADE_LOG_ALL")
        .ok()
        .is_some_and(|v| v == "1" || v.eq_ignore_ascii_case("true"));

    // Attribute this trade to our orders (maker vs taker). If it doesn't touch our orders,
    // ignore it by default to avoid confusing logs with unrelated market prints.
    let mut fills: Vec<(
        String, /*token*/
        String, /*side*/
        f64,    /*qty*/
        f64,    /*px*/
    )> = Vec::new();
    let mut model_signals: Vec<((String, Timeframe), f64)> = Vec::new();
    let mut role: &'static str = "OTHER";
    let mut touched = false;
    let mut latency_updates: Vec<(Option<&'static str>, i64)> = Vec::new();

    if let Some(taker_id) = msg.taker_order_id.as_deref() {
        let key = canon_id(taker_id);
        let mut g = orders.write().await;
        if let Some(o) = g.get_mut(&key) {
            role = "TAKER";
            touched = true;
            let q = size_msg.max(0.0);
            let px = price_msg.max(0.0);
            if q > 0.0 && px > 0.0 {
                // Prefer the ws-provided asset_id for correctness on restarts/rollovers.
                o.token_id = msg.asset_id.clone();
                fills.push((o.token_id.clone(), msg.side.clone(), q, px));
                let prev_matched = o.size_matched;
                if o.original_size > 0.0 {
                    o.size_matched =
                        (o.size_matched + q).min(o.original_size).max(o.size_matched);
                } else {
                    o.size_matched = (o.size_matched + q).max(o.size_matched);
                }
                o.last_update_at = now;
                if o.symbol.len() > 0 && o.side.eq_ignore_ascii_case("BUY") {
                    let s = outcome_sign(&o.outcome);
                    if s != 0.0 {
                        model_signals.push(((o.symbol.clone(), o.timeframe), s));
                    }
                }
                if let (Some(kind), Some(sub)) = (latency_kind, o.submitted_at) {
                    let ms = (event_ts - sub).num_milliseconds().max(0);
                    latency_updates.push((Some(kind), ms));
                }

                // Emit FILL event (only if size actually increased)
                if o.size_matched > prev_matched + 1e-9 {
                    let fill_ts_ms = now.timestamp_millis();
                    let fill_ms = o.submit_ts_ms.map(|sub| (fill_ts_ms - sub).max(0));
                    let fill_pct = if o.original_size > 0.0 { o.size_matched / o.original_size * 100.0 } else { 0.0 };
                    emit_order_event(&OrderTelemetry {
                        run_id: None,
                        schema_version: None,
                        session_id: o.market.clone(),
                        token_id: Some(o.token_id.clone()),
                        strategy_id: o.strategy_id.clone().unwrap_or_default(),
                        order_id: o.order_id.clone(),
                        outcome: o.outcome.clone(),
                        side: o.side.clone(),
                        action: "FILL".to_string(),
                        submit_ts_ms: o.submit_ts_ms.unwrap_or(0),
                        ack_ts_ms: None,
                        fill_ts_ms: Some(fill_ts_ms),
                        ack_ms: None,
                        fill_ms,
                        fill_pct,
                        avg_fill_q: Some(px),
                        best_bid_q_at_submit: o.best_bid_at_submit,
                        best_ask_q_at_submit: o.best_ask_at_submit,
                        spread_bps_at_submit: None,
                        slippage_vs_mid_bps: None,
                        cancel_reason: None,
                        reason_codes: o.reason_codes.clone(),
                    });
                }
            }
        }
    }

    if let Some(makers) = msg.maker_orders.as_ref() {
        let mut any_ours = false;
        let mut g = orders.write().await;
        for m in makers {
            let q = m.matched_amount.parse::<f64>().unwrap_or(0.0).max(0.0);
            let px = m.price.parse::<f64>().unwrap_or(price_msg).max(0.0);
            if q <= 0.0 || px <= 0.0 {
                continue;
            }

            let key = canon_id(&m.order_id);
            if let Some(o) = g.get_mut(&key) {
                any_ours = true;
                touched = true;
                if let Some(asset_id) = m.asset_id.as_ref() {
                    if !asset_id.is_empty() {
                        o.token_id = asset_id.clone();
                    }
                }
                fills.push((o.token_id.clone(), o.side.clone(), q, px));
                let prev_matched = o.size_matched;
                if o.original_size > 0.0 {
                    o.size_matched = (o.size_matched + q)
                        .min(o.original_size)
                        .max(o.size_matched);
                } else {
                    o.size_matched = (o.size_matched + q).max(o.size_matched);
                }
                o.last_update_at = now;
                if o.symbol.len() > 0 && o.side.eq_ignore_ascii_case("BUY") {
                    let s = outcome_sign(&o.outcome);
                    if s != 0.0 {
                        model_signals.push(((o.symbol.clone(), o.timeframe), s));
                    }
                }
                if let (Some(kind), Some(sub)) = (latency_kind, o.submitted_at) {
                    let ms = (event_ts - sub).num_milliseconds().max(0);
                    latency_updates.push((Some(kind), ms));
                }

                // Emit FILL event for maker (only if size actually increased)
                if o.size_matched > prev_matched + 1e-9 {
                    let fill_ts_ms = now.timestamp_millis();
                    let fill_ms = o.submit_ts_ms.map(|sub| (fill_ts_ms - sub).max(0));
                    let fill_pct = if o.original_size > 0.0 { o.size_matched / o.original_size * 100.0 } else { 0.0 };
                    emit_order_event(&OrderTelemetry {
                        run_id: None,
                        schema_version: None,
                        session_id: o.market.clone(),
                        token_id: Some(o.token_id.clone()),
                        strategy_id: o.strategy_id.clone().unwrap_or_default(),
                        order_id: o.order_id.clone(),
                        outcome: o.outcome.clone(),
                        side: o.side.clone(),
                        action: "FILL".to_string(),
                        submit_ts_ms: o.submit_ts_ms.unwrap_or(0),
                        ack_ts_ms: None,
                        fill_ts_ms: Some(fill_ts_ms),
                        ack_ms: None,
                        fill_ms,
                        fill_pct,
                        avg_fill_q: Some(px),
                        best_bid_q_at_submit: o.best_bid_at_submit,
                        best_ask_q_at_submit: o.best_ask_at_submit,
                        spread_bps_at_submit: None,
                        slippage_vs_mid_bps: None,
                        cancel_reason: None,
                        reason_codes: o.reason_codes.clone(),
                    });
                }
            }
        }

        if any_ours {
            role = "MAKER";
        }
    }

    // Apply position updates once per trade id (ignore later MINED/CONFIRMED repeats).
    // Important: only consume this dedupe if we actually apply position updates, otherwise we can
    // permanently miss fills when an early status arrives before we recognize our order ids.
    let apply_fill = if fills.is_empty() {
        false
    } else {
        let mut g = seen_trade_fills.write().await;
        if g.contains(&msg.id) {
            false
        } else {
            g.insert(msg.id.clone());
            if g.len() > 50_000 {
                g.clear();
            }
            true
        }
    };
    if apply_fill {
        for (token, side, qty, px) in &fills {
            apply_position_fill(&positions, token.clone(), side.clone(), *qty, *px).await;
        }
    }
    if !model_signals.is_empty() {
        let mut g = models.write().await;
        for ((symbol, tf), s) in model_signals {
            let m = g.entry((symbol, tf)).or_insert_with(ProbToxModel::new);
            m.on_fill(event_ts, s);
        }
    }

    if !latency_updates.is_empty() {
        let mut g = latencies.write().await;
        for (kind, ms) in latency_updates {
            match kind {
                Some("MINED") => {
                    g.last_trade_mined_ms = Some(ms);
                    LatencyStats::bump(&mut g.max_trade_mined_ms, ms);
                }
                Some("CONFIRMED") => {
                    g.last_trade_confirm_ms = Some(ms);
                    LatencyStats::bump(&mut g.max_trade_confirm_ms, ms);
                }
                _ => {}
            }
        }
    }

    if (!fills.is_empty() && (touched || log_all)) || log_all {
        let tot_qty: f64 = fills.iter().map(|(_, _, q, _)| *q).sum();
        let tot_cost: f64 = fills.iter().map(|(_, _, q, p)| *q * *p).sum();
        let px = if tot_qty > 0.0 {
            tot_cost / tot_qty
        } else {
            0.0
        };
        let side = fills
            .first()
            .map(|(_, s, _, _)| s.as_str())
            .unwrap_or(&msg.side);
        push_log(
            &logs,
            format!(
                "[trade][ws] {} {} @ {:.3} size {:.3} ({} {})",
                msg.asset_id,
                side,
                px,
                tot_qty.max(0.0),
                status_key,
                role
            ),
        )
        .await;
        if apply_fill && !fills.is_empty() && (touched || log_all) {
            emit_jsonl_event(
                "live_fill",
                serde_json::json!({
                    "asset_id": msg.asset_id,
                    "side": side,
                    "avg_price": px,
                    "total_qty": tot_qty.max(0.0),
                    "status": status_key,
                    "role": role,
                    "fills": fills.iter().map(|(token, side, qty, px)| {
                        serde_json::json!({
                            "token_id": token,
                            "side": side,
                            "qty": qty,
                            "price": px,
                        })
                    }).collect::<Vec<_>>(),
                }),
            );
        }
    }
}
