#!/usr/bin/env python3
"""
Production Smoke Test for Trade Metrics Logger
- Paper smoke test (2 trades)
- Real smoke test (1 trade)
- Integrity assertions
"""

import json
import sys
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.core.trade_metrics_logger import TradeMetricsLogger, TradeMetrics

ALLOWED_REASONS = {
    "clean conviction",
    "reversal held",
    "strong follow-through",
    "whipsaw",
    "late flip",
    "trend built against",
    "weak follow-through"
}

def run_smoke_test():
    print("=" * 70)
    print("  PRODUCTION SMOKE TEST: Trade Metrics Logger")
    print("=" * 70)
    print()

    # Create temp directory for test
    test_dir = Path(tempfile.mkdtemp(prefix="metrics_smoke_"))

    try:
        all_passed = True

        # ============================================================
        # PAPER SMOKE TEST (2 trades)
        # ============================================================
        print("[TEST 1] Paper Smoke Test (2 trades)")
        print("-" * 50)

        # Simulate timestamp like real system would generate
        run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        paper_log_dir = test_dir / "paper"

        # Create metrics logger (same as ui_dashboard_live.py does)
        logger = TradeMetricsLogger(paper_log_dir, run_timestamp)

        # Check 1: Metrics file path has same timestamp
        expected_metrics_path = f"logs/real/metrics/metrics_{run_timestamp}.jsonl"
        actual_metrics_path = logger.get_metrics_path()

        # The path format check (timestamp matches)
        ts_in_path = run_timestamp in actual_metrics_path
        print(f"  [{'PASS' if ts_in_path else 'FAIL'}] Timestamp in metrics path: {actual_metrics_path}")
        all_passed &= ts_in_path

        # Simulate 2 paper trades
        # Trade 1: Clean win
        logger.on_entry(
            trade_id=1,
            session_id="btc-updown-15m-test-001",
            direction="UP",
            entry_price=0.65,
            edge=0.66,
            elapsed=180.0,
            mode="paper"
        )
        # Simulate favorable price path
        for edge in [0.67, 0.68, 0.70, 0.72, 0.75, 0.80, 0.85, 0.90]:
            logger.on_tick(1, edge)
        logger.on_settlement(1, winner="UP", pnl=1.85)

        # Trade 2: Whipsaw loss
        logger.on_entry(
            trade_id=2,
            session_id="btc-updown-15m-test-002",
            direction="DOWN",
            entry_price=0.64,
            edge=0.65,
            elapsed=200.0,
            mode="paper"
        )
        # Simulate choppy price path (many crossings)
        for edge in [0.66, 0.63, 0.67, 0.62, 0.68, 0.61, 0.69, 0.60, 0.70, 0.58]:
            logger.on_tick(2, edge)
        logger.on_settlement(2, winner="UP", pnl=-4.00)

        # Read metrics file
        metrics_file = logger.metrics_file
        if not metrics_file.exists():
            print(f"  [FAIL] Metrics file not created: {metrics_file}")
            all_passed = False
        else:
            print(f"  [PASS] Metrics file created: {metrics_file.name}")

            with open(metrics_file) as f:
                lines = [l.strip() for l in f if l.strip()]

            # Check 2: Exactly 2 lines
            if len(lines) == 2:
                print(f"  [PASS] Exactly 2 JSON lines appended")
            else:
                print(f"  [FAIL] Expected 2 lines, got {len(lines)}")
                all_passed = False

            # Parse and validate
            trades = [json.loads(l) for l in lines]

            # Check 3: trade_id matches (1 and 2)
            ids = [t['trade_id'] for t in trades]
            if ids == [1, 2]:
                print(f"  [PASS] trade_id matches: {ids}")
            else:
                print(f"  [FAIL] trade_id mismatch: expected [1, 2], got {ids}")
                all_passed = False

            # Check 4: mode="paper" on both
            modes = [t['mode'] for t in trades]
            if all(m == "paper" for m in modes):
                print(f"  [PASS] mode='paper' on both trades")
            else:
                print(f"  [FAIL] mode mismatch: {modes}")
                all_passed = False

            # Check 5: reason present on both
            reasons = [t.get('reason', '') for t in trades]
            if all(r for r in reasons):
                print(f"  [PASS] reason present on both: {reasons}")
            else:
                print(f"  [FAIL] reason missing: {reasons}")
                all_passed = False

        print()

        # ============================================================
        # REAL SMOKE TEST (1 trade)
        # ============================================================
        print("[TEST 2] Real Smoke Test (1 trade)")
        print("-" * 50)

        real_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S") + "_real"
        real_log_dir = test_dir / "real"

        real_logger = TradeMetricsLogger(real_log_dir, real_timestamp)

        # Simulate 1 real trade
        real_logger.on_entry(
            trade_id=1,
            session_id="btc-updown-15m-real-001",
            direction="UP",
            entry_price=0.68,
            edge=0.69,
            elapsed=160.0,
            mode="real"  # <-- REAL mode
        )
        for edge in [0.70, 0.71, 0.72, 0.75, 0.80]:
            real_logger.on_tick(1, edge)
        real_logger.on_settlement(1, winner="UP", pnl=1.92)

        # Read and validate
        real_metrics_file = real_logger.metrics_file
        if not real_metrics_file.exists():
            print(f"  [FAIL] Real metrics file not created")
            all_passed = False
        else:
            with open(real_metrics_file) as f:
                real_lines = [l.strip() for l in f if l.strip()]

            # Check: Exactly 1 line
            if len(real_lines) == 1:
                print(f"  [PASS] Exactly 1 JSON line appended")
            else:
                print(f"  [FAIL] Expected 1 line, got {len(real_lines)}")
                all_passed = False

            real_trade = json.loads(real_lines[0])

            # Check: mode="real"
            if real_trade['mode'] == "real":
                print(f"  [PASS] mode='real'")
            else:
                print(f"  [FAIL] mode mismatch: {real_trade['mode']}")
                all_passed = False

        print()

        # ============================================================
        # INTEGRITY ASSERTIONS
        # ============================================================
        print("[TEST 3] Integrity Assertions")
        print("-" * 50)

        # Combine all trades for integrity checks
        all_trades = trades + [real_trade]

        # Check: time_in_favor_pct in [0, 100]
        tif_valid = all(0 <= t['time_in_favor_pct'] <= 100 for t in all_trades)
        tif_values = [t['time_in_favor_pct'] for t in all_trades]
        if tif_valid:
            print(f"  [PASS] time_in_favor_pct in [0,100]: {tif_values}")
        else:
            print(f"  [FAIL] time_in_favor_pct out of range: {tif_values}")
            all_passed = False

        # Check: entry_crossings >= 0
        crossings_valid = all(t['entry_crossings'] >= 0 for t in all_trades)
        crossings_values = [t['entry_crossings'] for t in all_trades]
        if crossings_valid:
            print(f"  [PASS] entry_crossings >= 0: {crossings_values}")
        else:
            print(f"  [FAIL] entry_crossings negative: {crossings_values}")
            all_passed = False

        # Check: reason is one of allowed labels
        reasons_valid = all(t['reason'] in ALLOWED_REASONS for t in all_trades)
        reason_values = [t['reason'] for t in all_trades]
        if reasons_valid:
            print(f"  [PASS] reason is valid label: {reason_values}")
        else:
            invalid = [r for r in reason_values if r not in ALLOWED_REASONS]
            print(f"  [FAIL] invalid reason(s): {invalid}")
            all_passed = False

        # Check: No exceptions (if we got here, none were thrown)
        print(f"  [PASS] No exceptions thrown (silent/non-blocking)")

        print()

        # ============================================================
        # FINAL VERDICT
        # ============================================================
        print("=" * 70)
        if all_passed:
            print("  RESULT: ALL TESTS PASSED - READY TO SHIP")
        else:
            print("  RESULT: SOME TESTS FAILED - DO NOT SHIP")
        print("=" * 70)

        return all_passed

    finally:
        # Cleanup temp directory
        shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == '__main__':
    success = run_smoke_test()
    sys.exit(0 if success else 1)
