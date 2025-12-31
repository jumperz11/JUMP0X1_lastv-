# RULEV3+ Phase 1 - LOCKED CONFIG

**Version:** PHASE1-v1.0
**Date:** 2024-12-24
**Status:** LOCKED - DO NOT MODIFY

---

## Change Summary

**Removed:** Alpha gate (was blocking 100% of trades due to structural bug)
**Added:** Spread hygiene gate (spread <= 0.02)

---

## Locked Configuration

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

## Gate Order (Phase 1)

1. **MODE_ZONE_GATE** - Only CORE zone allowed
2. **BOOK_GATE** - Must have valid bid/ask
3. **SESSION_CAP** - Max 1 trade per session
4. **EDGE_GATE** - edge >= 0.64
5. **HARD_PRICE_GATE** - ask <= 0.72
6. **PRICE_GATE** - ask < 0.72
7. **BAD_BOOK** - spread >= 0 AND bid <= ask (sanity)
8. **SPREAD_GATE** - spread <= 0.02 (skip if spread > 0.02)
9. **EXECUTOR_VALIDATION** - zone limits, cooldowns

---

## Backtest Results (2085 BTC Sessions)

| Metric | Value |
|--------|-------|
| Total sessions | 2,085 |
| Total trades | 1,064 |
| Trades per session | 0.5103 |
| Avg ask at entry | 0.6768 |
| Avg edge at entry | 0.6716 |
| Avg spread at entry | 0.0104 |
| Wins | 767 |
| Losses | 297 |
| **Win rate** | **72.09%** |
| **EV per trade** | **$0.3276** |
| **Total PnL** | **$348.58** |
| Max drawdown | $65.35 |
| BAD_BOOK skips | 0 |
| SPREAD_GATE skips | 755 |

---

## Reproducible Command

```bash
cd "C:\Users\Mega-PC\Desktop\New folder (3)\JUMP0X1-main (1)\JUMP0X1-main"
python backtest_alpha_test.py
```

**Expected output:**
- Trades: 1064
- Win rate: 72.09%
- EV/trade: $0.3276
- Total PnL: $348.58

---

## Files Modified (Phase 1)

1. `ui_dashboard_live.py`
   - Removed GATE 7 (ALPHA_GATE)
   - Added GATE 7 (BAD_BOOK sanity check)
   - Added GATE 8 (SPREAD_GATE <= 0.02)
   - Renumbered GATE 9 (EXECUTOR_VALIDATION)

2. `backtest_alpha_test.py`
   - Converted from alpha comparison to Phase 1 spread gate test
   - Locked config constants at top

---

## Why Alpha Gate Was Removed

The alpha formula was structurally broken:

```
edge = mid = (bid + ask) / 2
alpha = edge - ask = mid - ask = -spread/2

Result: alpha is ALWAYS negative
Gate condition: alpha >= 0.02 can NEVER pass
```

**Fix applied:** Remove alpha gate, add spread hygiene gate instead.

**Future (Phase 2):** Build p_model (predicted probability) from historical calibration, then reintroduce alpha = p_model - ask.

---

---

## Live Monitoring Targets

### Primary Metrics (vs Backtest)

| Metric | Backtest | Live Target | Kill If |
|--------|----------|-------------|---------|
| Win rate | 72.09% | Track stability | - |
| EV/trade | $0.3276 | Positive | Negative over 20+ trades |
| Max drawdown | $65.35 | < $130.70 | > 2× backtest |
| Avg ask | 0.6768 | Same universe | Consistently higher |
| Avg spread | 0.0104 | < 0.02 | - |

### Additional Monitoring (High Impact)

| Metric | Target | Notes |
|--------|--------|-------|
| Avg ask (live) | ~0.6768 | If higher, edge evaporates |
| Worst ask paid | Track | Flag if > 0.72 |
| Avg slippage (bps) | < 50 | (fill - ask_snapshot) / ask |
| P90 slippage (bps) | < 100 | 90th percentile |
| Skipped sessions | Track | Log reasons |

### Slippage Formula

```
slippage_bps = ((fill_price - ask_snapshot) / ask_snapshot) * 10000
```

---

## Kill Rules

| Trigger | Threshold | Action |
|---------|-----------|--------|
| Max Drawdown | > $130.70 (2× backtest) | STOP |
| Structural Deviation | Wrong zone/sizing/gate | STOP |
| Execution Error | Bad fills, cap failure | STOP |
| EV Negative | Over 20+ trades | REVIEW |

---

## Config Freeze Rule

**NO MID-SESSION EDITS**

Config must stay frozen until:
- 50 trades collected, OR
- A kill rule triggers

Changing parameters mid-collection invalidates all data.

---

## Final Scoreboard (Phase 1 Live)

Track these after 50 trades:

```
Trades count:          ___
Win rate:              ___% (backtest: 72.09%)
EV/trade:              $___ (backtest: $0.3276)
Total PnL:             $___ (backtest: $348.58)
Max drawdown:          $___ (backtest: $65.35)
Avg ask:               ___ (backtest: 0.6768)
Avg spread:            ___ (backtest: 0.0104)
Avg slippage (bps):    ___
P90 slippage (bps):    ___
Sessions skipped:      ___ (reasons: ___)
```

---

## Lock Notice

This configuration is LOCKED as of 2024-12-24.

Any future changes must:
1. Create a new PHASE2_LOCKED.md
2. NOT modify this file
3. NOT modify Phase 1 gate logic without explicit approval
4. Wait for 50 trades OR kill rule before any parameter changes

---

## Changelog

| Date | Change | Type |
|------|--------|------|
| 2024-12-24 | Phase 1 locked | CONFIG |
| 2024-12-24 | Renamed [STATS] `EV:` → `AvgPnL:` (label only, no logic change) | LABEL |

---

## Verification Hash

Config signature: `PHASE1-SPREAD-0.02-EDGE-0.64-CAP-0.72-CORE-ONLY`
