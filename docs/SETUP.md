# SETUP.md - Installation and Environment Setup

**Updated:** 2024-12-24
**System:** Python (RULEV3+ Phase 1)

---

## Prerequisites

### Python 3.8+

```powershell
# Check Python version
python --version  # Should be 3.8+
```

### Required Packages

```powershell
pip install python-dotenv rich websockets aiohttp py-clob-client
```

---

## Project Structure

```
JUMP0X1-main/
├── ui_dashboard_live.py     # Main trading dashboard
├── trade_executor.py        # Order execution engine
├── polymarket_connector.py  # WebSocket + API connector
├── backtest_alpha_test.py   # Phase 1 backtest
├── verify_pre_live.py       # Pre-live verification
├── .env                     # Configuration
├── VERSION                  # Version tag
├── PHASE1_LOCKED.md         # Locked config documentation
├── logs/
│   ├── paper/               # Paper trade logs
│   └── real/                # Real trade logs
├── markets_paper/           # Historical tick data
└── docs/                    # Documentation
```

---

## Configuration

### 1. Environment File (.env)

```bash
# ===================
# MODE
# ===================
TRADING_MODE=paper              # paper or real
EXECUTION_ENABLED=false         # true for real orders

# ===================
# STRATEGY (Phase 1 LOCKED)
# ===================
PM_EDGE_THRESHOLD=0.64          # Min edge to trade
PM_SAFETY_CAP=0.72              # Max price to pay
PM_CASH_PER_TRADE=5.00          # $ per trade
PM_MAX_POSITION=8.00            # Max total position

# ===================
# LIMITS
# ===================
MAX_LIVE_TRADES_PER_RUN=1       # Training wheel (0 = unlimited)

# ===================
# CREDENTIALS (for real trading)
# ===================
PM_PRIVATE_KEY=0xYOUR_KEY       # Wallet private key
PM_FUNDER_ADDRESS=0xYOUR_ADDR   # Funder address
PM_SIGNATURE_TYPE=2             # Signature type
```

### 2. Phase 1 Locked Parameters

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

## Running

### Paper Trading (Safe)

```powershell
cd "C:\Users\Mega-PC\Desktop\New folder (3)\JUMP0X1-main (1)\JUMP0X1-main"
python ui_dashboard_live.py
```

Dashboard shows:
- Live prices (UP/DOWN)
- Session info (zone, countdown)
- Performance (trades, W/L, PnL)
- Real-time logs

### Backtest

```powershell
python backtest_alpha_test.py
```

Expected output:
- Trades: 1064
- Win rate: 72.09%
- AvgPnL/trade: $0.3276
- Total PnL: $348.58

### Pre-Live Verification

```powershell
python verify_pre_live.py
```

Must pass all tests before going live.

---

## Go-Live Protocol

### 1. Collect 50 Paper Trades

Run dashboard in paper mode until 50 trades collected.

### 2. Verify Metrics

| Metric | Backtest | Live Target |
|--------|----------|-------------|
| Win rate | 72.09% | ~72% |
| AvgPnL | $0.3276 | Positive |
| Max DD | $65.35 | < $130.70 |

### 3. Update .env for Real Trading

```bash
TRADING_MODE=real
EXECUTION_ENABLED=true
MAX_LIVE_TRADES_PER_RUN=1
```

### 4. Fund Wallet

- Polymarket uses USDC on Polygon
- Minimum: $20-50 for testing
- Recommended: $100+

### 5. Start Small

```powershell
python ui_dashboard_live.py
```

Monitor first 10 trades manually.

---

## Troubleshooting

### "No module named 'dotenv'"

```powershell
pip install python-dotenv
```

### "No module named 'rich'"

```powershell
pip install rich
```

### "WebSocket connection failed"

- Check internet connection
- Polymarket API might be down
- Try again in a few minutes

### "TRADING_MODE not set"

- Ensure .env file exists
- Check .env has `TRADING_MODE=paper`

---

## File Permissions

### Windows

No special permissions needed.

### Linux/Mac

```bash
chmod 600 .env  # Protect credentials
```

---

## Security Checklist

- [ ] Private key NOT in git (.gitignore)
- [ ] Paper mode for testing first
- [ ] MAX_LIVE_TRADES_PER_RUN=1 for go-live
- [ ] Logs not containing credentials
- [ ] .env file protected

---

## Legacy Setup (Rust - Archived)

> **Note:** The Rust framework is archived. See ARCHITECTURE.md for details.

```bash
# Old Rust setup (not used)
rustc --version  # 1.70+
cargo build --release
```

---

**Config Signature:** `PHASE1-SPREAD-0.02-EDGE-0.64-CAP-0.72-CORE-ONLY`
