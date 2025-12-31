# ARCHITECTURE.md

**Status:** LEGACY (Rust) + CURRENT (Python)
**Updated:** 2025-12-26

---

## Current System (Python)

The active trading system is Python-based:

| File | Purpose |
|------|---------|
| `ui_dashboard_live.py` | Main trading dashboard (Rich TUI) |
| `trade_executor.py` | Order execution engine |
| `polymarket_connector.py` | WebSocket + CLOB API connector |
| `trade_metrics_logger.py` | Observational metrics (JSONL) |
| `backtest_alpha_test.py` | Phase 1 backtest |

### Python Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    POLYMARKET APIs                       │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│              polymarket_connector.py                     │
│   GammaClient (HTTP) + ClobWebSocket (WS)               │
│   SessionManager (rollover handling)                     │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│              ui_dashboard_live.py                        │
│   DashboardState + check_and_execute_signal()           │
│   9 gates: ZONE, BOOK, CAP, EDGE, PRICE, SPREAD...     │
└─────────────────────────────────────────────────────────┘
                          │
              ┌───────────┼───────────┐
              ▼           │           ▼
┌──────────────────────┐  │  ┌──────────────────────┐
│    PAPER MODE        │  │  │     REAL MODE        │
│  (settlement sim)    │  │  │  trade_executor.py   │
└──────────────────────┘  │  └──────────────────────┘
                          │
                          ▼
              ┌──────────────────────┐
              │  METRICS LOGGER      │
              │ (observational only) │
              │ trade_metrics_logger │
              └──────────────────────┘
                          │
                          ▼
              logs/real/metrics/*.jsonl
```

---

## Legacy System (Rust) - PK8_PH

> **Note:** The Rust framework below is archived. The current system uses Python.

---

# LEGACY: Rust Framework Internals

## Overview

The PK8_PH framework is a production-grade Polymarket trading system written in Rust. It handles all infrastructure:
- WebSocket connections to Polymarket
- Order execution and management
- Paper trading simulation
- Terminal UI dashboard
- Parameter optimization

**You only need to write strategy logic.** Everything else is handled.

---

## Directory Structure

```
PK8_PH/
├── Cargo.toml                    # Project config and dependencies
├── .env                          # Runtime configuration
│
├── src/
│   ├── lib.rs                    # Library root (exports engine)
│   │
│   ├── engine/                   # Core Polymarket integration
│   │   ├── mod.rs               # Module exports
│   │   ├── types.rs             # Data structures
│   │   ├── gamma.rs             # Gamma API client (market metadata)
│   │   ├── clob_ws.rs           # CLOB WebSocket (order book)
│   │   ├── live_data_ws.rs      # Live data WebSocket (trades)
│   │   ├── user_ws.rs           # User WebSocket (your orders)
│   │   ├── metrics.rs           # Book metrics calculations
│   │   └── rollover.rs          # Market session transitions
│   │
│   └── bin/
│       ├── live_console.rs       # Main trading app entry
│       │
│       ├── live_console/         # Trading app modules
│       │   ├── ws.rs            # WebSocket stream manager
│       │   ├── paper.rs         # Paper trading engine
│       │   ├── trade.rs         # Order execution
│       │   ├── ui.rs            # Terminal UI (ratatui)
│       │   ├── balance.rs       # USDC balance polling
│       │   ├── user_ws.rs       # User order updates
│       │   │
│       │   └── strategies/       # STRATEGY CODE GOES HERE
│       │       ├── mod.rs       # Strategy registry
│       │       ├── types.rs     # Strategy trait definition
│       │       ├── noop.rs      # Default (does nothing)
│       │       └── balanced_arb.rs  # Our strategy (to build)
│       │
│       ├── backtest/             # Historical replay (to build)
│       │   ├── main.rs
│       │   ├── data_loader.rs
│       │   └── simulator.rs
│       │
│       └── optimizer.rs          # Parameter sweep tool
│
└── logs/                         # Runtime logs
```

---

## Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                     POLYMARKET APIS                          │
└─────────────────────────────────────────────────────────────┘
           │              │              │              │
           ▼              ▼              ▼              ▼
     ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
     │  Gamma  │    │  CLOB   │    │  Live   │    │  User   │
     │   API   │    │   WS    │    │ Data WS │    │   WS    │
     └─────────┘    └─────────┘    └─────────┘    └─────────┘
           │              │              │              │
           │         Order Book      Trades        Your Orders
           │              │              │              │
           ▼              ▼              ▼              ▼
     ┌─────────────────────────────────────────────────────────┐
     │                    ENGINE LAYER                          │
     │   gamma.rs    clob_ws.rs   live_data_ws.rs   user_ws.rs │
     └─────────────────────────────────────────────────────────┘
                              │
                              ▼
     ┌─────────────────────────────────────────────────────────┐
     │                   STREAM MANAGER                         │
     │                      ws.rs                               │
     │   Aggregates all data, maintains market state           │
     └─────────────────────────────────────────────────────────┘
                              │
                              ▼
     ┌─────────────────────────────────────────────────────────┐
     │                    TRADE LOOP                            │
     │                     trade.rs                             │
     │   Calls strategy, manages execution slots                │
     └─────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐    ┌──────────┐
        │ STRATEGY │   │  PAPER   │    │   LIVE   │
        │  MODULE  │   │ TRADING  │    │  TRADING │
        │ (yours!) │   │  paper.rs│    │ trade.rs │
        └──────────┘   └──────────┘    └──────────┘
              │               │               │
              ▼               ▼               ▼
     ┌─────────────────────────────────────────────────────────┐
     │                    TERMINAL UI                           │
     │                       ui.rs                              │
     │   Markets | Portfolio | Logs | Controls                  │
     └─────────────────────────────────────────────────────────┘
```

---

## Key Components

### 1. Engine Layer (`src/engine/`)

**gamma.rs** - Gamma API Client
- Fetches market metadata (condition IDs, token IDs)
- Resolves current/next markets in a series
- Used for market discovery

**clob_ws.rs** - CLOB WebSocket
- Connects to `wss://clob.polymarket.com`
- Subscribes to order book updates
- Provides bid/ask levels, depth

**live_data_ws.rs** - Live Data WebSocket
- Subscribes to trade activity
- `orders_matched` events
- Last trade price, size, direction

**metrics.rs** - Book Metrics
- Calculates best bid/ask
- Mid price, spread
- Depth at top N levels

**rollover.rs** - Market Transitions
- 15-minute markets roll over
- Derives current market slug from timestamp
- Handles session boundaries

### 2. Stream Manager (`ws.rs`)

Central coordinator:
- Maintains connection to all WebSockets
- Aggregates into unified market state
- Pushes updates to trade loop

State structure:
```rust
struct StreamState {
    symbol: String,           // "BTC"
    timeframe: Timeframe,     // 15m
    condition_id: String,     // Market ID
    token_up: String,         // UP token ID
    token_down: String,       // DOWN token ID
    up: OutcomeSnapshot,      // UP order book
    down: OutcomeSnapshot,    // DOWN order book
    end_date: DateTime,       // Settlement time
}
```

### 3. Trade Loop (`trade.rs`)

Main trading orchestration:
```rust
loop {
    // 1. Wait for market updates
    tick = tick_rx.recv();

    // 2. For each tradeable market
    for market in tradelist {
        // 3. Build strategy context
        ctx = StrategyCtx {
            symbol, timeframe, tau_seconds,
            up_bid, up_ask, down_bid, down_ask,
            qy, qn,  // Current positions
            ...
        };

        // 4. Get strategy decision
        quote = strategy.quote(&ctx);

        // 5. Execute orders
        ensure_buy_order(quote.up);
        ensure_buy_order(quote.down);
    }
}
```

### 4. Paper Trading (`paper.rs`)

Simulates realistic execution:

**Latency Modeling:**
```
POST order → wait PM_PAPER_POST_LATENCY_MS → order "OPEN"
CANCEL request → wait PM_PAPER_CANCEL_REQ_LATENCY_MS → "CANCEL_REQ"
            → wait PM_PAPER_CANCEL_CLEAR_LATENCY_MS → "CANCELLED"
```

**Queue Position:**
- Tracks where you are in queue
- Others can jump ahead (PM_PAPER_QUEUE_ADD_AHEAD_FRAC)
- Must wait for queue to clear before fill

**Maker Flow:**
- Simulates sell-side flow hitting your bids
- Base rate + depth-scaled + noise
- Only fills when flow reaches your level

### 5. Strategy System (`strategies/`)

**types.rs** - Core trait:
```rust
pub trait Strategy {
    fn name(&self) -> &'static str;
    fn quote(&mut self, ctx: &StrategyCtx<'_>) -> StrategyQuote;
}

pub struct StrategyCtx<'a> {
    pub symbol: &'a str,           // "BTC"
    pub timeframe: Timeframe,      // 15m
    pub tau_seconds: f64,          // Time remaining

    pub market: &'a str,           // Condition ID
    pub tok_up: &'a str,           // UP token ID
    pub tok_dn: &'a str,           // DOWN token ID

    pub by: Option<f64>,           // Best bid UP
    pub bn: Option<f64>,           // Best bid DOWN
    pub up_mid: Option<f64>,       // Mid price UP
    pub down_mid: Option<f64>,     // Mid price DOWN

    pub min_order_size: f64,       // Min order (usually 5)
    pub tick_size: f64,            // Price increment (0.01)

    pub qy: f64,                   // Position UP shares
    pub qn: f64,                   // Position DOWN shares
    pub current_exposure: f64,     // Net exposure
}

pub struct StrategyQuote {
    pub up: Desired,
    pub down: Desired,
}

pub struct Desired {
    pub px: Option<f64>,      // Price (None = no order)
    pub q: f64,               // Quantity
    pub why: &'static str,    // Reason (for logging)
}
```

**mod.rs** - Strategy registry:
```rust
pub fn strategy_from_env() -> Result<Box<dyn Strategy + Send>> {
    let name = std::env::var("PM_STRATEGY")
        .unwrap_or("noop".to_string());

    match name.as_str() {
        "noop" => Ok(Box::new(NoopStrategy::default())),
        "balanced_arb" => Ok(Box::new(BalancedArbStrategy::new())),
        _ => Err(anyhow!("unknown strategy: {}", name)),
    }
}
```

### 6. Terminal UI (`ui.rs`)

Bloomberg-style dashboard built with ratatui:

```
┌─ Markets ──────────────────────────────────────────────────┐
│ BTC:15m  UP: 0.51/0.52  DOWN: 0.48/0.49  Spread: 0.99     │
│ ETH:15m  UP: 0.50/0.51  DOWN: 0.49/0.50  Spread: 1.00     │
│ SOL:15m  UP: 0.53/0.54  DOWN: 0.46/0.47  Spread: 0.99     │
│ XRP:15m  UP: 0.48/0.49  DOWN: 0.51/0.52  Spread: 0.99     │
└────────────────────────────────────────────────────────────┘
┌─ Portfolio ────────────────────────────────────────────────┐
│ Cash: $10,000.00        Reserved: $0.00                    │
│                                                            │
│ Position    Shares    Avg Cost    Current    P&L          │
│ BTC UP      5         $0.48       $0.51      +$0.15       │
│ BTC DOWN    5         $0.49       $0.48      -$0.05       │
└────────────────────────────────────────────────────────────┘
┌─ Logs ─────────────────────────────────────────────────────┐
│ [trade] balanced_arb up={px:0.52,q:5} dn={px:0.48,q:5}    │
│ [paper] fill BTC UP 5 @ 0.52                               │
│ [paper] fill BTC DOWN 5 @ 0.48                             │
└────────────────────────────────────────────────────────────┘
```

---

## Adding a New Strategy

### Step 1: Create the file

`src/bin/live_console/strategies/balanced_arb.rs`

### Step 2: Implement the trait

```rust
use super::types::{Desired, Strategy, StrategyCtx, StrategyQuote};

pub struct BalancedArbStrategy {
    // Your state here
}

impl BalancedArbStrategy {
    pub fn new() -> Self {
        Self {
            // Initialize
        }
    }
}

impl Strategy for BalancedArbStrategy {
    fn name(&self) -> &'static str {
        "balanced_arb"
    }

    fn quote(&mut self, ctx: &StrategyCtx<'_>) -> StrategyQuote {
        // Your logic here

        StrategyQuote {
            up: Desired { px: Some(0.48), q: 5.0, why: "entry" },
            down: Desired { px: Some(0.49), q: 5.0, why: "entry" },
        }
    }
}
```

### Step 3: Register in mod.rs

```rust
mod balanced_arb;

pub use balanced_arb::BalancedArbStrategy;

pub fn strategy_from_env() -> Result<Box<dyn Strategy + Send>> {
    match name.as_str() {
        "noop" => Ok(Box::new(NoopStrategy::default())),
        "balanced_arb" => Ok(Box::new(BalancedArbStrategy::new())),
        _ => Err(...),
    }
}
```

### Step 4: Set in .env

```
PM_STRATEGY=balanced_arb
```

---

## Configuration Reference

### Trading
| Env Var | Default | Purpose |
|---------|---------|---------|
| PM_STRATEGY | noop | Strategy to use |
| PM_TRADE_ENABLED | 1 | Enable trading |
| PM_DRY_RUN | 1 | No real orders |
| PM_PAPER_TRADING | 1 | Paper mode |
| PM_WATCHLIST | BTC:15m,... | Markets to watch |
| PM_TRADELIST | BTC:15m,... | Markets to trade |

### Timing
| Env Var | Default | Purpose |
|---------|---------|---------|
| PM_WARMUP_SECONDS | 5.0 | Wait on startup |
| PM_MIN_REQUOTE_SECONDS | 1.0 | Min between requotes |
| PM_REQUOTE_MIN_TICKS | 3 | Min price change |
| PM_REQUOTE_MAX_AGE_SECONDS | 10 | Force requote after |

### Paper Trading
| Env Var | Default | Purpose |
|---------|---------|---------|
| PM_PAPER_STARTING_CASH | 10000 | Starting balance |
| PM_PAPER_POST_LATENCY_MS | 50 | Order post delay |
| PM_PAPER_CANCEL_REQ_LATENCY_MS | 50 | Cancel request delay |
| PM_PAPER_FLOW_BASE | 5 | Base fill rate/sec |
| PM_PAPER_SEED | 1 | RNG seed |

---

## Event Logging

All events logged to `logs/live_console_events.jsonl`:

```json
{"event":"paper_order_submit","ts":"...","symbol":"BTC","price":0.48,"size":5}
{"event":"paper_fill","ts":"...","symbol":"BTC","qty":5,"price":0.48}
{"event":"paper_order_cancelled","ts":"...","order_id":"..."}
```

Use for analysis:
```bash
cat logs/live_console_events.jsonl | jq 'select(.event=="paper_fill")'
```

---

## Debugging

### Enable debug logs
```bash
RUST_LOG=debug cargo run --bin live_console
```

### Log levels
- `error` - Only errors
- `warn` - Warnings and errors
- `info` - Normal operation (default)
- `debug` - Detailed tracing
- `trace` - Everything

### Common issues

**"No quotes available"**
- Market may be between sessions
- WebSocket might be reconnecting
- Check network connection

**"Strategy not found"**
- PM_STRATEGY doesn't match registered name
- Check mod.rs registration

**"Order rejected"**
- Price out of bounds (0.01-0.99)
- Size below minimum (usually 5)
- Insufficient funds
