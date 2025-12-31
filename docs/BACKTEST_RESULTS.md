# Backtest Results

**Updated:** 2024-12-24
**Strategy:** RULEV3+ Phase 1 (Directional)

---

## Phase 1 Results (CURRENT)

**Dataset:** 2,085 BTC 15-minute sessions
**Config:** CORE-only, spread <= 0.02, edge >= 0.64, ask <= 0.72

| Metric | Value |
|--------|-------|
| Total sessions | 2,085 |
| Total trades | 1,064 |
| Trades per session | 0.51 |
| Avg ask at entry | 0.6768 |
| Avg edge at entry | 0.6716 |
| Avg spread at entry | 0.0104 |
| Wins | 767 |
| Losses | 297 |
| **Win rate** | **72.09%** |
| **AvgPnL/trade** | **$0.3276** |
| **Total PnL** | **$348.58** |
| Max drawdown | $65.35 |
| BAD_BOOK skips | 0 |
| SPREAD_GATE skips | 755 |

---

## Phase 1 Configuration

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

## Reproducible Command

```bash
cd "C:\Users\Mega-PC\Desktop\New folder (3)\JUMP0X1-main (1)\JUMP0X1-main"
python backtest_alpha_test.py
```

**Expected output:**
- Trades: 1064
- Win rate: 72.09%
- AvgPnL/trade: $0.3276
- Total PnL: $348.58

---

## Key Findings

### 1. Spread Gate is Critical
- 755 sessions skipped due to spread > 0.02
- Wide spreads indicate illiquid conditions
- Filtering improves fill quality

### 2. Entry Price Distribution
- Average entry: $0.6768
- All entries below $0.72 (safety cap)
- Spread at entry: $0.0104 average

### 3. Win Rate by Zone
- CORE (3:00-3:29): Primary signal window
- Phase 1 uses CORE only for validation

---

## Why Alpha Gate Was Removed

The original alpha formula was structurally broken:

```
edge = mid = (bid + ask) / 2
alpha = edge - ask = mid - ask = -spread/2

Result: alpha is ALWAYS negative
Gate: alpha >= 0.02 can NEVER pass
```

This blocked 100% of trades. Removed in Phase 1, replaced with spread gate.

---

## Legacy Results (Archived)

### Old Arbitrage Strategy (balanced_arb)

**Note:** This was a different strategy approach - balanced arbitrage, not directional trading.

| Metric | Value |
|--------|-------|
| Date | 2025-12-20 |
| Markets processed | 8,340 |
| Total trades | 304,061 |
| Win rate | 98.5% |
| Total PnL | $35,584.85 |

This strategy bought both UP and DOWN simultaneously for guaranteed profit. Abandoned in favor of directional trading (RULEV3+).

---

## Phase 2 Roadmap

- Build p_model (predicted probability) from historical calibration
- Reintroduce alpha = p_model - ask
- Create PHASE2_LOCKED.md after 50 live trades

---

**Config Signature:** `PHASE1-SPREAD-0.02-EDGE-0.64-CAP-0.72-CORE-ONLY`
