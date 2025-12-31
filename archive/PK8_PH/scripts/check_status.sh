#!/bin/bash

# Quick Status Check Script
# Shows current fill counts and P&L

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
RUNS_DIR="$PROJECT_DIR/logs/runs"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

# Find latest AB run
if [ -L "$RUNS_DIR/LATEST_AB" ]; then
    LATEST=$(readlink "$RUNS_DIR/LATEST_AB")
    AB_DIR="$RUNS_DIR/$LATEST"
else
    AB_DIR=$(ls -td "$RUNS_DIR"/*AB* 2>/dev/null | head -1)
    LATEST=$(basename "$AB_DIR")
fi

if [ -z "$AB_DIR" ] || [ ! -d "$AB_DIR" ]; then
    echo -e "${RED}No A/B test runs found${NC}"
    exit 1
fi

echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              A/B Test Status                                  ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${CYAN}Run:${NC} $LATEST"
echo ""

# Fill counts
echo -e "${YELLOW}Fill Counts:${NC}"
if [ -f "$AB_DIR/orders_paper_baseline.jsonl" ]; then
    BASELINE=$(grep -c '"action":"FILL"' "$AB_DIR/orders_paper_baseline.jsonl" 2>/dev/null || echo "0")
    echo "  paper_baseline: $BASELINE fills"
fi
if [ -f "$AB_DIR/orders_rule_v1.jsonl" ]; then
    RULE_V1=$(grep -c '"action":"FILL"' "$AB_DIR/orders_rule_v1.jsonl" 2>/dev/null || echo "0")
    echo "  rule_v1:        $RULE_V1 fills"
fi
echo ""

# Quick P&L for rule_v1
if [ -f "$AB_DIR/orders_rule_v1.jsonl" ] && command -v jq &> /dev/null; then
    echo -e "${YELLOW}rule_v1 Performance:${NC}"
    cat "$AB_DIR/orders_rule_v1.jsonl" | jq -s '
        [.[] | select(.action == "FILL")] |
        {
            wins: ([.[] | select(.outcome == "up")] | length),
            losses: ([.[] | select(.outcome == "down")] | length),
            pnl: ([.[] | if .outcome == "up" then (1 - .avg_fill_q) else (0 - .avg_fill_q) end] | add // 0)
        } |
        "  Wins: \(.wins), Losses: \(.losses), P&L: $\(.pnl | . * 100 | round / 100)"
    ' -r 2>/dev/null || echo "  (no fills yet)"
fi
echo ""
