"""
Morphe CLI (morphe-desktop) release updater and version manager for AutoMorph.

Tracks the latest Morphe CLI jar from MorpheApp/morphe-desktop releases on GitHub,
downloads and maintains tools/morphe.jar (symlinked or copied to the latest version),
and reports the current installed version for the AutoMorph Control Center.
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import shutil
import time
import zipfile
from typing import Any

import requests

logger = logging.getLogger("automorph.morphe_cli")

PROJECT_ROOT = Path(os.getenv("AUTOMORPH_ROOT", Path(__file__).resolve().parent.parent))
TOOLS_DIR = PROJECT_ROOT / "tools"
STATE_DIR = PROJECT_ROOT / "tools" / "automorph-data" / "state"
CLI_STATE_FILE = STATE_DIR / "morphe_cli.json"
MORPHE_REPO = "MorpheApp/morphe-desktop"
DEFAULT_FALLBACK_VERSION = "2.0.0"


def parse_semver(ver_str: str | None) -> tuple[int, int, int]:
    """Parses a version string into a comparable (major, minor, patch) tuple."""
    if not ver_str:
        return (0, 0, 0)
    clean = str(ver_str).strip().lstrip("v").strip()
    digits = re.findall(r"\d+", clean)
    if not digits:
        return (0, 0, 0)
    parts = [int(d) for d in digits[:3]]
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def get_morphe_jar_path(project_root: Path | None = None) -> Path | None:
    """Finds morphe.jar or morphe-desktop jar under tools/."""
    tools_dir = (project_root or PROJECT_ROOT) / "tools"
    primary = tools_dir / "morphe.jar"
    if primary.is_file():
        return primary
    for jar in sorted(tools_dir.glob("morphe-desktop*.jar"), reverse=True):
        if jar.is_file():
            return jar
    return None


def is_cli_installed(project_root: Path | None = None) -> bool:
    """Returns True if a valid Morphe CLI jar exists."""
    jar = get_morphe_jar_path(project_root)
    return jar is not None and jar.is_file() and jar.stat().st_size > 1000


def get_installed_cli_version(project_root: Path | None = None) -> str | None:
    """
    Determines the currently installed Morphe CLI version.
    Checks state file, symlink target name, jar filenames, or jar manifest.
    """
    root = project_root or PROJECT_ROOT
    state_file = root / "tools" / "automorph-data" / "state" / "morphe_cli.json"
    tools_dir = root / "tools"
    morphe_jar = tools_dir / "morphe.jar"

    # 1. If state file exists and morphe jar exists, return cached version
    if state_file.is_file() and morphe_jar.is_file():
        try:
            with state_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
                ver = data.get("version") or data.get("tag_name")
                if ver:
                    return str(ver).strip()
        except Exception:
            pass

    # 2. Check if morphe.jar is a symlink or real file with version in name
    if morphe_jar.is_file():
        try:
            resolved = morphe_jar.resolve()
            m = re.search(r"morphe-desktop-([\d\.\w\-]+?)(?:-all)?\.jar", resolved.name, re.IGNORECASE)
            if m:
                return m.group(1)
            m_digits = re.search(r"(\d+\.\d+(?:\.\d+)?)", resolved.name)
            if m_digits:
                return m_digits.group(1)
        except Exception:
            pass

    # 3. Check any morphe-desktop*.jar in tools/
    if tools_dir.is_dir():
        for jar in sorted(tools_dir.glob("morphe-desktop*.jar"), reverse=True):
            if jar.is_file():
                m = re.search(r"morphe-desktop-([\d\.\w\-]+?)(?:-all)?\.jar", jar.name, re.IGNORECASE)
                if m:
                    return m.group(1)
                m_digits = re.search(r"(\d+\.\d+(?:\.\d+)?)", jar.name)
                if m_digits:
                    return m_digits.group(1)

    # 4. Check jar manifest if morphe_jar exists
    if morphe_jar.is_file():
        try:
            with zipfile.ZipFile(morphe_jar, "r") as z:
                if "META-INF/MANIFEST.MF" in z.namelist():
                    manifest_content = z.read("META-INF/MANIFEST.MF").decode("utf-8", errors="ignore")
                    m = re.search(r"Implementation-Version:\s*([^\r\n]+)", manifest_content)
                    if m:
                        return m.group(1).strip()
        except Exception:
            pass

    return None


def get_latest_release_info() -> dict[str, Any] | None:
    """Queries GitHub Releases API for the latest Morphe CLI release."""
    api_url = f"https://api.github.com/repos/{MORPHE_REPO}/releases/latest"
    headers = {
        "User-Agent": "AutoMorph-MorpheCLIUpdater",
        "Accept": "application/vnd.github.v3+json",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token.strip()}"

    # Setup proxy if configured
    try:
        import proxy_utils
        proxy_utils.setup_proxy()
    except Exception:
        pass

    try:
        res = requests.get(api_url, headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json()
            tag_name = data.get("tag_name", "").strip()
            clean_ver = tag_name.lstrip("v")
            assets = data.get("assets", [])

            # Look for morphe-desktop-*-all.jar first, then -all.jar, then any jar
            target_asset = None
            for a in assets:
                name = str(a.get("name", ""))
                if name.startswith("morphe-desktop-") and name.endswith("-all.jar"):
                    target_asset = a
                    break
            if not target_asset:
                for a in assets:
                    name = str(a.get("name", ""))
                    if name.endswith("-all.jar"):
                        target_asset = a
                        break
            if not target_asset:
                for a in assets:
                    name = str(a.get("name", ""))
                    if name.endswith(".jar"):
                        target_asset = a
                        break

            if target_asset:
                return {
                    "tag_name": tag_name,
                    "version": clean_ver,
                    "asset_name": target_asset["name"],
                    "download_url": target_asset["browser_download_url"],
                    "size": target_asset.get("size", 0),
                    "published_at": data.get("published_at"),
                }
            logger.warning(f"No suitable jar asset found in {MORPHE_REPO} release {tag_name}")
            return None
        elif res.status_code == 403:
            logger.warning(f"GitHub API rate limit reached when checking {MORPHE_REPO} release.")
            return None
        else:
            logger.warning(f"GitHub API returned HTTP {res.status_code} for {MORPHE_REPO} release.")
            return None
    except Exception as e:
        logger.warning(f"Failed to query latest {MORPHE_REPO} release: {e}")
        return None


def download_and_install_cli(release_info: dict[str, Any], project_root: Path | None = None) -> bool:
    """
    Downloads the specified release asset, creates tools/morphe.jar symlink/copy,
    cleans older jars, and persists version metadata.
    """
    root = project_root or PROJECT_ROOT
    tools_dir = root / "tools"
    state_dir = root / "tools" / "automorph-data" / "state"
    tools_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    asset_name = release_info["asset_name"]
    download_url = release_info["download_url"]
    dest_path = tools_dir / asset_name
    tmp_path = tools_dir / f"{asset_name}.{os.getpid()}_{int(time.time() * 1000)}.tmp"

    dl_headers = {"User-Agent": "AutoMorphDownloader"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        dl_headers["Authorization"] = f"Bearer {token.strip()}"

    try:
        import proxy_utils
        proxy_utils.setup_proxy()
    except Exception:
        pass

    logger.info(f"[*] Downloading Morphe CLI from {download_url}...")
    try:
        with requests.get(download_url, headers=dl_headers, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=128 * 1024):
                    if chunk:
                        f.write(chunk)

        if not tmp_path.is_file() or tmp_path.stat().st_size < 1000:
            logger.error("Morphe CLI download produced an invalid or empty file.")
            tmp_path.unlink(missing_ok=True)
            return False

        tmp_path.replace(dest_path)
        logger.info(f" └── [✓] Download complete: {dest_path.name} ({dest_path.stat().st_size:,} bytes)")
    except Exception as e:
        logger.error(f"Failed to download Morphe CLI ({asset_name}): {e}")
        tmp_path.unlink(missing_ok=True)
        return False

    # Link / copy to tools/morphe.jar
    morphe_jar = tools_dir / "morphe.jar"
    morphe_jar.unlink(missing_ok=True)
    try:
        # Relative symlink inside tools/ directory
        morphe_jar.symlink_to(dest_path.name)
        logger.info(f" └── [✓] Symlinked {morphe_jar.name} -> {dest_path.name}")
    except OSError:
        try:
            shutil.copy2(dest_path, morphe_jar)
            logger.info(f" └── [✓] Copied {dest_path.name} -> {morphe_jar.name}")
        except Exception as e:
            logger.error(f"Failed to link/copy to {morphe_jar.name}: {e}")
            return False

    # Clean up older jars in tools/
    for old_jar in tools_dir.glob("morphe-desktop*.jar"):
        if old_jar != dest_path and old_jar.is_file():
            try:
                old_jar.unlink(missing_ok=True)
                logger.info(f" └── Removed older Morphe CLI jar: {old_jar.name}")
            except Exception as e:
                logger.warning(f"Could not remove old jar {old_jar.name}: {e}")

    # Write state file
    state_file = state_dir / "morphe_cli.json"
    state_data = {
        "version": release_info["version"],
        "tag_name": release_info.get("tag_name") or f"v{release_info['version']}",
        "asset_name": asset_name,
        "updated_at": datetime.now().isoformat(),
    }
    try:
        with state_file.open("w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not write {state_file.name}: {e}")

    return True


def update_morphe_cli(project_root: Path | None = None, force: bool = False) -> dict[str, Any]:
    """
    Checks GitHub for the latest Morphe CLI release and updates if a newer version is available
    or if the CLI jar is missing.
    """
    root = project_root or PROJECT_ROOT
    current_ver = get_installed_cli_version(root)
    morphe_jar = (root / "tools" / "morphe.jar")
    jar_missing = not morphe_jar.is_file()

    latest_info = get_latest_release_info()

    if not latest_info:
        if not jar_missing:
            logger.info(f"[*] Morphe CLI: GitHub API unavailable, continuing with installed version {current_ver}.")
            return {
                "updated": False,
                "status": "up_to_date",
                "version": current_ver,
                "message": f"Using installed version {current_ver} (GitHub check skipped)",
            }
        else:
            logger.warning("[!] Morphe CLI is not installed and GitHub API release lookup failed. Trying fallback release download...")
            fallback_url = f"https://github.com/{MORPHE_REPO}/releases/download/v{DEFAULT_FALLBACK_VERSION}/morphe-desktop-{DEFAULT_FALLBACK_VERSION}-all.jar"
            fallback_info = {
                "tag_name": f"v{DEFAULT_FALLBACK_VERSION}",
                "version": DEFAULT_FALLBACK_VERSION,
                "asset_name": f"morphe-desktop-{DEFAULT_FALLBACK_VERSION}-all.jar",
                "download_url": fallback_url,
            }
            if download_and_install_cli(fallback_info, root):
                return {
                    "updated": True,
                    "status": "installed_fallback",
                    "version": DEFAULT_FALLBACK_VERSION,
                    "tag": f"v{DEFAULT_FALLBACK_VERSION}",
                }
            return {
                "updated": False,
                "status": "error",
                "version": None,
                "error": "Failed to check or download Morphe CLI",
            }

    latest_ver = latest_info["version"]
    latest_tag = latest_info["tag_name"]

    curr_parsed = parse_semver(current_ver)
    latest_parsed = parse_semver(latest_ver)

    needs_update = jar_missing or force or (latest_parsed > curr_parsed) or (current_ver is None)

    if needs_update:
        logger.info(f"[*] Updating Morphe CLI: current={current_ver or 'none'}, latest={latest_tag}")
        success = download_and_install_cli(latest_info, root)
        if success:
            logger.info(f"[✓] Morphe CLI successfully updated to {latest_tag}")
            return {
                "updated": True,
                "status": "updated",
                "version": latest_ver,
                "tag": latest_tag,
            }
        else:
            return {
                "updated": False,
                "status": "error",
                "version": current_ver,
                "error": f"Failed to download Morphe CLI {latest_tag}",
            }
    else:
        logger.info(f"[✓] Morphe CLI is already up to date ({current_ver})")
        return {
            "updated": False,
            "status": "up_to_date",
            "version": current_ver,
            "tag": latest_tag,
        }
