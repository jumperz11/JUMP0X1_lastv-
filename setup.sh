#!/bin/bash
# JUMP0X1 - Ubuntu Setup Script

echo "============================================================"
echo "  JUMP0X1 - Ubuntu Setup"
echo "============================================================"
echo ""

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python3 not found. Install with: sudo apt install python3 python3-pip"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "Python version: $PYTHON_VERSION"

# Create virtual environment
echo ""
echo "Creating virtual environment..."
python3 -m venv venv

# Activate and install dependencies
echo "Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "============================================================"
echo "  Setup Complete!"
echo "============================================================"
echo ""
echo "  To activate the environment:"
echo "    source venv/bin/activate"
echo ""
echo "  To run paper trading:"
echo "    ./run_paper.sh"
echo ""
echo "  To run live trading:"
echo "    ./run_live.sh"
echo ""
echo "  Don't forget to copy .env.example to .env and configure!"
echo ""
