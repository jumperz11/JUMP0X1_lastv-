#!/bin/bash

# Run All - Starts both A/B test and Collector
# Opens in split view or shows commands

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
RUNS_DIR="$PROJECT_DIR/logs/runs"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Generate run names with same timestamp
TIMESTAMP="$(date +%Y%m%d_%H%M)"
AB_NAME="${TIMESTAMP}_AB"
COLLECT_NAME="${TIMESTAMP}_COLLECT"

AB_DIR="$RUNS_DIR/$AB_NAME"
COLLECT_DIR="$RUNS_DIR/$COLLECT_NAME"

echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    JUMP01X - Run All                          ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Build if needed
if [ ! -f "$PROJECT_DIR/target/release/live_console" ]; then
    echo -e "${YELLOW}Building binaries...${NC}"
    cd "$PROJECT_DIR"
    cargo build --release
fi

# Create directories
mkdir -p "$AB_DIR" "$COLLECT_DIR"

# Update symlinks
ln -sf "$AB_NAME" "$RUNS_DIR/LATEST_AB"
ln -sf "$COLLECT_NAME" "$RUNS_DIR/LATEST_COLLECT"

echo -e "${CYAN}Run Name:${NC} $TIMESTAMP"
echo -e "${CYAN}A/B Dir:${NC}  $AB_DIR"
echo -e "${CYAN}Collect:${NC}  $COLLECT_DIR"
echo ""

# Check if we can use tmux
if command -v tmux &> /dev/null; then
    echo -e "${GREEN}Starting in tmux split view...${NC}"
    echo ""

    cd "$PROJECT_DIR"

    # Create new tmux session with A/B test
    tmux new-session -d -s jump01x -n main \
        "LOG_DIR=$AB_DIR AB_TEST=1 ENABLE_ORDERS_JSONL=1 MAX_TRADES_PER_SESSION=1 ./target/release/live_console; read"

    # Split and run collector
    tmux split-window -h -t jump01x:main \
        "LOG_DIR=$COLLECT_DIR ./target/release/multi_signal_recorder --live BTC; read"

    # Attach
    tmux attach -t jump01x
else
    # No tmux - run A/B in foreground, collector in background
    echo -e "${YELLOW}No tmux found. Running collector in background...${NC}"
    echo ""

    cd "$PROJECT_DIR"

    # Start collector in background
    LOG_DIR="$COLLECT_DIR" ./target/release/multi_signal_recorder --live BTC &
    COLLECT_PID=$!
    echo -e "${CYAN}Collector PID:${NC} $COLLECT_PID"
    echo ""

    # Trap to kill collector on exit
    trap "kill $COLLECT_PID 2>/dev/null; echo 'Stopped collector'" EXIT

    echo -e "${GREEN}Starting A/B test (Ctrl+C to stop both)...${NC}"
    echo ""

    LOG_DIR="$AB_DIR" AB_TEST=1 ENABLE_ORDERS_JSONL=1 MAX_TRADES_PER_SESSION=1 ./target/release/live_console
fi
