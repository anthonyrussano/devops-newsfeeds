#!/bin/sh
set -eu
rm -f /usr/local/share/ca-certificates/README.md
# Bootstrap apk trust before installing ca-certificates on TLS-inspected networks.
for cert in /usr/local/share/ca-certificates/*.crt; do
    [ -f "$cert" ] || continue
    cat "$cert" >> /etc/ssl/certs/ca-certificates.crt
done
apk add --no-cache ca-certificates newsboat elinks tini "$@"
update-ca-certificates
