#!/bin/bash

# A/B Test Runner Script
# Runs live_console with paper_baseline vs rule_v1

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BINARY="$PROJECT_DIR/target/release/live_console"
RUNS_DIR="$PROJECT_DIR/logs/runs"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Generate run name
RUN_NAME="$(date +%Y%m%d_%H%M)_AB"
LOG_DIR="$RUNS_DIR/$RUN_NAME"

echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              A/B Test Runner (paper_baseline vs rule_v1)      ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check if binary exists
if [ ! -f "$BINARY" ]; then
    echo -e "${YELLOW}Binary not found. Building...${NC}"
    cd "$PROJECT_DIR"
    cargo build --release --bin live_console
fi

# Create directories
mkdir -p "$LOG_DIR"

# Update LATEST_AB symlink
ln -sf "$RUN_NAME" "$RUNS_DIR/LATEST_AB"

# Show configuration
echo -e "${CYAN}Configuration:${NC}"
echo "  Run Name:  $RUN_NAME"
echo "  Log Dir:   $LOG_DIR"
echo "  Symlink:   $RUNS_DIR/LATEST_AB -> $RUN_NAME"
echo ""
echo -e "${CYAN}Settings:${NC}"
echo "  AB_TEST=1"
echo "  ENABLE_ORDERS_JSONL=1"
echo "  MAX_TRADES_PER_SESSION=1"
echo ""
echo -e "${GREEN}Starting A/B test...${NC}"
echo "Press Ctrl+C to stop"
echo ""

# Run
cd "$PROJECT_DIR"
LOG_DIR="$LOG_DIR" AB_TEST=1 ENABLE_ORDERS_JSONL=1 MAX_TRADES_PER_SESSION=1 "$BINARY"
