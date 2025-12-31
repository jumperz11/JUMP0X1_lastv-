# RULEV3+ GO-LIVE CHECKLIST

**Updated:** 2025-12-26
**Status:** Live Ready

---

## Strategy Validation

- [x] CORE-only mode validated (T3 = 3:00-3:29)
- [x] Edge threshold >= 0.64 validated
- [x] Safety cap <= 0.72 validated
- [x] Spread gate <= 0.02 validated
- [x] Alpha gate REMOVED (structural bug)
- [x] Backtest: 1064 trades, 72.09% WR, +$348.58 PnL
- [x] Max 1 trade per session validated

---

## Bot Code

- [x] `ui_dashboard_live.py` - Main dashboard
- [x] `trade_executor.py` - Order execution
- [x] `polymarket_connector.py` - WebSocket + API
- [x] `backtest_alpha_test.py` - Phase 1 backtest
- [x] Gate logic implemented (9 gates)
- [x] Paper trade settlement tracking
- [x] Periodic stats logging

---

## UI Dashboard

- [x] Terminal UI (TUI) built with Rich
- [x] Live prices panel
- [x] Order book panel
- [x] Session info panel
- [x] Performance stats panel
- [x] Zones panel
- [x] Config panel
- [x] Live logs panel

---

## Safety Features

- [x] Double execution lock (zone limits)
- [x] Session trade cap (max 1)
- [x] Kill switch (2 degraded fills)
- [x] Cooldown between trades
- [x] Spread gate (hygiene)
- [x] Safety cap (max ask price)

---

## Infrastructure

- [x] Polymarket WebSocket connected
- [x] CLOB API connected
- [x] Wallet configured
- [x] Logging to file enabled
- [x] Paper/Real mode separation

---

## Metrics Logging (Observational)

- [x] `trade_metrics_logger.py` - Metrics collection module
- [x] Integrated in `ui_dashboard_live.py` (on_entry, on_tick, on_settlement)
- [x] JSONL output: `logs/real/metrics/metrics_YYYYMMDD_HHMMSS.jsonl`
- [x] Same timestamp as trades log (pairing)
- [x] `[RUN] metrics_file=...` pointer in log header
- [x] mode="paper" / mode="real" field
- [x] reason classification (7 types)
- [x] Smoke test passed (paper 2 + real 1 + integrity)
- [x] Silent + non-blocking (does not affect execution)

---

## Phase 1 Progress

- [x] Config LOCKED (PHASE1_LOCKED.md)
- [x] VERSION file created
- [x] Paper smoke tests passed
- [x] Metrics logging validated
- [x] Ready for live data collection

---

## Kill Rules

| Trigger | Threshold | Status |
|---------|-----------|--------|
| Max Drawdown | > $130.70 | Monitoring |
| Structural Deviation | Wrong gate | Monitoring |
| AvgPnL Negative | Over 20+ trades | Monitoring |

---

## LOCKED PARAMETERS (Phase 1)

```
STRATEGY:     RULEV3+ Phase 1
ZONE_MODE:    CORE-only (T3)
CORE:         3:00-3:29 (180-209s elapsed)
THRESHOLD:    >= 0.64
SAFETY CAP:   <= 0.72
SPREAD_MAX:   <= 0.02
ALPHA_GATE:   REMOVED
MAX TRADES:   1 per session
POSITION:     $5.00
```

---

## Go-Live Protocol

### Every Run Checklist

1. **Startup sanity (10 seconds)**
   - Confirm in log header: `[RUN] metrics_file=logs/real/metrics/metrics_YYYYMMDD_HHMMSS.jsonl`

2. **During live**
   - Do nothing. Let it trade.

3. **After first settled trade**
   - Check `logs/real/metrics/` exists
   - Confirm 1 JSON line with: `mode="real"`, `trade_id`, `result`, `pnl`, `reason`

4. **Offline validation**
   ```bash
   python src/core/trade_metrics_logger.py logs/real/metrics/metrics_*.jsonl
   ```

### First Live Session

1. [x] Set `TRADING_MODE=real`, `EXECUTION_ENABLED=true` in `.env`
2. [ ] Run: `python run_live.py`
3. [ ] Verify `[RUN] metrics_file=...` in startup
4. [ ] Wait for first trade to settle
5. [ ] Validate metrics file written correctly

---

## Phase 2 (Future)

- [ ] Build p_model from historical calibration
- [ ] Reintroduce alpha = p_model - ask
- [ ] Create PHASE2_LOCKED.md

---

**Config Signature:** `PHASE1-SPREAD-0.02-EDGE-0.64-CAP-0.72-CORE-ONLY`
