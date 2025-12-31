# JUMP01X - Project State

**Updated:** 2025-12-31
**Version:** v3.2.0
**Status:** Live Ready (Collecting Data)

---

## Current Status

| Item | Status |
|------|--------|
| Strategy | RULEV3+ CORE-only |
| Mode | Live Ready |
| Config | LOCKED (see PHASE1_LOCKED.md) |
| Trade Size | $5.00 per trade |
| Metrics | Observational logging enabled |

---

## Phase 1.1 Configuration (Post-Sweep)

```
ZONE_MODE          = CORE-only (T3 = 2:30-3:45)
EDGE_THRESHOLD     = 0.64 (dynamic: 0.64/0.67/0.70 by ask bucket)
SAFETY_CAP         = 0.68 (was 0.72, sweep optimized)
HARD_PRICE_CAP     = 0.68 (was 0.72, sweep optimized)
SPREAD_MAX         = 0.02
KILL_SWITCH        = DISABLED (was L=3, sweep showed it destroys edge)
MAX_TRADES_SESSION = 1
POSITION_SIZE      = $5.00
```

### Sweep Results (Dec 30, 2025)
- **240 parameter combinations tested**
- **Key finding**: Kill switch L=3 = -$22 avg PnL (DESTROYS edge)
- **Key finding**: Kill switch OFF = +$480 avg PnL
- **Best config**: ask_cap=0.68, spread=0.015, kill=OFF → $509.24 PnL, 6.75 efficiency

---

## Gate Order (Phase 1)

1. **MODE_ZONE_GATE** - Only CORE zone allowed
2. **BOOK_GATE** - Must have valid bid/ask
3. **SESSION_CAP** - Max 1 trade per session
4. **EDGE_GATE** - edge >= 0.64
5. **HARD_PRICE_GATE** - ask <= 0.68
6. **PRICE_GATE** - ask < 0.68
7. **BAD_BOOK** - spread >= 0 AND bid <= ask
8. **SPREAD_GATE** - spread <= 0.02
9. **EXECUTOR_VALIDATION** - zone limits, cooldowns

---

## Backtest Results (Phase 1)

| Metric | Value |
|--------|-------|
| Total sessions | 2,085 |
| Total trades | 1,064 |
| Win rate | 72.09% |
| AvgPnL/trade | $0.3276 |
| Total PnL | $348.58 |
| Max drawdown | $65.35 |

---

## Live Trade Progress

| Metric | Status |
|--------|--------|
| Metrics logging | Enabled |
| Smoke tests | Passed |
| Ready for | Data collection |

---

## Files

### Core System

| File | Purpose |
|------|---------|
| `ui_dashboard_live.py` | Main trading dashboard |
| `trade_executor.py` | Order execution engine |
| `polymarket_connector.py` | WebSocket + CLOB API |
| `trade_metrics_logger.py` | Observational metrics |
| `backtest_alpha_test.py` | Phase 1 backtest |
| `.env` | Configuration |

### Logs

| Location | Content |
|----------|---------|
| `logs/paper/` | Paper trade logs |
| `logs/real/` | Real trade logs |
| `logs/real/metrics/` | Observational metrics (JSONL) |

### Documentation

| File | Content |
|------|---------|
| `PHASE1_LOCKED.md` | Locked Phase 1 config |
| `docs/STATE.md` | This file |
| `docs/STRATEGY.md` | Strategy details |
| `docs/RESEARCH_REPORT_2025-12-31.md` | Full quantitative research report |
| `VERSION` | Version tag (v3.2.0) |

---

## Periodic Stats Format

```
[STATS] 5m | Sessions: 6 (skip:4) | Trades: 2 (pend:1) | W/L: 1/0 (100%) | AvgPnL: $+2.69 | PnL: $+2.69
```

Note: `AvgPnL` = cumulative PnL / settled trades (renamed from EV for clarity)

---

## Kill Rules

| Trigger | Threshold | Action |
|---------|-----------|--------|
| Max Drawdown | > $130.70 (2x backtest) | STOP |
| Structural Deviation | Wrong zone/sizing/gate | STOP |
| Execution Error | Bad fills, cap failure | STOP |
| AvgPnL Negative | Over 20+ trades | REVIEW |

---

## History

### December 31, 2025
- **VERSION LOCKED: v3.2.0**
- **Full Research Report**: `docs/RESEARCH_REPORT_2025-12-31.md`
- **Extended parameter sweep**: 300 configurations tested (was 240)
- **6-Phase Survivability Analysis completed**:
  - Loss streaks: Max 5, 99th percentile 9
  - Loss clustering: CONFIRMED (Chi-square 4.32 > 3.84)
  - Win rate after loss: 67.20% vs 72.95% after win
  - 87.2% of losses went green first (timing impossible)
  - Entry Quality Score: NOT predictive (r=0.018)
- **Bankroll requirements validated**: $50 covers 99% of paths
- Created `experiments/survivability_analysis.py`

### December 30, 2025
- **Parameter sweep completed** (240 combinations)
- **CRITICAL FINDING**: Kill switch L=3 destroys edge (-$22 avg PnL)
- **Kill switch DISABLED** (set to 999)
- **Price cap tightened**: 0.72 -> 0.68 (sweep optimal)
- Best config: ask_cap=0.68, spread=0.015-0.02, kill=OFF -> $509.24 PnL
- V3.2 regime modifier kept but disabled (CHOP_MOD_ENABLED=0)

### December 26, 2025
- Added observational metrics logging (`trade_metrics_logger.py`)
- Integrated in `ui_dashboard_live.py` (on_entry, on_tick, on_settlement)
- Added reason classification (7 types: clean conviction, whipsaw, late flip, etc.)
- Smoke tests passed (paper + real + integrity assertions)
- Metrics logic FROZEN - no changes until meaningful volume

### December 24, 2024
- Phase 1 LOCKED
- Removed alpha gate (structural bug: always negative)
- Added spread hygiene gate (spread <= 0.02)
- Renamed EV → AvgPnL in [STATS] log (label only)
- Created PHASE1_LOCKED.md
- Created VERSION file
- 3 paper trades collected (2W/1L)

### December 22-23, 2024
- First successful live trade executed
- Switched to paper mode for extended testing
- Added win/loss settlement tracking
- Completed pre-live verification (44 tests)

---

## Next Steps

1. Run live: `python run_live.py`
2. Collect real data with metrics logging
3. Review metrics offline only - no strategy changes
4. Revisit after meaningful volume

---

**Mode:** Live Ready
**Version:** v3.2.0
**Strategy:** RULEV3+ CORE-only
**Config:** LOCKED (ask_cap=0.68, kill_switch=OFF)
**Metrics:** FROZEN
**Research:** docs/RESEARCH_REPORT_2025-12-31.md
