# Directional Trading Strategy - Roadmap & Documentation

**Status:** ARCHIVE (Historical Research)
**Note:** This roadmap led to RULEV3+ Phase 1. See PHASE1_LOCKED.md for current config.

---

**Project:** PK8_PH - Polymarket 15-Minute Binary Options
**Status:** Completed - Evolved into RULEV3+ Phase 1
**Last Updated:** 2024-12-24

---

## Executive Summary

We pivoted from **balanced arbitrage** (buying both UP and DOWN) to **directional trading** (predicting the winner). The balanced approach failed because:

1. Can't get fills on both sides simultaneously
2. DOWN side rarely fills at limit prices
3. 1800+ people in Telegram tried and failed
4. Gabagool (the successful trader) likely has privileged/insider access

**New Strategy:** Find signals that predict which side (UP or DOWN) wins, then place directional bets.

---

## The Journey So Far

### Phase 1: Balanced Arbitrage (FAILED)
- Built backtest showing 98.5% win rate
- Reality: backtest assumed perfect fills
- Real win rate on one-sided positions: ~54%
- Conclusion: Backtest is fiction without fill simulation

### Phase 2: Telegram Research
- Analyzed 5,254 messages from Polymarket Lounge
- Key insight from InsideTrader: "Speed is not most important. I have 5-9% edge from testing data."
- Key insight from Serion: "50% of my signals come from OUTSIDE Polymarket"
- Discovered 500ms taker speed bump killed latency arb
- Gabagool survives using MAKER orders only

### Phase 3: The Pivot Decision
**Question:** What determines if UP or DOWN wins?
**Answer:** Chainlink price at T+15min vs T+0

**Question:** Can we predict Chainlink's direction?
**Answer:** Find which data source LEADS Chainlink, use it as a signal.

### Phase 4: Multi-Signal Data Collection (CURRENT)
Built tools to record every possible signal source and analyze correlation with settlement.

---

## Tools Built

### 1. `price_recorder`
Basic price tick recorder from 4 exchanges.

```bash
cargo run --release --bin price_recorder -- BTC ./logs
```

**Output:** `./logs/price_ticks_YYYYMMDD.jsonl`

### 2. `lead_lag_analyzer`
Analyzes which price source moves first on big moves.

```bash
cargo run --release --bin lead_lag_analyzer -- ./logs/price_ticks_*.jsonl 5
```

### 3. `correlation_analyzer`
Records prices at checkpoints, analyzes direction correlation.

```bash
# Record
cargo run --release --bin correlation_analyzer -- --live BTC ./logs

# Analyze
cargo run --release --bin correlation_analyzer -- --analyze BTC ./logs
```

### 4. `multi_signal_recorder` (MAIN TOOL)
Records 14 data sources at 17 checkpoints per session.

```bash
# Record (run overnight)
cargo run --release --bin multi_signal_recorder -- --live BTC ./logs

# Analyze
cargo run --release --bin multi_signal_recorder -- --analyze BTC ./logs
```

---

## Data Sources

### Price Sources (8)

| Source | WebSocket URL | What It Measures |
|--------|---------------|------------------|
| **Binance Spot** | `wss://stream.binance.com:9443/ws/btcusdt@trade` | Spot market price |
| **Binance Futures** | `wss://fstream.binance.com/ws/btcusdt@aggTrade` | Perp futures price |
| **Coinbase** | `wss://ws-feed.exchange.coinbase.com` | US institutional flow |
| **Bybit** | `wss://stream.bybit.com/v5/public/spot` | Asian retail flow |
| **Kraken** | `wss://ws.kraken.com` | European flow |
| **OKX** | `wss://ws.okx.com:8443/ws/v5/public` | Asian derivatives flow |
| **Chainlink RTDS** | Polymarket WebSocket | THE SETTLEMENT SOURCE |
| **Pyth Network** | `wss://hermes.pyth.network/ws` | Alternative oracle |

### Sentiment Sources (6)

| Source | Endpoint | Hypothesis |
|--------|----------|------------|
| **Funding Rate** | Binance REST | Negative = shorts paying = UP wins |
| **Open Interest** | Binance REST | Rising OI + price up = continuation |
| **Long/Short Ratio** | Binance REST | >1.5 = crowded longs = DOWN |
| **Liquidations** | Binance WS | More long liqs = DOWN momentum |
| **Orderbook Imbalance** | Binance WS | More bids = UP pressure |
| **CVD** | Binance WS | Positive CVD = buyers winning = UP |
| **Fear & Greed** | alternative.me | Extreme fear = UP, Extreme greed = DOWN |

---

## Checkpoints Recorded

For each 15-minute session, we record signals at:

```
T+0      (session start - baseline)
T+15s    (very early signal)
T+30s    (early signal)
T+45s
T+60s    (1 minute - early enough to trade)
T+90s
T+2m
T+3m
T+5m     (5 minutes - decent time window)
T+7m
T+10m    (10 minutes - getting late)
T+12m
T+13m
T+14m
T+14m30s
T+14m45s (15 seconds before settlement)
T+14m59s (1 second before - baseline for winner)
```

---

## Analysis Output

When you run `--analyze`, you get:

### Price Source Correlation Table
```
CHECKPOINT    BIN_S   BIN_F    COIN   BYBIT  KRAKEN     OKX   CHAIN    PYTH
--------------------------------------------------------------------------------
T+30s         51.5%   51.3%   51.0%   51.2%   51.1%   51.2%   51.4%   51.5%
T+60s         53.8%   53.5%   52.9%   53.1%   53.0%   53.2%   53.6%   53.9%
T+5m          58.2%   57.9%   57.1%   57.5%   57.3%   57.4%   58.0%   58.3%
T+10m         72.5%   72.1%   71.2%   71.8%   71.5%   71.6%   72.3%   72.6%
T+14m         89.1%   88.7%   87.5%   88.2%   87.9%   88.0%   88.9%   89.2%
```

### Sentiment Correlation Table
```
CHECKPOINT     FUND   LS_RAT    LIQS  OB_IMB     CVD
--------------------------------------------------------
T+0           52.1%   51.8%   50.0%   50.5%   50.2%
T+60s         52.3%   52.0%   51.2%   54.2%   55.1%
T+5m          52.5%   52.2%   52.8%   55.8%   56.9%
```

---

## Key Questions We're Answering

### 1. Which source leads?
If Binance Futures shows 55% correlation at T+60s while others show 52%, use Binance Futures.

### 2. When is correlation meaningful?
- T+30s: ~51% (basically noise)
- T+60s: ~54% (slight edge)
- T+5m: ~58% (tradeable edge)
- T+10m: ~72% (strong but late)

### 3. Can we stack signals?
If price direction = UP (53%) AND CVD positive (55%) AND OB imbalance positive (54%):
- Combined might give 60%+ accuracy
- Need data to test this hypothesis

---

## Roadmap

### Phase 5: Data Collection (NEXT)
- [ ] Run `multi_signal_recorder --live BTC ./logs` overnight
- [ ] Collect 50+ sessions (12+ hours)
- [ ] Ensure all sources are connecting properly

### Phase 6: Analysis
- [ ] Run `multi_signal_recorder --analyze BTC ./logs`
- [ ] Identify best single predictors at each checkpoint
- [ ] Test signal combinations
- [ ] Find optimal entry time (earliest checkpoint with >55% accuracy)

### Phase 7: Signal Validation
- [ ] Paper trade the winning signal for 1 week
- [ ] Track prediction accuracy vs actual winners
- [ ] Calculate expected value with realistic fill rates

### Phase 8: Trading Bot
- [ ] Build directional trading bot using winning signal
- [ ] Use MAKER orders only (avoid 500ms speed bump)
- [ ] Implement position sizing based on signal confidence
- [ ] Add risk management (max position, stop loss)

### Phase 9: Live Trading
- [ ] Start with minimum position sizes
- [ ] Monitor fill rates and actual PnL
- [ ] Iterate on signal weights based on live data

---

## File Structure

```
PK8_PH/
├── src/
│   └── bin/
│       ├── price_recorder.rs       # Basic price recorder
│       ├── lead_lag_analyzer.rs    # Lead/lag analysis
│       ├── correlation_analyzer.rs # Simple correlation
│       ├── multi_signal_recorder.rs # MAIN TOOL - 14 sources
│       ├── backtest.rs             # Original backtest (deprecated)
│       └── live_console.rs         # Live trading console
├── logs/
│   ├── price_ticks_YYYYMMDD.jsonl
│   ├── sessions_btc.jsonl
│   └── multi_signal_sessions_btc.jsonl
├── docs/
│   ├── BACKTEST_RESULTS.md
│   ├── TELEGRAM_ANALYSIS_REPORT.md
│   ├── TELEGRAM_ALPHA_REPORT.md
│   └── DIRECTIONAL_TRADING_ROADMAP.md (this file)
└── Cargo.toml
```

---

## Key Insights from Research

### InsideTrader's Rules
1. "+EV requires win rate > average entry price"
2. "Speed is not the most important thing"
3. "I have 5-9% edge from testing data"
4. "I record data and test on it" (not trading yet)

### Kizo Azuki's Forensic Finding
"Gabagool always ends with PERFECT variance between UP and DOWN shares. Even 0.5 share difference would show as anomaly - it never happens. Impossible without privileged fills."

### Felix Poirier (HFT Background)
1. "To get data from Binance to Polymarket is already 150ms"
2. "Making a robust backtester is probably not worth your time"
3. "Sub-200ms signal-to-order is competitive"
4. "Limit orders bypass the 500ms taker speed bump"

### The 500ms Speed Bump
- Polymarket added 500ms delay for TAKER orders
- MAKER orders (limit orders) are NOT affected
- This killed most latency arbitrage strategies
- Gabagool survives because he only uses limit orders

---

## Success Criteria

### Minimum Viable Edge
- Signal accuracy: >55% at T+60s or earlier
- Expected value: positive after fees (~2% round trip)
- Fill rate: >70% on limit orders

### Target Performance
- Signal accuracy: >60%
- Entry time: T+60s (14 minutes to settlement)
- Position size: $50-100 per trade
- Daily sessions: 96 (every 15 minutes)
- Expected daily profit: $50-200

---

## Commands Reference

```bash
# Build all tools
cargo build --release

# =====================================
# MAIN WORKFLOW
# =====================================

# Record multi-signal data (run overnight)
./target/release/multi_signal_recorder --live BTC ./logs

# Analyze correlation (Rust)
./target/release/multi_signal_recorder --analyze BTC ./logs

# Analyze correlation (Python - more detailed)
python3 scripts/analyze_data.py ./logs BTC

# =====================================
# HELPER SCRIPTS
# =====================================

# Run recorder with logging
./scripts/run_recorder.sh BTC ./logs --live

# Quick test (60 seconds)
./scripts/run_recorder.sh BTC ./test_logs --test

# Full test (2 sessions, ~35 minutes)
./scripts/quick_test.sh

# =====================================
# OTHER TOOLS
# =====================================

# Quick price recording
./target/release/price_recorder BTC ./logs

# Lead/lag analysis
./target/release/lead_lag_analyzer ./logs/price_ticks_*.jsonl 5
```

---

## Scripts

### `scripts/run_recorder.sh`
Wrapper script for running the recorder with different modes.

```bash
./scripts/run_recorder.sh BTC ./logs --live     # Continuous recording
./scripts/run_recorder.sh BTC ./logs --analyze  # Run analysis
./scripts/run_recorder.sh BTC ./test_logs --test  # 60-second test
```

### `scripts/analyze_data.py`
Python analysis script with detailed output.

```bash
python3 scripts/analyze_data.py ./logs BTC
python3 scripts/analyze_data.py ./test_logs BTC --verbose
```

### `scripts/quick_test.sh`
Runs recorder for 2 full sessions (~35 minutes) then analyzes.

```bash
./scripts/quick_test.sh
```

---

## Next Action

**RUN THIS OVERNIGHT:**
```bash
cd /Users/jumperz/PROJES/JUMP01X/PK8_PH
./target/release/multi_signal_recorder --live BTC ./logs
```

Then analyze in the morning:
```bash
./target/release/multi_signal_recorder --analyze BTC ./logs
```

---

*Document maintained as part of the PK8_PH project*
