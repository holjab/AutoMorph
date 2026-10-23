#!/bin/sh
# nfpm preremove (deb + rpm).
set -eu
if [ "${1:-}" = "purge" ] || [ "${1:-}" = "0" ]; then
  # "purge" (deb) / "0 = erase" (rpm): full cleanup is handled by postrm;
  # keep preremove minimal so upgrades ($1 = upgrade / 1) keep data.
  :
fi
if [ -d /run/systemd/system ]; then
  systemctl stop automorph || true
  systemctl disable automorph || true
fi
# Remove payload symlinks inside DATA_DIR (keep user data).
rm -f /var/lib/automorph/scripts /var/lib/automorph/web /var/lib/automorph/defaults /var/lib/automorph/.env || true
exit 0
