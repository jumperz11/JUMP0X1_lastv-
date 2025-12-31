# RULEV3+ Strategy

**Version:** Phase 1 (LOCKED)
**Updated:** 2024-12-24

Directional trading strategy for Polymarket BTC 15-minute Up/Down markets.

---

## Core Concept

RULEV3+ makes directional bets based on:
- **Edge** - The mid price of the stronger side (market conviction proxy)
- **Timing** - CORE window (3:00-3:29) for reliable signals
- **Position limits** - One trade per session

---

## Phase 1 Configuration (LOCKED)

```
ZONE_MODE          = CORE-only (T3 = 3:00-3:29)
EDGE_THRESHOLD     = 0.64
SAFETY_CAP         = 0.72
SPREAD_MAX         = 0.02
ALPHA_GATE         = REMOVED
MAX_TRADES_SESSION = 1
POSITION_SIZE      = $5.00
```

---

## Trading Window (Phase 1)

```
Session Timeline (15 minutes):
0:00 ─────────────────────────────────────────────── 15:00
     │           │────│                              │
     0min        3:00 3:29                          15min
     │           │────│                              │
     WAIT        CORE (trade here)                   END
```

| Zone | Elapsed Time | Action |
|------|--------------|--------|
| EARLY | 0:00 - 2:59 | No trade - too early |
| **CORE** | 3:00 - 3:29 | Entry window (Phase 1) |
| DEAD | 3:30 - 4:59 | No trade |
| LATE | 5:00+ | No trade |

**Note:** RECOVERY zone (5:00-5:59) disabled in Phase 1.

---

## Gate Order (Phase 1)

All gates must pass to enter:

```
1. MODE_ZONE_GATE    → zone == CORE
2. BOOK_GATE         → valid bid/ask exists
3. SESSION_CAP       → trades_this_session < 1
4. EDGE_GATE         → edge >= 0.64
5. HARD_PRICE_GATE   → ask <= 0.72
6. PRICE_GATE        → ask < 0.72
7. BAD_BOOK          → spread >= 0 AND bid <= ask
8. SPREAD_GATE       → spread <= 0.02
9. EXECUTOR_VALID    → zone limits, cooldowns
```

---

## Direction Selection

```python
up_mid = (up_bid + up_ask) / 2
down_mid = (down_bid + down_ask) / 2

if up_mid >= down_mid:
    direction = "Up"
    edge = up_mid
else:
    direction = "Down"
    edge = down_mid
```

The side with higher mid price shows stronger market conviction.

---

## Position Sizing

```python
shares = cash_per_trade / ask_price

# Example:
# $5.00 / $0.65 = 7.69 shares
```

---

## Payout Structure

```python
if_win = shares * 1.00 - cost   # Shares pay $1 each
if_lose = -cost                  # Lose entire stake
```

| Entry @ | Shares ($5) | If Win | If Lose | Break-even WR |
|---------|-------------|--------|---------|---------------|
| $0.65 | 7.69 | +$2.69 | -$5.00 | 65% |
| $0.68 | 7.35 | +$2.35 | -$5.00 | 68% |
| $0.70 | 7.14 | +$2.14 | -$5.00 | 70% |

---

## Backtest Results (Phase 1)

| Metric | Value |
|--------|-------|
| Total sessions | 2,085 |
| Total trades | 1,064 |
| Trades per session | 0.51 |
| **Win rate** | **72.09%** |
| **AvgPnL/trade** | **$0.3276** |
| **Total PnL** | **$348.58** |
| Max drawdown | $65.35 |
| Avg ask at entry | 0.6768 |
| Avg spread at entry | 0.0104 |

---

## Settlement

Polymarket auto-settles at session end:
- If BTC went up: UP shares pay $1.00, DOWN pays $0
- If BTC went down: DOWN shares pay $1.00, UP pays $0

**No need to sell** - hold until settlement.

---

## Safety Features

### Double Execution Lock
```
TRADING_MODE=real AND EXECUTION_ENABLED=true
```
Both must be true for real orders.

### Session Trade Cap
```python
if session_trade_count >= 1:
    SKIP  # Max 1 trade per session
```

### Spread Gate
```python
if spread > 0.02:
    SKIP  # Spread too wide
```

### Kill Switch
```python
if degraded_fills >= 2:
    kill_switch = True  # Stop all trading
```

---

## Why Alpha Gate Was Removed (Phase 1)

The alpha formula was structurally broken:

```
edge = mid = (bid + ask) / 2
alpha = edge - ask = mid - ask = -spread/2

Result: alpha is ALWAYS negative
Gate: alpha >= 0.02 can NEVER pass
```

**Fix:** Removed alpha gate, added spread hygiene gate instead.

**Phase 2:** Build p_model from historical calibration, then reintroduce alpha = p_model - ask.

---

## What NOT to Do

| Bad Practice | Why |
|--------------|-----|
| Trade outside CORE zone | Not validated in Phase 1 |
| Pay > $0.72 | Negative expected value |
| Multiple trades per session | Overexposure |
| Ignore spread gate | Wide spreads = bad fills |
| Trade when spread > 0.02 | Illiquid conditions |

---

## Key Insights

1. **Edge = Classification, not Speed**
   - We classify which side has conviction
   - Not racing on milliseconds

2. **CORE Window is Primary**
   - 3:00-3:29 (30 seconds)
   - Signal most reliable here

3. **Entry Price is Critical**
   - Average entry: $0.6768
   - Safety cap: $0.72
   - Never overpay

4. **One Trade Per Session**
   - Forces selectivity
   - Limits exposure
   - Matches backtest

---

**Strategy Version:** RULEV3+ Phase 1
**Config Signature:** `PHASE1-SPREAD-0.02-EDGE-0.64-CAP-0.72-CORE-ONLY`
