#!/usr/bin/env python3
"""
JUMP0X1 - Live Trading Mode
===========================
Run the live dashboard with real order execution.

WARNING: This will place REAL orders on Polymarket!

Requirements:
1. Set TRADING_MODE=real in .env
2. Set EXECUTION_ENABLED=true in .env
3. Have valid PM_PRIVATE_KEY in .env
4. Have sufficient USDC balance

Usage: python run_live.py
"""
import sys
import os
import threading
from pathlib import Path

# Load environment from .env (don't override - use .env settings)
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

if __name__ == "__main__":
    import asyncio
    from src.ui.ui_dashboard_live import run_dashboard, is_real_mode, is_execution_enabled

    # Start Telegram control listener (background thread)
    from src.notifications import telegram_control
    telegram_control._reload_config()  # Load env vars
    threading.Thread(target=telegram_control.start, daemon=True).start()

    print("=" * 60)
    print("  JUMP0X1 - LIVE TRADING MODE")
    print("=" * 60)
    print()

    if is_real_mode():
        print("  [!] REAL MODE ACTIVE")
        if is_execution_enabled():
            print("  [!] EXECUTION ENABLED - REAL ORDERS WILL BE PLACED")
        else:
            print("  [ ] Execution disabled - monitoring only")
    else:
        print("  [ ] Paper mode - no real orders")

    print()
    if telegram_control.TELEGRAM_ENABLED:
        print("  [T] Telegram: /status /kill")
    print("  Press Ctrl+C to stop")
    print()

    asyncio.run(run_dashboard())
