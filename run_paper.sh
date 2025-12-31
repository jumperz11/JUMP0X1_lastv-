#!/bin/bash
# JUMP0X1 - PAPER TRADING

cd "$(dirname "$0")"

echo ""
echo "============================================================"
echo "  JUMP0X1 - PAPER TRADING MODE"
echo "============================================================"
echo ""
echo "  No real orders will be placed."
echo ""

python3 run_paper.py
