#!/bin/bash
# AutoMorph one-liner installer.
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/9M2PJU/AutoMorph/main/packaging/install.sh | sudo bash
#   curl -fsSL ... | bash -s -- --repo holjab/AutoMorph --version 1.2.3 --port 8080
# Env overrides: REPO=owner/name VERSION=v1.2.3 PORT=8080 CHANNEL=appimage|deb|rpm|docker
set -euo pipefail
REPO="${REPO:-${1:-9M2PJU/AutoMorph}}"
VERSION="${VERSION:-latest}"
PORT="${PORT:-8080}"
CHANNEL="${CHANNEL:-auto}"

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2;;
    --repo=*) REPO="${1#*=}"; shift;;
    --version) VERSION="$2"; shift 2;;
    --version=*) VERSION="${1#*=}"; shift;;
    --port) PORT="$2"; shift 2;;
    --port=*) PORT="${1#*=}"; shift;;
    --channel) CHANNEL="$2"; shift 2;;
    --channel=*) CHANNEL="${1#*=}"; shift;;
    *) shift;;
  esac
done
# first positional arg may be repo
case "${REPO:-}" in -*|"") REPO="9M2PJU/AutoMorph";; esac

log(){ printf '[automorph] %s\n' "$*"; }
die(){ printf '[automorph] ERROR: %s\n' "$*" >&2; exit 1; }
need_root(){ if [ "$(id -u)" -ne 0 ]; then die "re-run with sudo/root (needed for system install)"; fi; }

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) DEB_ARCH=amd64; RPM_ARCH=x86_64; AI_ARCH=x86_64;;
  aarch64|arm64) DEB_ARCH=arm64; RPM_ARCH=aarch64; AI_ARCH=aarch64;;
  *) die "unsupported arch $ARCH";;
esac

if [ -f /etc/os-release ]; then . /etc/os-release; else ID=unknown; fi
PKG_MGR="unknown"
command -v apt-get >/dev/null 2>&1 && PKG_MGR="apt"
command -v dnf >/dev/null 2>&1 && PKG_MGR="dnf"
command -v yum >/dev/null 2>&1 && PKG_MGR="yum"
command -v zypper >/dev/null 2>&1 && PKG_MGR="zypper"

latest_tag(){
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" | grep -m1 '"tag_name"' | cut -d'"' -f4
  else
    wget -qO- "https://api.github.com/repos/${REPO}/releases/latest" | grep -m1 '"tag_name"' | cut -d'"' -f4
  fi
}

if [ "$VERSION" = "latest" ]; then
  log "resolving latest release for $REPO ..."
  VERSION="$(latest_tag || true)"
  [ -n "$VERSION" ] || die "could not resolve latest release (check REPO=$REPO)"
fi
CLEAN="${VERSION#v}"
log "installing AutoMorph $VERSION ($CLEAN) from $REPO on $ID/$ARCH port $PORT"
need_root
TMP="$(mktemp -d /tmp/opencode/automorph-install.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

install_deb(){
  f="$TMP/automorph_${CLEAN}-1_${DEB_ARCH}.deb"
  curl -fsSL -o "$f" "https://github.com/${REPO}/releases/download/${VERSION}/automorph_${CLEAN}-1_${DEB_ARCH}.deb"
  apt-get update && apt-get install -y "$f"
}
install_rpm(){
  f="$TMP/automorph-${CLEAN}-1.${RPM_ARCH}.rpm"
  curl -fsSL -o "$f" "https://github.com/${REPO}/releases/download/${VERSION}/automorph-${CLEAN}-1.${RPM_ARCH}.rpm"
  if [ "$PKG_MGR" = "dnf" ]; then dnf install -y "$f"; elif [ "$PKG_MGR" = "zypper" ]; then zypper --non-interactive install "$f"; else yum install -y "$f"; fi
}
install_appimage(){
  f="/usr/local/bin/automorph-${CLEAN}.AppImage"
  curl -fsSL -o "$f" "https://github.com/${REPO}/releases/download/${VERSION}/AutoMorph-${CLEAN}-${AI_ARCH}.AppImage"
  chmod +x "$f"
  ln -sf "$f" /usr/local/bin/automorph
  log "AppImage installed to $f (run 'AUTOMORPH_PORT=$PORT automorph')"
}
install_docker(){
  command -v docker >/dev/null 2>&1 || curl -fsSL https://get.docker.com | sh
  mkdir -p /opt/automorph
  curl -fsSL -o /opt/automorph/compose.yaml "https://raw.githubusercontent.com/${REPO}/main/default_compose.yaml"
  [ -f /opt/automorph/.env ] || curl -fsSL -o /opt/automorph/.env "https://raw.githubusercontent.com/${REPO}/main/example.env"
  (cd /opt/automorph && docker compose pull && docker compose up -d)
}

case "$CHANNEL" in
  deb) install_deb;;
  rpm) install_rpm;;
  appimage) install_appimage;;
  docker) install_docker;;
  auto)
    if [ "$PKG_MGR" = "apt" ]; then install_deb || install_appimage;
    elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ] || [ "$PKG_MGR" = "zypper" ]; then install_rpm || install_appimage;
    elif command -v docker >/dev/null 2>&1; then install_docker;
    else install_appimage; fi;;
  *) die "unknown channel $CHANNEL (deb|rpm|appimage|docker|auto)";;
esac

# point the service at the requested port (native only)
if [ -f /etc/automorph/automorph.env ] && [ "$CHANNEL" != "docker" ]; then
  grep -q AUTOMORPH_PORT /etc/automorph/automorph.env || echo "AUTOMORPH_PORT=$PORT" >> /etc/automorph/automorph.env
  systemctl try-restart automorph 2>/dev/null || true
fi

log "done. Open http://localhost:${PORT} (or your tunnel URL)."
log "Configure credentials in /etc/automorph/.env (native) or /opt/automorph/.env (docker)."
