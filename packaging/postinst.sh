#!/bin/sh
# nfpm postinstall (deb + rpm). Idempotent.
set -eu

APP_DIR="/opt/automorph"
DATA_DIR="/var/lib/automorph"
ETC_DIR="/etc/automorph"
MORPHE_VERSION="${MORPHE_VERSION:-1.19.0}"

# 1. service user
if ! id automorph >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir "$DATA_DIR" --shell /usr/sbin/nologin automorph
fi

# 2. mutable state dirs (code auto-creates these too, see web/backend/main.py startup_event)
mkdir -p "$DATA_DIR/apps/original" "$DATA_DIR/apps/patched" \
  "$DATA_DIR/tools/automorph-data" "$DATA_DIR/tools/morphe-data" \
  /var/log/automorph "$ETC_DIR"

# 3. link immutable payload into AUTOMORPH_ROOT so
#    PROJECT_ROOT=/var/lib/automorph resolves scripts/web/defaults.
for d in scripts web defaults; do
  if [ ! -e "$DATA_DIR/$d" ]; then
    ln -s "$APP_DIR/$d" "$DATA_DIR/$d"
  fi
done

# 4. morphe jar: prefer the copy staged at package build time (packaging/tools-staged/),
#    otherwise download it now.
if [ ! -f "$APP_DIR/tools/morphe.jar" ]; then
  JAR="$APP_DIR/tools/morphe-desktop-${MORPHE_VERSION}-all.jar"
  if [ ! -f "$JAR" ] && command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$JAR" \
      "https://github.com/MorpheApp/morphe-desktop/releases/download/v${MORPHE_VERSION}/morphe-desktop-${MORPHE_VERSION}-all.jar" || true
  fi
  if [ -f "$JAR" ] && [ ! -e "$APP_DIR/tools/morphe.jar" ]; then
    ln -sf "$(basename "$JAR")" "$APP_DIR/tools/morphe.jar"
  fi
fi

# 5. seed /etc/automorph/.env (never overwrite) and link into DATA_DIR for
#    dotenv loading (config_manager checks PROJECT_ROOT/.env).
if [ ! -f "$ETC_DIR/.env" ]; then
  if [ -f "$APP_DIR/../usr/share/doc/automorph/example.env" ]; then
    cp "$APP_DIR/../usr/share/doc/automorph/example.env" "$ETC_DIR/.env"
  elif [ -f /usr/share/doc/automorph/example.env ]; then
    cp /usr/share/doc/automorph/example.env "$ETC_DIR/.env"
  else
    : > "$ETC_DIR/.env"
  fi
  chmod 600 "$ETC_DIR/.env" || true
fi
if [ ! -e "$DATA_DIR/.env" ]; then
  ln -s "$ETC_DIR/.env" "$DATA_DIR/.env" || cp "$ETC_DIR/.env" "$DATA_DIR/.env" || true
fi
# Optional systemd EnvironmentFile (PORT/HOST overrides without editing the unit)
if [ ! -f "$ETC_DIR/automorph.env" ]; then
  printf '# Overrides for automorph.service\n#AUTOMORPH_PORT=8080\n#AUTOMORPH_HOST=0.0.0.0\n' > "$ETC_DIR/automorph.env"
fi

# 6. python venv + deps (venv keeps system python clean)
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip >/dev/null 2>&1 || true
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

chown -R automorph:automorph "$DATA_DIR" /var/log/automorph || true
chown -R root:root "$APP_DIR" || true
chmod 755 /usr/bin/automorph || true

# 7. enable + start (only under systemd)
if [ -d /run/systemd/system ]; then
  systemctl daemon-reload || true
  systemctl enable automorph >/dev/null 2>&1 || true
  systemctl try-restart automorph || systemctl start automorph || true
fi
exit 0
