#!/bin/sh
set -eu
cache_dir="${NEWSBOAT_CACHE_DIR:-/home/worker/.newsboat/cache}"
mkdir -p "$cache_dir"
exec newsboat -C /etc/newsboat/config -u /etc/newsboat/urls -c "$cache_dir/cache.db" "$@"
