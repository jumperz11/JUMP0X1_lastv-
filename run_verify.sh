#!/bin/bash
# JUMP0X1 - PRE-LIVE VERIFICATION

cd "$(dirname "$0")"

echo ""
echo "============================================================"
echo "  JUMP0X1 - PRE-LIVE VERIFICATION SUITE"
echo "============================================================"
echo ""
echo "  Running all safety checks before going live..."
echo ""

python3 scripts/verify_pre_live.py
