# JUMP01X Commands Reference

**Updated:** 2024-12-24
**System:** Python (RULEV3+ Phase 1)

---

## Current Commands (Python)

### Run Live Dashboard

```powershell
cd "C:\Users\Mega-PC\Desktop\New folder (3)\JUMP0X1-main (1)\JUMP0X1-main"
python ui_dashboard_live.py
```

**Controls:**
- `q` - Quit
- Dashboard auto-refreshes

### Run Backtest (Phase 1)

```powershell
cd "C:\Users\Mega-PC\Desktop\New folder (3)\JUMP0X1-main (1)\JUMP0X1-main"
python backtest_alpha_test.py
```

**Expected output:**
- Trades: 1064
- Win rate: 72.09%
- AvgPnL/trade: $0.3276
- Total PnL: $348.58

### Run Pre-Live Verification

```powershell
cd "C:\Users\Mega-PC\Desktop\New folder (3)\JUMP0X1-main (1)\JUMP0X1-main"
python verify_pre_live.py
```

---

## Configuration

### .env Settings (Phase 1)

```bash
# Mode
TRADING_MODE=paper          # paper or real
EXECUTION_ENABLED=false     # true to execute real orders

# Strategy
PM_EDGE_THRESHOLD=0.64      # Min edge to trade
PM_SAFETY_CAP=0.72          # Max price to pay
PM_CASH_PER_TRADE=5.00      # $ per trade

# Limits
MAX_LIVE_TRADES_PER_RUN=1   # Training wheel
```

---

## Log Files

| Location | Content |
|----------|---------|
| `logs/paper/trades_YYYYMMDD_HHMMSS.log` | Paper trade logs |
| `logs/real/trades_YYYYMMDD_HHMMSS.log` | Real trade logs |

### View Latest Log

```powershell
Get-Content "logs\paper\trades_*.log" -Tail 50
```

### Search for Trades

```powershell
Select-String -Path "logs\paper\*.log" -Pattern "ENTRY|SETTLED"
```

### Count Trades

```powershell
(Select-String -Path "logs\paper\*.log" -Pattern "\[ENTRY\]").Count
```

---

## Dashboard Stats Format

```
[STATS] 5m | Sessions: 6 (skip:4) | Trades: 2 (pend:1) | W/L: 1/0 (100%) | AvgPnL: $+2.69 | PnL: $+2.69
```

| Field | Meaning |
|-------|---------|
| `Sessions: 6 (skip:4)` | 6 seen, 4 skipped |
| `Trades: 2 (pend:1)` | 2 total, 1 pending settlement |
| `W/L: 1/0 (100%)` | Wins/Losses (win rate) |
| `AvgPnL: $+2.69` | Average PnL per settled trade |
| `PnL: $+2.69` | Total cumulative PnL |

---

## Go-Live Commands (After 50 Paper Trades)

```powershell
# 1. Update .env
TRADING_MODE=real
EXECUTION_ENABLED=true
MAX_LIVE_TRADES_PER_RUN=1

# 2. Run dashboard
python ui_dashboard_live.py

# 3. Monitor first trades carefully
```

---

## Legacy Commands (Rust - Archived)

> **Note:** The Rust framework (PK8_PH) is archived. See below for historical reference.

### Old Rust Commands

```bash
# Build (legacy)
cargo build --release

# Run live console (legacy)
cargo run --bin live_console

# Run backtest (legacy)
cargo run --bin backtest -- --base ../markets_paper
```

---

**Config Signature:** `PHASE1-SPREAD-0.02-EDGE-0.64-CAP-0.72-CORE-ONLY`
