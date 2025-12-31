"""
UP Token Trend Tracker - 5-minute Trend Context + Regime Detection
===================================================================
Tracks UP token mid-price trend (market-implied direction).
This is NOT actual BTC price - it's Polymarket's order book sentiment.

Features:
  - Trend tag: GREEN/RED/FLAT for Telegram context
  - Regime detection: STABLE/CHOPPY/NEUTRAL based on crossings count
  - Crossings = direction reversals in 5min window

Regime affects trading logic (RULEV3.2):
  - CHOPPY: +0.03 edge gate modifier
  - STABLE/NEUTRAL: no modifier

Env:
  BTC_TREND_TAG_ENABLED=1/0 (default 1)
"""

import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class PricePoint:
    """A single price observation."""
    timestamp: float  # Unix timestamp
    price: float      # UP token mid price


class BTCTrendTracker:
    """
    Tracks UP token price to derive 5-minute BTC trend context.

    UP token rising = market expects BTC up = GREEN
    UP token falling = market expects BTC down = RED
    """

    # Thresholds (in percent)
    GREEN_THRESHOLD = 0.05   # +0.05% = GREEN
    RED_THRESHOLD = -0.05    # -0.05% = RED

    # Time window
    WINDOW_SECONDS = 300  # 5 minutes
    MAX_POINTS = 600      # Max stored points (at 1/sec = 10 min history)

    # Debug logging
    DEBUG = False  # Set True to enable debug prints

    # Rate limiting - only record once per second to avoid buffer overflow
    MIN_RECORD_INTERVAL = 1.0  # seconds

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._buffer: deque = deque(maxlen=self.MAX_POINTS)
        self._record_count = 0
        self._last_record_time = 0.0  # For rate limiting
        if self.DEBUG:
            print(f"[BTC_TRACKER] Initialized enabled={enabled}")

    def record(self, up_mid: float):
        """
        Record a new UP token mid price.

        Args:
            up_mid: Current UP token mid price (0.0 to 1.0)

        Note: Records at most once per second to prevent buffer overflow
        when receiving frequent WebSocket updates. This ensures the buffer
        can hold at least 600 seconds (10 min) of data.
        """
        if not self.enabled:
            return

        if up_mid is None or up_mid <= 0:
            return

        # Rate limit: only record once per second
        now = time.time()
        if now - self._last_record_time < self.MIN_RECORD_INTERVAL:
            return
        self._last_record_time = now

        self._buffer.append(PricePoint(
            timestamp=now,
            price=up_mid
        ))
        self._record_count += 1

        # Debug: log buffer status every 100 records
        if self.DEBUG and self._record_count % 100 == 0:
            buf_len = len(self._buffer)
            oldest_age = 0
            if buf_len > 0:
                oldest_age = now - self._buffer[0].timestamp
            print(f"[BTC_TRACKER] Records={self._record_count} Buffer={buf_len} OldestAge={oldest_age:.0f}s")

    def get_trend(self) -> Tuple[str, float]:
        """
        Get current 5-minute trend.

        Returns:
            Tuple of (label, pct_change)
            label: "GREEN", "RED", "FLAT", or "N/A"
            pct_change: Percentage change over 5 minutes
        """
        if not self.enabled:
            return ("N/A", 0.0)

        if len(self._buffer) < 2:
            return ("N/A", 0.0)

        now = time.time()
        target_time = now - self.WINDOW_SECONDS

        # Find price closest to 5 minutes ago
        price_5m_ago = None
        for point in self._buffer:
            if point.timestamp <= target_time:
                price_5m_ago = point.price
            else:
                break

        # If no point from 5 minutes ago, check if we have enough history
        if price_5m_ago is None:
            oldest = self._buffer[0]
            age_seconds = now - oldest.timestamp
            if age_seconds < self.WINDOW_SECONDS:
                # Not enough history yet
                return ("N/A", 0.0)
            # Use oldest available
            price_5m_ago = oldest.price

        # Get current price
        current_price = self._buffer[-1].price

        # Compute percent change
        if price_5m_ago <= 0:
            return ("N/A", 0.0)

        pct_change = ((current_price - price_5m_ago) / price_5m_ago) * 100

        # Label
        if pct_change >= self.GREEN_THRESHOLD:
            label = "GREEN"
        elif pct_change <= self.RED_THRESHOLD:
            label = "RED"
        else:
            label = "FLAT"

        return (label, pct_change)

    def get_crossings(self) -> int:
        """
        Count significant direction reversals in the 5-minute window.

        A crossing = price moved meaningfully in one direction, then reversed.
        High crossings = choppy, indecisive market.

        Thresholds (from analysis):
          - crossings >= 6 → CHOPPY
          - crossings <= 2 → STABLE
          - else → NEUTRAL

        Returns:
            Number of direction reversals (typically 0-10)
        """
        if not self.enabled or len(self._buffer) < 10:
            return 0

        now = time.time()
        window_start = now - self.WINDOW_SECONDS

        # Get points in window
        window_points = [p for p in self._buffer if p.timestamp >= window_start]
        if len(window_points) < 10:
            return 0

        # Threshold for "significant" move (in absolute price terms)
        # UP token trades around 0.50, so 0.001 = 0.2% move
        MOVE_THRESHOLD = 0.001

        # Track direction and count reversals
        last_direction = None
        last_anchor = window_points[0].price
        crossings = 0

        for point in window_points[1:]:
            move = point.price - last_anchor

            if abs(move) >= MOVE_THRESHOLD:
                current_direction = "UP" if move > 0 else "DOWN"

                if last_direction is not None and current_direction != last_direction:
                    crossings += 1

                last_direction = current_direction
                last_anchor = point.price

        return crossings

    def get_regime(self) -> Tuple[str, int]:
        """
        Get current market regime based on crossings count.

        Regime definitions:
          - CHOPPY: crossings >= 6 (high oscillation, low conviction)
          - STABLE: crossings <= 2 (clear trend, high conviction)
          - NEUTRAL: 3-5 crossings (normal, no modifier)

        Returns:
            Tuple of (regime_label, crossings_count)
        """
        crossings = self.get_crossings()

        if crossings >= 6:
            return ("CHOPPY", crossings)
        elif crossings <= 2:
            return ("STABLE", crossings)
        else:
            return ("NEUTRAL", crossings)

    def format_tag(self) -> str:
        """
        Get formatted tag for Telegram.

        Returns:
            String like "(UP 5m: GREEN +0.18%)" or "(UP 5m: N/A)"
        """
        label, pct = self.get_trend()

        if label == "N/A":
            return "(UP 5m: N/A)"

        return f"(UP 5m: {label} {pct:+.2f}%)"

    def clear(self):
        """Clear the buffer (e.g., on session rollover)."""
        self._buffer.clear()


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

_tracker: Optional[BTCTrendTracker] = None


def init_btc_tracker(enabled: bool = True) -> BTCTrendTracker:
    """Initialize the BTC trend tracker singleton."""
    global _tracker
    _tracker = BTCTrendTracker(enabled=enabled)
    return _tracker


def get_btc_tracker() -> Optional[BTCTrendTracker]:
    """Get the BTC trend tracker singleton."""
    return _tracker


def btc_record(up_mid: float):
    """Record a price point (convenience function)."""
    if _tracker:
        _tracker.record(up_mid)


def btc_tag() -> str:
    """Get the formatted BTC tag (convenience function)."""
    if _tracker:
        return _tracker.format_tag()
    return "(UP 5m: N/A)"


def btc_debug_stats() -> str:
    """Get debug stats about the tracker (for diagnostics)."""
    if not _tracker:
        return "Tracker: None"
    buf_len = len(_tracker._buffer)
    oldest_age = 0
    if buf_len > 0:
        oldest_age = time.time() - _tracker._buffer[0].timestamp
    return f"Tracker: enabled={_tracker.enabled} records={_tracker._record_count} buffer={buf_len} oldest_age={oldest_age:.0f}s"


def is_btc_tracker_enabled() -> bool:
    """Check if BTC tracking is enabled from env."""
    return os.getenv("BTC_TREND_TAG_ENABLED", "1") == "1"


# =============================================================================
# REGIME DETECTION CONVENIENCE FUNCTIONS
# =============================================================================

def btc_regime() -> Tuple[str, int]:
    """
    Get current market regime (convenience function).

    Returns:
        Tuple of (regime_label, crossings_count)
        regime_label: "STABLE", "CHOPPY", or "NEUTRAL"
    """
    if _tracker:
        return _tracker.get_regime()
    return ("NEUTRAL", 0)


def btc_is_choppy() -> bool:
    """
    Check if current regime is CHOPPY (convenience function).

    Used by edge gate modifier: if True, add +0.03 to required edge.
    """
    if _tracker:
        regime, _ = _tracker.get_regime()
        return regime == "CHOPPY"
    return False


def btc_crossings() -> int:
    """Get current crossings count (convenience function)."""
    if _tracker:
        return _tracker.get_crossings()
    return 0
