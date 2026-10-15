#!/usr/bin/env bash
#
# screenshot.sh - take desktop and mobile screenshots of the tent web UI from
# the current checkout, for PRs and product reviews.
#
# Usage:
#   scripts/screenshot.sh OUT_DIR [VIEW ...]
#
#   OUT_DIR  Directory for the PNGs and server.log (created if missing).
#   VIEW     URL path/fragment appended to the base URL. Default: "/".
#            Works from any cwd; the app root is the parent of scripts/.
#
# Views (the single-page app in autocann/web switches views with the URL hash,
# see autocann/web/static/js/views.js; "#riego" and "#/riego" both work):
#   /            the default view (same as /#panel)
#   /#panel      "Panel de control"
#   /#riego      "Riego"
#   /#stats      "Estadisticas"
#   /#reportes   "Reportes"
#   /#cultivo    "Cultivo"
#   /#sistema    "Sistema"
#   Unknown hashes fall back to the panel view.
#
# Output per VIEW (slug = "home" for "/", else the view with every
# non-alphanumeric character turned into "-" and the dashes trimmed):
#   OUT_DIR/<slug>-desktop.png   1440x1000
#   OUT_DIR/<slug>-mobile.png    390x844
#
# Requirements: bash, python3 (or the app venv), curl, chromium, and the
# app's .venv (app root or /home/claudio/Autocann/apps/tent/.venv).
#
# Environment:
#   AUTOCANN_DB  If set, passed to the backend so a copy of real data is used.
#
# The script starts its own backend on a free 127.0.0.1 port and kills only
# that PID on exit. Ports 5000 and 5055 (production on this machine) are never
# used. Never replace the kill with a name-based kill (pkill/killall): the
# production backend runs on this same machine.
#
# Exit status: 0 when every screenshot was written and is at least 10 KB;
# 1 otherwise (missing or blank output, or the backend did not come up).

set -euo pipefail

readonly MIN_BYTES=10240
readonly REFUSED_PORTS=(5000 5055)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly SCRIPT_DIR APP_ROOT

die() {
    echo "error: $*" >&2
    exit 1
}

if [[ $# -lt 1 ]]; then
    sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
    exit 2
fi

OUT_DIR="$1"
shift
VIEWS=("$@")
if [[ ${#VIEWS[@]} -eq 0 ]]; then
    VIEWS=("/")
fi

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
readonly OUT_DIR

# --- Python interpreter for the backend -------------------------------------
if [[ -x "$APP_ROOT/.venv/bin/python" ]]; then
    PY="$APP_ROOT/.venv/bin/python"
elif [[ -x /home/claudio/Autocann/apps/tent/.venv/bin/python ]]; then
    PY=/home/claudio/Autocann/apps/tent/.venv/bin/python
else
    die "no executable venv python found at $APP_ROOT/.venv/bin/python or /home/claudio/Autocann/apps/tent/.venv/bin/python"
fi
readonly PY

command -v chromium >/dev/null 2>&1 || die "chromium not found in PATH"
command -v curl >/dev/null 2>&1 || die "curl not found in PATH"

# --- Cleanup -----------------------------------------------------------------
SERVER_PID=""
PROFILES=()

cleanup() {
    local rc=$?
    if [[ -n "$SERVER_PID" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    local profile
    for profile in ${PROFILES[@]+"${PROFILES[@]}"}; do
        rm -rf "$profile"
    done
    exit "$rc"
}
# Signals go through exit so the EXIT trap (cleanup) always runs.
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- Free port ---------------------------------------------------------------
PORT=""
for _ in $(seq 1 20); do
    candidate="$("$PY" -c 'import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()')"
    refused=0
    for bad in "${REFUSED_PORTS[@]}"; do
        [[ "$candidate" == "$bad" ]] && refused=1
    done
    if [[ $refused -eq 0 ]]; then
        PORT="$candidate"
        break
    fi
done
[[ -n "$PORT" ]] || die "could not pick a free port outside ${REFUSED_PORTS[*]}"
BASE_URL="http://127.0.0.1:$PORT"

# --- Backend -----------------------------------------------------------------
db_env=()
if [[ -n "${AUTOCANN_DB:-}" ]]; then
    db_env=("AUTOCANN_DB=$AUTOCANN_DB")
fi

cd "$APP_ROOT"
# No subshell here: $! must be the backend's own PID so the trap kills it.
env ${db_env[@]+"${db_env[@]}"} \
    AUTOCANN_WEB_HOST=127.0.0.1 AUTOCANN_WEB_PORT="$PORT" PYTHONPATH=. \
    "$PY" -m autocann.cli.backend > "$OUT_DIR/server.log" 2>&1 &
SERVER_PID=$!
echo "backend pid $SERVER_PID on $BASE_URL (log: $OUT_DIR/server.log)"

ready=0
for _ in $(seq 1 20); do
    if curl -sf "$BASE_URL/" -o /dev/null; then
        ready=1
        break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        break
    fi
    sleep 1
done

if [[ $ready -ne 1 ]]; then
    echo "error: backend did not answer $BASE_URL/ within 20s; tail of server.log:" >&2
    tail -n 40 "$OUT_DIR/server.log" >&2 || true
    exit 1
fi

# --- Screenshots -------------------------------------------------------------
slug_for() {
    local view="$1" slug
    if [[ "$view" == "/" ]]; then
        echo "home"
        return
    fi
    slug="$(printf '%s' "$view" | tr -c 'A-Za-z0-9\n' '-' | sed -E 's/-+/-/g; s/^-//; s/-$//')"
    echo "${slug:-home}"
}

# shot WIDTH HEIGHT URL FILE
shot() {
    local width="$1" height="$2" url="$3" file="$4" profile rc=0
    profile="$(mktemp -d)"
    PROFILES+=("$profile")
    rm -f "$file"
    # A fresh profile every time: with the default profile chromium hangs on
    # this machine.
    timeout 60 chromium --headless=new --no-sandbox --disable-gpu \
        --user-data-dir="$profile" --password-store=basic --hide-scrollbars \
        --window-size="$width,$height" --virtual-time-budget=6000 \
        --screenshot="$file" "$url" >/dev/null 2>&1 || rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "warning: chromium exited with status $rc for $url ($width x $height)" >&2
    fi
}

written=()
failed=0
for view in "${VIEWS[@]}"; do
    path="$view"
    if [[ "$path" != /* ]]; then
        path="/$path"
    fi
    url="$BASE_URL$path"
    slug="$(slug_for "$view")"

    for spec in "desktop 1440 1000" "mobile 390 844"; do
        read -r name width height <<<"$spec"
        file="$OUT_DIR/$slug-$name.png"
        shot "$width" "$height" "$url" "$file"
        written+=("$file")
        if [[ ! -f "$file" ]]; then
            echo "missing: $file" >&2
            failed=1
        elif [[ "$(stat -c %s "$file")" -lt $MIN_BYTES ]]; then
            echo "too small (blank page?): $file ($(stat -c %s "$file") bytes)" >&2
            failed=1
        fi
    done
done

for file in "${written[@]}"; do
    if [[ -f "$file" ]]; then
        echo "$file ($(stat -c %s "$file") bytes)"
    fi
done

if [[ $failed -ne 0 ]]; then
    echo "error: one or more screenshots are missing or blank" >&2
    exit 1
fi
exit 0
