#!/usr/bin/env python3
"""
Migration script for AutoMorph patched.json schema.

Migrates pre-restructure (pre-v0.44) flat patched.json entries into the modern
nested four-domain schema:
  - metadata: {"time": str, "time_ms": int}
  - package:  {"clean_name": str, "source_package_name": str, "patched_package_name": str, ...}
  - file:     {"file_name": str, "location": str, "url": str, "size_bytes": int, ...}
  - patch:    {"applied_patches": dict, "patch_aliases": dict, "repos": list, ...}

This script is temporary prior to AutoMorph v1.0 and can be safely deleted once
all existing instances have upgraded past v1.0.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def is_legacy_entry(entry: Any) -> bool:
    """Checks whether a version entry in patched.json conforms to the legacy flat schema."""
    if not isinstance(entry, dict):
        return False
    # A restructured modern entry must have dictionary blocks for metadata, package, and file
    if not isinstance(entry.get("metadata"), dict):
        return True
    if not isinstance(entry.get("package"), dict):
        return True
    if not isinstance(entry.get("file"), dict):
        return True
    return False


def migrate_version_entry(pkg_name: str, ver_name: str, old: dict[str, Any]) -> dict[str, Any]:
    """
    Normalizes a single version record into the restructured four-domain schema.
    Accommodates both completely flat legacy records and partially restructured records.
    """
    old_meta = old.get("metadata") if isinstance(old.get("metadata"), dict) else {}
    old_pkg = old.get("package") if isinstance(old.get("package"), dict) else {}
    old_file = old.get("file") if isinstance(old.get("file"), dict) else {}
    old_patch = old.get("patch") if isinstance(old.get("patch"), dict) else {}

    # 1. Metadata domain
    time_str = old_meta.get("time") or old.get("time") or ""
    time_ms = old_meta.get("time_ms") if old_meta.get("time_ms") is not None else old.get("time_ms")
    if time_ms is None and time_str:
        try:
            time_ms = int(datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
        except Exception:
            time_ms = 0
    elif time_ms is None:
        time_ms = 0

    # 2. Package domain
    p_code = old_pkg.get("patched_version_code") or old.get("patched_version_code") or old.get("version_code", 0)
    s_code = old_pkg.get("source_version_code") or old.get("source_version_code") or old.get("version_code", 0)

    # 3. File domain
    location = old_file.get("location") or old.get("location") or ""
    file_name = old_file.get("file_name") or old.get("file_name")
    if not file_name:
        file_name = Path(location).name if location else f"{pkg_name}_{ver_name}_patched.apk"

    size_bytes = old_file.get("size_bytes") if old_file.get("size_bytes") is not None else old.get("size_bytes", 0)
    try:
        size_bytes_int = int(size_bytes)
    except (ValueError, TypeError):
        size_bytes_int = 0

    file_size_str = old_file.get("file_size") or old.get("file_size") or "0 B"

    # 4. Patch domain
    repos = old_patch.get("repos") or old.get("repos") or []
    if not isinstance(repos, list):
        repos = list(repos)

    strat = old_patch.get("strat") or old.get("strat") or 1
    attempt = old_patch.get("attempt") or old.get("attempt") or 1

    return {
        "metadata": {
            "time": time_str,
            "time_ms": time_ms
        },
        "package": {
            "clean_name": old_pkg.get("clean_name") or old.get("clean_name") or pkg_name,
            "source_package_name": old_pkg.get("source_package_name") or old.get("source_package_name") or pkg_name,
            "patched_package_name": old_pkg.get("patched_package_name") or old.get("patched_package_name") or pkg_name,
            "patched_version_name": str(old_pkg.get("patched_version_name") or old.get("patched_version_name") or ver_name),
            "source_version_name": str(old_pkg.get("source_version_name") or old.get("source_version_name") or ver_name),
            "patched_version_code": int(p_code) if p_code is not None else 0,
            "source_version_code": int(s_code) if s_code is not None else 0,
            "arch": old_pkg.get("arch") or old.get("arch") or "arm64-v8a",
            "min_sdk": old_pkg.get("min_sdk") if "min_sdk" in old_pkg else old.get("min_sdk"),
            "target_sdk": old_pkg.get("target_sdk") if "target_sdk" in old_pkg else old.get("target_sdk")
        },
        "file": {
            "file_name": file_name,
            "location": location,
            "url": old_file.get("url") or old.get("url") or "",
            "size_bytes": size_bytes_int,
            "file_size": file_size_str,
            "sha256": old_file.get("sha256") or old.get("sha256") or ""
        },
        "patch": {
            "applied_patches": old_patch.get("applied_patches") or old.get("applied_patches") or {},
            "patch_aliases": old_patch.get("patch_aliases") or old.get("patch_aliases") or {},
            "repos": repos,
            "strat": strat,
            "attempt": attempt,
            "morphe_desktop_version": old_patch.get("morphe_desktop_version") or old.get("morphe_desktop_version") or "Unknown"
        }
    }


def migrate_patched_json(patched_json_path: Path | str, backup: bool = True) -> bool:
    """
    Checks if patched.json contains any legacy entries and migrates them in-place.
    Returns True if migrations were applied, False otherwise.
    """
    target = Path(patched_json_path)
    if not target.is_file():
        return False

    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read {target} for migration check: {e}")
        return False

    if not isinstance(data, dict):
        return False

    migrated_count = 0
    migrated_data: dict[str, dict[str, Any]] = {}

    for pkg_name, versions in data.items():
        if not isinstance(versions, dict):
            migrated_data[pkg_name] = versions
            continue

        migrated_data[pkg_name] = {}
        for ver_name, ver_entry in versions.items():
            if is_legacy_entry(ver_entry):
                migrated_data[pkg_name][ver_name] = migrate_version_entry(pkg_name, ver_name, ver_entry)
                migrated_count += 1
            else:
                migrated_data[pkg_name][ver_name] = ver_entry

    if migrated_count == 0:
        logger.debug(f"No legacy entries found in {target}; migration skipped.")
        return False

    logger.info(f"[*] Found {migrated_count} legacy version record(s) in {target}. Migrating schema...")

    # Optional backup
    if backup:
        bak_file = target.with_suffix(".json.bak")
        try:
            shutil.copy2(target, bak_file)
            logger.info(f" ├── Created backup of pre-migration patched.json at: {bak_file}")
        except Exception as e:
            logger.warning(f" ├── Could not create backup at {bak_file}: {e}")

    # Atomic write
    tmp_file = target.with_suffix(".tmp")
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(migrated_data, f, indent=2)
        tmp_file.replace(target)
        logger.info(f" └── [✓] Successfully migrated {migrated_count} record(s) in {target}.")
        return True
    except Exception as e:
        logger.error(f" └── [!] Failed to write migrated data to {target}: {e}")
        if tmp_file.is_file():
            tmp_file.unlink(missing_ok=True)
        return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Migrate AutoMorph patched.json to the restructured v0.44+ schema.")
    parser.add_argument("--patched-json", "-f", type=Path, help="Path to patched.json file")
    parser.add_argument("--state-dir", "-s", type=Path, help="Path to tools/automorph-data/state directory")
    parser.add_argument("--no-backup", action="store_true", help="Do not create a .bak copy before migrating")
    args = parser.parse_args()

    project_root = Path(os.getenv("AUTOMORPH_ROOT", Path(__file__).resolve().parent.parent))
    target_file = args.patched_json

    if not target_file:
        state_dir = args.state_dir or (project_root / "tools" / "automorph-data" / "state")
        target_file = state_dir / "patched.json"

    if not target_file.is_file():
        logger.error(f"[!] Error: Target patched.json not found at: {target_file}")
        sys.exit(1)

    migrated = migrate_patched_json(target_file, backup=not args.no_backup)
    if migrated:
        logger.info("[✓] Migration completed successfully.")
    else:
        logger.info("[✓] No migration needed. All entries are up to date.")


if __name__ == "__main__":
    main()
