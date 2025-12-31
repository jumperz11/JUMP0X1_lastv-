use super::*;

pub(crate) async fn stream_runner(
    gamma: GammaClient,
    ws: MarketWs,
    state: SharedState,
    logs: SharedLogs,
    seed: InstrumentSeed,
    tick_tx: Option<TradeTickTx>,
) -> Result<()> {
    let paper_trading = std::env::var("PM_PAPER_TRADING")
        .ok()
        .is_some_and(|v| v == "1" || v.eq_ignore_ascii_case("true"));
    let paper_require_trade_print = std::env::var("PM_PAPER_FLOW_REQUIRE_TRADE_PRINT")
        .ok()
        .is_some_and(|v| v == "1" || v.eq_ignore_ascii_case("true"));
    let mut use_orders_matched = std::env::var("PM_LIVE_DATA_ORDERS_MATCHED")
        .ok()
        .map(|v| v != "0" && !v.eq_ignore_ascii_case("false"))
        .unwrap_or(paper_trading);
    if paper_require_trade_print {
        use_orders_matched = true;
    }

    let now = chrono::Utc::now();
    let tick_key: TradeTick = (seed.symbol.to_string(), seed.timeframe);

    let mut current = match seed.timeframe {
        Timeframe::M15 => {
            let prefix = seed
                .timestamp_prefix
                .context("missing timestamp prefix for 15m")?;
            let slug = rollover::derive_current_timestamp_slug(prefix, "15m", now)
                .context("failed to derive current 15m slug")?;
            gamma
                .get_event_by_slug(&slug)
                .await
                .with_context(|| format!("gamma get slug={slug}"))?
        }
        Timeframe::H1 => {
            let series_id = seed.series_id.context("missing series_id for 1h")?;
            rollover::resolve_current_in_series(&gamma, series_id, now)
                .await?
                .context("no current event found in series")?
        }
    };
    push_log(
        &logs,
        format!(
            "[{} {}] start: {}",
            seed.symbol,
            seed.timeframe.label(),
            current.slug
        ),
    )
    .await;

    loop {
        let meta_changed = set_market_metadata(&state, &seed.symbol, seed.timeframe, &current).await;
        if meta_changed {
            if let Some(tx) = tick_tx.as_ref() {
                let _ = tx.send(tick_key.clone());
            }
        }

        let token_by_asset = invert_token_ids(&current);
        let token_ids = current.token_ids();
        if token_ids.is_empty() {
            set_error(
                &state,
                &seed.symbol,
                seed.timeframe,
                format!("no clobTokenIds for slug={}", current.slug),
            )
            .await;
            push_log(
                &logs,
                format!(
                    "[{} {}] no clobTokenIds: {}",
                    seed.symbol,
                    seed.timeframe.label(),
                    current.slug
                ),
            )
            .await;
            tokio::time::sleep(Duration::from_secs(3)).await;
            current = gamma.get_event_by_slug(&current.slug).await?;
            continue;
        }

        let next_event = if current.series_id.is_some() && current.end_date.is_some() {
            rollover::resolve_next_in_series(&gamma, &current, 20_000).await?
        } else {
            None
        };

        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<WsUpdate>();
        let state_worker = Arc::clone(&state);
        let key_worker = tick_key.clone();
        let tick_tx_worker = tick_tx.clone();
        tokio::spawn(async move {
            while let Some(update) = rx.recv().await {
                let tick_needed =
                    apply_ws_update(Arc::clone(&state_worker), &key_worker, update).await;
                if tick_needed {
                    if let Some(tx) = tick_tx_worker.as_ref() {
                        let _ = tx.send(key_worker.clone());
                    }
                }
            }
        });

        let (cancel_tx, cancel_rx) = tokio::sync::watch::channel(false);

        set_ws_connected(&state, &seed.symbol, seed.timeframe, true).await;
        push_log(
            &logs,
            format!(
                "[{} {}] subscribed: {}",
                seed.symbol,
                seed.timeframe.label(),
                current.slug
            ),
        )
        .await;

        let token_by_asset_ws = token_by_asset.clone();
        let tx_ws = tx.clone();
        let tx_rtt = tx.clone();
        let mut ws_task = tokio::spawn({
            let ws = ws.clone();
            async move {
                ws.stream_market(
                    token_ids,
                    cancel_rx,
                    move |msg| {
                        // Keep parsing cheap; ignore messages we don't recognize.
                        let Ok(v) = serde_json::from_str::<serde_json::Value>(&msg) else {
                            return;
                        };
                        let items: Vec<serde_json::Value> = match v {
                            serde_json::Value::Array(arr) => arr,
                            serde_json::Value::Object(_) => vec![v],
                            _ => return,
                        };

                        for item in items {
                            let Some(event_type) = item.get("event_type").and_then(|v| v.as_str())
                            else {
                                continue;
                            };
                            match event_type {
                                "book" => {
                                    if let Ok(book) = serde_json::from_value::<WsBookMsg>(item) {
                                        let outcome = token_by_asset_ws
                                            .get(&book.asset_id)
                                            .cloned()
                                            .unwrap_or_else(|| "UNK".to_string());
                                        let server_latency_ms = book
                                            .timestamp
                                            .as_deref()
                                            .and_then(|s| s.parse::<i64>().ok())
                                            .map(|ts_ms| {
                                                (chrono::Utc::now().timestamp_millis() - ts_ms)
                                                    .max(0)
                                            });
                                        let _ = tx_ws.send(WsUpdate::FullBook {
                                            outcome,
                                            book,
                                            server_latency_ms,
                                        });
                                    }
                                }
                                "price_change" => {
                                    if let Ok(pc) = serde_json::from_value::<WsPriceChangeMsg>(item)
                                    {
                                        let server_latency_ms = pc
                                            .timestamp
                                            .as_deref()
                                            .and_then(|s| s.parse::<i64>().ok())
                                            .map(|ts_ms| {
                                                (chrono::Utc::now().timestamp_millis() - ts_ms)
                                                    .max(0)
                                            });
                                        for ch in &pc.price_changes {
                                            let outcome = token_by_asset_ws
                                                .get(&ch.asset_id)
                                                .cloned()
                                                .unwrap_or_else(|| "UNK".to_string());
                                            let _ = tx_ws.send(WsUpdate::PriceChange {
                                                outcome,
                                                change: ch.clone(),
                                                server_latency_ms,
                                            });
                                        }
                                    }
                                }
                                "last_trade_price" => {
                                    if let Ok(lt) =
                                        serde_json::from_value::<WsLastTradePriceMsg>(item)
                                    {
                                        let outcome = token_by_asset_ws
                                            .get(&lt.asset_id)
                                            .cloned()
                                            .unwrap_or_else(|| "UNK".to_string());
                                        let server_latency_ms = lt
                                            .timestamp
                                            .as_deref()
                                            .and_then(|s| s.parse::<i64>().ok())
                                            .map(|ts_ms| {
                                                (chrono::Utc::now().timestamp_millis() - ts_ms)
                                                    .max(0)
                                            });
                                        let ts_ms = lt
                                            .timestamp
                                            .as_deref()
                                            .and_then(|s| s.parse::<i64>().ok());
                                        let _ = tx_ws.send(WsUpdate::LastTrade {
                                            outcome,
                                            price: lt.price,
                                            size: lt.size,
                                            side: lt.side,
                                            ts_ms,
                                            server_latency_ms,
                                        });
                                    }
                                }
                                _ => {}
                            }
                        }
                    },
                    move |rtt_ms| {
                        let _ = tx_rtt.send(WsUpdate::Rtt { ms: rtt_ms });
                    },
                )
                .await
            }
        });

        let mut orders_matched_task = if use_orders_matched {
            let live_ws = LiveDataWs::new("wss://ws-live-data.polymarket.com/")?;
            let symbol = seed.symbol;
            let tf = seed.timeframe;
            let slug = current.slug.clone();
            let slug_for_parse = slug.clone();
            let tx_ws = tx.clone();
            let cancel_rx = cancel_tx.subscribe();
            push_log(
                &logs,
                format!(
                    "[{} {}] live-data subscribed: {}",
                    seed.symbol,
                    seed.timeframe.label(),
                    current.slug
                ),
            )
            .await;
            Some(tokio::spawn(async move {
                live_ws
                    .stream_orders_matched(
                        slug.clone(),
                        cancel_rx,
                        move |msg| {
                            let Ok(v) = serde_json::from_str::<serde_json::Value>(&msg) else {
                                return;
                            };
                                let Some(trade) = parse_orders_matched_trade(&v) else {
                                    return;
                                };
                                if trade
                                    .event_slug
                                    .as_deref()
                                    .is_some_and(|s| s != slug_for_parse.as_str())
                                {
                                    return;
                                }

                                let outcome = trade.outcome.clone();
                                let side = trade.side.clone();
                                let price = trade.price;
                                let size = trade.size;
                                let ts_ms = trade.ts_ms;
                                let username = trade.username.clone();
                                let pseudonym = trade.pseudonym.clone();
                                let token_id = trade.token_id.clone();

                                let server_latency_ms = ts_ms
                                    .map(|ts| (chrono::Utc::now().timestamp_millis() - ts).max(0));
                                let _ = tx_ws.send(WsUpdate::LastTrade {
                                    outcome,
                                    price: price.to_string(),
                                    size: Some(size.to_string()),
                                    side: Some(side.clone()),
                                    ts_ms,
                                    server_latency_ms,
                                });

                                let _ = (username, pseudonym, token_id);
                            },
                            |_rtt_ms| {},
                        )
                        .await
                    .with_context(|| {
                        format!(
                            "orders_matched ws failed (symbol={} tf={} slug={})",
                            symbol,
                            tf.label(),
                            slug,
                        )
                    })?;
                Ok::<_, anyhow::Error>(())
            }))
        } else {
            None
        };

        let mut refresh = tokio::time::interval(Duration::from_secs(30));
        refresh.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        let mut rollover_sleep: Option<Pin<Box<tokio::time::Sleep>>> =
            current.end_date.map(|end| {
                Box::pin(tokio::time::sleep(duration_until(
                    end - chrono::Duration::seconds(1),
                )))
            });

        let mut rollover_triggered = false;
        loop {
            tokio::select! {
                _ = refresh.tick() => {
                    if let Ok(ev) = gamma.get_event_by_slug(&current.slug).await {
                        set_liquidity(&state, &seed.symbol, seed.timeframe, ev.liquidity_clob, ev.end_date).await;
                    }
                }
                _ = async {
                    if let Some(s) = &mut rollover_sleep {
                        s.as_mut().await
                    } else {
                        futures_util::future::pending::<()>().await
                    }
                } => {
                    rollover_triggered = true;
                    break;
                }
                res = &mut ws_task => {
                    match res {
                        Ok(Ok(())) => {}
                        Ok(Err(e)) => {
                            set_error(&state, &seed.symbol, seed.timeframe, format!("{e:#}")).await;
                            push_log(&logs, format!("[{} {}] ws error: {e}", seed.symbol, seed.timeframe.label())).await;
                            tokio::time::sleep(Duration::from_secs(2)).await;
                        }
                        Err(join_err) => {
                            set_error(&state, &seed.symbol, seed.timeframe, format!("ws task join error: {join_err}")).await;
                        }
                    }
                    break;
                }
                res = async { orders_matched_task.as_mut().unwrap().await }, if orders_matched_task.is_some() => {
                    match res {
                        Ok(Ok(())) => {}
                        Ok(Err(e)) => {
                            push_log(&logs, format!("[{} {}] orders_matched ws error: {e}", seed.symbol, seed.timeframe.label())).await;
                            tokio::time::sleep(Duration::from_secs(2)).await;
                        }
                        Err(join_err) => {
                            push_log(&logs, format!("[{} {}] orders_matched ws join error: {join_err}", seed.symbol, seed.timeframe.label())).await;
                        }
                    }
                    break;
                }
            }
        }

        let _ = cancel_tx.send(true);
        // `ws_task` may already have been awaited via `tokio::select!` above; avoid double-poll.
        if !ws_task.is_finished() {
            let _ = ws_task.await;
        }
        if let Some(t) = orders_matched_task.as_mut() {
            if !t.is_finished() {
                let _ = t.await;
            }
        }
        set_ws_connected(&state, &seed.symbol, seed.timeframe, false).await;

        if let Some(end) = current.end_date {
            if end <= chrono::Utc::now() + chrono::Duration::seconds(1) {
                if let Some(next) = next_event {
                    push_log(
                        &logs,
                        format!(
                            "[{} {}] rollover: {} -> {}",
                            seed.symbol,
                            seed.timeframe.label(),
                            current.slug,
                            next.slug
                        ),
                    )
                    .await;
                    current = gamma.get_event_by_slug(&next.slug).await?;
                    continue;
                }
            }
        }
        if rollover_triggered {
            if let Some(next) = next_event {
                push_log(
                    &logs,
                    format!(
                        "[{} {}] rollover: {} -> {}",
                        seed.symbol,
                        seed.timeframe.label(),
                        current.slug,
                        next.slug
                    ),
                )
                .await;
                current = gamma.get_event_by_slug(&next.slug).await?;
                continue;
            }
        }

        // 15m markets use timestamp-based slugs; roll forward by re-deriving the current slug.
        if seed.timeframe == Timeframe::M15 {
            if let Some(prefix) = seed.timestamp_prefix {
                let derived = rollover::derive_current_timestamp_slug(prefix, "15m", chrono::Utc::now())
                    .context("failed to derive current 15m slug")?;
                if derived != current.slug {
                    push_log(
                        &logs,
                        format!(
                            "[{} {}] rollover: {} -> {}",
                            seed.symbol,
                            seed.timeframe.label(),
                            current.slug,
                            derived
                        ),
                    )
                    .await;
                    current = gamma.get_event_by_slug(&derived).await?;
                    continue;
                }
            }
        }

        // If we dropped the ws without a rollover condition, refresh the same event.
        current = gamma.get_event_by_slug(&current.slug).await?;
    }
}

async fn set_liquidity(
    state: &SharedState,
    symbol: &str,
    tf: Timeframe,
    liquidity_clob: Option<f64>,
    end_date: Option<chrono::DateTime<chrono::Utc>>,
) {
    let mut guard = state.write().await;
    if let Some(s) = guard.get_mut(&(symbol.to_string(), tf)) {
        if liquidity_clob.is_some() {
            s.liquidity_clob = liquidity_clob;
        }
        if end_date.is_some() {
            s.end_date = end_date;
        }
    }
}

fn duration_until(target: chrono::DateTime<chrono::Utc>) -> std::time::Duration {
    let now = chrono::Utc::now();
    if target <= now {
        return std::time::Duration::from_secs(0);
    }
    (target - now)
        .to_std()
        .unwrap_or_else(|_| std::time::Duration::from_secs(0))
}

fn invert_token_ids(ev: &MarketEvent) -> HashMap<String, String> {
    let mut out = HashMap::new();
    for (outcome, token_id) in &ev.token_ids_by_outcome {
        out.insert(token_id.clone(), outcome.clone());
    }
    out
}

async fn set_market_metadata(
    state: &SharedState,
    symbol: &str,
    tf: Timeframe,
    ev: &MarketEvent,
) -> bool {
    let mut changed = false;
    let mut guard = state.write().await;
    if let Some(s) = guard.get_mut(&(symbol.to_string(), tf)) {
        let prev_condition_id = s.condition_id.clone();
        let prev_tok_up = s.token_up.clone();
        let prev_tok_down = s.token_down.clone();
        s.slug = ev.slug.clone();
        s.condition_id = ev.condition_id.clone();
        s.order_min_size = ev.order_min_size;
        s.min_tick_size = ev.min_tick_size;
        s.token_up = ev.token_ids_by_outcome.get("Up").cloned();
        s.token_down = ev.token_ids_by_outcome.get("Down").cloned();
        s.end_date = ev.end_date;
        s.liquidity_clob = ev.liquidity_clob;
        s.last_err = None;

        changed = prev_condition_id != s.condition_id
            || prev_tok_up != s.token_up
            || prev_tok_down != s.token_down;
    }
    changed
}

async fn set_ws_connected(state: &SharedState, symbol: &str, tf: Timeframe, connected: bool) {
    let mut guard = state.write().await;
    if let Some(s) = guard.get_mut(&(symbol.to_string(), tf)) {
        s.ws_connected = connected;
        if connected {
            s.last_err = None;
        }
    }
}

async fn set_error(state: &SharedState, symbol: &str, tf: Timeframe, err: String) {
    let mut guard = state.write().await;
    if let Some(s) = guard.get_mut(&(symbol.to_string(), tf)) {
        s.last_err = Some(err);
    }
}

#[derive(Debug)]
enum WsUpdate {
    FullBook {
        outcome: String,
        book: WsBookMsg,
        server_latency_ms: Option<i64>,
    },
    PriceChange {
        outcome: String,
        change: WsPriceChange,
        server_latency_ms: Option<i64>,
    },
    LastTrade {
        outcome: String,
        price: String,
        size: Option<String>,
        side: Option<String>,
        ts_ms: Option<i64>,
        server_latency_ms: Option<i64>,
    },
    Rtt {
        ms: i64,
    },
}

pub(crate) fn price_to_key(price: f64) -> i64 {
    // 1/1000 precision is enough for Polymarket tick sizes (0.01 and 0.001).
    (price * 1000.0).round() as i64
}

pub(crate) fn key_to_price(key: i64) -> f64 {
    (key as f64) / 1000.0
}

fn recompute_metrics(snapshot: &mut OutcomeSnapshot) {
    let best_bid = snapshot
        .bids
        .iter()
        .next_back()
        .map(|(k, _)| key_to_price(*k));
    let best_ask = snapshot.asks.iter().next().map(|(k, _)| key_to_price(*k));
    let mid = match (best_bid, best_ask) {
        (Some(b), Some(a)) => Some((b + a) / 2.0),
        _ => None,
    };

    let depth_bid_top = snapshot
        .bids
        .iter()
        .rev()
        .take(10)
        .map(|(_, s)| *s)
        .sum::<f64>();
    let depth_ask_top = snapshot.asks.iter().take(10).map(|(_, s)| *s).sum::<f64>();

    snapshot.metrics.best_bid = best_bid;
    snapshot.metrics.best_ask = best_ask;
    snapshot.metrics.mid = mid;
    snapshot.metrics.depth_bid_top = depth_bid_top;
    snapshot.metrics.depth_ask_top = depth_ask_top;
}

async fn apply_ws_update(state: SharedState, key: &TradeTick, update: WsUpdate) -> bool {
    let mut guard = state.write().await;
    let Some(s) = guard.get_mut(key) else {
        return false;
    };

    let mut tick_needed = false;
    match update {
        WsUpdate::FullBook {
            outcome,
            book,
            server_latency_ms,
        } => {
            let (prev_bid, prev_ask) = match outcome.as_str() {
                "Up" => s
                    .up
                    .as_ref()
                    .map(|x| (x.metrics.best_bid, x.metrics.best_ask))
                    .unwrap_or((None, None)),
                "Down" => s
                    .down
                    .as_ref()
                    .map(|x| (x.metrics.best_bid, x.metrics.best_ask))
                    .unwrap_or((None, None)),
                _ => (None, None),
            };
            let bids = book
                .bids
                .unwrap_or_default()
                .into_iter()
                .filter_map(|l| {
                    let p = l.price.parse::<f64>().ok()?;
                    let sz = l.size.parse::<f64>().ok()?;
                    Some((price_to_key(p), sz))
                })
                .collect::<BTreeMap<_, _>>();
            let asks = book
                .asks
                .unwrap_or_default()
                .into_iter()
                .filter_map(|l| {
                    let p = l.price.parse::<f64>().ok()?;
                    let sz = l.size.parse::<f64>().ok()?;
                    Some((price_to_key(p), sz))
                })
                .collect::<BTreeMap<_, _>>();

            let mut snap = OutcomeSnapshot {
                bids,
                asks,
                metrics: BookMetrics::default(),
            };
            recompute_metrics(&mut snap);
            match outcome.as_str() {
                "Up" => s.up = Some(snap),
                "Down" => s.down = Some(snap),
                _ => {}
            }
            let (new_bid, new_ask) = match outcome.as_str() {
                "Up" => s
                    .up
                    .as_ref()
                    .map(|x| (x.metrics.best_bid, x.metrics.best_ask))
                    .unwrap_or((None, None)),
                "Down" => s
                    .down
                    .as_ref()
                    .map(|x| (x.metrics.best_bid, x.metrics.best_ask))
                    .unwrap_or((None, None)),
                _ => (None, None),
            };
            if outcome == "Up" || outcome == "Down" {
                tick_needed = prev_bid != new_bid || prev_ask != new_ask;
            }
            if let Some(lat) = server_latency_ms {
                s.last_server_latency_ms = Some(lat);
            }
        }
        WsUpdate::PriceChange {
            outcome,
            change,
            server_latency_ms,
        } => {
            let (prev_bid, prev_ask) = match outcome.as_str() {
                "Up" => s
                    .up
                    .as_ref()
                    .map(|x| (x.metrics.best_bid, x.metrics.best_ask))
                    .unwrap_or((None, None)),
                "Down" => s
                    .down
                    .as_ref()
                    .map(|x| (x.metrics.best_bid, x.metrics.best_ask))
                    .unwrap_or((None, None)),
                _ => (None, None),
            };
            let target = match outcome.as_str() {
                "Up" => s.up.get_or_insert_with(|| OutcomeSnapshot {
                    bids: BTreeMap::new(),
                    asks: BTreeMap::new(),
                    metrics: BookMetrics::default(),
                }),
                "Down" => s.down.get_or_insert_with(|| OutcomeSnapshot {
                    bids: BTreeMap::new(),
                    asks: BTreeMap::new(),
                    metrics: BookMetrics::default(),
                }),
                _ => {
                    if let Some(lat) = server_latency_ms {
                        s.last_server_latency_ms = Some(lat);
                    }
                    s.last_msg_at = Some(chrono::Utc::now());
                    return false;
                }
            };

            let price = change.price.parse::<f64>().ok();
            let size = change.size.parse::<f64>().ok();
            if let (Some(p), Some(sz)) = (price, size) {
                let key = price_to_key(p);
                let map = if change.side.eq_ignore_ascii_case("BUY") {
                    &mut target.bids
                } else {
                    &mut target.asks
                };
                if sz <= 0.0 {
                    map.remove(&key);
                } else {
                    map.insert(key, sz);
                }
                recompute_metrics(target);
            }

            // best_bid/best_ask is provided on price_change; use it if we have no full book yet.
            if target.metrics.best_bid.is_none() {
                target.metrics.best_bid = change
                    .best_bid
                    .as_deref()
                    .and_then(|s| s.parse::<f64>().ok());
            }
            if target.metrics.best_ask.is_none() {
                target.metrics.best_ask = change
                    .best_ask
                    .as_deref()
                    .and_then(|s| s.parse::<f64>().ok());
            }
            if target.metrics.mid.is_none() {
                if let (Some(b), Some(a)) = (target.metrics.best_bid, target.metrics.best_ask) {
                    target.metrics.mid = Some((b + a) / 2.0);
                }
            }

            if outcome == "Up" || outcome == "Down" {
                let new_bid = target.metrics.best_bid;
                let new_ask = target.metrics.best_ask;
                tick_needed = prev_bid != new_bid || prev_ask != new_ask;
            }
            if let Some(lat) = server_latency_ms {
                s.last_server_latency_ms = Some(lat);
            }
        }
        WsUpdate::LastTrade {
            outcome,
            price,
            size,
            side,
            ts_ms,
            server_latency_ms,
        } => {
            let p = price.parse::<f64>().ok();
            let sz = size.as_deref().and_then(|s| s.parse::<f64>().ok());
            let is_sell = side
                .as_deref()
                .map(|s| s.eq_ignore_ascii_case("SELL"));
            if let Some(p) = p {
                match outcome.as_str() {
                    "Up" => {
                        if let Some(up) = s.up.as_mut() {
                            if should_update_trade_ts(up.metrics.last_trade_ts_ms, ts_ms) {
                                up.metrics.last_trade_price = Some(p);
                                up.metrics.last_trade_size = sz;
                                up.metrics.last_trade_is_sell = is_sell;
                                up.metrics.last_trade_ts_ms = ts_ms;
                            }
                        }
                    }
                    "Down" => {
                        if let Some(dn) = s.down.as_mut() {
                            if should_update_trade_ts(dn.metrics.last_trade_ts_ms, ts_ms) {
                                dn.metrics.last_trade_price = Some(p);
                                dn.metrics.last_trade_size = sz;
                                dn.metrics.last_trade_is_sell = is_sell;
                                dn.metrics.last_trade_ts_ms = ts_ms;
                            }
                        }
                    }
                    _ => {}
                }
            }
            if let Some(lat) = server_latency_ms {
                s.last_server_latency_ms = Some(lat);
            }
        }
        WsUpdate::Rtt { ms } => {
            s.last_rtt_ms = Some(ms);
        }
    };

    s.last_msg_at = Some(chrono::Utc::now());
    tick_needed
}

fn should_update_trade_ts(prev: Option<i64>, next: Option<i64>) -> bool {
    match (prev, next) {
        (None, None) => true,
        (None, Some(_)) => true,
        (Some(_), None) => false,
        (Some(p), Some(n)) => n >= p,
    }
}
