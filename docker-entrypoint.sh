#!/bin/bash
# Container entrypoint: start bgutil HTTP server, then exec the bot.
#
# Why HTTP server mode instead of script mode?
#   Script mode spawns a fresh Node.js process for every extraction request.
#   HTTP server mode keeps one persistent process that manages its own visitor_data
#   and PO token state, yielding a matched pair that YouTube accepts. Script mode
#   can end up using a visitor_data supplied externally (e.g. YTDLP_YOUTUBE_VISITOR_DATA)
#   which may be stale, causing the "Sign in to confirm you're not a bot" error even
#   when bgutil is correctly installed.

set -e

BGUTIL_SERVER_DIST="/opt/bgutil-ytdlp-pot-provider/server/build"
BGUTIL_PORT="${YTDLP_BGUTIL_PORT:-4416}"

if [ -f "${BGUTIL_SERVER_DIST}/server.js" ]; then
    echo "[entrypoint] Starting bgutil HTTP server on port ${BGUTIL_PORT}..."
    node "${BGUTIL_SERVER_DIST}/server.js" &
    BGUTIL_PID=$!

    # Wait up to 10 seconds for the port to become reachable using only Python,
    # which is guaranteed present in this image.
    echo "[entrypoint] Waiting for bgutil to be ready..."
    python3 - <<'PYEOF'
import socket, sys, time
port = int(__import__('os').getenv('YTDLP_BGUTIL_PORT', '4416'))
for attempt in range(20):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            sys.exit(0)
    except OSError:
        time.sleep(0.5)
print(f"[entrypoint] WARNING: bgutil did not open port {port} within 10 s", flush=True)
sys.exit(1)
PYEOF

    export YTDLP_BGUTIL_BASE_URL="http://127.0.0.1:${BGUTIL_PORT}"
    echo "[entrypoint] bgutil HTTP server is ready. YTDLP_BGUTIL_BASE_URL=${YTDLP_BGUTIL_BASE_URL}"

    # Stale visitor_data: in HTTP-server mode bgutil manages its own fresh visitor_data
    # and PO-token pair internally.  If the Container App env has old values set, they
    # would be passed to bgutil which generates a PO token *for that stale session* —
    # YouTube then rejects it with a bot-check error.  Unset them automatically so the
    # HTTP server always uses its own fresh state.
    if [ -n "${YTDLP_YOUTUBE_VISITOR_DATA}" ]; then
        echo "[entrypoint] Unsetting YTDLP_YOUTUBE_VISITOR_DATA (stale; bgutil HTTP server manages this internally)."
        unset YTDLP_YOUTUBE_VISITOR_DATA
    fi
    if [ -n "${YTDLP_YOUTUBE_PO_TOKEN}" ]; then
        echo "[entrypoint] Unsetting YTDLP_YOUTUBE_PO_TOKEN (stale; bgutil HTTP server manages this internally)."
        unset YTDLP_YOUTUBE_PO_TOKEN
    fi
else
    echo "[entrypoint] WARNING: bgutil dist not found at ${BGUTIL_SERVER_DIST}; skipping HTTP server."
    echo "[entrypoint]          Falling back to bgutil script mode (less reliable)."
fi

exec "$@"
