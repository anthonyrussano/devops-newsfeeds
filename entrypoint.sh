#!/bin/sh
# Generate a unique cache directory based on a session ID (or timestamp)
SESSION_ID=$(date +%s%N)  # Nanosecond timestamp for uniqueness
CACHE_DIR="/home/worker/.newsboat/cache/session-$SESSION_ID"
mkdir -p "$CACHE_DIR"

# Launch ttyd with newsboat, using the unique cache directory
exec ttyd -W -a newsboat -c "$CACHE_DIR/cache.db"
