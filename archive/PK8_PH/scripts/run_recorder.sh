#!/bin/bash

# Multi-Signal Recorder Runner Script
# This script runs the multi-signal recorder for data collection.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BINARY="$PROJECT_DIR/target/release/multi_signal_recorder"

# Default values
SYMBOL="${1:-BTC}"
LOG_DIR="${2:-$PROJECT_DIR/logs}"
MODE="${3:---live}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║       Multi-Signal Recorder Runner                            ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check if binary exists
if [ ! -f "$BINARY" ]; then
    echo -e "${YELLOW}Binary not found. Building...${NC}"
    cd "$PROJECT_DIR"
    cargo build --release --bin multi_signal_recorder
fi

# Create log directory
mkdir -p "$LOG_DIR"

# Show configuration
echo -e "${YELLOW}Configuration:${NC}"
echo "  Symbol:    $SYMBOL"
echo "  Log Dir:   $LOG_DIR"
echo "  Mode:      $MODE"
echo ""

if [ "$MODE" == "--live" ]; then
    echo -e "${GREEN}Starting live recording...${NC}"
    echo "Press Ctrl+C to stop"
    echo ""

    # Run with logging
    "$BINARY" --live "$SYMBOL" "$LOG_DIR" 2>&1 | tee "$LOG_DIR/recorder_$(date +%Y%m%d_%H%M%S).log"

elif [ "$MODE" == "--analyze" ]; then
    echo -e "${GREEN}Running analysis...${NC}"
    echo ""

    "$BINARY" --analyze "$SYMBOL" "$LOG_DIR"

elif [ "$MODE" == "--test" ]; then
    echo -e "${GREEN}Running test mode (60 seconds)...${NC}"
    echo ""

    # Run for 60 seconds then stop
    timeout 60 "$BINARY" --live "$SYMBOL" "$LOG_DIR" || true

    echo ""
    echo -e "${GREEN}Test complete. Checking data...${NC}"

    if [ -f "$LOG_DIR/multi_signal_sessions_${SYMBOL,,}.jsonl" ]; then
        LINES=$(wc -l < "$LOG_DIR/multi_signal_sessions_${SYMBOL,,}.jsonl")
        echo "Sessions recorded: $LINES"
    else
        echo "No sessions recorded yet (need to wait for session boundary)"
    fi

else
    echo "Usage: $0 [SYMBOL] [LOG_DIR] [MODE]"
    echo ""
    echo "Modes:"
    echo "  --live     Run continuous recording (default)"
    echo "  --analyze  Analyze recorded data"
    echo "  --test     Run for 60 seconds and stop"
    echo ""
    echo "Examples:"
    echo "  $0 BTC ./logs --live"
    echo "  $0 BTC ./logs --analyze"
    echo "  $0 ETH ./test_logs --test"
fi
