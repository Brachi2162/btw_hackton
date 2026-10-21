#!/bin/sh
# With NETFREE_REFRESH_CERTS=1, trust the NetFree intermediates the filter is
# serving right now, then run the given command (if any). No-op otherwise.
#
# Why: NetFree sends its node intermediate twice, once under an issuer that
# isn't distributed ("NetFree Sign ,..."). OpenSSL picks that copy and fails.
# The node intermediate is reissued every ~2 days, so a copy saved in certs/
# goes stale; fetching it at build and at container start keeps it current.
# Trust must still end at a NetFree root from certs/, so nothing is widened.
# (If the container runs for more than ~2 days, restart it.)

if [ "${NETFREE_REFRESH_CERTS:-0}" = "1" ]; then
    host="${NETFREE_PROBE_HOST:-api.anthropic.com}"
    dest=/usr/local/share/ca-certificates/netfree-live
    tmp=$(mktemp -d)
    echo | timeout 15 openssl s_client -connect "$host:443" -servername "$host" \
        -showcerts 2>/dev/null \
        | awk -v d="$tmp" '/BEGIN CERT/{n++} n>0{print > (d "/" n ".pem")}'
    rm -rf "$dest" && mkdir -p "$dest"
    for f in "$tmp"/*.pem; do
        [ -f "$f" ] && [ "$f" != "$tmp/1.pem" ] || continue  # 1 = the site itself
        issuer=$(openssl x509 -in "$f" -noout -issuer 2>/dev/null)
        case "$issuer" in
            *"NetFree Sign"*) ;;  # the dead-end copy
            *NetFree*) cp "$f" "$dest/$(basename "$f" .pem).crt" ;;
        esac
    done
    rm -rf "$tmp"
    update-ca-certificates >/dev/null 2>&1
    echo "netfree-certs: trusted $(ls "$dest" | wc -l) intermediate(s) from $host"
fi

[ "$#" -gt 0 ] && exec "$@"
exit 0
