#!/bin/bash

# Quick Test Script
# Runs the recorder for 2 full 15-minute sessions and analyzes

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BINARY="$PROJECT_DIR/target/release/multi_signal_recorder"
TEST_DIR="$PROJECT_DIR/test_logs"

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                    QUICK TEST MODE                            ║"
echo "║                                                               ║"
echo "║  This will run for ~35 minutes to capture 2 full sessions    ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# Build if needed
if [ ! -f "$BINARY" ]; then
    echo "Building binary..."
    cd "$PROJECT_DIR"
    cargo build --release --bin multi_signal_recorder
fi

# Create test directory
mkdir -p "$TEST_DIR"
rm -f "$TEST_DIR"/*.jsonl 2>/dev/null || true

echo "Starting recorder..."
echo "Will run until 2 complete sessions are captured."
echo "Press Ctrl+C to stop early."
echo ""

# Calculate time until next session boundary + 2 sessions
# Sessions start at :00, :15, :30, :45
CURRENT_MIN=$(date +%M)
CURRENT_SEC=$(date +%S)

# Time to next boundary
NEXT_BOUNDARY=$((15 - (CURRENT_MIN % 15)))
if [ $NEXT_BOUNDARY -eq 15 ]; then
    NEXT_BOUNDARY=0
fi

# We need: time to next boundary + 2 full sessions (30 min) + buffer (1 min)
TOTAL_SECONDS=$(( (NEXT_BOUNDARY * 60) - CURRENT_SEC + (30 * 60) + 60 ))

echo "Current time: $(date)"
echo "Minutes to next session: $NEXT_BOUNDARY"
echo "Total runtime: $((TOTAL_SECONDS / 60)) minutes"
echo ""

# Run with timeout
if command -v gtimeout &> /dev/null; then
    TIMEOUT_CMD="gtimeout"
elif command -v timeout &> /dev/null; then
    TIMEOUT_CMD="timeout"
else
    echo "Warning: No timeout command found. Running indefinitely (Ctrl+C to stop)"
    TIMEOUT_CMD=""
fi

if [ -n "$TIMEOUT_CMD" ]; then
    $TIMEOUT_CMD $TOTAL_SECONDS "$BINARY" --live BTC "$TEST_DIR" || true
else
    # Mac without gtimeout - use background + sleep + kill
    "$BINARY" --live BTC "$TEST_DIR" &
    PID=$!
    sleep $TOTAL_SECONDS
    kill $PID 2>/dev/null || true
fi

echo ""
echo "Recording complete!"
echo ""

# Analyze
echo "Running analysis..."
echo ""

python3 "$SCRIPT_DIR/analyze_data.py" "$TEST_DIR" BTC

echo ""
echo "Test complete. Data saved to: $TEST_DIR"
