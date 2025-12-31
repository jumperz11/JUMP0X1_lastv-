# RULEV3.1 LOGIC (V3.2 Regime Modifier Disabled)
**Date: 2025-12-31**

---

## ENTRY GATES (Sequential)

| Gate | Name | Condition | Action if FAIL |
|------|------|-----------|----------------|
| 1 | **MODE_ZONE** | zone == "CORE" | SKIP - zone not allowed |
| 2 | **BOOK** | bid > 0 AND ask > 0 | SKIP - no order book |
| 3 | **SESSION_CAP** | session_trades < 1 | SKIP - max 1/session |
| 4 | **DYNAMIC_EDGE** | see below | SKIP (silent) |
| 5 | **HARD_PRICE** | ask <= **0.72** | SKIP - price too high |
| 6 | **SAFETY_CAP** | ask < **0.72** | SKIP - safety cap |
| 7 | **BAD_BOOK** | spread >= 0, bid <= ask | SKIP - invalid book |
| 8 | **SPREAD** | spread <= 0.02 | SKIP - spread too wide |
| 9 | **EXECUTOR** | cooldown, zone limits | SKIP - executor block |

### DYNAMIC_EDGE Gate (V3.2)

Replaces fixed `edge >= 0.64` with pricing-aware thresholds:

```python
if ask <= 0.66:
    required_edge = 0.64   # Cheap prices = forgiving
elif ask <= 0.69:
    required_edge = 0.67   # Mid prices = moderate
else:
    required_edge = 0.70   # Expensive prices = ruthless

# REGIME MODIFIER (V3.2 - Key Change)
if regime == "CHOPPY":
    required_edge += 0.03  # Raise bar during fragmented sentiment
```

**Rationale:** Aligns required accuracy with payout math AND market conviction.
- At ask=0.65, payout ratio = 54% (forgiving)
- At ask=0.70, payout ratio = 43% (need higher confidence)
- During CHOPPY regime, add +0.03 to compensate for whipsaw risk

### REGIME DETECTION (V3.2 - New)

Based on UP token mid-price oscillations over 5 minutes:

```python
# Count direction reversals (crossings) in 5-minute window
# Crossing = price moved >= 0.1% one direction, then reversed

if crossings >= 6:
    regime = "CHOPPY"   # High oscillation, low conviction → +0.03 edge
elif crossings <= 2:
    regime = "STABLE"   # Clear trend, high conviction → no modifier
else:
    regime = "NEUTRAL"  # Normal → no modifier
```

**Key Insight:** Losses don't correlate with direction (GREEN/RED).
They correlate with FLAT/flickering sentiment (belief fragmentation).
Direction = irrelevant. Stability = predictive.

---

## ZONE TIMING

```
Session: 15 minutes total (900 seconds)

0:00─────2:30─────3:45─────────────────15:00
  EARLY    │ CORE  │       NO TRADE
           │       │
         TRADE   BLOCKED
```

| Zone | Time | Status |
|------|------|--------|
| EARLY | 0:00 - 2:29 | NO TRADE |
| **CORE** | **2:30 - 3:45** | **TRADE** |
| DEAD | 3:46 - 4:59 | NO TRADE |
| RECOVERY | 5:00 - 5:59 | **DISABLED** (requires PM_ZONE_MODE=T3+T5) |
| LATE | 6:00+ | NO TRADE |

**RULEV3 = CORE ONLY** (one window, one trade)

---

## EDGE CALCULATION

```python
# Direction = which side is more expensive (market thinks will win)
if down_ask > up_ask:
    direction = "Down"
    edge = down_ask  # Use ask as edge proxy
else:
    direction = "Up"
    edge = up_ask
```

---

## TRADE SIZING

```python
cash_per_trade = $5.00
shares = cash_per_trade / ask_price
# e.g., $5 / $0.65 = 7.69 shares
```

---

## OUTCOME

```
IF WIN:  pnl = shares * (1.00 - ask)  # e.g., 7.69 * 0.35 = +$2.69
IF LOSE: pnl = -cost                   # e.g., -$5.00
```

---

## KILLSWITCH CONDITIONS

| Trigger | Threshold | Action |
|---------|-----------|--------|
| Consecutive losses | >= 3 | KILL |
| Cumulative PnL | <= -$9.00 | KILL |
| Degraded fills | >= 2 | KILL |

---

## CONFIG (LOCKED)

```python
CONFIG = {
    "strategy": "RULEV3.2",
    "mode": "T3-only",           # CORE zone only
    "threshold": 0.64,           # Base edge (actual is dynamic)
    "safety_cap": 0.72,          # Max ask price
    "hard_price_cap": 0.72,      # Hard price ceiling
    "cash_per_trade": 5.00,      # Risk per trade
    "max_trades_per_session": 1, # One shot per session
}

# DYNAMIC_EDGE thresholds (base):
#   ask <= 0.66 → edge >= 0.64
#   ask <= 0.69 → edge >= 0.67
#   else        → edge >= 0.70

# REGIME MODIFIER (V3.2):
#   CHOPPY (crossings >= 6) → add +0.03 to required edge
#   STABLE/NEUTRAL         → no modifier
```

---

## EXECUTION FLOW

```
1. WebSocket receives live prices
2. Check zone == CORE
3. Check session_trades < 1
4. DYNAMIC_EDGE: Compute required_edge (based on ask)
5. REGIME: Check crossings → if CHOPPY, add +0.03 to required_edge
6. Check edge >= required_edge
7. Check ask < 0.72
8. Check spread <= 0.02
9. BUY {direction} @ {ask}
10. Wait for session end
11. Settle: winner = side closer to $1.00
12. Update PnL, check killswitch
```

---

## VALIDATION RESULTS (2025-12-26)

Tested proposed momentum filter against 1,008 backtest trades:

| Test | Result |
|------|--------|
| Block counter-trend in TRENDING | **REJECTED** - loses $7.60 |
| Edge+0.03 in TRENDING | **REJECTED** - loses $9.45 |
| Loss clustering check | **NOT FOUND** - losses spread evenly |

---

## V3.1 BACKTEST RESULTS (2025-12-30)

Tested DYNAMIC_EDGE gate against 2,085 sessions (1,448 V3 trades):

| Ask Range | Trades | Win Rate | PnL |
|-----------|--------|----------|-----|
| 0.65-0.66 | 813 | 70.6% | +$336.63 |
| 0.67-0.68 | 183 | 74.9% | +$101.46 |
| 0.69-0.70 | 156 | 73.7% | +$47.02 |
| >0.70 | 296 | 71.6% | **-$1.62** |

**Key Finding:** Trades at ask > 0.70 are NET NEGATIVE despite 71.6% win rate.
The DYNAMIC_EDGE gate targets these expensive trades.

| Metric | RULEV3 | RULEV3.1 |
|--------|--------|----------|
| Trades | 1,448 | 1,445 |
| Win Rate | 71.69% | 71.70% |
| Total PnL | $483.50 | $483.58 |

**Note:** Small improvement in backtest. Real edge comes from skipping
expensive trades where payout doesn't justify the risk.

---

## PHILOSOPHY

- Trust the distribution, not the story
- Some days will hurt
- 11 trades is noise, 1,008 trades is signal
- Cheap prices = forgiving, Expensive prices = ruthless
- Edge justifies risk → less conviction = need more edge
- Direction doesn't predict losses. Stability does.
- RULEV3.2 = pricing-aware + regime-aware edge thresholds

---

## V3.2 CHANGES (2025-12-31)

| What | Before (V3.1) | After (V3.2) |
|------|---------------|--------------|
| Edge gate | Price-based only | Price-based + regime modifier |
| CHOPPY handling | None | +0.03 to required edge |
| Crossings tracking | None | 5-min rolling window |

**Files modified:**
- `src/core/btc_trend_tracker.py` - added get_crossings(), get_regime()
- `src/ui/ui_dashboard_live.py` - regime modifier in edge gate

**Rationale:** Dec 30 analysis showed losses cluster during choppy regimes, not during wrong directional bets. The regime modifier gates risk during fragmented sentiment without skipping trades entirely.

---

## V3.2 BACKTEST RESULTS (2025-12-31)

**RESULT: REGIME MODIFIER DISABLED**

Backtest of 2085 sessions showed regime modifier HURTS performance:

| Regime | Trades | Win Rate | PnL |
|--------|--------|----------|-----|
| STABLE (<=2) | 5 | 100% | +$11.85 |
| NEUTRAL (3-5) | 91 | 74.7% | +$44.68 |
| **CHOPPY (>=6)** | **1349** | **71.4%** | **+$427.05** |

93% of trades are CHOPPY, and they're PROFITABLE. Filtering them loses $427.

**Threshold sweep:**
| Threshold | Trades Skipped | PnL Lost |
|-----------|----------------|----------|
| >= 6 | 1349 | -$427 |
| >= 10 | 716 | -$219 |
| >= 12 | 360 | -$106 |

**Conclusion:** Historical data doesn't show the loss-clustering-in-chop pattern. Regime modifier available but OFF by default.

**Env flag:** `CHOP_MOD_ENABLED=1` to enable for A/B testing.

**Next candidates to test:**
1. ask>0.69 tightening (known negative EV area)
2. Spread gate (spread widening = toxicity)
3. Late-session gate (last X minutes = more whipsaw)
4. Edge deterioration (edge falling into entry)
