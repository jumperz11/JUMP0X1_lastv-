#!/usr/bin/env python3
"""
RULEV3+ Trade Executor - PRODUCTION VERSION
============================================
Handles order placement with strict execution policies.

Key policies:
- Short fill timeouts (600-1500ms depending on zone)
- Conditional retries only if signal still valid
- Partial fill handling with cancel logic
- Slippage guard with kill-switch
- Max 1 trade per zone per session
- Cooldown between trades
"""

import os
import time
import asyncio
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict
from enum import Enum

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    BalanceAllowanceParams, AssetType,
    OrderArgs, OrderType
)

# Real trade logging (only writes when enabled=True in real mode)
from .real_trade_logger import (
    init_real_logger, get_real_logger,
    real_log_submit, real_log_filled, real_log_kill
)


class OrderStatus(Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    MATCHED = "MATCHED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    DEGRADED = "DEGRADED"  # Filled but with bad slippage


@dataclass
class OrderResult:
    order_id: str = ""
    status: OrderStatus = OrderStatus.PENDING
    direction: str = ""
    zone: str = ""
    expected_price: float = 0.0
    fill_price: float = 0.0
    size: float = 0.0
    filled_size: float = 0.0
    slippage_bps: float = 0.0
    submit_time: Optional[datetime] = None
    fill_time: Optional[datetime] = None
    latency_ms: int = 0
    error: str = ""
    retries: int = 0
    degraded: bool = False


@dataclass
class ExecutorConfig:
    # Trade sizing
    cash_per_trade: float = 5.0  # $5 risk per trade

    # Timeouts by zone (ms)
    core_timeout_ms: int = 1000      # 600-1200ms recommended
    recovery_timeout_ms: int = 1200  # 800-1500ms recommended
    poll_interval_ms: int = 100      # Fast polling

    # Retry policy
    max_retries: int = 1  # Only 1 retry, not automatic
    retry_delay_ms: int = 200

    # Slippage
    max_slippage_bps: float = 100.0  # 1% max
    degraded_threshold_bps: float = 100.0  # Mark as degraded above this
    degraded_kill_count: int = 2  # Kill switch after N degraded fills

    # Partial fill policy
    partial_min_remaining: float = 0.1  # Cancel if remaining < 10%

    # Session limits
    max_trades_per_zone: int = 1  # Max 1 trade in CORE, 1 in RECOVERY
    cooldown_seconds: float = 30.0  # Wait between trades

    # RULEV3+ thresholds
    edge_threshold: float = 0.64
    safety_cap: float = 0.68  # From sweep: 0.68 optimal (was 0.72)

    # === KILLSWITCH SETTINGS ===
    # SWEEP FINDING: Kill switch L=3 DESTROYS edge (-$22 avg PnL)
    # Kill switch OFF = +$480 avg PnL. Set to 999 = effectively disabled.
    max_consec_losses: int = 999     # DISABLED (was 3, sweep showed it destroys edge)
    pnl_floor_dollars: float = -50.0 # Raised floor (only emergency stop)
    pause_on_kill: bool = True       # Pause (vs exit) when killed


class TradeExecutor:
    """
    RULEV3+ Trade Executor with strict execution policies.
    """

    def __init__(
        self,
        private_key: str,
        funder: str,
        signature_type: int = 2,
        config: Optional[ExecutorConfig] = None
    ):
        self.private_key = private_key
        self.funder = funder
        self.signature_type = signature_type
        self.config = config or ExecutorConfig()

        self.client: Optional[ClobClient] = None
        self.connected = False
        self.balance = 0.0

        # Callbacks
        self.on_log: Optional[Callable[[str], None]] = None
        self.on_order_update: Optional[Callable[[OrderResult], None]] = None

        # Session state
        self.current_session: str = ""
        self.session_trades: Dict[str, int] = {}  # zone -> count
        self.degraded_count: int = 0
        self.kill_switch: bool = False
        self.kill_reason: str = ""
        self.last_trade_time: Optional[datetime] = None

        # Killswitch tracking (persists across sessions)
        self.consecutive_losses: int = 0
        self.cumulative_pnl: float = 0.0
        self.total_trades: int = 0
        self.total_wins: int = 0
        self.total_losses: int = 0
        self.settled_trade_ids: set = set()  # Guard against double-settlement

    def log(self, msg: str):
        if self.on_log:
            self.on_log(msg)

    def connect(self) -> bool:
        """Connect to Polymarket CLOB API."""
        try:
            self.client = ClobClient(
                "https://clob.polymarket.com",
                key=self.private_key,
                chain_id=137,
                signature_type=self.signature_type,
                funder=self.funder
            )
            creds = self.client.derive_api_key()
            self.client.set_api_creds(creds)
            self.connected = True
            self.log("Executor connected to CLOB API")
            return True
        except Exception as e:
            self.log(f"Executor connection failed: {e}")
            self.connected = False
            return False

    def refresh_balance(self) -> float:
        """Refresh USDC balance from Polymarket."""
        if not self.client:
            return 0.0
        try:
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            result = self.client.get_balance_allowance(params)
            self.balance = int(result.get("balance", 0)) / 1_000_000
            return self.balance
        except:
            return self.balance

    def new_session(self, session_id: str):
        """Reset state for a new session."""
        if session_id != self.current_session:
            self.current_session = session_id
            self.session_trades = {"CORE": 0, "RECOVERY": 0}
            # NOTE: degraded_count is NOT reset per session - execution issues persist
            # NOTE: kill_switch is NOT reset per session - only manually via reset_killswitch()
            self.log(f"[EXEC] New session: {session_id}")
            if self.kill_switch:
                self.log(f"[EXEC] KILLSWITCH still active: {self.kill_reason}")
            if self.degraded_count > 0:
                self.log(f"[EXEC] Degraded fills: {self.degraded_count}/{self.config.degraded_kill_count}")

    def reset_killswitch(self):
        """Manually reset killswitch (use with caution)."""
        if self.kill_switch:
            self.log(f"[KILLSWITCH] Manually reset (was: {self.kill_reason})")
        self.kill_switch = False
        self.kill_reason = ""
        self.consecutive_losses = 0
        self.degraded_count = 0  # Also reset degraded counter
        # Note: cumulative_pnl is NOT reset

    def can_trade(self, zone: str) -> tuple[bool, str]:
        """
        Check if we can trade in this zone.
        Returns (can_trade, reason).
        """
        # Kill switch active?
        if self.kill_switch:
            return False, f"KILL SWITCH: {self.kill_reason}"

        # Consecutive losses check
        if self.consecutive_losses >= self.config.max_consec_losses:
            self.kill_switch = True
            self.kill_reason = f"{self.consecutive_losses} consecutive losses"
            self.log(f"[KILLSWITCH] Activated: {self.kill_reason}")
            return False, f"KILL SWITCH: {self.kill_reason}"

        # PnL floor check (<=, not <, to kill exactly at floor)
        if self.cumulative_pnl <= self.config.pnl_floor_dollars:
            self.kill_switch = True
            self.kill_reason = f"PnL ${self.cumulative_pnl:.2f} below floor ${self.config.pnl_floor_dollars}"
            self.log(f"[KILLSWITCH] Activated: {self.kill_reason}")
            return False, f"KILL SWITCH: {self.kill_reason}"

        # Zone limit reached?
        zone_count = self.session_trades.get(zone, 0)
        if zone_count >= self.config.max_trades_per_zone:
            return False, f"Max trades reached for {zone} ({zone_count})"

        # Cooldown?
        if self.last_trade_time:
            elapsed = (datetime.now() - self.last_trade_time).total_seconds()
            if elapsed < self.config.cooldown_seconds:
                remaining = self.config.cooldown_seconds - elapsed
                return False, f"Cooldown ({remaining:.0f}s remaining)"

        # Balance check
        if self.balance < self.config.cash_per_trade:
            return False, f"Insufficient balance (${self.balance:.2f})"

        return True, "OK"

    def record_result(self, won: bool, pnl: float, trade_id: str = None):
        """
        Record trade result and check killswitch conditions.
        Call this after each trade settles.

        Args:
            won: True if trade won
            pnl: Realized PnL for this trade
            trade_id: Unique trade identifier to prevent double-counting
        """
        # Guard against duplicate settlements
        if trade_id:
            if trade_id in self.settled_trade_ids:
                self.log(f"[WARN] Duplicate settlement ignored: {trade_id}")
                return
            self.settled_trade_ids.add(trade_id)

        self.total_trades += 1
        self.cumulative_pnl += pnl

        if won:
            self.total_wins += 1
            self.consecutive_losses = 0  # Reset streak
        else:
            self.total_losses += 1
            self.consecutive_losses += 1

        # Log status
        self.log(f"[RESULT] {'WIN' if won else 'LOSS'} | PnL: ${pnl:+.2f} | "
                 f"Cumulative: ${self.cumulative_pnl:+.2f} | "
                 f"Consec Losses: {self.consecutive_losses}/{self.config.max_consec_losses}")

        # Check killswitch conditions
        if self.consecutive_losses >= self.config.max_consec_losses:
            self.kill_switch = True
            self.kill_reason = f"{self.consecutive_losses} consecutive losses"
            self.log(f"[KILLSWITCH] ACTIVATED: {self.kill_reason}")
            real_log_kill("consec_losses", str(self.consecutive_losses))

        if self.cumulative_pnl <= self.config.pnl_floor_dollars:
            self.kill_switch = True
            self.kill_reason = f"PnL ${self.cumulative_pnl:.2f} hit floor ${self.config.pnl_floor_dollars}"
            self.log(f"[KILLSWITCH] ACTIVATED: {self.kill_reason}")
            real_log_kill("pnl_floor", f"${self.cumulative_pnl:.2f}")

    def validate_signal(
        self,
        zone: str,
        direction: str,
        edge: float,
        ask_price: float
    ) -> tuple[bool, str]:
        """
        Validate if signal meets RULEV3+ criteria.
        """
        if zone not in ["CORE", "RECOVERY"]:
            return False, f"Invalid zone: {zone}"

        if edge < self.config.edge_threshold:
            return False, f"Edge {edge:.3f} < {self.config.edge_threshold}"

        if ask_price >= self.config.safety_cap:
            return False, f"Ask {ask_price:.3f} >= safety cap {self.config.safety_cap}"

        # Can we trade?
        can, reason = self.can_trade(zone)
        if not can:
            return False, reason

        return True, "OK"

    def _should_retry(
        self,
        zone: str,
        edge: float,
        ask_price: float,
        original_ask: float
    ) -> tuple[bool, str]:
        """
        Conditional retry - only if signal still valid and price didn't move against us.
        """
        # Still in valid zone?
        if zone not in ["CORE", "RECOVERY"]:
            return False, "No longer in trading zone"

        # Edge still valid?
        if edge < self.config.edge_threshold:
            return False, f"Edge dropped to {edge:.3f}"

        # Ask still under safety cap?
        if ask_price >= self.config.safety_cap:
            return False, f"Ask moved to {ask_price:.3f}"

        # Price moved against us?
        if ask_price > original_ask * 1.005:  # More than 0.5% worse
            return False, f"Price moved against us ({original_ask:.3f} -> {ask_price:.3f})"

        return True, "OK"

    async def execute_trade(
        self,
        token_id: str,
        direction: str,
        price: float,
        zone: str,
        edge: float,
        market_id: str = "",
        get_current_state: Optional[Callable] = None
    ) -> OrderResult:
        """
        Execute a trade with strict RULEV3+ policies.

        get_current_state: Callback to get current (zone, edge, ask) for retry validation
        """
        result = OrderResult(
            direction=direction,
            zone=zone,
            expected_price=price,
            size=self.config.cash_per_trade / price,
            submit_time=datetime.now()
        )

        if not self.client or not self.connected:
            result.status = OrderStatus.FAILED
            result.error = "Not connected"
            return result

        # Get timeout based on zone
        timeout_ms = (self.config.core_timeout_ms if zone == "CORE"
                     else self.config.recovery_timeout_ms)

        original_ask = price
        retries_done = 0

        while retries_done <= self.config.max_retries:
            try:
                # Step 1: Submit order
                self.log(f"[{zone}] Submit {direction} @ {price:.3f} x{result.size:.2f}")
                submit_start = time.time()

                order_args = OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=result.size,
                    side="BUY"
                )

                signed_order = self.client.create_order(order_args)
                response = self.client.post_order(signed_order, OrderType.GTC)

                submit_ms = int((time.time() - submit_start) * 1000)
                result.latency_ms = submit_ms

                if not response:
                    result.status = OrderStatus.FAILED
                    result.error = "No response from API"
                    self.log(f"[{zone}] Submit failed: no response")
                    break

                # Extract order ID
                order_id = None
                if isinstance(response, dict):
                    order_id = response.get("orderID") or response.get("id")
                if not order_id:
                    result.status = OrderStatus.FAILED
                    result.error = f"No order ID: {response}"
                    self.log(f"[{zone}] Submit failed: no order ID")
                    break

                result.order_id = str(order_id)
                result.status = OrderStatus.SUBMITTED
                self.log(f"[{zone}] Submitted: {result.order_id} ({submit_ms}ms)")

                # Log to real trade log (only if real mode enabled)
                real_log_submit(
                    order_id=result.order_id,
                    side=direction,
                    limit_price=price,
                    shares=result.size,
                    max_loss_estimate=self.config.cash_per_trade
                )

                # Step 2: Poll for fill (short timeout)
                fill_start = time.time()
                timeout_sec = timeout_ms / 1000

                while (time.time() - fill_start) < timeout_sec:
                    await asyncio.sleep(self.config.poll_interval_ms / 1000)

                    try:
                        order_info = self.client.get_order(result.order_id)

                        if order_info:
                            status_str = str(order_info.get("status", "")).upper()
                            size_matched = float(order_info.get("size_matched", 0) or 0)
                            fill_pct = size_matched / result.size if result.size > 0 else 0

                            # FILLED (guard against re-processing if callback fails)
                            if status_str == "MATCHED" or fill_pct >= 0.99:
                                if result.status == OrderStatus.FILLED:
                                    return result  # Already processed, exit
                                result.status = OrderStatus.FILLED
                                result.filled_size = size_matched
                                result.fill_price = float(order_info.get("price", price) or price)
                                result.fill_time = datetime.now()

                                # Calculate slippage
                                if result.fill_price > 0:
                                    slippage = (result.fill_price - original_ask) / original_ask
                                    result.slippage_bps = slippage * 10000

                                # Check if degraded
                                if result.slippage_bps > self.config.degraded_threshold_bps:
                                    result.degraded = True
                                    result.status = OrderStatus.DEGRADED
                                    self.degraded_count += 1
                                    self.log(f"[{zone}] DEGRADED fill: {result.slippage_bps:.0f}bps slippage")

                                    # Kill switch?
                                    if self.degraded_count >= self.config.degraded_kill_count:
                                        self.kill_switch = True
                                        self.kill_reason = f"{self.degraded_count} degraded fills"
                                        self.log(f"[{zone}] KILL SWITCH activated ({self.degraded_count} degraded fills)")
                                        real_log_kill("degraded_fills", str(self.degraded_count))
                                else:
                                    self.log(f"[{zone}] FILLED: {size_matched:.3f} @ {result.fill_price:.3f} "
                                            f"(slip: {result.slippage_bps:.0f}bps)")

                                # Detailed fill log
                                self.log(f"")
                                self.log(f"{'='*50}")
                                self.log(f"REAL TRADE FILLED")
                                self.log(f"{'='*50}")
                                self.log(f"ORDER_ID:  {result.order_id}")
                                self.log(f"STATUS:    {result.status.value}")
                                self.log(f"DIRECTION: {result.direction}")
                                self.log(f"ZONE:      {result.zone}")
                                self.log(f"EXPECTED:  ${result.expected_price:.4f}")
                                self.log(f"FILLED:    ${result.fill_price:.4f}")
                                self.log(f"SHARES:    {result.filled_size:.4f}")
                                self.log(f"SLIPPAGE:  {result.slippage_bps:.2f}bps")
                                self.log(f"LATENCY:   {result.latency_ms}ms")
                                self.log(f"DEGRADED:  {result.degraded}")
                                self.log(f"BALANCE:   ${self.balance:.2f}")
                                self.log(f"{'='*50}")
                                self.log(f"")

                                # Log to real trade log (only if real mode enabled)
                                real_log_filled(
                                    order_id=result.order_id,
                                    fill_price=result.fill_price,
                                    fill_time=result.fill_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if result.fill_time else "",
                                    slippage_bps=result.slippage_bps,
                                    degraded=result.degraded
                                )

                                # Update state
                                self.balance -= result.fill_price * result.filled_size
                                self.last_trade_time = datetime.now()
                                self.session_trades[zone] = self.session_trades.get(zone, 0) + 1

                                if self.on_order_update:
                                    self.on_order_update(result)
                                return result

                            # PARTIAL - check if should cancel rest
                            elif size_matched > 0:
                                remaining_pct = 1 - fill_pct
                                if remaining_pct < self.config.partial_min_remaining:
                                    # Small remainder, cancel it
                                    self.log(f"[{zone}] Partial {fill_pct*100:.0f}%, cancelling rest")
                                    try:
                                        self.client.cancel(result.order_id)
                                    except:
                                        pass
                                    result.status = OrderStatus.FILLED
                                    result.filled_size = size_matched
                                    result.fill_price = float(order_info.get("price", price) or price)

                                    self.balance -= result.fill_price * result.filled_size
                                    self.last_trade_time = datetime.now()
                                    self.session_trades[zone] = self.session_trades.get(zone, 0) + 1

                                    if self.on_order_update:
                                        self.on_order_update(result)
                                    return result

                            # CANCELLED
                            elif status_str == "CANCELLED":
                                result.status = OrderStatus.CANCELLED
                                result.error = "Cancelled by exchange"
                                break

                    except Exception as poll_err:
                        pass  # Continue polling

                # Timeout - cancel order
                if result.status == OrderStatus.SUBMITTED:
                    self.log(f"[{zone}] Timeout ({timeout_ms}ms), cancelling")
                    try:
                        self.client.cancel(result.order_id)
                    except:
                        pass
                    result.status = OrderStatus.CANCELLED
                    result.error = f"Fill timeout ({timeout_ms}ms)"

                # Should we retry?
                if result.status in [OrderStatus.CANCELLED, OrderStatus.FAILED]:
                    retries_done += 1
                    result.retries = retries_done

                    if retries_done <= self.config.max_retries and get_current_state:
                        # Get current market state
                        try:
                            current_zone, current_edge, current_ask = get_current_state()
                            can_retry, retry_reason = self._should_retry(
                                current_zone, current_edge, current_ask, original_ask
                            )
                            if can_retry:
                                self.log(f"[{zone}] Retry {retries_done}: signal still valid")
                                price = current_ask  # Use current price
                                await asyncio.sleep(self.config.retry_delay_ms / 1000)
                                continue
                            else:
                                self.log(f"[{zone}] No retry: {retry_reason}")
                        except:
                            self.log(f"[{zone}] No retry: couldn't get current state")

                break

            except Exception as e:
                result.status = OrderStatus.FAILED
                # Capture full error details
                error_type = type(e).__name__
                error_msg = str(e)
                result.error = f"{error_type}: {error_msg}"
                self.log(f"[{zone}] Execute error: {error_type}")
                self.log(f"[{zone}] Error detail: {error_msg[:200]}")
                # Check for common API errors
                if "allowance" in error_msg.lower():
                    self.log(f"[{zone}] HINT: Need to approve USDC spending on Polymarket")
                elif "balance" in error_msg.lower():
                    self.log(f"[{zone}] HINT: Insufficient USDC balance")
                elif "size" in error_msg.lower():
                    self.log(f"[{zone}] HINT: Order size issue (min size?)")
                break

        if self.on_order_update:
            self.on_order_update(result)

        return result

    async def cancel_all(self) -> bool:
        """Cancel all open orders."""
        if not self.client:
            return False
        try:
            self.client.cancel_all()
            self.log("Cancelled all orders")
            return True
        except Exception as e:
            self.log(f"Cancel all failed: {e}")
            return False


# ============================================================
# STANDALONE TEST
# ============================================================

async def test_executor():
    """Test the executor config."""
    from dotenv import load_dotenv
    from pathlib import Path

    load_dotenv(Path(__file__).parent.parent.parent / ".env")

    private_key = os.getenv("PM_PRIVATE_KEY", "")
    funder = os.getenv("PM_FUNDER_ADDRESS", "")
    sig_type = int(os.getenv("PM_SIGNATURE_TYPE", "2"))

    print("=" * 60)
    print("  RULEV3+ TRADE EXECUTOR - CONFIG CHECK")
    print("=" * 60)
    print()

    config = ExecutorConfig()

    print("[EXECUTION POLICY]")
    print(f"  CORE timeout:      {config.core_timeout_ms}ms")
    print(f"  RECOVERY timeout:  {config.recovery_timeout_ms}ms")
    print(f"  Poll interval:     {config.poll_interval_ms}ms")
    print(f"  Max retries:       {config.max_retries} (conditional)")
    print()

    print("[SAFETY LIMITS]")
    print(f"  Max slippage:      {config.max_slippage_bps}bps")
    print(f"  Degraded threshold:{config.degraded_threshold_bps}bps")
    print(f"  Kill after:        {config.degraded_kill_count} degraded fills")
    print(f"  Partial cancel:    <{config.partial_min_remaining*100:.0f}% remaining")
    print()

    print("[SESSION LIMITS]")
    print(f"  Max per zone:      {config.max_trades_per_zone}")
    print(f"  Cooldown:          {config.cooldown_seconds}s")
    print()

    print("[RULEV3+ THRESHOLDS]")
    print(f"  Edge threshold:    >= {config.edge_threshold}")
    print(f"  Safety cap:        < {config.safety_cap}")
    print(f"  Trade size:        ${config.cash_per_trade:.2f}")
    print()

    # Connect and check balance
    executor = TradeExecutor(
        private_key=private_key,
        funder=funder,
        signature_type=sig_type,
        config=config
    )
    executor.on_log = lambda msg: print(f"  {msg}")

    print("[CONNECTION TEST]")
    if executor.connect():
        balance = executor.refresh_balance()
        print(f"  Balance: ${balance:.2f}")

        # Test signal validation
        print()
        print("[SIGNAL VALIDATION TEST]")
        valid, reason = executor.validate_signal("CORE", "Down", 0.65, 0.65)
        print(f"  CORE edge=0.65 ask=0.65: {valid} ({reason})")

        valid, reason = executor.validate_signal("CORE", "Down", 0.60, 0.65)
        print(f"  CORE edge=0.60 ask=0.65: {valid} ({reason})")

        valid, reason = executor.validate_signal("CORE", "Down", 0.65, 0.75)
        print(f"  CORE edge=0.65 ask=0.75: {valid} ({reason})")

    print()
    print("=" * 60)
    print("  READY FOR LIVE TRADING")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_executor())
