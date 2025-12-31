# KNOWLEDGE.md - InsideTrader Intel Archive

**Status:** ARCHIVE (Historical Reference)
**Note:** This document preserves research from the balanced arbitrage phase. Current strategy is RULEV3+ Phase 1 (directional trading).

---

This document preserves all trading knowledge acquired from InsideTrader, a trader with 17 years of development experience who shared his Polymarket framework and insights.

---

## Who is InsideTrader

- 17 years development experience
- Built the Rust trading framework we're using
- Active Polymarket trader
- Sold tick data and shared framework knowledge
- Uses Bloomberg-terminal style dashboard

---

## Core Trading Insights

### 1. Edge Decay

> "if you have 54% edge at open, you dont have it in 1m in"

**What this means:**
- Best trading opportunities exist in the first 1-2 minutes of a 15-minute market
- After that, the market prices in efficiently
- Don't chase late entries thinking you have edge

**Application:**
- Entry window: First 2 minutes only (PM_TAU0_SECONDS=120)
- After 2 minutes elapsed, no new positions

---

### 2. Don't Snipe 0.99

> "Thats the most worse strategy you can build"
> "one flip in the last seconds will set you back for the rest of 100 trades"

**What this means:**
- When price is 0.99/0.01, outcome looks "certain"
- But last-second flips happen
- One loss at 0.99 wipes 99 wins at 0.01

**Example:**
```
Buy 100 shares @ $0.99 = $99.00 cost
If wins: Get $100, profit $1.00
If loses: Get $0, lose $99.00

Risk/Reward: 99:1 against you
```

**Application:**
- Exit window: Stop trading last 30 seconds (PM_STOP_NEW_SECONDS=30)
- Never buy at extreme prices (>0.90 or <0.10)

---

### 3. Binance/Chainlink Correlation

> "binance kline outcome like 98% matches with chainlink"

**What this means:**
- Chainlink oracle = what Polymarket uses to settle markets
- Binance candle data (klines) predicts Chainlink 98% of time
- Can use Binance to gauge direction

**Technical detail:**
- Polymarket settles based on Chainlink price feed
- Chainlink aggregates from multiple sources including Binance
- 15-minute candle close on Chainlink determines settlement

**Application:**
- Could use Binance API to track spot price
- But NOT for latency arbitrage (see below)
- Useful for understanding market direction

---

### 4. 500ms Taker Speedbump

> "latency not tradable since 500ms taker speedbump"
> "otherwise it was very profitable before"

**What this means:**
- Polymarket added 500ms delay on taker orders
- Can't race to take liquidity faster than others
- Pure latency arbitrage is dead

**Historical context:**
- Before speedbump: Fast connections could snipe prices
- Now: Everyone has same 500ms delay
- Speed advantage no longer exists

**Application:**
- Don't build for speed arbitrage
- Need PREDICTIVE edge, not latency edge
- Our balanced arbitrage doesn't rely on speed

---

### 5. Position Balance = Profit

From InsideTrader's dashboard screenshot comparing his portfolio to tracked users:

| Trader | Delta (Imbalance) | P&L |
|--------|-------------------|-----|
| InsideTrader | ~60 | +$53 |
| ExpressoMartini | +1883 | -$709 |
| Other tracked | Large deltas | Red (losses) |

**What this means:**
- Low delta (balanced positions) = profit
- High delta (one-sided) = losses
- The pattern is consistent across traders

**Visual from his dashboard:**
- His positions: Small deltas, green P&L
- Tracked users: Large deltas, red P&L

**Application:**
- MAX_ONE_SIDED = 6 is critical
- Always maintain balance
- Prioritize lagging side fills

---

## Framework Details

### Why Rust (100ms vs 2000ms)

InsideTrader built in Rust because:
- Node.js execution: ~2000ms
- Rust execution: ~100ms
- 20x faster execution

Not for latency arbitrage (speedbump killed that), but for:
- More reliable fills
- Better order management
- Handling multiple markets simultaneously

### Paper Trading Simulation

His paper trading engine simulates:
- Order post latency (PM_PAPER_POST_LATENCY_MS)
- Cancel latency (PM_PAPER_CANCEL_REQ_LATENCY_MS)
- Queue position modeling
- Maker flow simulation

This allows realistic backtesting without risking real money.

### Optimizer Parameters (Ghost Parameters)

These parameters exist in the optimizer but aren't implemented in the noop strategy - they're hints at what his actual strategy tuned:

| Parameter | Purpose | Range |
|-----------|---------|-------|
| PM_EPS0 | Primary spread threshold | 0.001-0.030 |
| PM_EPS1 | Secondary spread threshold | 0.005-0.080 |
| PM_DELTA0 | Base price edge | 0.0-0.050 |
| PM_DELTA1 | Aggressive price edge | 0.0-0.080 |
| PM_TAU0_SECONDS | Time threshold for edge decay | 120-3600s |
| PM_U0 | Base position size | 10-400 |
| PM_U_MIN | Minimum position size | 0-100 |
| PM_BETA_SKEW | Skew aggressiveness | 0.2-1.0 |
| PM_SKEW_DEADZONE | Imbalance tolerance | 0.0-0.05 |
| PM_SKEW_TARGET_EXTRA_DELTA | Extra edge for skew | 0.0-0.05 |
| PM_STOP_NEW_SECONDS | Stop before expiry | 5-180s |
| PM_PROB_ALPHA | Probability smoothing | 0.05-0.50 |
| PM_SIGMA_ALPHA | Volatility smoothing | 0.05-0.50 |
| PM_TOX_RHO | Toxicity penalty | 0.00-0.50 |
| PM_TOX_LOOKAHEAD_SECONDS | Adverse selection window | 1-30s |

---

## Data Details

### Tick Data Specs
- 23 days: Nov 27 - Dec 20, 2025
- Markets: BTC, ETH, SOL, XRP (15-minute)
- ~8,340 total market sessions
- ~250ms between ticks (~4 ticks/second)
- ~14GB uncompressed

### Tick Format
```json
{
  "ts": "2025-11-29T17:15:06.142Z",
  "t": 1764436506142,
  "slug": "btc-updown-15m-1764436500",
  "symbol": "BTC",
  "variant": "15m",
  "startUnix": 1764436500,
  "endUnix": 1764437400,
  "spotBn": 90722.49,
  "best": {
    "Up": {"bid": 0.49, "ask": 0.51},
    "Down": {"bid": null, "ask": null}
  },
  "liquidity": {
    "Up": 144124.8595,
    "Down": 0
  },
  "minutesLeft": 14.9
}
```

### Key Fields
- `ts` - ISO timestamp
- `t` - Unix milliseconds
- `spotBn` - Binance spot price
- `best.Up/Down.bid/ask` - Order book best prices
- `liquidity` - Available depth
- `minutesLeft` - Time remaining in market

---

## TEST1 Failure Analysis

### What Happened
- Strategy: Early entry arbitrage (correct concept)
- Win rate: 84%
- Result: LOST MONEY

### The Problem
Position accumulation without balance:
```
Tick 1: Buy 2 UP @ $0.48 (DOWN not quoted)
Tick 2: Buy 2 UP @ $0.47 (DOWN still missing)
Tick 3: Buy 3 UP @ $0.46 (DOWN appears at $0.54)
Tick 4: Buy 3 UP @ $0.45 (Keep buying UP, ignore DOWN)
Final: 10 UP, 0 DOWN
```

When UP lost: -$4.86 (one loss wiped 6-7 wins)

### The Fix
1. **MAX_ONE_SIDED = 6** - Never more than 6 shares without the other side
2. **Prioritize lagging side** - If UP > DOWN, bid aggressively on DOWN
3. **Stop if imbalanced** - At 6-0, stop buying leading side entirely

### Correlation Found
| Position Type | Outcome |
|--------------|---------|
| 4+4, 5+5, 6+6 (balanced) | WIN |
| 10+0, 9+1, 8+2 (one-sided) | LOSS |

Early balanced entries correlated with winning trades.

---

## Market Observations

### Liquidity Patterns
- UP side often has liquidity first
- DOWN side can lag by several seconds
- Both sides needed for true arbitrage

### Spread Patterns
- Best spreads at market open
- Spreads widen near settlement
- 0.49/0.51 common early (2 cent spread)

### Settlement Behavior
- Chainlink determines outcome
- Settlement at exact 15-minute mark
- No early settlement possible

---

## Quotes Archive

Direct quotes from InsideTrader for reference:

On edge decay:
> "if you have 54% edge at open, you dont have it in 1m in"

On sniping 0.99:
> "Thats the most worse strategy you can build"
> "one flip in the last seconds will set you back for the rest of 100 trades"

On Binance correlation:
> "binance kline outcome like 98% matches with chainlink"

On latency:
> "latency not tradable since 500ms taker speedbump"
> "otherwise it was very profitable before"

---

## Unanswered Questions

Things we don't know:
1. What was his actual strategy code? (Only gave us noop)
2. What parameters did he settle on after optimization?
3. What's his actual live trading P&L?
4. Are there other markets he trades (hourly, daily)?
5. Does he use Binance data in his strategy or just for monitoring?

---

## Key Takeaways

1. **Edge is temporal** - First 2 minutes or nothing
2. **Balance is everything** - Imbalance = death
3. **Don't be greedy** - 0.99 snipes are traps
4. **Speed doesn't matter** - 500ms speedbump levels field
5. **Binance correlates** - 98% match with Chainlink settlement
6. **Small wins compound** - 3% per trade, many trades per day

---

## Additional Wisdom (Dec 2025)

### On Spread Lock
> "Spread lock is important"

**Meaning:** When entering, ensure you can get both sides at favorable prices. Don't enter if only one side is available.

### On Early Edge
> "Slight edge at START to build toward"

**Meaning:** The edge exists at market open. You build your position in the first 60 seconds, not react to mid-session moves.

### On Speed (Confirmed)
> "Speed is not most important"

**What we learned (Dec 20, 2025):**
- Tick resolution ~130ms average
- Cannot reliably measure sub-100ms leads
- Edge is STRUCTURAL (classification), not SPEED (milliseconds)
- Chainlink lags by SECONDS, not milliseconds

### On Win Rate Requirements
> "+EV requires win rate > entry price"

**Example:**
- Entry at 48¢ means need >48% win rate
- Entry at 55¢ means need >55% win rate
- Rule v1 aims for 60%+ accuracy on 48-52¢ entries

---

## December 2025 Research Findings

### Lead-Lag Analysis (Corrected)

**Original (Buggy):** "Coinbase leads by 300ms"
**Corrected:** "Coinbase tends to move first in ~75% of events, within a 100ms resolution window"

**Why correction:**
- $0.50 threshold was noise (6000+ fake events)
- $17.60 threshold (0.02%) = real events (110 total)
- Tick spacing ~130ms means can't claim sub-100ms precision
- Event ordering is valid; precise latency is not

### Chainlink Behavior

**Measured:**
- Median tick gap: 840ms
- Max gap observed: 3.4 seconds
- Often frozen while exchanges move $30-50+
- Settlement oracle - lags by design

**Trading implication:**
- Don't try to race Chainlink (impossible)
- Watch for persistent dislocation (seconds, not ms)
- Frozen Chainlink + moving exchanges = directional signal

### OB Slope Discovery

**Finding:** OB value at T+0 is noise. OB CHANGE in first 60s predicts winners.

**Session example (20:15:00 UTC):**
```
T+0:   OB = -0.83 (very bearish)
T+60s: OB = +0.04 (turned bullish)
Winner: UP

OB_slope = +0.87 → predicted UP correctly
Raw OB at T+0 would have said DOWN (wrong)
```

**Rule derived:**
- OB_slope ≥ +0.5 in 60s → lean UP
- OB_slope ≤ -0.5 in 60s → lean DOWN

### CVD Behavior

**Finding:** CVD lags at session start, catches up by T+10m.

**Same session:**
```
T+60s: CVD = -0.29 (wrong signal)
T+10m: CVD = +3.47 (finally correct)
Winner: UP
```

**Trading implication:**
- Don't use CVD for early entry decisions
- CVD is confirmation, not prediction
- OB slope is better early signal

---

## Key Quote

**"I don't trade colors. I trade color CHANGE in the first minute."**

This summarizes the directional trading insight:
- T+0 snapshot is context, not signal
- Change from T+0 to T+60s is the signal
- Enter T+30s to T+60s with improving indicators
