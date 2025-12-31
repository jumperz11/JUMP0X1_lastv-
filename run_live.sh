#!/bin/bash
# JUMP0X1 - LIVE TRADING

cd "$(dirname "$0")"

echo ""
echo "============================================================"
echo "  JUMP0X1 - LIVE TRADING MODE"
echo "============================================================"
echo ""
echo "  WARNING: Real orders may be placed!"
echo "  Check .env settings before proceeding."
echo ""

python3 run_live.py
