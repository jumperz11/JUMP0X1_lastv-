#!/usr/bin/env python3
"""
RULEV3+ Live Dashboard - REAL POLYMARKET DATA
==============================================
Connects to real Polymarket WebSocket for live BTC 15-minute prices.
"""

import asyncio
import time
import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional
from collections import deque
from pathlib import Path

try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.live import Live
    from rich.text import Text
    from rich import box
except ImportError:
    print("Install rich: pip install rich")
    exit(1)

try:
    from dotenv import load_dotenv
    # Load .env from project root
    env_path = Path(__file__).parent.parent.parent / ".env"
    load_dotenv(env_path)
except ImportError:
    pass  # dotenv not required

# Add project root to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.polymarket_connector import (
    SessionManager, SessionState, GammaClient,
    derive_current_slug, format_elapsed, get_zone,
    SESSION_DURATION
)

from src.core.trade_executor import TradeExecutor, ExecutorConfig, OrderStatus, OrderResult

# Real trade logging (only writes when enabled=True in real mode)
from src.core.real_trade_logger import (
    init_real_logger, get_real_logger,
    real_log_start, real_log_stop, real_log_signal, real_log_settled
)

# Metrics logger (observational only - tracks patterns for analysis)
from src.core.trade_metrics_logger import init_metrics_logger, get_metrics_logger

# Telegram remote control (reads state, /kill command)
from src.notifications import telegram_control

# Telegram notifications (for paper trade alerts)
from src.notifications import telegram_notifier as tg

# BTC trend tracker (observational context for Telegram + regime detection)
from src.core.btc_trend_tracker import (
    init_btc_tracker, get_btc_tracker, btc_record, btc_tag,
    is_btc_tracker_enabled, btc_is_choppy, btc_regime, btc_crossings
)


# ============================================================
# PAPER TRADE TRACKING
# ============================================================

@dataclass
class PaperTrade:
    """Track paper trades for win/loss settlement."""
    trade_id: int
    session_id: str
    direction: str  # "Up" or "Down"
    entry_price: float
    shares: float
    cost: float
    potential_win: float
    potential_loss: float
    zone: str
    timestamp: datetime = field(default_factory=datetime.now)
    settled: bool = False
    won: bool = False
    pnl: float = 0.0


# ============================================================
# CREDENTIALS & MODE
# ============================================================

def load_credentials():
    """Load credentials from .env file."""
    return {
        "private_key": os.getenv("PM_PRIVATE_KEY", ""),
        "wallet_address": os.getenv("PM_WALLET_ADDRESS", ""),
        "funder_address": os.getenv("PM_FUNDER_ADDRESS", ""),
        "signature_type": int(os.getenv("PM_SIGNATURE_TYPE", "0")),
        "trading_mode": os.getenv("TRADING_MODE", "paper").lower(),
        "execution_enabled": os.getenv("EXECUTION_ENABLED", "false").lower() == "true",
        "max_live_trades_per_run": int(os.getenv("MAX_LIVE_TRADES_PER_RUN", "1")),
        "cash_per_trade": float(os.getenv("PM_CASH_PER_TRADE", "10.00")),
        "max_position": float(os.getenv("PM_MAX_POSITION", "50.00")),
    }

CREDENTIALS = load_credentials()

def is_real_mode():
    """Check if TRADING_MODE=real (but execution may still be disabled)."""
    return (
        CREDENTIALS["trading_mode"] == "real" and
        CREDENTIALS["private_key"] and
        len(CREDENTIALS["private_key"]) > 10
    )

def is_execution_enabled():
    """Check if execution is enabled (requires BOTH real mode AND execution_enabled=true)."""
    return is_real_mode() and CREDENTIALS["execution_enabled"]


def fetch_usdc_balance():
    """Fetch USDC balance from Polymarket CLOB API."""
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

        private_key = CREDENTIALS["private_key"]
        funder = CREDENTIALS["funder_address"]
        sig_type = CREDENTIALS["signature_type"]

        if private_key:
            client = ClobClient(
                "https://clob.polymarket.com",
                key=private_key,
                chain_id=137,
                signature_type=sig_type,
                funder=funder
            )
            creds = client.derive_api_key()
            client.set_api_creds(creds)

            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            result = client.get_balance_allowance(params)
            if "balance" in result:
                return int(result["balance"]) / 1_000_000
    except Exception as e:
        pass
    return 0.0

# ============================================================
# CONFIG
# ============================================================

# Zone mode: "T3-only" = CORE only, "T3+T5" = CORE + RECOVERY
ZONE_MODE = os.getenv("PM_ZONE_MODE", "T3-only")

# Map mode to allowed zones
# CORE = 2:30-3:45 (V2 extended window, was 3:00-3:29)
# T5 = minute 5 (5:00-5:59) = RECOVERY zone
ALLOWED_ZONES = {
    "T3-only": ["CORE"],
    "T3+T5": ["CORE", "RECOVERY"],
}

# Zone to Window ID mapping for clear logging
ZONE_TO_WINDOW = {
    "EARLY": "T0-T2",   # 0:00-2:29
    "CORE": "T2.5-T3",  # 2:30-3:45 (V2 extended)
    "DEAD": "T3.5-T4",  # 3:30-4:59
    "RECOVERY": "T5",   # 5:00-5:59
    "LATE": "T6+",      # 6:00+
}

def get_window_id(zone: str, elapsed: float) -> str:
    """Get window ID like T3 or T5 from zone and elapsed time."""
    minute = int(elapsed // 60)
    if zone == "CORE":
        return "T3"
    elif zone == "RECOVERY":
        return "T5"
    else:
        return f"T{minute}"

CONFIG = {
    "strategy": "RULEV3.1",
    "version": "3.1",  # Dynamic edge (regime modifier available but OFF by default)
    "mode": ZONE_MODE,
    "allowed_zones": ALLOWED_ZONES.get(ZONE_MODE, ["CORE"]),
    "core_window": "2:30-3:45",  # V2 extended (was 3:00-3:29)
    "recovery_window": "5:00-5:59",

    # EDGE GATE: Base threshold (used for display, actual gate is dynamic per RULEV3.1)
    "threshold": float(os.getenv("PM_EDGE_THRESHOLD", "0.64")),

    # PRICE GATE: Maximum ask price (from sweep: 0.68 optimal, was 0.72)
    "safety_cap": float(os.getenv("PM_SAFETY_CAP", "0.68")),

    # HARD PRICE CAP: Maximum ask regardless of alpha (from sweep: 0.68 optimal)
    "hard_price_cap": float(os.getenv("PM_HARD_PRICE_CAP", "0.68")),

    # ALPHA GATE: Minimum edge over ask (edge - ask >= margin)
    # This is the BASE margin - actual margin is max(base, 0.5*spread + fee_buffer)
    "alpha_margin_base": float(os.getenv("PM_ALPHA_MARGIN", "0.02")),
    "alpha_spread_mult": float(os.getenv("PM_ALPHA_SPREAD_MULT", "0.5")),  # Multiply spread by this
    "alpha_fee_buffer": float(os.getenv("PM_ALPHA_FEE_BUFFER", "0.005")),  # Fee buffer (0.5%)

    # SESSION GATE: Max trades per session (total, not per zone)
    "max_trades_per_session": int(os.getenv("PM_MAX_TRADES_PER_SESSION", "1")),

    # Trade sizing
    "cash_per_trade": float(os.getenv("PM_CASH_PER_TRADE", "5.00")),
    "max_position": float(os.getenv("PM_MAX_POSITION", "8.00")),
}

# ============================================================
# STATE
# ============================================================

@dataclass
class DashboardState:
    # Live market state
    session: SessionState = field(default_factory=SessionState)

    # Stats
    start_time: datetime = field(default_factory=datetime.now)
    trades_total: int = 0
    trades_won: int = 0
    trades_lost: int = 0
    pnl_total: float = 0.0
    core_entries: int = 0
    recovery_entries: int = 0
    sessions_seen: int = 0
    sessions_skipped: int = 0

    # Wallet
    usdc_balance: float = 0.0
    last_balance_check: Optional[datetime] = None

    # Logs
    logs: deque = field(default_factory=lambda: deque(maxlen=25))
    log_file: Optional[str] = None

    # Connection
    connected: bool = False
    last_update: Optional[datetime] = None

    # Executor state
    executor: Optional[TradeExecutor] = None
    executor_connected: bool = False
    current_session_id: str = ""
    pending_order: Optional[OrderResult] = None
    last_order: Optional[OrderResult] = None
    kill_switch: bool = False

    # Safety limiters
    live_trades_this_run: int = 0  # Counter for MAX_LIVE_TRADES_PER_RUN
    max_live_trades_per_run: int = 1  # From .env

    # Session trade counter (reset on session change)
    session_trade_count: int = 0  # Total trades this session (not per zone)

    # Clean shutdown flag (for max_trades_cap auto-stop)
    should_stop: bool = False
    stop_reason: str = ""

    # Paper trade tracking for win/loss settlement
    pending_paper_trades: List[PaperTrade] = field(default_factory=list)
    settled_paper_trades: List[PaperTrade] = field(default_factory=list)

    # Real trade tracking for win/loss settlement
    pending_real_trades: List[PaperTrade] = field(default_factory=list)
    settled_real_trades: List[PaperTrade] = field(default_factory=list)

    # Periodic logging tracker
    _last_stats_log: int = 0

    # Daily stats tracking for Telegram summary
    _current_date: str = ""
    _daily_sessions: int = 0
    _daily_trades: int = 0
    _daily_wins: int = 0
    _daily_losses: int = 0
    _daily_pnl: float = 0.0

    # 10-session notification tracker
    _last_periodic_notify: int = 0

    def log(self, msg):
        t = datetime.now().strftime("%H:%M:%S")
        # Color code based on message content
        if "error" in msg.lower() or "fail" in msg.lower():
            colored_msg = f"[red]{t} ✗ {msg}[/]"
        elif "connect" in msg.lower() or "subscrib" in msg.lower():
            colored_msg = f"[green]{t} ✓ {msg}[/]"
        elif "roll" in msg.lower() or "loaded" in msg.lower():
            colored_msg = f"[cyan]{t} → {msg}[/]"
        elif "CORE" in msg or "signal" in msg.lower() or "buy" in msg.lower():
            colored_msg = f"[bold green]{t} ★ {msg}[/]"
        elif "RECOVERY" in msg:
            colored_msg = f"[yellow]{t} ★ {msg}[/]"
        elif "DEAD" in msg or "skip" in msg.lower():
            colored_msg = f"[red]{t} ○ {msg}[/]"
        elif "token" in msg.lower():
            colored_msg = f"[dim]{t}   {msg}[/]"
        else:
            colored_msg = f"[white]{t}   {msg}[/]"
        self.logs.append(colored_msg)

        # Write to file (with explicit flush)
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
                    f.flush()
            except:
                pass


state = DashboardState()


def settle_paper_trades(old_session_id: str, final_up_price: float, final_down_price: float):
    """Settle paper trades when session ends. Determine win/loss based on final prices."""
    if not state.pending_paper_trades:
        return

    # Get trades for this session
    session_trades = [t for t in state.pending_paper_trades if t.session_id == old_session_id and not t.settled]

    if not session_trades:
        return

    # Determine winner: the side closer to 1.0 wins
    # At settlement: winner ~= 1.0, loser ~= 0.0
    # Mid-session we use the current price direction as proxy
    up_won = final_up_price > final_down_price

    state.log("")
    state.log(f"{'='*50}")
    state.log(f"SESSION SETTLEMENT: {old_session_id}")
    state.log(f"{'='*50}")
    state.log(f"FINAL UP:   ${final_up_price:.4f}")
    state.log(f"FINAL DOWN: ${final_down_price:.4f}")
    state.log(f"WINNER:     {'UP' if up_won else 'DOWN'}")
    state.log("")

    for trade in session_trades:
        trade.settled = True

        # Check if trade direction matches winner
        if (trade.direction == "Up" and up_won) or (trade.direction == "Down" and not up_won):
            # WIN: we get $1 per share, minus cost
            trade.won = True
            trade.pnl = trade.shares - trade.cost  # $1 * shares - cost
            state.trades_won += 1
            state._daily_wins += 1
        else:
            # LOSE: we get $0, lose entire cost
            trade.won = False
            trade.pnl = -trade.cost
            state.trades_lost += 1
            state._daily_losses += 1

        state.pnl_total += trade.pnl
        state._daily_pnl += trade.pnl
        state.settled_paper_trades.append(trade)

        result_emoji = "WIN" if trade.won else "LOSS"
        pnl_str = f"+${trade.pnl:.2f}" if trade.pnl >= 0 else f"-${abs(trade.pnl):.2f}"

        # Construct paper order ID (matches the format from check_and_execute_signal)
        paper_order_id = f"PAPER-{trade.timestamp.strftime('%Y%m%d%H%M%S')}-{trade.trade_id:04d}"

        state.log(f"TRADE #{trade.trade_id}: {trade.direction} @ ${trade.entry_price:.2f}")
        state.log(f"  RESULT: {result_emoji} | PnL: {pnl_str}")
        state.log(f"[SETTLED] {paper_order_id} {result_emoji} pnl={pnl_str}")

        # Track metrics (observational only) - call first to get reason
        reason = ""
        if get_metrics_logger():
            reason = get_metrics_logger().on_settlement(
                trade_id=trade.trade_id,
                winner="Up" if up_won else "Down",
                pnl=trade.pnl
            ) or ""

        trade_key = f"{trade.session_id}_{trade.trade_id}"

        # Update executor killswitch tracking (with trade_id to prevent double-count)
        if state.executor:
            state.executor.record_result(won=trade.won, pnl=trade.pnl, trade_id=trade_key)

            # Real mode: use real_log_settled (writes log + sends telegram)
            if is_real_mode():
                real_log_settled(
                    order_id=trade_key,
                    market_id=trade.session_id,
                    won=trade.won,
                    trade_pnl=trade.pnl,
                    cumulative_pnl=state.executor.cumulative_pnl,
                    consecutive_losses=state.executor.consecutive_losses,
                    reason=reason
                )
            else:
                # Paper mode: send telegram directly
                tg.send(tg.fmt_settled(
                    trade_id=trade.trade_id,
                    won=trade.won,
                    pnl=trade.pnl,
                    cumulative_pnl=state.executor.cumulative_pnl,
                    wins=state.trades_won,
                    losses=state.trades_lost,
                    consecutive_losses=state.executor.consecutive_losses,
                    consecutive_wins=0,
                    reason=reason,
                    btc_tag=btc_tag()
                ))
        else:
            # No executor (paper mode fallback): send telegram directly
            tg.send(tg.fmt_settled(
                trade_id=trade.trade_id,
                won=trade.won,
                pnl=trade.pnl,
                cumulative_pnl=state.pnl_total,
                wins=state.trades_won,
                losses=state.trades_lost,
                consecutive_losses=0,
                consecutive_wins=0,
                reason=reason,
                btc_tag=btc_tag()
            ))

    # Remove settled trades from pending
    state.pending_paper_trades = [t for t in state.pending_paper_trades if t.session_id != old_session_id]

    # Summary
    wr = 100 * state.trades_won / (state.trades_won + state.trades_lost) if (state.trades_won + state.trades_lost) > 0 else 0
    state.log("")
    state.log(f"SESSION STATS:")
    state.log(f"  Trades settled: {len(session_trades)}")
    state.log(f"  Total W/L:      {state.trades_won}/{state.trades_lost}")
    state.log(f"  Win Rate:       {wr:.1f}%")
    state.log(f"  Running PnL:    ${state.pnl_total:+.2f}")


def settle_real_trades(old_session_id: str, final_up_price: float, final_down_price: float):
    """Settle real trades when session ends. Determine win/loss based on final prices."""
    if not state.pending_real_trades:
        return

    # Get trades for this session
    session_trades = [t for t in state.pending_real_trades if t.session_id == old_session_id and not t.settled]

    if not session_trades:
        return

    # Determine winner: the side closer to 1.0 wins
    up_won = final_up_price > final_down_price

    state.log("")
    state.log(f"{'='*50}")
    state.log(f"[REAL] SESSION SETTLEMENT: {old_session_id}")
    state.log(f"{'='*50}")
    state.log(f"FINAL UP:   ${final_up_price:.4f}")
    state.log(f"FINAL DOWN: ${final_down_price:.4f}")
    state.log(f"WINNER:     {'UP' if up_won else 'DOWN'}")
    state.log("")

    for trade in session_trades:
        trade.settled = True

        # Check if trade direction matches winner
        if (trade.direction == "Up" and up_won) or (trade.direction == "Down" and not up_won):
            # WIN: we get $1 per share, minus cost
            trade.won = True
            trade.pnl = trade.shares - trade.cost  # $1 * shares - cost
            state.trades_won += 1
            state._daily_wins += 1
        else:
            # LOSE: we get $0, lose entire cost
            trade.won = False
            trade.pnl = -trade.cost
            state.trades_lost += 1
            state._daily_losses += 1

        state.pnl_total += trade.pnl
        state._daily_pnl += trade.pnl
        state.settled_real_trades.append(trade)

        result_emoji = "✅ WIN" if trade.won else "❌ LOSS"
        pnl_str = f"+${trade.pnl:.2f}" if trade.pnl >= 0 else f"-${abs(trade.pnl):.2f}"

        state.log(f"[REAL] TRADE #{trade.trade_id}: {trade.direction} @ ${trade.entry_price:.2f}")
        state.log(f"  RESULT: {result_emoji} | PnL: {pnl_str}")

        # Track metrics (observational only) - call first to get reason
        reason = ""
        if get_metrics_logger():
            reason = get_metrics_logger().on_settlement(
                trade_id=trade.trade_id,
                winner="Up" if up_won else "Down",
                pnl=trade.pnl
            ) or ""

        # Update executor killswitch tracking
        if state.executor:
            trade_key = f"REAL_{trade.session_id}_{trade.trade_id}"
            state.executor.record_result(won=trade.won, pnl=trade.pnl, trade_id=trade_key)

            # Log settlement to real trade log
            real_log_settled(
                order_id=trade_key,
                market_id=trade.session_id,
                won=trade.won,
                trade_pnl=trade.pnl,
                cumulative_pnl=state.executor.cumulative_pnl,
                consecutive_losses=state.executor.consecutive_losses,
                reason=reason
            )

    # Remove settled trades from pending
    state.pending_real_trades = [t for t in state.pending_real_trades if t.session_id != old_session_id]

    # Summary
    wr = 100 * state.trades_won / (state.trades_won + state.trades_lost) if (state.trades_won + state.trades_lost) > 0 else 0
    state.log("")
    state.log(f"[REAL] SESSION STATS:")
    state.log(f"  Trades settled: {len(session_trades)}")
    state.log(f"  Total W/L:      {state.trades_won}/{state.trades_lost}")
    state.log(f"  Win Rate:       {wr:.1f}%")
    state.log(f"  Running PnL:    ${state.pnl_total:+.2f}")
    state.log(f"{'='*50}")
    state.log("")


# ============================================================
# UI COMPONENTS
# ============================================================

def make_header():
    s = state.session

    # Session countdown timer
    tau_mins = int(s.tau // 60)
    tau_secs = int(s.tau % 60)
    elapsed_mins = int(s.elapsed // 60)
    elapsed_secs = int(s.elapsed % 60)

    # Format: 14:32 → 00:00 | Elapsed 0:28 | T=28
    countdown_fmt = f"{tau_mins:02d}:{tau_secs:02d}"
    elapsed_fmt = f"{elapsed_mins}:{elapsed_secs:02d}"

    # Color countdown based on zone
    if s.zone == "CORE":
        timer_style = "bold green"
    elif s.zone == "RECOVERY":
        timer_style = "bold yellow"
    elif s.zone == "DEAD":
        timer_style = "red"
    else:
        timer_style = "white"

    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="center", ratio=2)
    grid.add_column(justify="right", ratio=1)

    # Connection and mode status
    if state.connected:
        # MODE indicator
        if is_real_mode():
            mode_str = "[bold red]REAL[/]"
        else:
            mode_str = "[yellow]PAPER[/]"

        # EXEC indicator (only relevant in real mode)
        if is_real_mode():
            if is_execution_enabled():
                exec_str = "[bold green on dark_green] EXEC:ON [/]"
            else:
                exec_str = "[bold white on red] EXEC:OFF [/]"
        else:
            exec_str = ""

        # Kill switch indicator
        if state.executor and state.executor.kill_switch:
            kill_str = " [bold red blink]KILL[/]"
        else:
            kill_str = ""

        # Live trade limit indicator
        if is_real_mode():
            max_trades = state.max_live_trades_per_run
            used = state.live_trades_this_run
            if max_trades == 0:
                limit_str = f" [dim]({used}/∞)[/]"
            elif used >= max_trades:
                limit_str = f" [red]({used}/{max_trades})[/]"
            else:
                limit_str = f" [dim]({used}/{max_trades})[/]"
        else:
            limit_str = ""

        status = f"[bold green]LIVE[/] {mode_str}{exec_str}{kill_str}{limit_str}"
    else:
        status = "[red]DISCONNECTED[/]"

    pnl_color = "green" if state.pnl_total >= 0 else "red"
    settled = state.trades_won + state.trades_lost
    # Count pending trades based on mode
    if is_real_mode():
        pending = len(state.pending_real_trades)
    else:
        pending = len(state.pending_paper_trades)
    wr = int(100 * state.trades_won / settled) if settled > 0 else 0

    # Wallet short address
    wallet = CREDENTIALS["wallet_address"]
    wallet_short = f"{wallet[:6]}...{wallet[-4:]}" if wallet else "---"

    # Build stats string
    stats_str = (
        f"[bold]S:[/]{state.sessions_seen}({state.sessions_skipped}skip) | "
        f"[bold]T:[/]{state.trades_total}({pending}pend) | "
        f"[bold]W/L:[/][green]{state.trades_won}[/]/[red]{state.trades_lost}[/]({wr}%) | "
        f"[bold]PnL:[/][{pnl_color}]${state.pnl_total:+.2f}[/]"
    )

    grid.add_row(
        f"[bold cyan]RULEV3+[/] {status}",
        stats_str,
        f"[{timer_style}]{countdown_fmt}[/] | E={elapsed_fmt} | T={int(s.tau)}"
    )

    return Panel(grid, style="white on dark_blue", height=3)


def make_live_prices():
    s = state.session
    table = Table(box=box.SIMPLE, expand=True, show_header=True)
    table.add_column("Sym", style="cyan", width=5)
    table.add_column("UP Bid", justify="right", width=8)
    table.add_column("UP Ask", justify="right", width=8)
    table.add_column("DOWN Bid", justify="right", width=8)
    table.add_column("DOWN Ask", justify="right", width=8)
    table.add_column("Edge", justify="right", width=10)
    table.add_column("Dir", justify="center", width=6)

    up_bid = s.up.best_bid or 0
    up_ask = s.up.best_ask or 0
    down_bid = s.down.best_bid or 0
    down_ask = s.down.best_ask or 0

    # Use connector's edge value (correctly handles missing bid/ask)
    edge = s.edge
    edge_dir = s.edge_direction.upper() if s.edge_direction else "---"

    # Edge color based on RULEV3+ threshold
    if edge >= 0.64:
        edge_fmt = f"[bold green]{edge:.3f}[/]"
        dir_fmt = f"[bold green]{edge_dir}[/]"
    elif edge >= 0.60:
        edge_fmt = f"[yellow]{edge:.3f}[/]"
        dir_fmt = f"[yellow]{edge_dir}[/]"
    else:
        edge_fmt = f"[dim]{edge:.3f}[/]"
        dir_fmt = f"[dim]{edge_dir}[/]"

    table.add_row(
        "BTC",
        f"[green]{up_bid:.2f}[/]",
        f"{up_ask:.2f}",
        f"[red]{down_bid:.2f}[/]",
        f"{down_ask:.2f}",
        edge_fmt,
        dir_fmt
    )

    return Panel(table, title="[bold]LIVE PRICES - BTC 15m[/]", border_style="blue")


def make_session_info():
    s = state.session

    # Session times
    if s.session_start_ts > 0:
        start_dt = datetime.fromtimestamp(s.session_start_ts)
        end_dt = datetime.fromtimestamp(s.session_end_ts)
        session_time_str = f"{start_dt.strftime('%H:%M:%S')} - {end_dt.strftime('%H:%M:%S')}"
    else:
        session_time_str = "---"

    # Format elapsed and tau as M:SS
    elapsed_mins = int(s.elapsed // 60)
    elapsed_secs = int(s.elapsed % 60)
    elapsed_fmt = f"{elapsed_mins}:{elapsed_secs:02d}"

    tau_mins = int(s.tau // 60)
    tau_secs = int(s.tau % 60)
    tau_fmt = f"{tau_mins}:{tau_secs:02d}"

    # Zone with color (V2 extended CORE window)
    zone = s.zone
    if zone == "CORE":
        zone_fmt = "[bold green]CORE (2:30-3:45)[/]"
    elif zone == "RECOVERY":
        zone_fmt = "[bold yellow]RECOVERY (5:00-5:59)[/]"
    elif zone == "DEAD":
        zone_fmt = "[red]DEAD (3:46-4:59)[/]"
    elif zone == "EARLY":
        zone_fmt = "[dim]EARLY (0:00-2:29)[/]"
    else:
        zone_fmt = "[dim]LATE (6:00+)[/]"

    # Signal check - RULEV3+ logic
    signal = "WAITING"
    signal_style = "dim"
    if s.zone in ["CORE", "RECOVERY"]:
        if s.edge >= CONFIG["threshold"]:
            ask = s.up.best_ask if s.edge_direction == "Up" else s.down.best_ask
            if ask and ask < CONFIG["safety_cap"]:
                signal = f"BUY {s.edge_direction.upper()} @ {ask:.2f}"
                signal_style = "bold green"
            elif ask:
                signal = f"SAFETY CAP: {ask:.2f} >= {CONFIG['safety_cap']}"
                signal_style = "yellow"
        else:
            signal = f"NO EDGE: {s.edge:.2f} < {CONFIG['threshold']}"
            signal_style = "dim"
    elif s.zone == "DEAD":
        signal = "DEAD ZONE - NO TRADE"
        signal_style = "red"
    elif s.zone == "LATE":
        signal = "TOO LATE"
        signal_style = "dim"

    content = Table.grid(padding=(0, 2))
    content.add_column(width=12)
    content.add_column(justify="left")

    content.add_row("Session ID:", f"[cyan]{s.slug if s.slug else '---'}[/]")
    content.add_row("Time:", f"[white]{session_time_str}[/]")
    content.add_row("Elapsed:", f"[bold]{elapsed_fmt}[/] into session")
    content.add_row("Remaining:", f"[bold]{tau_fmt}[/] (Polymarket timer)")
    content.add_row("Zone:", zone_fmt)
    content.add_row("", "")
    content.add_row("SIGNAL:", f"[{signal_style}]{signal}[/]")

    # Executor status
    if state.executor and state.executor_connected:
        # Cooldown check
        if state.executor.last_trade_time:
            cooldown_elapsed = (datetime.now() - state.executor.last_trade_time).total_seconds()
            cooldown_remaining = state.executor.config.cooldown_seconds - cooldown_elapsed
            if cooldown_remaining > 0:
                content.add_row("Cooldown:", f"[yellow]{cooldown_remaining:.0f}s[/]")
            else:
                content.add_row("Cooldown:", "[green]Ready[/]")
        else:
            content.add_row("Cooldown:", "[green]Ready[/]")

        # Zone trades
        core_trades = state.executor.session_trades.get("CORE", 0)
        recovery_trades = state.executor.session_trades.get("RECOVERY", 0)
        max_per_zone = state.executor.config.max_trades_per_zone
        content.add_row("Session:", f"CORE {core_trades}/{max_per_zone}  REC {recovery_trades}/{max_per_zone}")

    # Last order
    if state.last_order:
        o = state.last_order
        if o.status == OrderStatus.FILLED:
            order_str = f"[green]FILLED {o.direction} @ {o.fill_price:.2f}[/]"
        elif o.status == OrderStatus.DEGRADED:
            order_str = f"[yellow]DEGRADED {o.direction} (slip: {o.slippage_bps:.0f}bps)[/]"
        elif o.status == OrderStatus.CANCELLED:
            order_str = f"[dim]CANCELLED {o.direction}[/]"
        elif o.status == OrderStatus.FAILED:
            order_str = f"[red]FAILED: {o.error}[/]"
        else:
            order_str = f"[dim]{o.status.value}[/]"
        content.add_row("Last Order:", order_str)

    return Panel(content, title="[bold]SESSION - RULEV3+[/]", border_style="cyan")


def make_performance():
    settled = state.trades_won + state.trades_lost
    # Count pending trades based on mode
    if is_real_mode():
        pending = len(state.pending_real_trades)
    else:
        pending = len(state.pending_paper_trades)
    total = state.trades_total
    wr = 100 * state.trades_won / settled if settled else 0
    ev = state.pnl_total / settled if settled else 0

    content = Table.grid(padding=(0, 2))
    content.add_column()
    content.add_column(justify="right")

    content.add_row("Trades:", f"[bold]{total}[/] ({pending} pending)")
    content.add_row("Settled:", f"[bold]{settled}[/]")
    content.add_row("Won:", f"[green]{state.trades_won}[/]")
    content.add_row("Lost:", f"[red]{state.trades_lost}[/]")
    content.add_row("Win Rate:", f"[bold]{wr:.1f}%[/]")
    content.add_row("EV/Trade:", f"[cyan]${ev:+.2f}[/]")
    content.add_row("Total PnL:", f"[{'green' if state.pnl_total >= 0 else 'red'}]${state.pnl_total:+.2f}[/]")

    # Label as simulated in paper mode
    if not is_real_mode():
        title = "[bold]PERFORMANCE[/] [dim](SIMULATED)[/]"
    else:
        title = "[bold]PERFORMANCE[/]"

    return Panel(content, title=title, border_style="green")


def make_zones():
    content = Table.grid(padding=(0, 2))
    content.add_column()
    content.add_column(justify="right")

    content.add_row("[green]CORE[/] entries:", f"{state.core_entries}")
    content.add_row("[yellow]RECOVERY[/] entries:", f"{state.recovery_entries}")
    content.add_row("Sessions seen:", f"{state.sessions_seen}")
    content.add_row("Sessions skipped:", f"{state.sessions_skipped}")

    return Panel(content, title="[bold]ZONES[/]", border_style="yellow")


def make_config():
    content = Table.grid(padding=(0, 1))
    content.add_column()
    content.add_column(justify="right")

    content.add_row("Strategy:", f"[cyan]{CONFIG['strategy']}[/]")
    content.add_row("Mode:", CONFIG['mode'])
    content.add_row("CORE:", CONFIG['core_window'])
    content.add_row("RECOVERY:", CONFIG['recovery_window'])
    content.add_row("Threshold:", f">= {CONFIG['threshold']}")
    content.add_row("Safety Cap:", f"< {CONFIG['safety_cap']}")
    content.add_row("", "")
    content.add_row("Trade Size:", f"[green]${CONFIG['cash_per_trade']:.2f}[/]")
    content.add_row("Max Position:", f"[green]${CONFIG['max_position']:.2f}[/]")

    return Panel(content, title="[bold]CONFIG[/]", border_style="magenta")


def make_book_depth():
    s = state.session

    content = Table.grid(padding=(0, 2))
    content.add_column()
    content.add_column(justify="right")
    content.add_column(justify="right")

    content.add_row("", "[green]UP[/]", "[red]DOWN[/]")
    content.add_row("Best Bid:", f"{s.up.best_bid or 0:.2f}", f"{s.down.best_bid or 0:.2f}")
    content.add_row("Best Ask:", f"{s.up.best_ask or 0:.2f}", f"{s.down.best_ask or 0:.2f}")
    content.add_row("Mid:", f"{s.up.mid or 0:.2f}", f"{s.down.mid or 0:.2f}")
    content.add_row("Depth Bid:", f"{s.up.depth_bid:.0f}", f"{s.down.depth_bid:.0f}")
    content.add_row("Depth Ask:", f"{s.up.depth_ask:.0f}", f"{s.down.depth_ask:.0f}")

    return Panel(content, title="[bold]ORDER BOOK[/]", border_style="blue")


def make_logs():
    log_text = "\n".join(list(state.logs)[-18:]) if state.logs else "[dim]Connecting...[/]"
    return Panel(log_text, title="[bold]LOGS[/]", border_style="dim")


# ============================================================
# LAYOUT
# ============================================================

def make_layout():
    layout = Layout()

    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main", ratio=1),
        Layout(name="logs", size=14),
    )

    layout["main"].split_row(
        Layout(name="left", ratio=2),
        Layout(name="right", ratio=1),
    )

    layout["left"].split_column(
        Layout(name="prices", size=6),
        Layout(name="book", size=10),
        Layout(name="session", ratio=1),
    )

    layout["right"].split_column(
        Layout(name="performance", size=10),
        Layout(name="zones", size=8),
        Layout(name="config", ratio=1),
    )

    return layout


def update_layout(layout):
    layout["header"].update(make_header())
    layout["prices"].update(make_live_prices())
    layout["book"].update(make_book_depth())
    layout["session"].update(make_session_info())
    layout["performance"].update(make_performance())
    layout["zones"].update(make_zones())
    layout["config"].update(make_config())
    layout["logs"].update(make_logs())


# ============================================================
# EXECUTOR INTEGRATION
# ============================================================

def init_executor() -> Optional[TradeExecutor]:
    """Initialize the trade executor."""
    private_key = CREDENTIALS["private_key"]
    funder = CREDENTIALS["funder_address"]
    sig_type = CREDENTIALS["signature_type"]

    if not private_key:
        state.log("No private key - executor disabled")
        return None

    config = ExecutorConfig(
        cash_per_trade=CONFIG["cash_per_trade"],
        edge_threshold=CONFIG["threshold"],
        safety_cap=CONFIG["safety_cap"],
    )

    executor = TradeExecutor(
        private_key=private_key,
        funder=funder,
        signature_type=sig_type,
        config=config
    )
    executor.on_log = lambda msg: state.log(f"[EXEC] {msg}")
    executor.on_order_update = on_order_update

    return executor


def on_order_update(result: OrderResult):
    """Handle order status updates."""
    state.last_order = result

    if result.status in [OrderStatus.FILLED, OrderStatus.DEGRADED]:
        state.trades_total += 1
        if result.zone == "CORE":
            state.core_entries += 1
        elif result.zone == "RECOVERY":
            state.recovery_entries += 1

        # Update balance from executor
        if state.executor:
            state.usdc_balance = state.executor.balance

        # Track real trades for settlement
        if is_real_mode():
            cost = result.fill_price * result.filled_size
            potential_win = (1.0 - result.fill_price) * result.filled_size
            real_trade = PaperTrade(
                trade_id=state.trades_total,
                session_id=state.session.slug if state.session else "",
                direction=result.direction,
                entry_price=result.fill_price,
                shares=result.filled_size,
                cost=cost,
                potential_win=potential_win,
                potential_loss=cost,
                zone=result.zone,
                timestamp=datetime.now()
            )
            state.pending_real_trades.append(real_trade)
            state._daily_trades += 1
            state.log(f"[TRACK] Real trade added: {result.direction} x{result.filled_size:.2f} @ {result.fill_price:.3f}")

            # Telegram: notify on every trade entry
            tg.send(tg.fmt_trade(
                trade_id=real_trade.trade_id,
                direction=result.direction,
                entry_price=result.fill_price,
                cost=cost,
                zone=result.zone
            ))

            # Track metrics (observational only)
            if get_metrics_logger():
                get_metrics_logger().on_entry(
                    trade_id=real_trade.trade_id,
                    session_id=real_trade.session_id,
                    direction=result.direction,
                    entry_price=result.fill_price,
                    edge=state.session.edge if state.session else 0.5,
                    elapsed=state.session.elapsed if state.session else 0,
                    mode="real"
                )


def get_current_state():
    """Get current (zone, edge, ask) for retry validation."""
    s = state.session
    ask = s.up.best_ask if s.edge_direction == "Up" else s.down.best_ask
    return (s.zone, s.edge, ask or 0)


async def check_and_execute_signal():
    """Check if RULEV3.1 signal is valid and execute trade.

    GATES (in order):
    1. MODE_ZONE_GATE: T3-only means only CORE zone
    2. BOOK_GATE: Must have valid bid/ask
    3. SESSION_CAP: Max 1 trade per session (total)
    4. DYNAMIC_EDGE_GATE (RULEV3.1):
       - ask <= 0.66 → edge >= 0.64
       - ask <= 0.69 → edge >= 0.67
       - else        → edge >= 0.70
       + REGIME MODIFIER (OFF by default, set CHOP_MOD_ENABLED=1): if CHOPPY, add +0.03
    5. HARD_PRICE_GATE: ask <= hard_price_cap (0.72)
    6. PRICE_GATE: ask < safety_cap (0.72)
    7. BAD_BOOK: spread >= 0 and bid <= ask
    8. SPREAD_GATE: spread <= 0.02
    9. EXECUTOR: zone limits, cooldowns, balance
    """
    s = state.session
    executor = state.executor

    if not executor or not executor.connected:
        return

    # Already have a pending order?
    if state.pending_order:
        return

    # Kill switch active?
    if executor.kill_switch:
        return

    # Get window ID for logging
    window_id = get_window_id(s.zone, s.elapsed)

    # ============================================================
    # GATE 1: MODE_ZONE_GATE - T3-only means only CORE zone
    # ============================================================
    allowed_zones = CONFIG.get("allowed_zones", ["CORE"])
    if s.zone not in allowed_zones:
        # Only log once per zone change (avoid spam)
        last_zone_skip = getattr(state, '_last_zone_skip', "")
        if s.edge >= CONFIG["threshold"] and s.zone != last_zone_skip:
            state._last_zone_skip = s.zone
            state.log(f"[SKIP] MODE_ZONE_GATE: {s.zone}({window_id}) not in {allowed_zones}")
        return

    # Check for new session - reset counters
    session_id = s.slug or ""
    if session_id and session_id != state.current_session_id:
        if state.current_session_id:
            state.log(f"[SESSION] New: {session_id}")
        state.current_session_id = session_id
        state.session_trade_count = 0  # Reset session trade counter
        state._session_cap_logged = False  # Reset skip log flag
        state._last_zone_skip = ""  # Reset zone skip tracker
        executor.new_session(session_id)
        state.log(f"[SESSION] Counters reset: session_trades=0")

    # Get direction and prices
    direction = s.edge_direction
    if direction == "Up":
        ask = s.up.best_ask
        bid = s.up.best_bid
        token_id = s.token_up
    else:
        ask = s.down.best_ask
        bid = s.down.best_bid
        token_id = s.token_down

    # ============================================================
    # GATE 2: BOOK_GATE - Must have valid bid/ask
    # ============================================================
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        state.log(f"[SKIP] NO_BOOK: {s.zone}({window_id}) bid={bid} ask={ask}")
        return

    if not token_id:
        state.log(f"[SKIP] NO_TOKEN: {s.zone}({window_id}) {direction}")
        return

    # Calculate spread for later use
    spread = ask - bid

    # ============================================================
    # GATE 3: SESSION_CAP - Max trades per session (total)
    # ============================================================
    max_per_session = CONFIG.get("max_trades_per_session", 1)
    if state.session_trade_count >= max_per_session:
        # Only log once per session
        if not getattr(state, '_session_cap_logged', False):
            state._session_cap_logged = True
            state.log(f"[SKIP] SESSION_CAP: {s.zone}({window_id}) {state.session_trade_count}/{max_per_session}")
        return

    # ============================================================
    # GATE 4: DYNAMIC_EDGE_GATE - pricing-aware thresholds (RULEV3.1)
    # Cheap prices = forgiving, Expensive prices = ruthless
    # Aligns required edge with payout math
    #
    # REGIME MODIFIER (disabled by default, set CHOP_MOD_ENABLED=1 to enable):
    #   CHOPPY (crossings >= 6) → add +0.03 to required edge
    #   STABLE/NEUTRAL → no modifier
    # ============================================================
    if ask <= 0.66:
        required_edge = 0.64
    elif ask <= 0.69:
        required_edge = 0.67
    else:
        required_edge = 0.70

    # Always compute regime for logging/analysis (even when modifier disabled)
    regime, crossings = btc_regime()

    # Regime modifier: DISABLED by default (backtest showed it hurts PnL)
    # Set CHOP_MOD_ENABLED=1 to enable for A/B testing
    chop_mod_enabled = os.getenv("CHOP_MOD_ENABLED", "0") == "1"
    if chop_mod_enabled and regime == "CHOPPY":
        required_edge += 0.03

    if s.edge < required_edge:
        if chop_mod_enabled and regime == "CHOPPY":
            state.log(f"[SKIP] EDGE_GATE+CHOP: {s.zone}({window_id}) edge={s.edge:.3f} < {required_edge:.3f} (base+0.03) crossings={crossings}")
        return  # Silent when not choppy - dynamic edge not met

    # ============================================================
    # GATE 5: HARD_PRICE_GATE - ask <= hard_price_cap (prevents tiny payouts)
    # ============================================================
    hard_cap = CONFIG.get("hard_price_cap", 0.65)
    if ask > hard_cap:
        state.log(f"[SKIP] HARD_PRICE_CAP: {s.zone}({window_id}) ask={ask:.3f} > {hard_cap}")
        return

    # ============================================================
    # GATE 6: PRICE_GATE - ask < safety_cap
    # ============================================================
    if ask >= CONFIG["safety_cap"]:
        state.log(f"[SKIP] PRICE_CAP: {s.zone}({window_id}) ask={ask:.3f} >= {CONFIG['safety_cap']}")
        return

    # ============================================================
    # GATE 7: BAD_BOOK - Sanity check (spread >= 0 and bid <= ask)
    # ============================================================
    if spread < 0 or bid > ask:
        state.log(f"[SKIP] BAD_BOOK: {s.zone}({window_id}) bid={bid:.3f} ask={ask:.3f} spread={spread:.3f} tau={s.tau:.0f}s")
        return

    # ============================================================
    # GATE 8: SPREAD_GATE - spread <= 0.02 (spread hygiene)
    # ============================================================
    if spread > 0.02:
        state.log(f"[SKIP] SPREAD_GATE: {s.zone}({window_id}) bid={bid:.3f} ask={ask:.3f} spread={spread:.3f} tau={s.tau:.0f}s")
        return

    # ============================================================
    # GATE 9: EXECUTOR VALIDATION (zone limits, cooldowns, balance)
    # Only check in REAL mode - paper mode skips balance/executor checks
    # ============================================================
    if is_real_mode():
        valid, reason = executor.validate_signal(s.zone, direction, s.edge, ask)
        if not valid:
            state.log(f"[GATE] {reason}")
            return

    # ============================================================
    # ALL GATES PASSED - Execute trade
    # ============================================================
    # Log with regime info for analysis (regime computed above but modifier may be disabled)
    state.log(f"[SIGNAL] {s.zone}({window_id}) {direction} edge={s.edge:.3f} ask={ask:.2f} spread={spread:.3f} regime={regime} x={crossings}")

    # Log signal to real trade log (only if real mode enabled)
    real_log_signal(
        market_id=s.slug or "",
        question=f"BTC 15min {direction}",
        zone=s.zone,
        edge=s.edge,
        direction=direction,
        best_bid=bid,
        best_ask=ask,
        spread=spread
    )

    if is_execution_enabled():
        # REAL EXECUTION - check live trade limit first (0 = unlimited)
        if state.max_live_trades_per_run > 0 and state.live_trades_this_run >= state.max_live_trades_per_run:
            state.log(f"[BLOCKED] Live trade limit reached ({state.live_trades_this_run}/{state.max_live_trades_per_run})")
            # Still update executor state to prevent repeated attempts
            executor.session_trades[s.zone] = executor.session_trades.get(s.zone, 0) + 1
            executor.last_trade_time = datetime.now()
            return

        state.log(f"[REAL] Executing {direction} @ {ask:.2f}")
        result = await executor.execute_trade(
            token_id=token_id,
            direction=direction,
            price=ask,
            zone=s.zone,
            edge=s.edge,
            get_current_state=get_current_state
        )
        state.pending_order = None

        # Increment live trade counter on successful submission
        if result.status in [OrderStatus.FILLED, OrderStatus.DEGRADED, OrderStatus.SUBMITTED]:
            state.live_trades_this_run += 1
            state.session_trade_count += 1  # Also increment session counter
            state.log(f"[REAL] Live trades: {state.live_trades_this_run}/{state.max_live_trades_per_run}")

            # Check if we've hit the max trades cap - trigger clean shutdown
            if state.max_live_trades_per_run > 0 and state.live_trades_this_run >= state.max_live_trades_per_run:
                state.log(f"[STOP] Max live trades reached ({state.live_trades_this_run}/{state.max_live_trades_per_run})")
                state.should_stop = True
                state.stop_reason = "max_trades_cap"
                # Log to real trade log
                real_log_stop(
                    reason="max_trades_cap",
                    final_balance=state.usdc_balance,
                    final_pnl=state.pnl_total
                )

    elif is_real_mode():
        # Real mode but EXECUTION_ENABLED=false - blocked
        state.log(f"[BLOCKED] EXEC:OFF - Would BUY {direction} @ {ask:.2f}")
        # Update executor state to prevent spam
        executor.session_trades[s.zone] = executor.session_trades.get(s.zone, 0) + 1
        executor.last_trade_time = datetime.now()

    else:
        # Paper mode - log it and update executor state to prevent double-fire
        global session_had_trade
        session_had_trade = True  # Mark this session as having a trade

        shares = CONFIG["cash_per_trade"] / ask
        potential_profit = (1.0 - ask) * shares
        potential_loss = CONFIG["cash_per_trade"]  # We lose our cost
        trade_time = datetime.now()

        # EV placeholder - will be calculated properly once p_model exists (Phase 2)
        ev_per_trade = 0.0  # Cannot calculate without p_model

        # CRITICAL: Update executor state even in paper mode to prevent double-fire
        executor.session_trades[s.zone] = executor.session_trades.get(s.zone, 0) + 1
        executor.last_trade_time = trade_time

        # INCREMENT SESSION TRADE COUNTER (key fix for session cap)
        state.session_trade_count += 1

        # Update UI stats
        if s.zone == "CORE":
            state.core_entries += 1
        else:
            state.recovery_entries += 1
        state.trades_total += 1

        # Create paper trade for tracking
        paper_trade = PaperTrade(
            trade_id=state.trades_total,
            session_id=s.slug or "",
            direction=direction,
            entry_price=ask,
            shares=shares,
            cost=CONFIG["cash_per_trade"],
            potential_win=potential_profit,
            potential_loss=potential_loss,
            zone=s.zone,
            timestamp=trade_time
        )
        state.pending_paper_trades.append(paper_trade)
        state._daily_trades += 1

        # Telegram: notify on every trade entry
        tg.send(tg.fmt_trade(
            trade_id=paper_trade.trade_id,
            direction=direction,
            entry_price=ask,
            cost=CONFIG["cash_per_trade"],
            zone=s.zone
        ))

        # Track metrics (observational only)
        if get_metrics_logger():
            get_metrics_logger().on_entry(
                trade_id=paper_trade.trade_id,
                session_id=s.slug or "",
                direction=direction,
                entry_price=ask,
                edge=s.edge,
                elapsed=s.elapsed,
                mode="paper"
            )

        # Generate paper order ID for tracking
        paper_order_id = f"PAPER-{trade_time.strftime('%Y%m%d%H%M%S')}-{state.trades_total:04d}"

        # Log submit step (matches real trade lifecycle)
        state.log(f"[SUBMIT] {s.zone} {direction} @ {ask:.3f} x{shares:.2f} order_id={paper_order_id}")

        # Check bid liquidity
        fill_status = "FILLED" if spread < 0.05 else "WIDE SPREAD"

        # Helper for safe price formatting
        def fmt_px(x):
            return f"${x:.4f}" if x is not None and x > 0 else "NA"

        # Structured paper trade log
        state.log(f"")
        state.log(f"{'='*50}")
        state.log(f"PAPER TRADE #{state.trades_total}")
        state.log(f"{'='*50}")
        state.log(f"ORDER_ID:  {paper_order_id}")
        state.log(f"TIME:      {trade_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        state.log(f"SESSION:   {s.slug}")
        state.log(f"ZONE:      {s.zone} ({window_id})")
        state.log(f"DIRECTION: {direction}")
        state.log(f"FILL:      {fill_status} (spread: ${spread:.4f})")
        state.log(f"EDGE:      {s.edge:.4f}")
        state.log(f"ASK:       {fmt_px(ask)}")
        state.log(f"BID:       {fmt_px(bid)}")
        state.log(f"SPREAD:    ${spread:.4f}")
        state.log(f"SHARES:    {shares:.4f}")
        state.log(f"COST:      ${CONFIG['cash_per_trade']:.2f}")
        state.log(f"IF_WIN:    +${potential_profit:.2f}")
        state.log(f"IF_LOSE:   -${potential_loss:.2f}")
        state.log(f"EV/TRADE:  N/A (awaiting p_model)")
        state.log(f"TOKEN:     {token_id}")
        state.log(f"UP:        bid={fmt_px(s.up.best_bid)} ask={fmt_px(s.up.best_ask)}")
        state.log(f"DOWN:      bid={fmt_px(s.down.best_bid)} ask={fmt_px(s.down.best_ask)}")
        state.log(f"TAU:       {s.tau:.1f}s")
        state.log(f"ELAPSED:   {s.elapsed:.1f}s")
        state.log(f"SESS_CNT:  {state.session_trade_count}/{CONFIG['max_trades_per_session']}")
        state.log(f"PENDING:   {len(state.pending_paper_trades)} trades awaiting settlement")
        state.log(f"STATS:     W:{state.trades_won} L:{state.trades_lost} PnL:${state.pnl_total:+.2f}")
        state.log(f"{'='*50}")
        state.log(f"")

        # Explicit FILLED log for lifecycle tracking
        state.log(f"[FILLED] {paper_order_id} @ {ask:.3f} x{shares:.2f} (paper)")

        # Send Telegram FILLED notification (paper mode)
        tg.send(tg.fmt_filled(
            trade_id=state.trades_total,
            direction=direction,
            fill_price=ask,
            max_loss=potential_loss,
            btc_tag=btc_tag()
        ))


# ============================================================
# CALLBACKS
# ============================================================

last_slug = ""
last_up_price = 0.5
last_down_price = 0.5
session_had_trade = False  # Track if current session had a trade

def on_market_update(session_state: SessionState):
    global last_slug, last_up_price, last_down_price, session_had_trade

    # BEFORE updating state, check for session change and settle pending trades
    if session_state.slug and session_state.slug != last_slug:
        # Session is changing!
        if last_slug:
            # Check if old session had any trades
            if not session_had_trade:
                state.sessions_skipped += 1
                state.log(f"[SESSION] Skipped (no signal): {last_slug}")

            # Settle any pending paper trades from old session
            if state.pending_paper_trades:
                # Use the LAST known prices from old session
                settle_paper_trades(last_slug, last_up_price, last_down_price)

            # Settle any pending real trades from old session
            if state.pending_real_trades:
                settle_real_trades(last_slug, last_up_price, last_down_price)

        state.sessions_seen += 1
        state._daily_sessions += 1
        session_had_trade = False  # Reset for new session
        last_slug = session_state.slug

        # --- TELEGRAM NOTIFICATIONS ---
        # Check for daily rollover (send daily summary)
        today = datetime.now().strftime("%Y-%m-%d")
        if state._current_date and state._current_date != today:
            # Day changed - send daily summary
            msg = tg.fmt_daily(
                date=state._current_date,
                sessions=state._daily_sessions,
                trades=state._daily_trades,
                wins=state._daily_wins,
                losses=state._daily_losses,
                pnl=state._daily_pnl
            )
            tg.send(msg)
            state.log(f"[TG] Daily summary sent for {state._current_date}")

            # Reset daily counters
            state._daily_sessions = 1  # Current session counts for new day
            state._daily_trades = 0
            state._daily_wins = 0
            state._daily_losses = 0
            state._daily_pnl = 0.0

        state._current_date = today

        # Send 10-session update
        if state.sessions_seen > 0 and state.sessions_seen % 10 == 0:
            if state.sessions_seen > state._last_periodic_notify:
                pending = len(state.pending_paper_trades) + len(state.pending_real_trades)
                msg = tg.fmt_periodic(
                    sessions=state.sessions_seen,
                    trades=state.trades_total,
                    wins=state.trades_won,
                    losses=state.trades_lost,
                    pnl=state.pnl_total,
                    pending=pending
                )
                tg.send(msg)
                state._last_periodic_notify = state.sessions_seen

    # Update state with new session data
    state.session = session_state
    state.connected = session_state.connected
    state.last_update = datetime.now()

    # Track ticks for active trades (metrics - observational only)
    if get_metrics_logger():
        for trade in state.pending_paper_trades:
            get_metrics_logger().on_tick(trade.trade_id, session_state.edge)
        for trade in state.pending_real_trades:
            get_metrics_logger().on_tick(trade.trade_id, session_state.edge)

    # Store current prices for settlement on next session change
    if session_state.up.best_bid:
        last_up_price = (session_state.up.best_bid + (session_state.up.best_ask or session_state.up.best_bid)) / 2
        # Record UP token price for BTC trend tracking (observational)
        btc_record(last_up_price)
    if session_state.down.best_bid:
        last_down_price = (session_state.down.best_bid + (session_state.down.best_ask or session_state.down.best_bid)) / 2


def on_log(msg: str):
    state.log(msg)


# ============================================================
# MAIN
# ============================================================

async def run_dashboard():
    console = Console()
    layout = make_layout()

    # Initialize log file - separate folders for real/paper
    # Logs go to project root /logs folder
    base_logs_dir = Path(__file__).parent.parent.parent / "logs"
    if is_real_mode():
        logs_dir = base_logs_dir / "real"
    else:
        logs_dir = base_logs_dir / "paper"
    logs_dir.mkdir(parents=True, exist_ok=True)
    run_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_filename = f"trades_{run_timestamp}.log"
    state.log_file = str(logs_dir / log_filename)

    # Init metrics logger (same timestamp for linking)
    metrics_logger = init_metrics_logger(logs_dir, run_timestamp)

    state.log("RULEV3+ Live Dashboard starting...")
    state.log(f"Log file: {log_filename}")
    state.log(f"[RUN] metrics_file={metrics_logger.get_metrics_path()}")
    state.log(f"Strategy: {CONFIG['strategy']} v{CONFIG['version']}")
    state.log(f"Mode: {CONFIG['mode']}")

    # Initialize safety limiters
    state.max_live_trades_per_run = CREDENTIALS["max_live_trades_per_run"]
    state.live_trades_this_run = 0

    # Show trading mode and fetch initial balance
    wallet = CREDENTIALS["wallet_address"]
    wallet_short = f"{wallet[:6]}...{wallet[-4:]}" if len(wallet) > 10 else "???"

    if is_real_mode():
        state.log(f"REAL MODE - Wallet: {wallet_short}")
        if is_execution_enabled():
            state.log(f"EXEC:ON - Live trades enabled (max {state.max_live_trades_per_run}/run)")
        else:
            state.log(f"EXEC:OFF - Orders will be BLOCKED until EXECUTION_ENABLED=true")
    else:
        state.log(f"PAPER MODE - Wallet: {wallet_short}")

    # Initialize real trade logger (only writes if real mode)
    init_real_logger(log_dir=str(logs_dir), enabled=is_real_mode())

    # Initialize BTC trend tracker (for Telegram context)
    tracker_enabled = is_btc_tracker_enabled()
    init_btc_tracker(enabled=tracker_enabled)
    state.log(f"[TRACKER] BTC trend tracker initialized: enabled={tracker_enabled}")

    # Start Telegram control listener (for /status, /pnl, /logs commands)
    import threading
    def _run_telegram_control():
        telegram_control.start()
    tg_thread = threading.Thread(target=_run_telegram_control, daemon=True)
    tg_thread.start()
    state.log("[TELEGRAM] Control listener started")

    # RESTART WARNING - session state does NOT persist
    state.log("[WARN] Session state resets on restart (zone counters = 0)")

    # Initialize trade executor
    state.log("Initializing trade executor...")
    state.executor = init_executor()

    if state.executor:
        if state.executor.connect():
            state.executor_connected = True
            state.usdc_balance = state.executor.refresh_balance()
            state.log(f"Executor connected - Balance: ${state.usdc_balance:.2f}")
        else:
            state.log("Executor connection failed - running in monitor mode")
    else:
        # No executor - fetch balance directly
        state.log("Fetching Polymarket balance...")
        state.usdc_balance = fetch_usdc_balance()

    state.last_balance_check = datetime.now()
    state.log(f"Balance: ${state.usdc_balance:.2f} USDC")

    # Log session start for real trading
    real_log_start(balance=state.usdc_balance, config=CONFIG)

    state.log("Connecting to Polymarket WebSocket...")

    # Start session manager
    manager = SessionManager()
    manager.on_update = on_market_update
    manager.on_log = on_log

    # Run manager in background
    manager_task = asyncio.create_task(manager.start())

    try:
        with Live(layout, console=console, refresh_per_second=4, screen=True):
            while not state.should_stop:
                update_layout(layout)

                # Check for trading signals
                await check_and_execute_signal()

                # Refresh balance every 60 seconds
                if state.last_balance_check:
                    elapsed = (datetime.now() - state.last_balance_check).total_seconds()
                    if elapsed > 60:
                        if state.executor and state.executor_connected:
                            state.usdc_balance = state.executor.refresh_balance()
                        else:
                            state.usdc_balance = fetch_usdc_balance()
                        state.last_balance_check = datetime.now()

                # Periodic stats log every 5 minutes
                runtime_mins = (datetime.now() - state.start_time).total_seconds() / 60
                if runtime_mins > 0 and int(runtime_mins) % 5 == 0 and int(runtime_mins) != getattr(state, '_last_stats_log', 0):
                    state._last_stats_log = int(runtime_mins)
                    settled = state.trades_won + state.trades_lost
                    pending = len(state.pending_paper_trades)
                    wr = 100 * state.trades_won / settled if settled > 0 else 0
                    ev = state.pnl_total / settled if settled > 0 else 0
                    state.log(f"[STATS] {int(runtime_mins)}m | Sessions: {state.sessions_seen} (skip:{state.sessions_skipped}) | Trades: {state.trades_total} (pend:{pending}) | W/L: {state.trades_won}/{state.trades_lost} ({wr:.0f}%) | AvgPnL: ${ev:+.2f} | PnL: ${state.pnl_total:+.2f}")
                    # Log BTC tracker status for debugging
                    tracker = get_btc_tracker()
                    if tracker:
                        buf_len = len(tracker._buffer)
                        rec_count = getattr(tracker, '_record_count', 0)
                        state.log(f"[TRACKER] UP_5m: {btc_tag()} | buf={buf_len} rec={rec_count}")

                # Periodic price/status log every 30 seconds for paper mode monitoring
                runtime_secs = int((datetime.now() - state.start_time).total_seconds())
                if runtime_secs > 0 and runtime_secs % 30 == 0 and runtime_secs != getattr(state, '_last_price_log', 0):
                    state._last_price_log = runtime_secs
                    s = state.session
                    # Safe price formatting - show NA if None
                    def px(x): return f"{x:.2f}" if x is not None and x > 0 else "NA"
                    state.log(f"[LIVE] Zone:{s.zone} T={int(s.tau)}s | UP:{px(s.up.best_bid)}/{px(s.up.best_ask)} DOWN:{px(s.down.best_bid)}/{px(s.down.best_ask)} | Edge:{s.edge:.3f} {s.edge_direction}")

                # Sync state to Telegram control
                import time as _time
                telegram_control._state.last_heartbeat = _time.time()
                telegram_control._state.execution_enabled = is_execution_enabled()
                telegram_control._state.zone_mode = CONFIG.get("mode", "T3-only")
                telegram_control._state.zone = state.session.zone if state.session else ""
                telegram_control._state.edge = state.session.edge if state.session else 0.0
                telegram_control._state.balance = state.usdc_balance
                telegram_control._state.pnl = state.pnl_total
                telegram_control._state.wins = state.trades_won
                telegram_control._state.losses = state.trades_lost
                telegram_control._state.trades_total = state.trades_total
                telegram_control._state.consecutive_losses = state.executor.consecutive_losses if state.executor else 0
                telegram_control._state.killswitch_active = state.executor.kill_switch if state.executor else False
                telegram_control._state.current_session = state.session.slug if state.session else ""

                # Check if /kill was requested via Telegram
                if telegram_control._state.kill_requested and state.executor:
                    state.executor.kill_switch = True
                    state.executor.kill_reason = "Telegram /kill"
                    state.log("[TELEGRAM] Kill requested remotely")
                    telegram_control._state.kill_requested = False  # Clear flag

                await asyncio.sleep(0.25)

        # Clean exit due to should_stop flag (e.g., max_trades_cap)
        if state.should_stop:
            state.log(f"Clean shutdown: {state.stop_reason}")

            # Final session summary
            runtime_mins = (datetime.now() - state.start_time).total_seconds() / 60
            settled = state.trades_won + state.trades_lost
            pending = len(state.pending_paper_trades)
            wr = 100 * state.trades_won / settled if settled > 0 else 0
            ev = state.pnl_total / settled if settled > 0 else 0

            state.log("")
            state.log("=" * 50)
            state.log(f"SESSION COMPLETE - {state.stop_reason.upper()}")
            state.log("=" * 50)
            state.log(f"Runtime:        {int(runtime_mins)} minutes")
            state.log(f"Live trades:    {state.live_trades_this_run}/{state.max_live_trades_per_run}")
            state.log(f"Total PnL:      ${state.pnl_total:+.2f}")
            state.log(f"Final balance:  ${state.usdc_balance:.2f}")
            state.log("=" * 50)
            state.log("Bot stopped cleanly. No Ctrl+C required.")

    except KeyboardInterrupt:
        state.log("Shutdown requested...")

        # Log session stop for real trading
        real_log_stop(
            reason="manual",
            final_balance=state.usdc_balance,
            final_pnl=state.pnl_total
        )

        # Final session summary
        runtime_mins = (datetime.now() - state.start_time).total_seconds() / 60
        settled = state.trades_won + state.trades_lost
        pending = len(state.pending_paper_trades)
        wr = 100 * state.trades_won / settled if settled > 0 else 0
        ev = state.pnl_total / settled if settled > 0 else 0

        state.log("")
        state.log("=" * 50)
        state.log("FINAL SESSION SUMMARY")
        state.log("=" * 50)
        state.log(f"Runtime:        {int(runtime_mins)} minutes")
        state.log(f"Sessions seen:  {state.sessions_seen}")
        state.log(f"Sessions skip:  {state.sessions_skipped}")
        state.log(f"Total trades:   {state.trades_total}")
        state.log(f"CORE entries:   {state.core_entries}")
        state.log(f"RECOV entries:  {state.recovery_entries}")
        state.log(f"Settled:        {settled}")
        state.log(f"Wins:           {state.trades_won}")
        state.log(f"Losses:         {state.trades_lost}")
        state.log(f"Win Rate:       {wr:.1f}%")
        state.log(f"EV/Trade:       ${ev:+.2f}")
        state.log(f"Total PnL:      ${state.pnl_total:+.2f}")
        state.log(f"Pending:        {pending} unsettled trades")
        state.log("=" * 50)

        if state.executor:
            await state.executor.cancel_all()
        manager.stop()
        await asyncio.sleep(0.5)
        print("\nDashboard stopped.")


def main():
    asyncio.run(run_dashboard())


if __name__ == "__main__":
    main()
