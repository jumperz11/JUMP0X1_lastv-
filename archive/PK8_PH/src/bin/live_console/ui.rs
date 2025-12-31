use super::*;
use std::collections::{HashMap, VecDeque};

#[derive(Clone)]
struct UiSnapshot {
    streams: Vec<StreamUi>,
    logs: VecDeque<String>,
    positions: HashMap<String, PositionSnapshot>,
    orders: HashMap<String, OrderSnapshot>,
    cash_usdc: Option<f64>,
    latencies: LatencyStats,
}

#[derive(Clone)]
struct StreamUi {
    symbol: String,
    timeframe: Timeframe,
    slug: String,
    token_up: Option<String>,
    token_down: Option<String>,
    end_date: Option<chrono::DateTime<chrono::Utc>>,
    liquidity_clob: Option<f64>,
    up_mid: Option<f64>,
    down_mid: Option<f64>,
    depth_up: f64,
    depth_down: f64,
    ws_connected: bool,
    last_rtt_ms: Option<i64>,
    last_server_latency_ms: Option<i64>,
    decision: String,
}

#[derive(Clone)]
struct PortfolioRowData {
    symbol: String,
    tf: Timeframe,
    q_up: f64,
    q_dn: f64,
    avg_up: f64,
    avg_dn: f64,
    avg_tot: Option<f64>,
    delta: f64,
    lock_pair: Option<f64>,
    worst_settle: Option<f64>,
    pnl: Option<f64>,
    decision: String,
}

async fn collect_ui_snapshot(
    state: &SharedState,
    logs: &SharedLogs,
    positions: &SharedPositions,
    orders: &SharedOrders,
    cash_usdc: &SharedCash,
    latencies: &SharedLatency,
) -> UiSnapshot {
    let streams = {
        let guard = state.read().await;
        let mut out = Vec::with_capacity(guard.len());
        for s in guard.values() {
            let depth_up = s
                .up
                .as_ref()
                .map(|x| x.metrics.depth_bid_top + x.metrics.depth_ask_top)
                .unwrap_or(0.0);
            let depth_down = s
                .down
                .as_ref()
                .map(|x| x.metrics.depth_bid_top + x.metrics.depth_ask_top)
                .unwrap_or(0.0);
            out.push(StreamUi {
                symbol: s.symbol.clone(),
                timeframe: s.timeframe,
                slug: s.slug.clone(),
                token_up: s.token_up.clone(),
                token_down: s.token_down.clone(),
                end_date: s.end_date,
                liquidity_clob: s.liquidity_clob,
                up_mid: s.up.as_ref().and_then(|x| x.metrics.mid),
                down_mid: s.down.as_ref().and_then(|x| x.metrics.mid),
                depth_up,
                depth_down,
                ws_connected: s.ws_connected,
                last_rtt_ms: s.last_rtt_ms,
                last_server_latency_ms: s.last_server_latency_ms,
                decision: s.decision.clone(),
            });
        }
        out
    };
    let logs = logs.read().await.clone();
    let positions = positions.read().await.clone();
    let orders = orders.read().await.clone();
    let cash_usdc = *cash_usdc.read().await;
    let latencies = latencies.read().await.clone();
    UiSnapshot {
        streams,
        logs,
        positions,
        orders,
        cash_usdc,
        latencies,
    }
}

pub(crate) async fn run_tui(
    state: SharedState,
    logs: SharedLogs,
    positions: SharedPositions,
    orders: SharedOrders,
    cash_usdc: SharedCash,
    latencies: SharedLatency,
) -> Result<()> {
    enable_raw_mode().context("enable raw mode")?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen).context("enter alt screen")?;
    let backend = ratatui::backend::CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend).context("terminal init")?;

    let mut table_state = TableState::default();
    let mut positions_state = TableState::default();
    let mut orders_state = TableState::default();

    let tick_rate = Duration::from_millis(200);
    loop {
        let snap = collect_ui_snapshot(
            &state,
            &logs,
            &positions,
            &orders,
            &cash_usdc,
            &latencies,
        )
        .await;
        terminal.draw(|f| {
            draw_ui_snapshot(f, &snap, &mut table_state, &mut positions_state, &mut orders_state)
        })?;

        if event::poll(tick_rate)? {
            match event::read()? {
                Event::Key(key) if key.kind == KeyEventKind::Press => match key.code {
                    KeyCode::Char('q') | KeyCode::Esc => break,
                    KeyCode::Down => {
                        table_state
                            .select(Some(table_state.selected().unwrap_or(0).saturating_add(1)));
                    }
                    KeyCode::Up => {
                        table_state
                            .select(Some(table_state.selected().unwrap_or(0).saturating_sub(1)));
                    }
                    _ => {}
                },
                _ => {}
            }
        }
    }

    teardown_tui(terminal)?;
    Ok(())
}

pub(crate) async fn run_plain(state: SharedState) -> Result<()> {
    // Minimal fallback for non-interactive stdout.
    loop {
        let guard = state.read().await;
        let mut connected = 0usize;
        let mut total = 0usize;
        let mut max_lat: Option<i64> = None;
        for v in guard.values() {
            total += 1;
            if v.ws_connected {
                connected += 1;
            }
            if let Some(lat) = v.last_rtt_ms {
                max_lat = Some(max_lat.map(|m| m.max(lat)).unwrap_or(lat));
            }
        }
        let lat = max_lat
            .map(|v| format!("{v}ms"))
            .unwrap_or_else(|| "-".to_string());
        let header = format!("Console | ws {connected}/{total} | ws_max {lat} | q to quit");
        println!("{header}");
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}

fn teardown_tui(mut terminal: Terminal<ratatui::backend::CrosstermBackend<Stdout>>) -> Result<()> {
    disable_raw_mode().context("disable raw mode")?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen).context("leave alt screen")?;
    terminal.show_cursor().context("show cursor")?;
    Ok(())
}

fn draw_ui_snapshot(
    f: &mut Frame,
    snap: &UiSnapshot,
    table_state: &mut TableState,
    positions_state: &mut TableState,
    orders_state: &mut TableState,
) {
    let area = f.area();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Min(10)].as_ref())
        .split(area);

    // Line 1: Mode + Strategy + USDC + WS
    let header = build_header_snapshot(snap);
    let header_p = Paragraph::new(header)
        .alignment(Alignment::Left)
        .block(Block::default());
    f.render_widget(
        header_p,
        Rect::new(chunks[0].x, chunks[0].y, chunks[0].width, 1),
    );

    // Line 2: Caps status
    let caps_line = build_caps_line();
    let caps_p = Paragraph::new(caps_line)
        .alignment(Alignment::Left)
        .block(Block::default());
    f.render_widget(
        caps_p,
        Rect::new(chunks[0].x, chunks[0].y + 1, chunks[0].width, 1),
    );

    // Separator
    let header_block = Block::default().borders(Borders::BOTTOM);
    f.render_widget(header_block, chunks[0]);

    let body = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Min(10),
            Constraint::Length(7),
            Constraint::Length(7),
            Constraint::Length(8),
        ])
        .split(chunks[1]);

    draw_table_snapshot(f, &snap.streams, body[0], table_state);
    draw_portfolio_snapshot(f, &snap.streams, &snap.positions, body[1], positions_state);
    draw_open_orders_snapshot(f, &snap.orders, body[2], orders_state);
    draw_logs_snapshot(f, &snap.logs, body[3]);
}

fn build_caps_line() -> String {
    // Read caps from env
    let max_trades = std::env::var("MAX_TRADES_PER_SESSION")
        .ok().and_then(|v| v.parse::<u32>().ok()).unwrap_or(5);
    let max_usd = std::env::var("MAX_WORST_TOTAL_USD")
        .ok().and_then(|v| v.parse::<f64>().ok()).unwrap_or(5.0);
    let kill_file = std::env::var("KILL_SWITCH_FILE")
        .unwrap_or_else(|_| "KILL_SWITCH".to_string());
    let kill_active = std::path::Path::new(&kill_file).exists();
    let kill_str = if kill_active { "ACTIVE" } else { "off" };

    format!(
        "Caps: 0/{} trades | $0.00/${:.0} exp | Kill: {}",
        max_trades, max_usd, kill_str
    )
}

fn build_header_snapshot(snap: &UiSnapshot) -> String {
    let mut connected = 0usize;
    let mut total = 0usize;
    for v in snap.streams.iter() {
        total += 1;
        if v.ws_connected {
            connected += 1;
        }
    }

    // Mode indicator
    let armed = std::env::var("PM_ARMED").ok().map(|v| v == "1").unwrap_or(false);
    let mode_str = if armed { "ARMED" } else { "OBSERVE" };

    let cash_str = snap
        .cash_usdc
        .map(|v| format!("${v:.2}"))
        .unwrap_or_else(|| "$-".to_string());

    // Strategy
    let strategy = std::env::var("PM_STRATEGY").unwrap_or_else(|_| "?".to_string());

    format!(
        "[{mode_str}] | {strategy} | USDC {cash_str} | ws {connected}/{total} | q=quit"
    )
}

fn draw_table_snapshot(
    f: &mut Frame,
    streams: &[StreamUi],
    area: Rect,
    table_state: &mut TableState,
) {
    let rows = snapshot_rows_all_state(streams);
    let title = "Markets".to_string();

    if let Some(sel) = table_state.selected() {
        if !rows.is_empty() && sel >= rows.len() {
            table_state.select(Some(rows.len() - 1));
        }
    }

    let header_style = Style::default()
        .fg(Color::Cyan)
        .add_modifier(Modifier::BOLD);
    let ok_style = Style::default().fg(Color::Green);
    let bad_style = Style::default().fg(Color::Red);

    let header = Row::new(vec![
        Cell::from("Sym"),
        Cell::from("TF"),
        Cell::from("Left"),
        Cell::from("UpMid"),
        Cell::from("DnMid"),
        Cell::from("Spread"),
        Cell::from("Decision"),
        Cell::from("WS"),
    ])
    .style(header_style);

    let table_rows: Vec<Row> = rows
        .into_iter()
        .map(|r| {
            let ws_cell = if r.ws_connected { "OK" } else { "DOWN" };
            let ws_style = if r.ws_connected { ok_style } else { bad_style };

            // Calculate spread (sum of mids)
            let spread = r.up_mid_f.zip(r.down_mid_f).map(|(u, d)| format!("{:.3}", u + d)).unwrap_or_else(|| "-".to_string());

            Row::new(vec![
                Cell::from(r.symbol),
                Cell::from(r.tf),
                Cell::from(r.time_left),
                Cell::from(r.up_mid),
                Cell::from(r.down_mid),
                Cell::from(spread),
                Cell::from(r.decision),
                Cell::from(ws_cell).style(ws_style),
            ])
        })
        .collect();

    let table = Table::new(
        table_rows,
        [
            Constraint::Length(5),
            Constraint::Length(4),
            Constraint::Length(7),
            Constraint::Length(7),
            Constraint::Length(7),
            Constraint::Length(7),
            Constraint::Percentage(50),
            Constraint::Length(5),
        ],
    )
    .header(header)
    .block(Block::default().title(title).borders(Borders::ALL))
    .row_highlight_style(Style::default().add_modifier(Modifier::REVERSED));

    f.render_stateful_widget(table, area, table_state);
}

fn snapshot_rows_all_state(streams: &[StreamUi]) -> Vec<UiRow> {
    let mut items: Vec<_> = streams.to_vec();
    items.sort_by(|a, b| {
        sym_rank(&a.symbol)
            .cmp(&sym_rank(&b.symbol))
            .then_with(|| tf_rank(a.timeframe).cmp(&tf_rank(b.timeframe)))
    });

    items
        .into_iter()
        .map(|s| {
            let slug_short = if s.slug.len() > 44 {
                format!("{}...", &s.slug[..43])
            } else {
                s.slug.clone()
            };

            let time_left = match s.end_date {
                None => "-".to_string(),
                Some(end) => {
                    let now = chrono::Utc::now();
                    if end <= now {
                        "0s".to_string()
                    } else {
                        let d = end - now;
                        let mins = d.num_minutes();
                        let secs = (d - chrono::Duration::minutes(mins)).num_seconds();
                        if mins >= 60 {
                            format!("{}h{}m", mins / 60, mins % 60)
                        } else if mins > 0 {
                            format!("{}m{}s", mins, secs.max(0))
                        } else {
                            format!("{}s", d.num_seconds().max(0))
                        }
                    }
                }
            };

            let up_mid = s
                .up_mid
                .map(|v| format!("{v:.3}"))
                .unwrap_or_else(|| "-".to_string());
            let down_mid = s
                .down_mid
                .map(|v| format!("{v:.3}"))
                .unwrap_or_else(|| "-".to_string());

            let depth = format!("{:.0}/{:.0}", s.depth_up.max(0.0), s.depth_down.max(0.0));

            let liq = s
                .liquidity_clob
                .map(|v| format!("{v:.0}"))
                .unwrap_or_else(|| "-".to_string());
            let latency = s
                .last_rtt_ms
                .map(|v| format!("{v}ms"))
                .or_else(|| s.last_server_latency_ms.map(|v| format!("{v}ms*")))
                .unwrap_or_else(|| "-".to_string());
            let decision = if s.decision.is_empty() {
                "-".to_string()
            } else {
                shorten(&s.decision, 64)
            };

            UiRow {
                symbol: s.symbol,
                tf: s.timeframe.label().to_string(),
                slug_short,
                time_left,
                up_mid,
                down_mid,
                up_mid_f: s.up_mid,
                down_mid_f: s.down_mid,
                decision,
                depth,
                liq,
                latency,
                ws_connected: s.ws_connected,
            }
        })
        .collect()
}

fn draw_logs_snapshot(f: &mut Frame, logs: &VecDeque<String>, area: Rect) {
    let lines: Vec<String> = logs
        .iter()
        .rev()
        .take(6)
        .cloned()
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();
    let text = lines.join("\n");
    let p = Paragraph::new(text).block(Block::default().title("Logs").borders(Borders::ALL));
    f.render_widget(p, area);
}

fn draw_portfolio_snapshot(
    f: &mut Frame,
    streams: &[StreamUi],
    positions: &HashMap<String, PositionSnapshot>,
    area: Rect,
    table_state: &mut TableState,
) {
    let mut rows: Vec<PortfolioRowData> = Vec::new();
    for snap in streams.iter() {
        let Some(tok_up) = snap.token_up.as_deref() else {
            continue;
        };
        let Some(tok_dn) = snap.token_down.as_deref() else {
            continue;
        };

        let (qy, avg_y) = positions
            .get(tok_up)
            .map(|p| (p.size, p.avg_price))
            .unwrap_or((0.0, 0.0));
        let (qn, avg_n) = positions
            .get(tok_dn)
            .map(|p| (p.size, p.avg_price))
            .unwrap_or((0.0, 0.0));

        let qy_pos = qy.max(0.0);
        let qn_pos = qn.max(0.0);
        if qy_pos <= 1e-9 && qn_pos <= 1e-9 {
            continue;
        }
        let cost_up = qy_pos * avg_y;
        let cost_dn = qn_pos * avg_n;
        let cost_total = cost_up + cost_dn;

        let up_mid = snap.up_mid;
        let dn_mid = snap.down_mid;
        let avg_tot = if qy_pos > 1e-9 && qn_pos > 1e-9 {
            Some(avg_y + avg_n)
        } else {
            None
        };

        let lock_pair = if qy_pos > 1e-9 && qn_pos > 1e-9 {
            let m = qy_pos.min(qn_pos);
            Some(m * (1.0 - (avg_y + avg_n)))
        } else {
            None
        };
        let worst_settle = Some(qy_pos.min(qn_pos) - cost_total);
        let pnl = match (up_mid, dn_mid) {
            (Some(uy), Some(un)) => Some(qy_pos * uy + qn_pos * un - cost_total),
            _ => None,
        };

        rows.push(PortfolioRowData {
            symbol: snap.symbol.clone(),
            tf: snap.timeframe,
            q_up: qy_pos,
            q_dn: qn_pos,
            avg_up: avg_y,
            avg_dn: avg_n,
            avg_tot,
            delta: qy_pos - qn_pos,
            lock_pair,
            worst_settle,
            pnl,
            decision: snap.decision.clone(),
        });
    }

    rows.sort_by(|a, b| {
        sym_rank(&a.symbol)
            .cmp(&sym_rank(&b.symbol))
            .then_with(|| tf_rank(a.tf).cmp(&tf_rank(b.tf)))
    });

    let header_style = Style::default()
        .fg(Color::Cyan)
        .add_modifier(Modifier::BOLD);
    let header = Row::new(vec![
        Cell::from("Sym"),
        Cell::from("TF"),
        Cell::from("Up"),
        Cell::from("Dn"),
        Cell::from("Delta"),
        Cell::from("LockPair"),
        Cell::from("Worst"),
        Cell::from("PnL"),
        Cell::from("UpAvg"),
        Cell::from("DnAvg"),
        Cell::from("AvgTot"),
        Cell::from("Decision"),
    ])
    .style(header_style);

    let table_rows: Vec<Row> = rows
        .into_iter()
        .take(area.height.saturating_sub(3) as usize)
        .map(|r| {
            let lock_str = r
                .lock_pair
                .map(|v| format!("{v:.2}"))
                .unwrap_or_else(|| "-".to_string());
            let worst_str = r
                .worst_settle
                .map(|v| format!("{v:.2}"))
                .unwrap_or_else(|| "-".to_string());
            let pnl_str = r
                .pnl
                .map(|v| format!("{v:.2}"))
                .unwrap_or_else(|| "-".to_string());

            let lock_style = match r.lock_pair {
                Some(v) if v > 0.0 => Style::default().fg(Color::Green),
                Some(v) if v < 0.0 => Style::default().fg(Color::Red),
                _ => Style::default(),
            };
            let worst_style = match r.worst_settle {
                Some(v) if v > 0.0 => Style::default().fg(Color::Green),
                Some(v) if v < 0.0 => Style::default().fg(Color::Red),
                _ => Style::default(),
            };
            let pnl_style = match r.pnl {
                Some(v) if v > 0.0 => Style::default().fg(Color::Green),
                Some(v) if v < 0.0 => Style::default().fg(Color::Red),
                _ => Style::default(),
            };

            Row::new(vec![
                Cell::from(r.symbol),
                Cell::from(r.tf.label()),
                Cell::from(format!("{:.3}", r.q_up)),
                Cell::from(format!("{:.3}", r.q_dn)),
                Cell::from(format!("{:+.3}", r.delta)),
                Cell::from(lock_str).style(lock_style),
                Cell::from(worst_str).style(worst_style),
                Cell::from(pnl_str).style(pnl_style),
                Cell::from(format!("{:.3}", r.avg_up)),
                Cell::from(format!("{:.3}", r.avg_dn)),
                Cell::from(
                    r.avg_tot
                        .map(|v| format!("{v:.3}"))
                        .unwrap_or_else(|| "-".to_string()),
                ),
                Cell::from(if r.decision.is_empty() {
                    "-".to_string()
                } else {
                    r.decision
                }),
            ])
        })
        .collect();

    let table = Table::new(
        table_rows,
        [
            Constraint::Length(4),
            Constraint::Length(4),
            Constraint::Length(10),
            Constraint::Length(10),
            Constraint::Length(10),
            Constraint::Length(10),
            Constraint::Length(10),
            Constraint::Length(10),
            Constraint::Length(7),
            Constraint::Length(7),
            Constraint::Length(7),
            Constraint::Min(20),
        ],
    )
    .header(header)
    .block(Block::default().title("Portfolio").borders(Borders::ALL))
    .row_highlight_style(Style::default().add_modifier(Modifier::REVERSED));

    f.render_stateful_widget(table, area, table_state);
}

fn draw_open_orders_snapshot(
    f: &mut Frame,
    orders: &HashMap<String, OrderSnapshot>,
    area: Rect,
    table_state: &mut TableState,
) {
    let now = chrono::Utc::now();

    let mut rows: Vec<OrderSnapshot> = orders
        .values()
        .filter(|o| order_is_open(o))
        .cloned()
        .collect();
    rows.sort_by(|a, b| b.last_update_at.cmp(&a.last_update_at));

    let header_style = Style::default()
        .fg(Color::Cyan)
        .add_modifier(Modifier::BOLD);
    let header = Row::new(vec![
        Cell::from("Sym"),
        Cell::from("TF"),
        Cell::from("Out"),
        Cell::from("Side"),
        Cell::from("Px"),
        Cell::from("Rem"),
        Cell::from("Matched"),
        Cell::from("Age"),
        Cell::from("Order"),
        Cell::from("Event"),
    ])
    .style(header_style);

    let table_rows: Vec<Row> = rows
        .into_iter()
        .take(area.height.saturating_sub(3) as usize)
        .map(|o| {
            let remaining = order_remaining(&o);
            let age_s = (now - o.last_update_at).num_seconds().max(0);
            Row::new(vec![
                Cell::from(o.symbol),
                Cell::from(o.timeframe.label()),
                Cell::from(o.outcome),
                Cell::from(o.side),
                Cell::from(format!("{:.3}", o.price)),
                Cell::from(format!("{:.3}", remaining)),
                Cell::from(format!("{:.3}", o.size_matched)),
                Cell::from(format!("{age_s}s")),
                Cell::from(shorten(&o.order_id, 14)),
                Cell::from(shorten(&o.last_event, 16)),
            ])
        })
        .collect();

    let table = Table::new(
        table_rows,
        [
            Constraint::Length(4),
            Constraint::Length(4),
            Constraint::Length(6),
            Constraint::Length(6),
            Constraint::Length(7),
            Constraint::Length(9),
            Constraint::Length(9),
            Constraint::Length(6),
            Constraint::Length(16),
            Constraint::Min(10),
        ],
    )
    .header(header)
    .block(Block::default().title("Open Orders").borders(Borders::ALL))
    .row_highlight_style(Style::default().add_modifier(Modifier::REVERSED));

    f.render_stateful_widget(table, area, table_state);
}

#[allow(dead_code)]
fn draw_ui(
    f: &mut Frame,
    state: &SharedState,
    logs: &SharedLogs,
    positions: &SharedPositions,
    orders: &SharedOrders,
    cash_usdc: &SharedCash,
    latencies: &SharedLatency,
    table_state: &mut TableState,
    positions_state: &mut TableState,
    orders_state: &mut TableState,
) {
    let area = f.area();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(2), Constraint::Min(10)].as_ref())
        .split(area);

    let header = build_header(
        state,
        cash_usdc,
        latencies,
        Some(positions),
        Some(table_state),
    );
    let header_block = Block::default().borders(Borders::BOTTOM);
    f.render_widget(header_block, chunks[0]);
    let header_p = Paragraph::new(header)
        .alignment(Alignment::Left)
        .block(Block::default());
    f.render_widget(
        header_p,
        Rect::new(chunks[0].x, chunks[0].y, chunks[0].width, 1),
    );

    let body = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Min(10),
            Constraint::Length(7),
            Constraint::Length(7),
            Constraint::Length(8),
        ])
        .split(chunks[1]);

    draw_table(f, state, body[0], table_state);
    draw_portfolio(f, state, positions, body[1], positions_state);
    draw_open_orders(f, orders, body[2], orders_state);
    draw_logs(f, logs, body[3]);
}

#[allow(dead_code)]
fn build_header(
    state: &SharedState,
    cash_usdc: &SharedCash,
    latencies: &SharedLatency,
    _positions: Option<&SharedPositions>,
    _market_table_state: Option<&TableState>,
) -> String {
    // Summary: connected count and max latency.
    let guard = state.blocking_read();

    let mut connected = 0usize;
    let mut total = 0usize;
    let mut max_lat: Option<i64> = None;
    for v in guard.values() {
        total += 1;
        if v.ws_connected {
            connected += 1;
        }
        if let Some(lat) = v.last_rtt_ms {
            max_lat = Some(max_lat.map(|m| m.max(lat)).unwrap_or(lat));
        }
    }
    let lat = max_lat
        .map(|v| format!("{v}ms"))
        .unwrap_or_else(|| "-".to_string());
    let cash_str = cash_usdc
        .blocking_read()
        .map(|v| format!("USDC ${v:.2}"))
        .unwrap_or_else(|| "USDC -".to_string());

    let g = latencies.blocking_read();
    let (post, place, cancel, mine) = (
        g.max_post_ms
            .map(|v| format!("{v}ms"))
            .unwrap_or_else(|| "-".to_string()),
        g.max_place_ms
            .map(|v| format!("{v}ms"))
            .unwrap_or_else(|| "-".to_string()),
        g.max_cancel_clear_ms
            .map(|v| format!("{v}ms"))
            .unwrap_or_else(|| "-".to_string()),
        g.max_trade_mined_ms
            .map(|v| format!("{v}ms"))
            .unwrap_or_else(|| "-".to_string()),
    );

    format!(
        "Console | {cash_str} | ws {connected}/{total} | ws_max {lat} | post_max {post} | place_max {place} | mine_max {mine} | cancel_max {cancel} | q to quit"
    )
}

#[allow(dead_code)]
fn draw_table(f: &mut Frame, state: &SharedState, area: Rect, table_state: &mut TableState) {
    let rows = snapshot_rows_all(state);
    let title = "Markets".to_string();

    let header_style = Style::default()
        .fg(Color::Cyan)
        .add_modifier(Modifier::BOLD);
    let ok_style = Style::default().fg(Color::Green);
    let bad_style = Style::default().fg(Color::Red);

    let header = Row::new(vec![
        Cell::from("Sym"),
        Cell::from("TF"),
        Cell::from("Slug"),
        Cell::from("Left"),
        Cell::from("UpMid"),
        Cell::from("DnMid"),
        Cell::from("Decision"),
        Cell::from("Depth(U/D)"),
        Cell::from("Liq"),
        Cell::from("Lat"),
        Cell::from("WS"),
    ])
    .style(header_style);

    let table_rows: Vec<Row> = rows
        .into_iter()
        .map(|r| {
            let ws_cell = if r.ws_connected { "OK" } else { "DOWN" };
            let ws_style = if r.ws_connected { ok_style } else { bad_style };

            Row::new(vec![
                Cell::from(r.symbol),
                Cell::from(r.tf),
                Cell::from(r.slug_short),
                Cell::from(r.time_left),
                Cell::from(r.up_mid),
                Cell::from(r.down_mid),
                Cell::from(r.decision),
                Cell::from(r.depth),
                Cell::from(r.liq),
                Cell::from(r.latency),
                Cell::from(ws_cell).style(ws_style),
            ])
        })
        .collect();

    let table = Table::new(
        table_rows,
        [
            Constraint::Length(4),
            Constraint::Length(4),
            Constraint::Percentage(22),
            Constraint::Length(8),
            Constraint::Length(8),
            Constraint::Length(8),
            Constraint::Percentage(28),
            Constraint::Length(14),
            Constraint::Length(10),
            Constraint::Length(8),
            Constraint::Length(6),
        ],
    )
    .header(header)
    .block(Block::default().title(title).borders(Borders::ALL))
    .row_highlight_style(Style::default().add_modifier(Modifier::REVERSED));

    f.render_stateful_widget(table, area, table_state);
}

#[derive(Debug)]
struct UiRow {
    symbol: String,
    tf: String,
    slug_short: String,
    time_left: String,
    up_mid: String,
    down_mid: String,
    up_mid_f: Option<f64>,
    down_mid_f: Option<f64>,
    decision: String,
    depth: String,
    liq: String,
    latency: String,
    ws_connected: bool,
}

#[allow(dead_code)]
fn snapshot_rows_all(state: &SharedState) -> Vec<UiRow> {
    let guard = state.blocking_read();

    let mut items: Vec<_> = guard.values().cloned().collect();
    items.sort_by(|a, b| {
        sym_rank(&a.symbol)
            .cmp(&sym_rank(&b.symbol))
            .then_with(|| tf_rank(a.timeframe).cmp(&tf_rank(b.timeframe)))
    });

    items
        .into_iter()
        .map(|s| {
            let slug_short = if s.slug.len() > 44 {
                format!("{}...", &s.slug[..43])
            } else {
                s.slug.clone()
            };

            let time_left = match s.end_date {
                None => "-".to_string(),
                Some(end) => {
                    let now = chrono::Utc::now();
                    if end <= now {
                        "0s".to_string()
                    } else {
                        let d = end - now;
                        let mins = d.num_minutes();
                        let secs = (d - chrono::Duration::minutes(mins)).num_seconds();
                        if mins >= 60 {
                            format!("{}h{}m", mins / 60, mins % 60)
                        } else if mins > 0 {
                            format!("{}m{}s", mins, secs.max(0))
                        } else {
                            format!("{}s", d.num_seconds().max(0))
                        }
                    }
                }
            };

            let up_mid_f = s.up.as_ref().and_then(|x| x.metrics.mid);
            let down_mid_f = s.down.as_ref().and_then(|x| x.metrics.mid);
            let up_mid = up_mid_f
                    .map(|v| format!("{v:.3}"))
                    .unwrap_or_else(|| "-".to_string());
            let down_mid = down_mid_f
                .map(|v| format!("{v:.3}"))
                .unwrap_or_else(|| "-".to_string());

            let depth = format!(
                "{:.0}/{:.0}",
                s.up.as_ref()
                    .map(|x| x.metrics.depth_bid_top)
                    .unwrap_or(0.0)
                    + s.up
                        .as_ref()
                        .map(|x| x.metrics.depth_ask_top)
                        .unwrap_or(0.0),
                s.down
                    .as_ref()
                    .map(|x| x.metrics.depth_bid_top)
                    .unwrap_or(0.0)
                    + s.down
                        .as_ref()
                        .map(|x| x.metrics.depth_ask_top)
                        .unwrap_or(0.0),
            );

            let liq = s
                .liquidity_clob
                .map(|v| format!("{v:.0}"))
                .unwrap_or_else(|| "-".to_string());
            let latency = s
                .last_rtt_ms
                .map(|v| format!("{v}ms"))
                .or_else(|| s.last_server_latency_ms.map(|v| format!("{v}ms*")))
                .unwrap_or_else(|| "-".to_string());
            let decision = if s.decision.is_empty() {
                "-".to_string()
            } else {
                shorten(&s.decision, 64)
            };

            UiRow {
                symbol: s.symbol,
                tf: s.timeframe.label().to_string(),
                slug_short,
                time_left,
                up_mid,
                down_mid,
                up_mid_f,
                down_mid_f,
                decision,
                depth,
                liq,
                latency,
                ws_connected: s.ws_connected,
            }
        })
        .collect()
}

#[allow(dead_code)]
fn draw_logs(f: &mut Frame, logs: &SharedLogs, area: Rect) {
    let g = logs.blocking_read();
    let lines: Vec<String> = g
        .iter()
        .rev()
        .take(6)
        .cloned()
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();
    let text = lines.join("\n");
    let p = Paragraph::new(text).block(Block::default().title("Logs").borders(Borders::ALL));
    f.render_widget(p, area);
}

#[allow(dead_code)]
fn draw_portfolio(
    f: &mut Frame,
    state: &SharedState,
    positions: &SharedPositions,
    area: Rect,
    table_state: &mut TableState,
) {
    let s_guard = state.blocking_read();
    let p_guard = positions.blocking_read();

    #[derive(Clone)]
    struct RowData {
        symbol: String,
        tf: Timeframe,
        q_up: f64,
        q_dn: f64,
        avg_up: f64,
        avg_dn: f64,
        avg_tot: Option<f64>,
        delta: f64,
        lock_pair: Option<f64>,
        worst_settle: Option<f64>,
        pnl: Option<f64>,
        decision: String,
    }

    let mut rows: Vec<RowData> = Vec::new();
    for snap in s_guard.values() {
        let Some(tok_up) = snap.token_up.as_deref() else {
            continue;
        };
        let Some(tok_dn) = snap.token_down.as_deref() else {
            continue;
        };

        let (qy, avg_y) = p_guard
            .get(tok_up)
            .map(|p| (p.size, p.avg_price))
            .unwrap_or((0.0, 0.0));
        let (qn, avg_n) = p_guard
            .get(tok_dn)
            .map(|p| (p.size, p.avg_price))
            .unwrap_or((0.0, 0.0));

        let qy_pos = qy.max(0.0);
        let qn_pos = qn.max(0.0);
        if qy_pos <= 1e-9 && qn_pos <= 1e-9 {
            continue;
        }
        let cost_up = qy_pos * avg_y;
        let cost_dn = qn_pos * avg_n;
        let cost_total = cost_up + cost_dn;

        let up_mid = snap.up.as_ref().and_then(|u| u.metrics.mid);
        let dn_mid = snap.down.as_ref().and_then(|d| d.metrics.mid);
        let avg_tot = if qy_pos > 1e-9 && qn_pos > 1e-9 {
            Some(avg_y + avg_n)
        } else {
            None
        };

        let lock_pair = if qy_pos > 1e-9 && qn_pos > 1e-9 {
            let m = qy_pos.min(qn_pos);
            Some(m * (1.0 - (avg_y + avg_n)))
        } else {
            None
        };
        // Worst-case PnL at settlement (guaranteed): min(q_up, q_dn) - total_cost.
        let worst_settle = Some(qy_pos.min(qn_pos) - cost_total);
        let pnl = match (up_mid, dn_mid) {
            (Some(uy), Some(un)) => Some(qy_pos * uy + qn_pos * un - cost_total),
            _ => None,
        };

        rows.push(RowData {
            symbol: snap.symbol.clone(),
            tf: snap.timeframe,
            q_up: qy_pos,
            q_dn: qn_pos,
            avg_up: avg_y,
            avg_dn: avg_n,
            avg_tot,
            delta: qy_pos - qn_pos,
            lock_pair,
            worst_settle,
            pnl,
            decision: snap.decision.clone(),
        });
    }

    rows.sort_by(|a, b| {
        sym_rank(&a.symbol)
            .cmp(&sym_rank(&b.symbol))
            .then_with(|| tf_rank(a.tf).cmp(&tf_rank(b.tf)))
    });

    let header_style = Style::default()
        .fg(Color::Cyan)
        .add_modifier(Modifier::BOLD);
    let header = Row::new(vec![
        Cell::from("Sym"),
        Cell::from("TF"),
        Cell::from("Up"),
        Cell::from("Dn"),
        Cell::from("Delta"),
        Cell::from("LockPair"),
        Cell::from("Worst"),
        Cell::from("PnL"),
        Cell::from("UpAvg"),
        Cell::from("DnAvg"),
        Cell::from("AvgTot"),
        Cell::from("Decision"),
    ])
    .style(header_style);

    let table_rows: Vec<Row> = rows
        .into_iter()
        .take(area.height.saturating_sub(3) as usize)
        .map(|r| {
            let lock_str = r
                .lock_pair
                .map(|v| format!("{v:.2}"))
                .unwrap_or_else(|| "-".to_string());
            let worst_str = r
                .worst_settle
                .map(|v| format!("{v:.2}"))
                .unwrap_or_else(|| "-".to_string());
            let pnl_str = r
                .pnl
                .map(|v| format!("{v:.2}"))
                .unwrap_or_else(|| "-".to_string());

            let lock_style = match r.lock_pair {
                Some(v) if v > 0.0 => Style::default().fg(Color::Green),
                Some(v) if v < 0.0 => Style::default().fg(Color::Red),
                _ => Style::default(),
            };
            let worst_style = match r.worst_settle {
                Some(v) if v > 0.0 => Style::default().fg(Color::Green),
                Some(v) if v < 0.0 => Style::default().fg(Color::Red),
                _ => Style::default(),
            };
            let pnl_style = match r.pnl {
                Some(v) if v > 0.0 => Style::default().fg(Color::Green),
                Some(v) if v < 0.0 => Style::default().fg(Color::Red),
                _ => Style::default(),
            };

            Row::new(vec![
                Cell::from(r.symbol),
                Cell::from(r.tf.label()),
                Cell::from(format!("{:.3}", r.q_up)),
                Cell::from(format!("{:.3}", r.q_dn)),
                Cell::from(format!("{:+.3}", r.delta)),
                Cell::from(lock_str).style(lock_style),
                Cell::from(worst_str).style(worst_style),
                Cell::from(pnl_str).style(pnl_style),
                Cell::from(format!("{:.3}", r.avg_up)),
                Cell::from(format!("{:.3}", r.avg_dn)),
                Cell::from(
                    r.avg_tot
                        .map(|v| format!("{v:.3}"))
                        .unwrap_or_else(|| "-".to_string()),
                ),
                Cell::from(if r.decision.is_empty() {
                    "-".to_string()
                } else {
                    r.decision
                }),
            ])
        })
        .collect();

    let table = Table::new(
        table_rows,
        [
            Constraint::Length(4),
            Constraint::Length(4),
            Constraint::Length(10),
            Constraint::Length(10),
            Constraint::Length(10),
            Constraint::Length(10),
            Constraint::Length(10),
            Constraint::Length(10),
            Constraint::Length(7),
            Constraint::Length(7),
            Constraint::Length(7),
            Constraint::Min(20),
        ],
    )
    .header(header)
    .block(Block::default().title("Portfolio").borders(Borders::ALL))
    .row_highlight_style(Style::default().add_modifier(Modifier::REVERSED));

    f.render_stateful_widget(table, area, table_state);
}

#[allow(dead_code)]
fn draw_open_orders(
    f: &mut Frame,
    orders: &SharedOrders,
    area: Rect,
    table_state: &mut TableState,
) {
    let o_guard = orders.blocking_read();

    let now = chrono::Utc::now();

    let mut rows: Vec<OrderSnapshot> = o_guard
        .values()
        .filter(|o| order_is_open(o))
        .cloned()
        .collect();
    rows.sort_by(|a, b| b.last_update_at.cmp(&a.last_update_at));

    let header_style = Style::default()
        .fg(Color::Cyan)
        .add_modifier(Modifier::BOLD);
    let header = Row::new(vec![
        Cell::from("Sym"),
        Cell::from("TF"),
        Cell::from("Out"),
        Cell::from("Side"),
        Cell::from("Px"),
        Cell::from("Rem"),
        Cell::from("Matched"),
        Cell::from("Age"),
        Cell::from("Order"),
        Cell::from("Event"),
    ])
    .style(header_style);

    let table_rows: Vec<Row> = rows
        .into_iter()
        .take(area.height.saturating_sub(3) as usize)
        .map(|o| {
            let remaining = order_remaining(&o);
            let age_s = (now - o.last_update_at).num_seconds().max(0);
            Row::new(vec![
                Cell::from(o.symbol),
                Cell::from(o.timeframe.label()),
                Cell::from(o.outcome),
                Cell::from(o.side),
                Cell::from(format!("{:.3}", o.price)),
                Cell::from(format!("{:.3}", remaining)),
                Cell::from(format!("{:.3}", o.size_matched)),
                Cell::from(format!("{age_s}s")),
                Cell::from(shorten(&o.order_id, 14)),
                Cell::from(shorten(&o.last_event, 16)),
            ])
        })
        .collect();

    let table = Table::new(
        table_rows,
        [
            Constraint::Length(4),
            Constraint::Length(4),
            Constraint::Length(6),
            Constraint::Length(6),
            Constraint::Length(7),
            Constraint::Length(9),
            Constraint::Length(9),
            Constraint::Length(6),
            Constraint::Length(16),
            Constraint::Min(10),
        ],
    )
    .header(header)
    .block(Block::default().title("Open Orders").borders(Borders::ALL))
    .row_highlight_style(Style::default().add_modifier(Modifier::REVERSED));

    f.render_stateful_widget(table, area, table_state);
}

fn shorten(s: &str, max: usize) -> String {
    if s.len() <= max {
        return s.to_string();
    }
    let keep = max.saturating_sub(3);
    format!("{}...", &s[..keep])
}

fn sym_rank(symbol: &str) -> i32 {
    if symbol.eq_ignore_ascii_case("BTC") {
        0
    } else if symbol.eq_ignore_ascii_case("ETH") {
        1
    } else if symbol.eq_ignore_ascii_case("SOL") {
        2
    } else if symbol.eq_ignore_ascii_case("XRP") {
        3
    } else {
        99
    }
}

fn tf_rank(tf: Timeframe) -> i32 {
    match tf {
        Timeframe::M15 => 0,
        Timeframe::H1 => 1,
    }
}
