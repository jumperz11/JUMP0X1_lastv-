#!/usr/bin/env python3
"""
Polymarket Connector for BTC 15-minute markets
===============================================
Connects to real Polymarket APIs for live price data.

Based on the Rust implementation in PK8_PH.
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, Callable, List
import aiohttp

# ============================================================
# CONFIGURATION
# ============================================================

GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
LIVE_DATA_WS_URL = "wss://ws-live-data.polymarket.com/"

# BTC 15-minute market config
BTC15_PREFIX = "btc-updown-15m"
SESSION_DURATION = 900  # 15 minutes in seconds


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class MarketEvent:
    slug: str
    condition_id: Optional[str] = None
    token_up: Optional[str] = None
    token_down: Optional[str] = None
    end_date: Optional[datetime] = None
    start_time: Optional[datetime] = None
    liquidity_clob: Optional[float] = None
    order_min_size: Optional[float] = None
    min_tick_size: Optional[float] = None


@dataclass
class BookSnapshot:
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    mid: Optional[float] = None
    depth_bid: float = 0.0
    depth_ask: float = 0.0
    last_trade_price: Optional[float] = None
    last_trade_size: Optional[float] = None
    last_update: Optional[datetime] = None


@dataclass
class SessionState:
    slug: str = ""
    session_start_ts: int = 0
    session_end_ts: int = 0
    tau: float = 899.0  # seconds until settlement
    elapsed: float = 0.0  # seconds since start
    zone: str = "WAITING"
    up: BookSnapshot = field(default_factory=BookSnapshot)
    down: BookSnapshot = field(default_factory=BookSnapshot)
    edge: float = 0.50
    edge_direction: str = ""
    connected: bool = False
    last_msg_at: Optional[datetime] = None
    rtt_ms: Optional[int] = None
    # Token IDs for trading
    token_up: str = ""
    token_down: str = ""


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def derive_current_slug(prefix: str = BTC15_PREFIX) -> str:
    """Derive current session slug from current time.

    The slug timestamp is the SESSION START time, aligned to 15-min boundary.
    """
    now_ts = int(time.time())
    aligned = now_ts - (now_ts % SESSION_DURATION)
    return f"{prefix}-{aligned}"


def derive_next_slug(prefix: str = BTC15_PREFIX) -> str:
    """Derive next session slug."""
    now_ts = int(time.time())
    aligned = now_ts - (now_ts % SESSION_DURATION)
    return f"{prefix}-{aligned + SESSION_DURATION}"


def parse_slug_timestamp(slug: str) -> int:
    """Extract timestamp from slug like btc-updown-15m-1766430000"""
    parts = slug.split("-")
    if len(parts) >= 4:
        return int(parts[-1])
    return 0


def get_zone(elapsed: float) -> str:
    """Determine zone from elapsed seconds since session start.

    V2 Window Extension: CORE expanded from 3:00-3:29 to 2:30-3:45
    (+36% trades, same WR, +39% PnL in backtest)
    """
    if elapsed < 150:        # 0:00-2:29
        return "EARLY"
    elif elapsed <= 225:     # 2:30-3:45 (V2 extended CORE)
        return "CORE"
    elif elapsed < 300:      # 3:46-4:59
        return "DEAD"
    elif elapsed <= 359:     # 5:00-5:59
        return "RECOVERY"
    else:                    # 6:00+
        return "LATE"


def format_elapsed(elapsed: float) -> str:
    """Format elapsed seconds as M:SS"""
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)
    return f"{mins}:{secs:02d}"


# ============================================================
# GAMMA API CLIENT
# ============================================================

class GammaClient:
    """HTTP client for Polymarket Gamma API."""

    def __init__(self, base_url: str = GAMMA_API_URL):
        self.base_url = base_url.rstrip("/")

    async def get_event_by_slug(self, slug: str) -> Optional[MarketEvent]:
        """Fetch market event metadata by slug."""
        url = f"{self.base_url}/events/slug/{slug}"

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, timeout=10) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return self._parse_event(data)
            except Exception as e:
                print(f"Gamma API error: {e}")
                return None

    def _parse_event(self, data: dict) -> MarketEvent:
        """Parse Gamma API response into MarketEvent."""
        slug = data.get("slug", "")

        # Parse end_date
        end_date = None
        if data.get("endDate"):
            try:
                end_date = datetime.fromisoformat(data["endDate"].replace("Z", "+00:00"))
            except:
                pass

        # Get market data (first market)
        markets = data.get("markets", [])
        market = markets[0] if markets else {}

        condition_id = market.get("conditionId")
        liquidity_clob = None
        if market.get("liquidityClob"):
            try:
                liquidity_clob = float(market["liquidityClob"])
            except:
                pass

        order_min_size = None
        if market.get("orderMinSize"):
            try:
                order_min_size = float(market["orderMinSize"])
            except:
                pass

        min_tick_size = None
        if market.get("orderPriceMinTickSize"):
            try:
                min_tick_size = float(market["orderPriceMinTickSize"])
            except:
                pass

        # Parse outcomes and token IDs
        token_up = None
        token_down = None
        outcomes = market.get("outcomes", [])
        token_ids = market.get("clobTokenIds", [])

        # Handle JSON string format
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except:
                outcomes = []
        if isinstance(token_ids, str):
            try:
                token_ids = json.loads(token_ids)
            except:
                token_ids = []

        for i, outcome in enumerate(outcomes):
            if i < len(token_ids):
                if outcome == "Up":
                    token_up = token_ids[i]
                elif outcome == "Down":
                    token_down = token_ids[i]

        return MarketEvent(
            slug=slug,
            condition_id=condition_id,
            token_up=token_up,
            token_down=token_down,
            end_date=end_date,
            liquidity_clob=liquidity_clob,
            order_min_size=order_min_size,
            min_tick_size=min_tick_size,
        )


# ============================================================
# CLOB WEBSOCKET CLIENT
# ============================================================

class ClobWebSocket:
    """WebSocket client for Polymarket CLOB order book data."""

    def __init__(self, url: str = CLOB_WS_URL):
        self.url = url
        self.ws = None
        self.running = False
        self.state = SessionState()
        self.token_to_outcome: Dict[str, str] = {}
        self.on_update: Optional[Callable[[SessionState], None]] = None
        self.on_log: Optional[Callable[[str], None]] = None

    def log(self, msg: str):
        if self.on_log:
            self.on_log(msg)

    async def connect(self, market: MarketEvent):
        """Connect and subscribe to market data."""
        self.state.slug = market.slug
        self.state.session_start_ts = parse_slug_timestamp(market.slug)
        self.state.session_end_ts = self.state.session_start_ts + SESSION_DURATION
        # Store token IDs for trading
        self.state.token_up = market.token_up or ""
        self.state.token_down = market.token_down or ""

        # Build token mapping
        self.token_to_outcome = {}
        token_ids = []
        if market.token_up:
            self.token_to_outcome[market.token_up] = "Up"
            token_ids.append(market.token_up)
        if market.token_down:
            self.token_to_outcome[market.token_down] = "Down"
            token_ids.append(market.token_down)

        if not token_ids:
            self.log("No token IDs available")
            return

        self.running = True

        async with aiohttp.ClientSession() as session:
            try:
                async with session.ws_connect(self.url, heartbeat=30) as ws:
                    self.ws = ws
                    self.state.connected = True
                    self.log(f"Connected to CLOB WS: {market.slug}")

                    # Subscribe to market
                    subscribe_msg = json.dumps({
                        "assets_ids": token_ids,
                        "type": "market"
                    })
                    await ws.send_str(subscribe_msg)
                    self.log(f"Subscribed to tokens: {len(token_ids)}")

                    # Process messages
                    ping_task = asyncio.create_task(self._ping_loop(ws))

                    try:
                        async for msg in ws:
                            if not self.running:
                                break

                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._handle_message(msg.data)
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                self.log(f"WS error: {ws.exception()}")
                                break
                            elif msg.type == aiohttp.WSMsgType.CLOSED:
                                break
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass

            except Exception as e:
                self.log(f"WS connection error: {e}")
            finally:
                self.state.connected = False
                self.ws = None

    async def _ping_loop(self, ws):
        """Send periodic pings."""
        while self.running:
            await asyncio.sleep(10)
            if self.running and not ws.closed:
                try:
                    t0 = time.time()
                    await ws.send_str("PING")
                except:
                    break

    async def _handle_message(self, data: str):
        """Handle incoming WebSocket message."""
        if data == "PONG":
            return

        try:
            msg = json.loads(data)
        except:
            return

        # Handle array of messages
        items = msg if isinstance(msg, list) else [msg]

        for item in items:
            event_type = item.get("event_type")

            if event_type == "book":
                await self._handle_book(item)
            elif event_type == "price_change":
                await self._handle_price_change(item)
            elif event_type == "last_trade_price":
                await self._handle_last_trade(item)

        # Update timing
        self._update_timing()

        # Notify listener
        if self.on_update:
            self.on_update(self.state)

    async def _handle_book(self, msg: dict):
        """Handle full book snapshot."""
        asset_id = msg.get("asset_id")
        outcome = self.token_to_outcome.get(asset_id)
        if not outcome:
            return

        book = self.state.up if outcome == "Up" else self.state.down

        bids = msg.get("bids", [])
        asks = msg.get("asks", [])

        # Find best bid/ask
        best_bid = None
        best_ask = None
        depth_bid = 0.0
        depth_ask = 0.0

        for level in bids:
            try:
                price = float(level.get("price", 0))
                size = float(level.get("size", 0))
                if best_bid is None or price > best_bid:
                    best_bid = price
                depth_bid += size
            except:
                pass

        for level in asks:
            try:
                price = float(level.get("price", 0))
                size = float(level.get("size", 0))
                if best_ask is None or price < best_ask:
                    best_ask = price
                depth_ask += size
            except:
                pass

        book.best_bid = best_bid
        book.best_ask = best_ask
        book.depth_bid = depth_bid
        book.depth_ask = depth_ask
        if best_bid and best_ask:
            book.mid = (best_bid + best_ask) / 2
        book.last_update = datetime.now(timezone.utc)

        self.state.last_msg_at = datetime.now(timezone.utc)
        self._update_edge()

    async def _handle_price_change(self, msg: dict):
        """Handle price change delta."""
        changes = msg.get("price_changes", [msg])

        for change in changes:
            asset_id = change.get("asset_id")
            outcome = self.token_to_outcome.get(asset_id)
            if not outcome:
                continue

            book = self.state.up if outcome == "Up" else self.state.down

            # Update best bid/ask if provided
            if change.get("best_bid"):
                try:
                    book.best_bid = float(change["best_bid"])
                except:
                    pass
            if change.get("best_ask"):
                try:
                    book.best_ask = float(change["best_ask"])
                except:
                    pass

            if book.best_bid and book.best_ask:
                book.mid = (book.best_bid + book.best_ask) / 2

            book.last_update = datetime.now(timezone.utc)

        self.state.last_msg_at = datetime.now(timezone.utc)
        self._update_edge()

    async def _handle_last_trade(self, msg: dict):
        """Handle last trade price update."""
        asset_id = msg.get("asset_id")
        outcome = self.token_to_outcome.get(asset_id)
        if not outcome:
            return

        book = self.state.up if outcome == "Up" else self.state.down

        try:
            book.last_trade_price = float(msg.get("price", 0))
        except:
            pass

        if msg.get("size"):
            try:
                book.last_trade_size = float(msg["size"])
            except:
                pass

        self.state.last_msg_at = datetime.now(timezone.utc)

    def _update_timing(self):
        """Update tau and zone based on current time."""
        now_ts = time.time()
        end_ts = self.state.session_end_ts

        self.state.tau = max(0, end_ts - now_ts)
        self.state.elapsed = SESSION_DURATION - self.state.tau
        self.state.zone = get_zone(self.state.elapsed)

    def _update_edge(self):
        """Update edge calculation.

        Edge = max(up_mid, down_mid) where mid = (best_bid + best_ask) / 2
        This represents the market's probability estimate for the winning side.
        """
        # Calculate mid prices
        up_bid = self.state.up.best_bid
        up_ask = self.state.up.best_ask
        down_bid = self.state.down.best_bid
        down_ask = self.state.down.best_ask

        up_mid = (up_bid + up_ask) / 2 if up_bid and up_ask else None
        down_mid = (down_bid + down_ask) / 2 if down_bid and down_ask else None

        # Update mid in book snapshots
        if up_mid:
            self.state.up.mid = up_mid
        if down_mid:
            self.state.down.mid = down_mid

        # Edge = probability of winning side
        if up_mid and down_mid:
            if up_mid >= down_mid:
                self.state.edge = up_mid
                self.state.edge_direction = "Up"
            else:
                self.state.edge = down_mid
                self.state.edge_direction = "Down"
        elif up_mid:
            self.state.edge = up_mid
            self.state.edge_direction = "Up"
        elif down_mid:
            self.state.edge = down_mid
            self.state.edge_direction = "Down"

    def stop(self):
        """Stop the WebSocket connection."""
        self.running = False


# ============================================================
# SESSION MANAGER
# ============================================================

class SessionManager:
    """Manages session rollover and WebSocket lifecycle."""

    def __init__(self):
        self.gamma = GammaClient()
        self.ws: Optional[ClobWebSocket] = None
        self.current_market: Optional[MarketEvent] = None
        self.running = False
        self.on_update: Optional[Callable[[SessionState], None]] = None
        self.on_log: Optional[Callable[[str], None]] = None

    def log(self, msg: str):
        if self.on_log:
            self.on_log(msg)

    async def start(self):
        """Start session management loop."""
        self.running = True

        while self.running:
            try:
                # Get current session
                slug = derive_current_slug()
                self.log(f"Fetching market: {slug}")

                market = await self.gamma.get_event_by_slug(slug)
                if not market:
                    self.log(f"Market not found: {slug}")
                    await asyncio.sleep(5)
                    continue

                if not market.token_up or not market.token_down:
                    self.log(f"No tokens for: {slug}")
                    await asyncio.sleep(3)
                    continue

                self.current_market = market
                self.log(f"Market loaded: {slug}")
                self.log(f"  Token Up: {market.token_up[:20]}...")
                self.log(f"  Token Down: {market.token_down[:20]}...")

                # Connect WebSocket
                self.ws = ClobWebSocket()
                self.ws.on_update = self.on_update
                self.ws.on_log = self.on_log

                # Run until session ends or rollover needed
                ws_task = asyncio.create_task(self.ws.connect(market))
                rollover_task = asyncio.create_task(self._wait_for_rollover())

                done, pending = await asyncio.wait(
                    [ws_task, rollover_task],
                    return_when=asyncio.FIRST_COMPLETED
                )

                # Cancel pending tasks
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                if self.ws:
                    self.ws.stop()

                # Check if we need to roll over
                current_slug = derive_current_slug()
                if current_slug != slug:
                    self.log(f"Rolling over: {slug} -> {current_slug}")

            except Exception as e:
                self.log(f"Session manager error: {e}")
                await asyncio.sleep(5)

    async def _wait_for_rollover(self):
        """Wait until current session ends."""
        while self.running:
            now_ts = int(time.time())
            aligned = now_ts - (now_ts % SESSION_DURATION)
            next_session = aligned + SESSION_DURATION
            wait_time = next_session - now_ts + 1  # +1 second buffer

            await asyncio.sleep(min(wait_time, 30))

            # Check if slug changed
            current_slug = derive_current_slug()
            if self.current_market and current_slug != self.current_market.slug:
                return

    def stop(self):
        """Stop session manager."""
        self.running = False
        if self.ws:
            self.ws.stop()


# ============================================================
# SIMPLE TEST
# ============================================================

async def test_connection():
    """Test the Polymarket connection."""
    print("Testing Polymarket connection...")
    print()

    # Test Gamma API
    gamma = GammaClient()
    slug = derive_current_slug()
    print(f"Current slug: {slug}")

    market = await gamma.get_event_by_slug(slug)
    if market:
        print(f"Market found:")
        print(f"  Slug: {market.slug}")
        print(f"  Condition ID: {market.condition_id}")
        print(f"  Token Up: {market.token_up}")
        print(f"  Token Down: {market.token_down}")
        print(f"  End Date: {market.end_date}")
    else:
        print("Market not found!")
        return

    print()
    print("Testing WebSocket (10 seconds)...")

    ws = ClobWebSocket()

    def on_update(state: SessionState):
        up_bid = state.up.best_bid or 0
        up_ask = state.up.best_ask or 0
        down_bid = state.down.best_bid or 0
        down_ask = state.down.best_ask or 0
        print(f"\r  UP: {up_bid:.2f}/{up_ask:.2f}  DOWN: {down_bid:.2f}/{down_ask:.2f}  "
              f"Zone: {state.zone}  Tau: {state.tau:.0f}s  Edge: {state.edge:.2f} {state.edge_direction}",
              end="", flush=True)

    def on_log(msg: str):
        print(f"\n  [LOG] {msg}")

    ws.on_update = on_update
    ws.on_log = on_log

    # Run for 10 seconds
    task = asyncio.create_task(ws.connect(market))
    await asyncio.sleep(10)
    ws.stop()

    try:
        await asyncio.wait_for(task, timeout=2)
    except asyncio.TimeoutError:
        pass

    print()
    print()
    print("Test complete!")


if __name__ == "__main__":
    asyncio.run(test_connection())
