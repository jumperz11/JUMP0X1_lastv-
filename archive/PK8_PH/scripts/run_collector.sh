#!/bin/bash

# Signal Collector Runner Script
# Runs multi_signal_recorder to collect price/signal data

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BINARY="$PROJECT_DIR/target/release/multi_signal_recorder"
RUNS_DIR="$PROJECT_DIR/logs/runs"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Generate run name
RUN_NAME="$(date +%Y%m%d_%H%M)_COLLECT"
LOG_DIR="$RUNS_DIR/$RUN_NAME"

echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              Signal Collector (multi_signal_recorder)         ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check if binary exists
if [ ! -f "$BINARY" ]; then
    echo -e "${YELLOW}Binary not found. Building...${NC}"
    cd "$PROJECT_DIR"
    cargo build --release --bin multi_signal_recorder
fi

# Create directories
mkdir -p "$LOG_DIR"

# Update LATEST_COLLECT symlink
ln -sf "$RUN_NAME" "$RUNS_DIR/LATEST_COLLECT"

# Show configuration
echo -e "${CYAN}Configuration:${NC}"
echo "  Run Name:  $RUN_NAME"
echo "  Log Dir:   $LOG_DIR"
echo "  Symlink:   $RUNS_DIR/LATEST_COLLECT -> $RUN_NAME"
echo ""
echo -e "${GREEN}Starting collector...${NC}"
echo "Press Ctrl+C to stop"
echo ""

# Run
cd "$PROJECT_DIR"
LOG_DIR="$LOG_DIR" "$BINARY" --live BTC
