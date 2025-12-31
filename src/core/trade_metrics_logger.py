"""
Trade Metrics Logger - Observational Only
==========================================
Tracks pattern metrics for post-session analysis.
Does NOT influence trading decisions.

File structure:
  logs/real/trades_20251226_001239.log       <- existing
  logs/real/metrics/metrics_20251226_001239.jsonl  <- metrics

Linking:
  - Same timestamp = same run
  - trade_id in JSON matches [TRADE N] in log
"""

import json
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict


@dataclass
class TradeMetrics:
    """Metrics for a single trade."""
    trade_id: int = 0
    session_id: str = ""
    entry_time: str = ""
    mode: str = ""  # "real" or "paper"

    # Entry conditions
    direction: str = ""
    entry_price: float = 0.0
    entry_edge: float = 0.0
    entry_elapsed: float = 0.0
    hour_of_day: int = 0

    # Settlement
    winner: str = ""
    result: str = ""
    pnl: float = 0.0

    # === PATTERN METRICS ===
    entry_crossings: int = 0
    time_in_favor_pct: float = 0.0
    peak_favorable_pct: float = 0.0
    max_adverse_pct: float = 0.0

    # Context
    direction_flipped: bool = False
    prev_winner: str = ""
    consecutive_wins: int = 0
    consecutive_losses: int = 0

    # Classification (observational only)
    reason: str = ""


class TradeMetricsLogger:
    """
    Observational metrics logger.
    Writes to: logs/real/metrics/metrics_TIMESTAMP.jsonl
    """

    def __init__(self, log_dir: Path, run_timestamp: str):
        """
        Args:
            log_dir: Base log directory (e.g., logs/real)
            run_timestamp: Timestamp string (e.g., 20251226_001239)
        """
        self.metrics_dir = log_dir / 'metrics'
        self.metrics_dir.mkdir(parents=True, exist_ok=True)

        self.run_timestamp = run_timestamp
        self.metrics_file = self.metrics_dir / f'metrics_{run_timestamp}.jsonl'

        # Active trades: trade_id -> (entry_edge, prices[])
        self.active: Dict[int, dict] = {}

        # Context for direction_flipped
        self.last_winner: str = ""
        self.consec_wins: int = 0
        self.consec_losses: int = 0

    def get_metrics_path(self) -> str:
        """Return relative path for header pointer."""
        return f"logs/real/metrics/metrics_{self.run_timestamp}.jsonl"

    def on_entry(
        self,
        trade_id: int,
        session_id: str,
        direction: str,
        entry_price: float,
        edge: float,
        elapsed: float,
        mode: str = "paper"
    ):
        """Call when trade is placed."""

        self.active[trade_id] = {
            'trade_id': trade_id,
            'session_id': session_id,
            'entry_time': datetime.now().isoformat(),
            'mode': mode,
            'direction': direction,
            'entry_price': entry_price,
            'entry_edge': edge,
            'entry_elapsed': elapsed,
            'hour_of_day': datetime.now().hour,
            'prices': [edge],  # Start with entry edge
            'prev_winner': self.last_winner,
            'consec_wins': self.consec_wins,
            'consec_losses': self.consec_losses,
        }

    def on_tick(self, trade_id: int, current_edge: float):
        """Call on price updates (sample every few ticks to save space)."""

        if trade_id in self.active:
            self.active[trade_id]['prices'].append(current_edge)

    def _classify_reason(
        self,
        result: str,
        crossings: int,
        time_in_favor: float,
        peak_favorable: float,
        direction_flipped: bool
    ) -> str:
        """
        Derive reason from metrics. Observational classification only.

        WIN reasons:
          - clean conviction: low crossings, high time in favor
          - reversal held: direction flipped but still won
          - strong follow-through: high peak favorable

        LOSS reasons:
          - whipsaw: high crossings (choppy market)
          - late flip: was ahead then reversed
          - trend built against: never really in favor
          - weak follow-through: some favorable move but didn't hold
        """
        if result == "WIN":
            if direction_flipped:
                return "reversal held"
            if crossings <= 5 and time_in_favor >= 70:
                return "clean conviction"
            if peak_favorable >= 25:
                return "strong follow-through"
            return "clean conviction"
        else:
            if crossings >= 8:
                return "whipsaw"
            if time_in_favor >= 55 and peak_favorable >= 10:
                return "late flip"  # dominated but lost anyway
            if time_in_favor < 35 and peak_favorable < 8:
                return "trend built against"
            return "weak follow-through"

    def on_settlement(self, trade_id: int, winner: str, pnl: float) -> str:
        """Call when trade settles. Computes metrics and writes to file.

        Returns:
            reason: Classification string (e.g., "whipsaw", "late flip")
        """

        if trade_id not in self.active:
            return ""

        data = self.active[trade_id]
        direction = data['direction']
        entry_edge = data['entry_edge']
        prices = data['prices']

        # Determine result
        result = "WIN" if direction == winner else "LOSS"

        # Direction flip
        direction_flipped = (self.last_winner != "" and self.last_winner != winner)

        # === COMPUTE PATTERN METRICS ===

        # entry_crossings
        crossings = 0
        if len(prices) > 1:
            above = prices[0] >= entry_edge
            for p in prices[1:]:
                curr_above = p >= entry_edge
                if curr_above != above:
                    crossings += 1
                    above = curr_above

        # time_in_favor_pct
        time_favor = sum(1 for p in prices if p >= entry_edge)
        time_in_favor_pct = (time_favor / len(prices) * 100) if prices else 0

        # peak_favorable_pct
        max_p = max(prices) if prices else entry_edge
        peak_favorable_pct = max(0, (max_p - entry_edge) / entry_edge * 100) if entry_edge > 0 else 0

        # max_adverse_pct
        min_p = min(prices) if prices else entry_edge
        max_adverse_pct = max(0, (entry_edge - min_p) / entry_edge * 100) if entry_edge > 0 else 0

        # Classify reason (observational only)
        reason = self._classify_reason(
            result=result,
            crossings=crossings,
            time_in_favor=time_in_favor_pct,
            peak_favorable=peak_favorable_pct,
            direction_flipped=direction_flipped
        )

        # Build metrics object
        metrics = TradeMetrics(
            trade_id=trade_id,
            session_id=data['session_id'],
            entry_time=data['entry_time'],
            mode=data.get('mode', 'paper'),
            direction=direction,
            entry_price=data['entry_price'],
            entry_edge=entry_edge,
            entry_elapsed=data['entry_elapsed'],
            hour_of_day=data['hour_of_day'],
            winner=winner,
            result=result,
            pnl=pnl,
            entry_crossings=crossings,
            time_in_favor_pct=round(time_in_favor_pct, 1),
            peak_favorable_pct=round(peak_favorable_pct, 1),
            max_adverse_pct=round(max_adverse_pct, 1),
            direction_flipped=direction_flipped,
            prev_winner=data['prev_winner'],
            consecutive_wins=data['consec_wins'],
            consecutive_losses=data['consec_losses'],
            reason=reason,
        )

        # Write to file
        with open(self.metrics_file, 'a') as f:
            f.write(json.dumps(asdict(metrics)) + '\n')

        # Update context
        self.last_winner = winner
        if result == "WIN":
            self.consec_wins += 1
            self.consec_losses = 0
        else:
            self.consec_losses += 1
            self.consec_wins = 0

        # Cleanup
        del self.active[trade_id]

        return reason


# ============================================================
# GLOBAL INSTANCE
# ============================================================

_logger: Optional[TradeMetricsLogger] = None


def init_metrics_logger(log_dir: Path, run_timestamp: str) -> TradeMetricsLogger:
    """Initialize metrics logger with same timestamp as trades log."""
    global _logger
    _logger = TradeMetricsLogger(log_dir, run_timestamp)
    return _logger


def get_metrics_logger() -> Optional[TradeMetricsLogger]:
    """Get the global metrics logger."""
    return _logger


# ============================================================
# CLI ANALYSIS
# ============================================================

def analyze(filepath: str):
    """Analyze metrics file."""

    path = Path(filepath)
    if not path.exists():
        print(f"Not found: {filepath}")
        return

    trades = []
    with open(path) as f:
        for line in f:
            if line.strip():
                trades.append(json.loads(line))

    if not trades:
        print("No trades")
        return

    wins = [t for t in trades if t['result'] == 'WIN']
    losses = [t for t in trades if t['result'] == 'LOSS']

    def avg(lst, key):
        return sum(t.get(key, 0) for t in lst) / len(lst) if lst else 0

    print("=" * 60)
    print(f"  METRICS: {path.name}")
    print("=" * 60)
    print(f"  Trades: {len(trades)} | Wins: {len(wins)} | Losses: {len(losses)}")
    print(f"  Win Rate: {len(wins)/len(trades)*100:.1f}%")
    print()
    print(f"  {'Metric':<22} {'Wins':>10} {'Losses':>10}")
    print(f"  {'-'*45}")
    print(f"  {'Avg Crossings':<22} {avg(wins,'entry_crossings'):>10.1f} {avg(losses,'entry_crossings'):>10.1f}")
    print(f"  {'Avg Time in Favor':<22} {avg(wins,'time_in_favor_pct'):>9.1f}% {avg(losses,'time_in_favor_pct'):>9.1f}%")
    print(f"  {'Avg Peak Favorable':<22} {avg(wins,'peak_favorable_pct'):>9.1f}% {avg(losses,'peak_favorable_pct'):>9.1f}%")
    print(f"  {'Avg Max Adverse':<22} {avg(wins,'max_adverse_pct'):>9.1f}% {avg(losses,'max_adverse_pct'):>9.1f}%")
    print()

    # Losses by hour
    from collections import Counter
    loss_hours = Counter(t.get('hour_of_day', 0) for t in losses)
    if loss_hours:
        print("  Losses by Hour:")
        for h in sorted(loss_hours):
            print(f"    {h:02d}:00  {'#' * loss_hours[h]}")

    print("=" * 60)


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        analyze(sys.argv[1])
    else:
        print("Usage: python trade_metrics_logger.py <metrics_file.jsonl>")
