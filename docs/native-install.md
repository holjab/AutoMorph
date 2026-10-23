# Native install (no Docker)

Three formats are built from the same layout on every `v*.*.*` tag
(see `.github/workflows/release-native.yaml`, source of truth `packaging/nfpm.yaml`):

| Format | File | Install target |
|---|---|---|
| Debian/Ubuntu | `automorph_<ver>-1_amd64|arm64.deb` | `/opt/automorph` + `/var/lib/automorph` + systemd |
| Fedora/RHEL/openSUSE | `automorph-<ver>-1.x86_64|aarch64.rpm` | same as above |
| Portable | `AutoMorph-<ver>-x86_64.AppImage` | `~/.local/share/automorph`, needs host `python3.12+` + `java17+` |

## 1-liner (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/9M2PJU/AutoMorph/main/packaging/install.sh | sudo bash
# options:
curl -fsSL .../install.sh | sudo bash -s -- --version v1.2.3 --port 8080 --channel deb
# channels: auto (default) | deb | rpm | appimage | docker
# REPO override: sudo REPO=holjab/AutoMorph bash install.sh
```

What it does: detects `apt/dnf/yum/zypper`, resolves `latest` via the GitHub
releases API, installs the matching asset, writes `/etc/automorph/.env`
(first install only, `0600`), enables `automorph.service`, and prints
`http://localhost:8080`.

## Manual install

```bash
# Debian/Ubuntu
sudo apt install ./automorph_1.2.3-1_amd64.deb
# Fedora
sudo dnf install ./automorph-1.2.3-1.x86_64.rpm
# AppImage
chmod +x AutoMorph-1.2.3-x86_64.AppImage
AUTOMORPH_PORT=8080 ./AutoMorph-1.2.3-x86_64.AppImage
```

## Arch Linux (AUR)

`automorph-git` tracks the git source and rebuilds on update. The PKGBUILD lives in
[`packaging/aur/automorph-git`](../packaging/aur/automorph-git/) with pinned
Morphe jar checksums:

```bash
yay -S automorph-git   # or: paru -S automorph-git
sudo nano /etc/automorph/.env
sudo systemctl enable --now automorph
```

Layout and service behaviour match the .deb/.rpm above
(`/opt/automorph` payload, `/var/lib/automorph` state, `automorph.service`).
Maintainer: 9M2PJU.

Then edit secrets and restart:

```bash
sudo nano /etc/automorph/.env        # native
systemctl status automorph
sudo journalctl -u automorph -f
```

File layout (native):

* immutable code: `/opt/automorph/{scripts,web,defaults,requirements.txt,tools/morphe*.jar}`
* mutable state (`AUTOMORPH_ROOT`): `/var/lib/automorph/{apps,tools/automorph-data,tools/morphe-data}`
  with symlinks `scripts|web|defaults -> /opt/automorph/...` created by
  `packaging/postinst.sh`, plus `/var/lib/automorph/.env -> /etc/automorph/.env`
* wrapper: `/usr/bin/automorph` (`automorph --port 8080`, `automorph run <pkg>`)
* service: `/lib/systemd/system/automorph.service`, overrides in `/etc/automorph/automorph.env`

Docker remains fully supported — see `default_compose.yaml` and `docs/oracle-setup.md`.
