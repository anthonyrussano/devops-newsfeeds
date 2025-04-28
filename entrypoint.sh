#!/bin/sh
# Run ttyd with a command that generates a unique cache for each connection
exec ttyd -W -a sh -c 'SESSION_ID=$(date +%s%N); CACHE_DIR="/home/worker/.newsboat/cache/session-$SESSION_ID"; mkdir -p "$CACHE_DIR"; newsboat -c "$CACHE_DIR/cache.db"'