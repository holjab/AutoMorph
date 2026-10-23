#!/bin/bash
# Local native-package builder (no nfpm required).
# Mirrors packaging/nfpm.yaml layout using dpkg-deb / rpmbuild / appimagetool.
# Usage: packaging/build.sh [--version 0.01] [--arch amd64] [--morphe-version 1.19.0] [--skip-jar]
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$REPO_ROOT/dist"
VERSION="${VERSION:-0.01}"
ARCH_IN="${ARCH:-amd64}"
MORPHE_VERSION="${MORPHE_VERSION:-1.19.0}"
SKIP_JAR=0
for a in "$@"; do
  case "$a" in
    --version=*) VERSION="${a#*=}";; --version) shift; VERSION="${1:-$VERSION}";;
    --arch=*) ARCH_IN="${a#*=}";; --arch) shift; ARCH_IN="${1:-$ARCH_IN}";;
    --morphe-version=*) MORPHE_VERSION="${a#*=}";;
    --skip-jar) SKIP_JAR=1;;
    --version*) VERSION="$2"; shift;;
  esac
done
# strip leading v (tags are v1.2.3, deb version is 1.2.3)
VERSION="${VERSION#v}"

case "$ARCH_IN" in
  amd64|x86_64) DEB_ARCH=amd64; RPM_ARCH=x86_64; APPIMAGE_ARCH=x86_64;;
  arm64|aarch64) DEB_ARCH=arm64; RPM_ARCH=aarch64; APPIMAGE_ARCH=aarch64;;
  *) echo "unsupported arch $ARCH_IN" >&2; exit 1;;
esac

mkdir -p "$OUT"
STAGE="$(mktemp -d /tmp/opencode/automorph-pkg.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

echo "[*] staging payload $VERSION ($DEB_ARCH) morphe=$MORPHE_VERSION"
mkdir -p "$STAGE/opt/automorph/tools" "$STAGE/usr/bin" \
  "$STAGE/lib/systemd/system" "$STAGE/usr/share/doc/automorph" "$STAGE/DEBIAN"
cp -r "$REPO_ROOT/scripts" "$REPO_ROOT/web" "$REPO_ROOT/defaults" "$STAGE/opt/automorph/"
cp "$REPO_ROOT/requirements.txt" "$REPO_ROOT/LICENSE" "$STAGE/opt/automorph/"
cp "$REPO_ROOT/example.env" "$STAGE/usr/share/doc/automorph/example.env"
cp "$REPO_ROOT/packaging/automorph" "$STAGE/usr/bin/automorph"
cp "$REPO_ROOT/packaging/automorph.service" "$STAGE/lib/systemd/system/automorph.service"
chmod 755 "$STAGE/usr/bin/automorph" "$REPO_ROOT/packaging/postinst.sh" "$REPO_ROOT/packaging/prerm.sh"

if [ "$SKIP_JAR" = "0" ]; then
  JAR="$STAGE/opt/automorph/tools/morphe-desktop-${MORPHE_VERSION}-all.jar"
  if [ ! -f "$JAR" ]; then
    curl -fsSL -o "$JAR" "https://github.com/MorpheApp/morphe-desktop/releases/download/v${MORPHE_VERSION}/morphe-desktop-${MORPHE_VERSION}-all.jar"
  fi
  (cd "$STAGE/opt/automorph/tools" && ln -sf "morphe-desktop-${MORPHE_VERSION}-all.jar" morphe.jar)
  # stamp version for the web UI
  printf '{"version":"%s","built_at":"%s"}\n' "$VERSION" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STAGE/opt/automorph/web/version.json"
fi

# ---------------- .deb ----------------
cat > "$STAGE/DEBIAN/control" <<EOF
Package: automorph
Version: ${VERSION}-1
Section: utils
Priority: optional
Architecture: ${DEB_ARCH}
Maintainer: AutoMorph Contributors
Description: Self-hosted tool for automatically patching and distributing Android packages.
 Depends: python3 (>= 3.12), python3-venv, python3-pip, openjdk-21-jre-headless | openjdk-21-jre, curl, ca-certificates
Homepage: https://github.com/holjab/AutoMorph
EOF
cp "$REPO_ROOT/packaging/postinst.sh" "$STAGE/DEBIAN/postinst"
cp "$REPO_ROOT/packaging/prerm.sh" "$STAGE/DEBIAN/prerm"
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm"
DEB="$OUT/automorph_${VERSION}-1_${DEB_ARCH}.deb"
dpkg-deb --build "$STAGE" "$DEB"
echo "[✓] $DEB"

# ---------------- .rpm ----------------
if command -v rpmbuild >/dev/null 2>&1; then
  RPMBUILDROOT="$(mktemp -d /tmp/opencode/rpmbuild.XXXXXX)"
  mkdir -p "$RPMBUILDROOT"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
  SPEC="$RPMBUILDROOT/SPECS/automorph.spec"
  cat > "$SPEC" <<EOF
Name: automorph
Version: ${VERSION}
Release: 1%{?dist}
Summary: Self-hosted tool for automatically patching Android packages
License: MIT
URL: https://github.com/holjab/AutoMorph
BuildArch: ${RPM_ARCH}
Requires: python3 >= 3.12, java-21-openjdk-headless, curl, ca-certificates
%description
Self-hosted tool for automatically patching and distributing Android packages, built on Morphe Desktop.
%install
rm -rf %{buildroot}
mkdir -p %{buildroot}/opt/automorph %{buildroot}/usr/bin %{buildroot}/lib/systemd/system %{buildroot}/usr/share/doc/automorph
cp -r $STAGE/opt/automorph/* %{buildroot}/opt/automorph/
cp $STAGE/usr/bin/automorph %{buildroot}/usr/bin/automorph
cp $STAGE/lib/systemd/system/automorph.service %{buildroot}/lib/systemd/system/automorph.service
cp $STAGE/usr/share/doc/automorph/example.env %{buildroot}/usr/share/doc/automorph/example.env
%files
/opt/automorph
/usr/bin/automorph
/lib/systemd/system/automorph.service
/usr/share/doc/automorph/example.env
%post
sh /opt/automorph-tools-postinst.sh || sh %{_sourcedir}/postinst.sh || true
%preun
[ "\$1" = "0" ] && systemctl stop automorph 2>/dev/null || true
%changelog
* $(date '+%a %b %d %Y') AutoMorph - ${VERSION}-1
- native package build
EOF
  cp "$REPO_ROOT/packaging/postinst.sh" "$RPMBUILDROOT/SOURCES/postinst.sh"
  # inline postinst into %post (rpmbuild has no access to STAGE at install time)
  python3 - "$SPEC" "$REPO_ROOT/packaging/postinst.sh" <<'PYEOF'
import sys
spec_path, postinst_path = sys.argv[1], sys.argv[2]
postinst = open(postinst_path).read()
content = open(spec_path).read()
placeholder = "sh /opt/automorph-tools-postinst.sh || sh %{_sourcedir}/postinst.sh || true"
assert placeholder in content, "placeholder missing from spec"
open(spec_path, "w").write(content.replace(placeholder, postinst))
PYEOF
  rpmbuild --define "_topdir $RPMBUILDROOT" -bb "$SPEC"
  find "$RPMBUILDROOT/RPMS" -name '*.rpm' -exec cp {} "$OUT/" \;
  rm -rf "$RPMBUILDROOT"
  echo "[✓] rpm copied to $OUT/"
else
  echo "[!] rpmbuild not found, skipping rpm" >&2
fi

# ---------------- .AppImage ----------------
if command -v appimagetool >/dev/null 2>&1; then
  APPDIR="$OUT/AppDir"
  rm -rf "$APPDIR"
  mkdir -p "$APPDIR/usr/opt/automorph" "$APPDIR/usr/bin" "$APPDIR/usr/share/applications"
  cp -r "$STAGE/opt/automorph/scripts" "$STAGE/opt/automorph/web" "$STAGE/opt/automorph/defaults" "$APPDIR/usr/opt/automorph/"
  cp "$STAGE/opt/automorph/requirements.txt" "$APPDIR/usr/opt/automorph/"
  if [ -f "$STAGE/opt/automorph/tools/morphe-desktop-${MORPHE_VERSION}-all.jar" ]; then
    mkdir -p "$APPDIR/usr/opt/automorph/tools"
    cp "$STAGE/opt/automorph/tools/"*.jar "$APPDIR/usr/opt/automorph/tools/"
  fi
  cp "$REPO_ROOT/packaging/AppRun.sh" "$APPDIR/AppRun"
  cp "$REPO_ROOT/packaging/automorph.desktop" "$APPDIR/automorph.desktop"
  cp "$REPO_ROOT/packaging/automorph.desktop" "$APPDIR/usr/share/applications/automorph.desktop"
  cp "$REPO_ROOT/images/logo.png" "$APPDIR/automorph.png" 2>/dev/null || printf '' > "$APPDIR/automorph.png"
  cp "$REPO_ROOT/images/logo.png" "$APPDIR/usr/share/icons/automorph.png" 2>/dev/null || true
  chmod +x "$APPDIR/AppRun"
  (cd "$OUT" && ARCH="$APPIMAGE_ARCH" appimagetool "$APPDIR" "AutoMorph-${VERSION}-${APPIMAGE_ARCH}.AppImage")
  echo "[✓] AppImage built"
else
  echo "[!] appimagetool not found, skipping AppImage" >&2
fi

echo "[✓] done. Artifacts in $OUT/"
ls -la "$OUT/"
