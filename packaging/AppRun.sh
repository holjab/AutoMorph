#!/bin/sh
# AppRun for the AutoMorph AppImage.
# Portable layout: payload lives in $APPDIR/usr/opt/automorph (read-only),
# mutable state lives in ${AUTOMORPH_DATA_DIR:-$HOME/.local/share/automorph}.
set -eu
APPDIR_HERE="$(dirname "$(readlink -f "$0")")"
export APPDIR="${APPDIR:-$APPDIR_HERE}"
APP_PAYLOAD="$APPDIR/usr/opt/automorph"
DATA_DIR="${AUTOMORPH_DATA_DIR:-$HOME/.local/share/automorph}"
PORT="${AUTOMORPH_PORT:-8080}"

mkdir -p "$DATA_DIR/apps/original" "$DATA_DIR/apps/patched" \
  "$DATA_DIR/tools/automorph-data" "$DATA_DIR/tools/morphe-data"

for d in scripts web defaults; do
  if [ ! -e "$DATA_DIR/$d" ]; then
    ln -s "$APP_PAYLOAD/$d" "$DATA_DIR/$d" 2>/dev/null || true
  fi
done
if [ ! -e "$DATA_DIR/requirements.txt" ]; then
  ln -s "$APP_PAYLOAD/requirements.txt" "$DATA_DIR/requirements.txt" 2>/dev/null || true
fi

export AUTOMORPH_ROOT="$DATA_DIR"
export PYTHONPATH="$APP_PAYLOAD:${PYTHONPATH:-}"

# Prefer bundled venv if builders included one, else host python3.
if [ -x "$APP_PAYLOAD/.venv/bin/python" ]; then
  PY="$APP_PAYLOAD/.venv/bin/python"
else
  PY="$(command -v python3)"
fi

if [ "${1:-}" = "run" ]; then
  shift
  exec "$PY" "$APP_PAYLOAD/scripts/main.py" "$@"
fi

if ! command -v java >/dev/null 2>&1; then
  echo "AutoMorph AppImage requires Java 17+ on the host (openjdk-21-jre recommended)." >&2
fi

exec "$PY" -m uvicorn web.backend.main:app \
  --host 0.0.0.0 --port "$PORT" --app-dir "$APP_PAYLOAD" "$@"
