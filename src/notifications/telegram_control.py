"""
Telegram Control - Remote Observability
========================================
Reads state. Does NOT trade or change logic.

Commands:
  /status     - View current state
  /pnl        - PnL summary
  /btc        - View UP token 5m trend
  /list       - Show all logs
  /paper      - Get paper log
  /real       - Get real log
  /1 /2..     - Pick from list
  /kill       - Flip EXECUTION_ENABLED to False

Env:
  TELEGRAM_ENABLED=1
  TELEGRAM_BOT_TOKEN=xxx
  TELEGRAM_CHAT_ID=xxx,yyy  (comma-separated for multiple chats/groups)
"""

import os
import time
import requests
from typing import Optional, List
from pathlib import Path

# =============================================================================
# CONFIG (from env)
# =============================================================================

TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "0") == "1"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_raw_chat_ids = os.getenv("TELEGRAM_CHAT_ID", "")
# Support multiple chat IDs (comma-separated)
TELEGRAM_CHAT_IDS: List[str] = [cid.strip() for cid in _raw_chat_ids.split(",") if cid.strip()]

# =============================================================================
# SHARED STATE (set by dashboard, read by telegram)
# =============================================================================

class State:
    """Shared state - dashboard writes, telegram reads."""
    execution_enabled: bool = False
    zone_mode: str = "T3-only"
    zone: str = ""
    edge: float = 0.0
    balance: float = 0.0
    pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    trades_total: int = 0
    consecutive_losses: int = 0
    killswitch_active: bool = False
    last_heartbeat: float = 0.0

    # Control flag - telegram sets, dashboard reads
    kill_requested: bool = False


_state = State()


def get_state() -> State:
    """Get shared state."""
    return _state


# =============================================================================
# TELEGRAM API
# =============================================================================

_last_update_id: int = 0


def _poll_updates() -> list:
    """Fetch new messages."""
    global _last_update_id
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        params = {"offset": _last_update_id + 1, "timeout": 1}
        resp = requests.get(url, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                return data.get("result", [])
    except:
        pass
    return []


def _send(text: str, chat_id: str = None):
    """Send message to one or all authorized chats."""
    targets = [chat_id] if chat_id else TELEGRAM_CHAT_IDS
    for cid in targets:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(url, json={
                "chat_id": cid,
                "text": text,
                "parse_mode": "HTML"
            }, timeout=5)
        except:
            pass


# =============================================================================
# COMMANDS
# =============================================================================

def _cmd_status():
    """/status - show current state."""
    import time
    s = _state

    # Check if bot is alive (heartbeat within last 10 seconds)
    now = time.time()
    is_alive = (now - s.last_heartbeat) < 10 if s.last_heartbeat > 0 else False

    if not is_alive:
        _send("<b>OFFLINE</b>\nBot is not running.")
        return

    wr = (s.wins / (s.wins + s.losses) * 100) if (s.wins + s.losses) > 0 else 0

    status = "KILLED" if s.killswitch_active or s.kill_requested else "RUNNING"
    exec_status = "ON" if s.execution_enabled else "OFF"

    msg = (
        f"<b>{status}</b> | EXEC:{exec_status}\n"
        f"Mode: {s.zone_mode}\n"
        f"Zone: {s.zone} | Edge: {s.edge:.3f}\n"
        f"Balance: ${s.balance:.2f}\n"
        f"PnL: ${s.pnl:+.2f}\n"
        f"W/L: {s.wins}/{s.losses} ({wr:.0f}%)\n"
        f"CL: {s.consecutive_losses}"
    )
    _send(msg)


def _cmd_btc():
    """/btc - show UP token 5m trend."""
    import time
    s = _state

    # Check if bot is alive
    now = time.time()
    is_alive = (now - s.last_heartbeat) < 10 if s.last_heartbeat > 0 else False

    if not is_alive:
        _send("<b>OFFLINE</b>\nBot is not running.")
        return

    # Get BTC trend from tracker
    try:
        from src.core.btc_trend_tracker import get_btc_tracker, btc_debug_stats
        tracker = get_btc_tracker()
        if tracker:
            label, pct = tracker.get_trend()
            buf_size = len(tracker._buffer)
            rec_count = getattr(tracker, '_record_count', 0)
            if buf_size > 0:
                oldest_age = now - tracker._buffer[0].timestamp
                age_str = f"{oldest_age:.0f}s"
            else:
                age_str = "0s"

            if label == "N/A":
                msg = f"<b>UP 5m: N/A</b>\nBuffer: {buf_size} pts, age: {age_str}\nRecords: {rec_count}\nNeed 300s of data"
            else:
                emoji = "🟢" if label == "GREEN" else "🔴" if label == "RED" else "⚪"
                msg = f"<b>UP 5m: {label} {pct:+.2f}%</b> {emoji}\n({buf_size} pts, {rec_count} records)"
        else:
            msg = "<b>UP 5m: N/A</b>\nTracker not initialized\n" + btc_debug_stats()
    except Exception as e:
        msg = f"<b>UP 5m: ERROR</b>\n{str(e)[:80]}"

    _send(msg)


def _cmd_kill():
    """/kill - request execution stop."""
    _state.kill_requested = True
    _send("KILL requested. Execution will stop.")


def _cmd_pnl():
    """/pnl - show PnL summary."""
    s = _state

    # Check if bot is alive
    now = time.time()
    is_alive = (now - s.last_heartbeat) < 10 if s.last_heartbeat > 0 else False

    if not is_alive:
        _send("<b>OFFLINE</b>\nBot is not running.")
        return

    total_trades = s.wins + s.losses
    wr = (s.wins / total_trades * 100) if total_trades > 0 else 0
    avg_pnl = s.pnl / total_trades if total_trades > 0 else 0

    # Emoji based on PnL
    if s.pnl > 0:
        emoji = "🟢"
    elif s.pnl < 0:
        emoji = "🔴"
    else:
        emoji = "⚪"

    msg = (
        f"{emoji} <b>PnL SUMMARY</b>\n\n"
        f"<b>Total PnL:</b> ${s.pnl:+.2f}\n"
        f"<b>Trades:</b> {total_trades}\n"
        f"<b>W/L:</b> {s.wins}/{s.losses} ({wr:.1f}%)\n"
        f"<b>Avg PnL:</b> ${avg_pnl:+.2f}\n"
        f"<b>Streak:</b> {s.consecutive_losses}L\n"
        f"<b>Balance:</b> ${s.balance:.2f}"
    )
    _send(msg)


def _cmd_help():
    """/help - show commands."""
    _send(
        "<b>Commands</b>\n"
        "/status - View state\n"
        "/pnl - PnL summary\n"
        "/btc - UP 5m trend\n"
        "/list - Show all logs\n"
        "/paper - Get paper log\n"
        "/real - Get real log\n"
        "/1 /2.. - Pick from list\n"
        "/kill - Stop execution\n"
        "/help - This"
    )


def _get_latest_log(log_type: str) -> Optional[Path]:
    """Get the latest log file of specified type."""
    # Find project root (3 levels up from this file)
    project_root = Path(__file__).parent.parent.parent
    logs_dir = project_root / "logs" / log_type

    if not logs_dir.exists():
        return None

    # Find most recent trades_*.log file
    log_files = list(logs_dir.glob("trades_*.log"))
    if not log_files:
        return None

    # Sort by modification time, get newest
    log_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return log_files[0]


def _send_file(file_path: Path, caption: str = ""):
    """Send a file via Telegram to all authorized chats."""
    success = False
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"

            with open(file_path, 'rb') as f:
                files = {'document': (file_path.name, f)}
                data = {'chat_id': chat_id}
                if caption:
                    data['caption'] = caption

                resp = requests.post(url, files=files, data=data, timeout=30)
                if resp.status_code == 200:
                    success = True
        except Exception as e:
            _send(f"Error uploading file: {str(e)[:100]}", chat_id)
    return success


def _get_all_logs(log_type: str, limit: int = 5):
    """Get list of recent log files."""
    project_root = Path(__file__).parent.parent.parent
    logs_dir = project_root / "logs" / log_type

    if not logs_dir.exists():
        return []

    log_files = list(logs_dir.glob("trades_*.log"))
    log_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return log_files[:limit]


# Store last listed logs for selection
_listed_logs = []


def _cmd_logs(log_type: str = ""):
    """/logs - show log options or upload log file."""
    if not log_type:
        _send(
            "<b>📁 Log Files</b>\n\n"
            "/logs paper - Latest paper log\n"
            "/logs real - Latest real log\n"
            "/logs list - Show recent logs"
        )
        return

    log_type = log_type.lower().strip()

    # Handle /logs list
    if log_type == "list":
        global _listed_logs
        _listed_logs = []

        paper_logs = _get_all_logs("paper", 5)
        real_logs = _get_all_logs("real", 5)

        msg = "<b>📁 Recent Logs</b>\n"
        msg += "Type number to download\n\n"

        num = 1
        msg += "<b>Paper:</b>\n"
        for f in paper_logs:
            size_kb = f.stat().st_size / 1024
            msg += f"/{num} - {f.name} ({size_kb:.0f}KB)\n"
            _listed_logs.append(f)
            num += 1

        msg += "\n<b>Real:</b>\n"
        for f in real_logs:
            size_kb = f.stat().st_size / 1024
            msg += f"/{num} - {f.name} ({size_kb:.0f}KB)\n"
            _listed_logs.append(f)
            num += 1

        _send(msg)
        return

    if log_type not in ["paper", "real"]:
        _send(f"Unknown: {log_type}\nUse: /logs paper, /logs real, /logs list")
        return

    log_file = _get_latest_log(log_type)

    if not log_file:
        _send(f"No {log_type} logs found.")
        return

    # Get file stats
    size_kb = log_file.stat().st_size / 1024
    mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(log_file.stat().st_mtime))

    _send(f"Uploading {log_type} log...\n{log_file.name}\n{size_kb:.1f} KB | {mtime}")

    # Check file size (Telegram limit is 50MB, but keep it reasonable)
    if size_kb > 10000:  # 10MB limit for logs
        _send("File too large. Uploading last 500KB only...")
        # Create a temp file with last 500KB
        _send_tail(log_file, 500 * 1024, log_type)
    else:
        _send_file(log_file, f"{log_type.upper()} log - {mtime}")


def _send_tail(file_path: Path, max_bytes: int, log_type: str):
    """Send the tail of a file (for large files)."""
    import tempfile
    try:
        with open(file_path, 'rb') as f:
            f.seek(0, 2)  # End of file
            file_size = f.tell()

            # Read last max_bytes
            start_pos = max(0, file_size - max_bytes)
            f.seek(start_pos)
            content = f.read()

        # Write to temp file
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.log', delete=False,
                                          prefix=f'{log_type}_tail_') as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(file_path.stat().st_mtime))
        _send_file(tmp_path, f"{log_type.upper()} log (tail) - {mtime}")

        # Clean up
        tmp_path.unlink()
    except Exception as e:
        _send(f"Error reading log: {str(e)[:100]}")


def _handle(text: str, chat_id: str):
    """Handle incoming command."""
    if str(chat_id) not in TELEGRAM_CHAT_IDS:
        return  # Ignore unauthorized chats

    text = text.strip()
    cmd = text.lower()

    if cmd == "/status":
        _cmd_status()
    elif cmd == "/pnl":
        _cmd_pnl()
    elif cmd == "/btc":
        _cmd_btc()
    elif cmd == "/kill":
        _cmd_kill()
    elif cmd == "/list":
        _cmd_logs("list")
    elif cmd == "/paper":
        _cmd_logs("paper")
    elif cmd == "/real":
        _cmd_logs("real")
    elif cmd in ["/help", "/start"]:
        _cmd_help()
    elif cmd.startswith("/") and cmd[1:].isdigit():
        # Numbered log selection: /1, /2, etc.
        num = int(cmd[1:])
        if _listed_logs and 1 <= num <= len(_listed_logs):
            log_file = _listed_logs[num - 1]
            size_kb = log_file.stat().st_size / 1024
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(log_file.stat().st_mtime))
            _send(f"Uploading {log_file.name}...")
            _send_file(log_file, f"{log_file.name} | {size_kb:.0f}KB | {mtime}")
        elif not _listed_logs:
            _send("Use /list first to see available logs.")
        else:
            _send(f"Invalid number. Use 1-{len(_listed_logs)}.")


# =============================================================================
# MAIN LOOP
# =============================================================================

_running = False


def start():
    """Start polling loop. Call from background thread."""
    global _running, _last_update_id

    if not TELEGRAM_ENABLED:
        return

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        print("[TELEGRAM] Missing token or chat_id")
        return

    _running = True
    print("[TELEGRAM] Control listener started")

    while _running:
        updates = _poll_updates()

        for update in updates:
            _last_update_id = update.get("update_id", _last_update_id)
            msg = update.get("message", {})
            text = msg.get("text", "")
            chat_id = msg.get("chat", {}).get("id", "")

            if text.startswith("/"):
                _handle(text, chat_id)

        time.sleep(1)


def stop():
    """Stop polling loop."""
    global _running
    _running = False
    print("[TELEGRAM] Control listener stopped")


# =============================================================================
# TEST
# =============================================================================

def _reload_config():
    """Reload config from environment (for testing)."""
    global TELEGRAM_ENABLED, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS
    TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "0") == "1"
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    _raw = os.getenv("TELEGRAM_CHAT_ID", "")
    TELEGRAM_CHAT_IDS = [cid.strip() for cid in _raw.split(",") if cid.strip()]


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env")

    # Reload config after dotenv
    _reload_config()

    print("Telegram Control Test")
    print("=" * 40)
    print(f"Enabled: {TELEGRAM_ENABLED}")
    print(f"Token: {TELEGRAM_BOT_TOKEN[:15]}..." if TELEGRAM_BOT_TOKEN else "Token: MISSING")
    print(f"Chat ID: {TELEGRAM_CHAT_ID}")
    print()
    print("Send /status or /kill from Telegram")
    print("Ctrl+C to stop")
    print()

    # Set test state
    _state.execution_enabled = True
    _state.zone_mode = "T3-only"
    _state.zone = "CORE"
    _state.edge = 0.675
    _state.balance = 12.51
    _state.pnl = 2.50
    _state.wins = 3
    _state.losses = 1
    _state.consecutive_losses = 0

    try:
        start()
    except KeyboardInterrupt:
        stop()
