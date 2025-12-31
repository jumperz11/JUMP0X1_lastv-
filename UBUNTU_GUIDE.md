# JUMP0X1 Ubuntu Guide

Complete guide for running JUMP0X1 on Ubuntu VPS.

## Fresh Install

```bash
# Clean old files (if any)
cd ~
rm -rf JUMP0X1* venv

# Clone repo
git clone https://github.com/jumperz11/JUMP0X1_lastv-.git
cd JUMP0X1_lastv-

# Setup
chmod +x *.sh
./setup.sh

# If rich missing
pip install rich

# Activate environment
source venv/bin/activate
```

## Configure .env

```bash
nano ~/JUMP0X1_lastv-/.env
```

Paste this (replace YOUR_PRIVATE_KEY):
```
TRADING_MODE=paper
EXECUTION_ENABLED=false
MAX_LIVE_TRADES_PER_RUN=0
PM_PRIVATE_KEY=YOUR_PRIVATE_KEY_HERE
PM_WALLET_ADDRESS=YOUR_POLYMARKET_PROXY_WALLET
PM_FUNDER_ADDRESS=YOUR_POLYMARKET_PROXY_WALLET
PM_SIGNATURE_TYPE=1
PM_POLYGON_RPC_URL=https://polygon-rpc.com
PM_USDC_CONTRACT=0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359
PM_CASH_PER_TRADE=4.00
PM_MAX_POSITION=8.00
PM_EDGE_THRESHOLD=0.64
PM_SAFETY_CAP=0.68
PM_ZONE_MODE=T3-only
TELEGRAM_ENABLED=1
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
TELEGRAM_CHAT_ID=YOUR_CHAT_ID
BTC_TREND_TAG_ENABLED=1
```

Save: `Ctrl+O` → `Enter` → `Ctrl+X`

## Wallet Setup

- **PM_PRIVATE_KEY** = Your MetaMask private key
- **PM_WALLET_ADDRESS** = Polymarket PROXY wallet (find at polymarket.com → Deposit)
- **PM_FUNDER_ADDRESS** = Same as wallet address
- **PM_SIGNATURE_TYPE** = 1 (for Polymarket proxy)

**Important:** You need USDC in your Polymarket proxy wallet to trade!

## Run Bot

### Option 1: Direct (closes when you disconnect)
```bash
cd ~/JUMP0X1_lastv-
source venv/bin/activate
python3 run_paper.py
```

### Option 2: Screen (keeps running)
```bash
screen -S bot
cd ~/JUMP0X1_lastv-
source venv/bin/activate
python3 run_paper.py
```
- Detach: `Ctrl+A` then `D`
- Reconnect: `screen -r bot`
- List screens: `screen -ls`

### Option 3: nohup (background)
```bash
cd ~/JUMP0X1_lastv-
source venv/bin/activate
nohup python3 run_paper.py > output.log 2>&1 &
```
- View logs: `tail -f output.log`
- Check running: `ps aux | grep python`
- Stop: `pkill -f run_paper`

### Option 4: tmux
```bash
tmux new -s bot
cd ~/JUMP0X1_lastv-
source venv/bin/activate
python3 run_paper.py
```
- Detach: `Ctrl+B` then `D`
- Reconnect: `tmux attach -t bot`

## Update to Latest Version

```bash
cd ~/JUMP0X1_lastv-
git pull
source venv/bin/activate
python3 run_paper.py
```

Your `.env` is safe - git won't overwrite it.

## Check Version

```bash
cd ~/JUMP0X1_lastv-
git log -1 --oneline
```

## Telegram Commands

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

## Test Telegram Bot

```bash
curl -s -X POST "https://api.telegram.org/botYOUR_BOT_TOKEN/sendMessage" \
  -d "chat_id=YOUR_CHAT_ID" \
  -d "text=Bot test from Ubuntu"
```

## Multi-Chat (Personal + Group)

To add bot to a Telegram group:

1. Add bot to group
2. Send `/start` in group
3. Get group ID:
```bash
curl "https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates"
```
Look for: `"chat":{"id":-100xxxxxxxxxx`

4. Update `.env`:
```
TELEGRAM_CHAT_ID=YOUR_CHAT_ID,-100YOUR_GROUP_ID
```

## Trading Modes

| Mode | .env Settings |
|------|---------------|
| Paper (simulation) | `TRADING_MODE=paper` |
| Real (blocked) | `TRADING_MODE=real`, `EXECUTION_ENABLED=false` |
| Real (live) | `TRADING_MODE=real`, `EXECUTION_ENABLED=true` |

## Common Issues

### "Executor connection failed"
- Normal for paper trading with no balance
- Bot still works in monitor mode
- Deposit USDC to fix

### "Install rich"
```bash
pip install rich
```

### Commands not found
```bash
source venv/bin/activate
```

### Bot not responding to Telegram
Check if running:
```bash
ps aux | grep python
```

## Stop Bot

```bash
pkill -f run_paper
# or
pkill -f run_live
```

## Rules (RULEV3.1)

| Setting | Value |
|---------|-------|
| CORE window | 2:30-3:45 |
| Edge threshold | 0.64 |
| Safety cap | 0.68 |
| Trade size | $4.00 |
| Max position | $8.00 |

---

Last updated: Dec 31, 2025
