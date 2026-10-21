#!/bin/sh
set -eu
# Each browser connection gets independent read state and a SQLite database.
session_dir=$(mktemp -d /tmp/newsboat.XXXXXX)
# Newsboat also writes history and creates its config directory at startup.
# Keep every per-session writable path inside the temporary directory.
export HOME="$session_dir"
export XDG_CONFIG_HOME="$session_dir/config"
export XDG_DATA_HOME="$session_dir/data"
trap 'rm -rf "$session_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' HUP TERM
newsboat -C /etc/newsboat/config -u /etc/newsboat/urls -c "$session_dir/cache.db"
