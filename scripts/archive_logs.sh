#!/bin/bash
# Archive current session logs with timestamp
# Run this after each trading session

LOGS_DIR="/Users/jumperz/PROJES/JUMP01X/logs"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

# Create archive directory
ARCHIVE_DIR="$LOGS_DIR/archive/$TIMESTAMP"
mkdir -p "$ARCHIVE_DIR"

# Move current logs to archive
if [ -f "$LOGS_DIR/sessions/live_console.log" ]; then
    cp "$LOGS_DIR/sessions/live_console.log" "$ARCHIVE_DIR/session.log"
    echo "Archived: session.log"
fi

if [ -f "$LOGS_DIR/events/trades.jsonl" ]; then
    cp "$LOGS_DIR/events/trades.jsonl" "$ARCHIVE_DIR/trades.jsonl"
    echo "Archived: trades.jsonl"
fi

# Clear current logs for next session
> "$LOGS_DIR/sessions/live_console.log"
> "$LOGS_DIR/events/trades.jsonl"

echo "Logs archived to: $ARCHIVE_DIR"
echo "Current logs cleared for next session"
