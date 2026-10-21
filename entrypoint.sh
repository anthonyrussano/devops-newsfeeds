#!/bin/sh
set -eu
# Server options can be supplied here; browser-supplied command arguments are disabled.
exec ttyd -W -O "$@" /usr/local/bin/newsboat-session
