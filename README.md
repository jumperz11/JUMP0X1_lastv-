# JUMP0X1 - RULEV3+ Polymarket Trading Bot

Directional trading bot for Polymarket BTC 15-minute Up/Down prediction markets.

## What It Does

Trades Polymarket's "Will BTC go up in the next 15 minutes?" markets using RULEV3+ strategy.

**The Strategy:**
- Enter in specific time windows (CORE: 3:00-3:29)
- Edge threshold >= 0.64 (mid price of stronger side)
- Safety cap < 0.72 (avoid overpaying)
- Auto-settlement at session end (no need to sell)

**Example:**
```
Session starts: 00:00:00
CORE window:    00:03:00 - 00:03:29
Signal: UP @ $0.65, Edge = 0.64

Buy 7.69 UP shares @ $0.65 = $5.00
If BTC goes up: Win $7.69 - $5.00 = +$2.69
If BTC goes down: Lose $5.00
```

## Quick Start (Ubuntu)

```bash
# 1. Clone and setup
chmod +x setup.sh run_*.sh
./setup.sh

# 2. Configure
cp .env.example .env
nano .env  # Add your Polymarket credentials

# 3. Activate virtual environment
source venv/bin/activate

# 4. Run paper trading (simulation)
./run_paper.sh
```

## How to Run

### Paper Trading (Simulation)
```bash
./run_paper.sh
# or
python3 run_paper.py
```
No real orders placed. Safe for testing.

### Live Trading (Real Money)
```bash
# First, update .env:
# TRADING_MODE=real
# EXECUTION_ENABLED=true

./run_live.sh
# or
python3 run_live.py
```

### Pre-Live Verification
```bash
./run_verify.sh
# or
python3 scripts/verify_pre_live.py
```
Run this before going live to verify all safety checks pass.

## Trading Modes

| Mode | Settings | Description |
|------|----------|-------------|
| **Paper** | `TRADING_MODE=paper` | Simulated trades, no real money |
| **Real (blocked)** | `TRADING_MODE=real`, `EXECUTION_ENABLED=false` | Shows signals but blocks orders |
| **Real (live)** | `TRADING_MODE=real`, `EXECUTION_ENABLED=true` | Actual order execution |

## Project Structure

```
JUMP0X1/
├── run_paper.py              # Entry: Paper trading
├── run_live.py               # Entry: Live trading
├── run_paper.sh              # Ubuntu: Paper trading
├── run_live.sh               # Ubuntu: Live trading
├── run_verify.sh             # Ubuntu: Pre-live checks
├── setup.sh                  # Ubuntu: Setup script
├── requirements.txt          # Python dependencies
├── .env                      # Configuration (gitignored)
│
├── src/
│   ├── core/                 # Core trading modules
│   │   ├── trade_executor.py
│   │   ├── polymarket_connector.py
│   │   ├── real_trade_logger.py
│   │   └── trade_metrics_logger.py
│   ├── notifications/        # Telegram integration
│   │   ├── telegram_notifier.py
│   │   └── telegram_control.py
│   └── ui/
│       └── ui_dashboard_live.py
│
├── scripts/
│   └── verify_pre_live.py    # Pre-live verification
│
├── experiments/              # Backtest experiments
│   └── backtest_*.py
│
├── docs/                     # Documentation
│   ├── GO_LIVE_CHECKLIST.md
│   └── ARCHITECTURE.md
│
├── logs/                     # Trade logs (gitignored)
│   ├── paper/
│   └── real/
│       └── metrics/
│
└── archive/                  # Archived files
```

## Configuration (.env)

```bash
# Trading Mode
TRADING_MODE=paper           # paper or real
EXECUTION_ENABLED=false      # true to allow real orders
MAX_LIVE_TRADES_PER_RUN=1    # Safety limit (0 = unlimited)

# Polymarket Credentials
PM_PRIVATE_KEY=0x...         # Your wallet private key
PM_WALLET_ADDRESS=0x...      # Proxy wallet address
PM_FUNDER_ADDRESS=0x...      # Funder address
PM_SIGNATURE_TYPE=2          # 0=EOA, 1=Poly, 2=Gnosis

# Strategy Settings
PM_CASH_PER_TRADE=5.00       # $ per trade
PM_MAX_POSITION=8.00         # Max position size
PM_EDGE_THRESHOLD=0.64       # Min edge to trade
PM_SAFETY_CAP=0.72           # Max price to pay

# Telegram (optional)
TELEGRAM_ENABLED=0           # 1 to enable
TELEGRAM_BOT_TOKEN=          # From @BotFather
TELEGRAM_CHAT_ID=            # Your chat ID
```

## Telegram Commands

When enabled, control the bot via Telegram:

| Command | Description |
|---------|-------------|
| `/status` | View current state |
| `/pnl` | PnL summary |
| `/btc` | UP 5m trend |
| `/list` | Show all logs |
| `/paper` | Get paper log |
| `/real` | Get real log |
| `/1 /2..` | Pick from list |
| `/kill` | Stop execution |
| `/help` | Show commands |

## Dashboard Features

```
┌─────────────────────────────────────────────────────────────────────┐
│ RULEV3+ LIVE PAPER │ S:6(2skip) | T:3(1pend) | W/L:1/1(50%) | PnL:$-2.31 │
└─────────────────────────────────────────────────────────────────────┘
```

**Live Stats:**
- `S:6(2skip)` - Sessions seen (skipped without signal)
- `T:3(1pend)` - Total trades (pending settlement)
- `W/L:1/1(50%)` - Wins/Losses (win rate)
- `PnL:$-2.31` - Running profit/loss

## Safety Features

1. **Double lock** - Requires BOTH `TRADING_MODE=real` AND `EXECUTION_ENABLED=true`
2. **Trade limiter** - `MAX_LIVE_TRADES_PER_RUN` caps live orders
3. **Kill switch** - Auto-stops after 2 degraded fills or 3 consecutive losses
4. **Zone limits** - Max 1 trade per zone per session
5. **Cooldown** - 30s between trades
6. **PnL floor** - Auto-stops if cumulative PnL drops below -$5
7. **Telegram /kill** - Remote execution stop

## Logs

**Paper trades:** `logs/paper/trades_YYYYMMDD_HHMMSS.log`
**Real trades:** `logs/real/trades_YYYYMMDD_HHMMSS.log`
**Metrics:** `logs/real/metrics/metrics_YYYYMMDD_HHMMSS.jsonl`

## Backtest Results

| Strategy | Trades | Win Rate | Total PnL | EV/Trade |
|----------|--------|----------|-----------|----------|
| RULEV1 (baseline) | 2,046 | 59.8% | -$26.94 | -$0.013 |
| **RULEV3+ T3-only** | 662 | 63.5% | **+$13.42** | **+$0.020** |

**T3-only (CORE zone) is the only positive-EV configuration.**

---

**Status:** Live Ready (Dec 2025)
**Strategy:** RULEV3+ CORE-only
**Platform:** Ubuntu/Linux
