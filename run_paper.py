#!/usr/bin/env python3
"""
JUMP0X1 - Paper Trading Mode
============================
Run the live dashboard in paper (simulation) mode.
No real orders will be placed.

Usage: python run_paper.py
"""
import sys
import os
from pathlib import Path

# Ensure TRADING_MODE is paper
os.environ["TRADING_MODE"] = "paper"
os.environ["EXECUTION_ENABLED"] = "false"

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

if __name__ == "__main__":
    import asyncio
    from src.ui.ui_dashboard_live import run_dashboard

    print("=" * 60)
    print("  JUMP0X1 - PAPER TRADING MODE")
    print("=" * 60)
    print()
    print("  No real orders will be placed.")
    print("  All trades are simulated.")
    print()

    asyncio.run(run_dashboard())
