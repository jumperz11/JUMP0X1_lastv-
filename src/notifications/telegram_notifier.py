"""
Telegram Notifier - Minimal Trade Notifications
================================================
Sends key trade events to Telegram.

Events: START, FILLED, SETTLED, KILL, STOP
Rate-limited: max 1 msg/sec, de-dupe for 10s

Env vars:
  TELEGRAM_ENABLED=1       # Enable/disable
  TELEGRAM_BOT_TOKEN=xxx   # Bot token from @BotFather
  TELEGRAM_CHAT_ID=xxx     # Your chat ID
"""

import os
import time
import hashlib
import threading
import requests
from typing import Optional
from functools import wraps


class TelegramNotifier:
    """
    Minimal Telegram notifier with rate limiting and de-duplication.
    """

    def __init__(self):
        self.enabled = os.getenv("TELEGRAM_ENABLED", "0") == "1"
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

        # Rate limiting
        self._last_send_time: float = 0
        self._min_interval: float = 1.0  # 1 second between messages

        # De-duplication
        self._last_msg_hash: str = ""
        self._last_msg_time: float = 0
        self._dedupe_window: float = 10.0  # 10 seconds

        # Thread safety
        self._lock = threading.Lock()

        # Validate config
        if self.enabled and (not self.bot_token or not self.chat_id):
            print("[TELEGRAM] WARNING: Enabled but missing BOT_TOKEN or CHAT_ID")
            self.enabled = False

    def _get_hash(self, msg: str) -> str:
        """Get hash of message for de-duplication."""
        return hashlib.md5(msg.encode()).hexdigest()[:8]

    def _is_duplicate(self, msg: str) -> bool:
        """Check if message is duplicate within dedupe window."""
        msg_hash = self._get_hash(msg)
        now = time.time()

        if msg_hash == self._last_msg_hash:
            if (now - self._last_msg_time) < self._dedupe_window:
                return True

        return False

    def _rate_limit(self) -> float:
        """Return seconds to wait before sending, 0 if ready."""
        now = time.time()
        elapsed = now - self._last_send_time
        if elapsed < self._min_interval:
            return self._min_interval - elapsed
        return 0

    def send(self, msg: str) -> bool:
        """
        Send message to Telegram.

        Args:
            msg: Message text (keep short)

        Returns:
            True if sent, False if skipped/failed
        """
        if not self.enabled:
            return False

        with self._lock:
            # Check duplicate
            if self._is_duplicate(msg):
                return False

            # Rate limit
            wait_time = self._rate_limit()
            if wait_time > 0:
                time.sleep(wait_time)

            # Send
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                payload = {
                    "chat_id": self.chat_id,
                    "text": msg,
                    "parse_mode": "HTML",
                    "disable_notification": False,
                }

                response = requests.post(url, json=payload, timeout=5)

                if response.status_code == 200:
                    # Update state
                    self._last_send_time = time.time()
                    self._last_msg_hash = self._get_hash(msg)
                    self._last_msg_time = time.time()
                    return True
                else:
                    print(f"[TELEGRAM] Failed: {response.status_code} {response.text[:100]}")
                    return False

            except Exception as e:
                print(f"[TELEGRAM] Error: {e}")
                return False


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

_notifier: Optional[TelegramNotifier] = None


def get_notifier() -> TelegramNotifier:
    """Get or create the singleton notifier."""
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier


def send(msg: str) -> bool:
    """Send a message via Telegram (convenience function)."""
    return get_notifier().send(msg)


def is_enabled() -> bool:
    """Check if Telegram notifications are enabled."""
    return get_notifier().enabled


# =============================================================================
# MESSAGE FORMATTERS
# =============================================================================

def fmt_start(balance: float, config: dict) -> str:
    """Format START message."""
    mode = config.get("mode", "T3-only")
    thr = config.get("threshold", 0.64)
    cap = config.get("safety_cap", 0.72)
    return f"START balance=${balance:.2f} config: mode={mode} thr={thr} cap={cap}"


def fmt_filled(trade_id: int, direction: str, fill_price: float, max_loss: float, btc_tag: str = "") -> str:
    """Format FILLED message with optional BTC trend tag."""
    msg = f"FILLED #{trade_id} BUY {direction} @ {fill_price:.3f} cost=${max_loss:.2f}"
    if btc_tag:
        msg += f"\n{btc_tag}"
    return msg


def fmt_settled(
    trade_id: int,
    won: bool,
    pnl: float,
    cumulative_pnl: float,
    wins: int,
    losses: int,
    consecutive_losses: int,
    consecutive_wins: int = 0,
    reason: str = "",
    btc_tag: str = ""
) -> str:
    """Format SETTLED message with streak info and optional BTC trend tag."""
    if won:
        # Fire streak for wins (🔥 for 3+)
        if consecutive_wins >= 3:
            streak = f" 🔥{consecutive_wins}"
        else:
            streak = ""
        result = f"WIN{streak}"
    else:
        # Loss streak
        if consecutive_losses >= 2:
            streak = f" ({consecutive_losses}L)"
        else:
            streak = ""
        result = f"LOSS{streak}"

    # Add reason if provided
    reason_str = f" [{reason}]" if reason else ""

    msg = (
        f"SETTLED #{trade_id} {result}{reason_str}\n"
        f"pnl=${pnl:+.2f} run=${cumulative_pnl:+.2f} W/L={wins}/{losses}"
    )
    if btc_tag:
        msg += f"\n{btc_tag}"
    return msg


def fmt_kill(reason: str, value: str, cumulative_pnl: float) -> str:
    """Format KILL message."""
    return f"KILL {reason} value={value} run_pnl=${cumulative_pnl:+.2f}"


def fmt_stop(reason: str, final_pnl: float, final_balance: float) -> str:
    """Format STOP message."""
    return f"STOP reason={reason} final_pnl=${final_pnl:+.2f} balance=${final_balance:.2f}"


def fmt_periodic(
    sessions: int,
    trades: int,
    wins: int,
    losses: int,
    pnl: float,
    pending: int = 0
) -> str:
    """Format periodic summary (every 10 sessions)."""
    wr = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    avg_pnl = pnl / trades if trades > 0 else 0

    return (
        f"📊 <b>10-SESSION UPDATE</b>\n"
        f"Sessions: {sessions}\n"
        f"Trades: {trades} (pending: {pending})\n"
        f"W/L: {wins}/{losses} ({wr:.1f}%)\n"
        f"PnL: ${pnl:+.2f} (avg: ${avg_pnl:+.2f})"
    )


def fmt_daily(
    date: str,
    sessions: int,
    trades: int,
    wins: int,
    losses: int,
    pnl: float
) -> str:
    """Format daily summary."""
    wr = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    avg_pnl = pnl / trades if trades > 0 else 0

    emoji = "🟢" if pnl >= 0 else "🔴"

    return (
        f"{emoji} <b>DAILY SUMMARY</b> {date}\n"
        f"Sessions: {sessions}\n"
        f"Trades: {trades}\n"
        f"W/L: {wins}/{losses} ({wr:.1f}%)\n"
        f"PnL: ${pnl:+.2f} (avg: ${avg_pnl:+.2f})"
    )


def fmt_trade(
    trade_id: int,
    direction: str,
    entry_price: float,
    cost: float,
    zone: str
) -> str:
    """Format trade entry notification."""
    return (
        f"📈 <b>TRADE #{trade_id}</b>\n"
        f"Direction: {direction}\n"
        f"Entry: ${entry_price:.3f}\n"
        f"Cost: ${cost:.2f}\n"
        f"Zone: {zone}"
    )


# =============================================================================
# TEST
# =============================================================================

if __name__ == "__main__":
    print("Telegram Notifier Test")
    print("=" * 40)
    print(f"Enabled: {is_enabled()}")

    if is_enabled():
        print("Sending test message...")
        result = send("TEST: JUMP0X1 bot connected")
        print(f"Result: {result}")
    else:
        print("Set TELEGRAM_ENABLED=1, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID in .env")
