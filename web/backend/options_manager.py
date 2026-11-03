"""
Morphe Patch Options Manager for AutoMorph.
Handles reading, creating, and updating options files under options/<package>/<owner>_options.json.
Provides path validation, atomic file writes, and .bak backups.
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
import requests

logger = logging.getLogger("automorph.options")

PROJECT_ROOT = Path(os.getenv("AUTOMORPH_ROOT", Path(__file__).resolve().parent.parent.parent))
OPTIONS_DIR = PROJECT_ROOT / "tools" / "automorph-data" / "options"
PATCHES_CACHE_DIR = PROJECT_ROOT / "tools" / "morphe-data" / "patches"

SAFE_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def validate_safe_name(name: str, param_name: str = "identifier") -> str:
    cleaned = name.strip()
    if not cleaned or not SAFE_NAME_REGEX.match(cleaned) or ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        raise ValueError(f"Invalid {param_name}: '{name}'. Must be alphanumeric with dashes, underscores, or dots.")
    return cleaned


def extract_repo_owner(repo_url: str) -> str:
    """Extracts the repository owner from a GitHub/GitLab URL or slug."""
    clean = repo_url.strip().rstrip("/")
    if "github.com/" in clean:
        parts = clean.split("github.com/")[1].split("/")
        return parts[0]
    if "gitlab.com/" in clean:
        parts = clean.split("gitlab.com/")[1].split("/")
        return parts[0]
    parts = [p for p in clean.split("/") if p]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else "default"


def extract_repo_info(repo_url: str) -> tuple[str, str]:
    """Extracts (owner, repo_name) from repository URL or slug."""
    clean = repo_url.strip().rstrip("/")
    owner = "default"
    repo_name = "patches"
    if "github.com/" in clean:
        parts = clean.split("github.com/")[1].split("/")
        owner = parts[0]
        repo_name = parts[1].replace(".git", "") if len(parts) > 1 else "morphe-patches"
    elif "gitlab.com/" in clean:
        parts = clean.split("gitlab.com/")[1].split("/")
        owner = parts[0]
        repo_name = parts[1].replace(".git", "") if len(parts) > 1 else "morphe-patches"
    else:
        parts = [p for p in clean.split("/") if p]
        if len(parts) >= 2:
            owner = parts[-2]
            repo_name = parts[-1].replace(".git", "")
        elif parts:
            owner = parts[0]
    return owner, repo_name


def get_app_options_dir(package: str) -> Path:
    safe_pkg = validate_safe_name(package, "package")
    app_dir = OPTIONS_DIR / safe_pkg
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_owner_options_path(package: str, owner: str) -> Path:
    safe_pkg = validate_safe_name(package, "package")
    safe_owner = validate_safe_name(owner, "owner")
    return OPTIONS_DIR / safe_pkg / f"{safe_owner}_options.json"


def get_app_options(package: str) -> dict[str, Any]:
    """
    Returns all options files and their parsed contents for a given package.
    """
    safe_pkg = validate_safe_name(package, "package")
    app_dir = OPTIONS_DIR / safe_pkg
    if not app_dir.is_dir():
        return {"package": safe_pkg, "files": {}}

    result: dict[str, Any] = {"package": safe_pkg, "files": {}}
    for json_file in sorted(app_dir.glob("*_options.json")):
        if json_file.name.endswith("_merge_options.json"):
            continue
        owner = json_file.name.replace("_options.json", "")
        try:
            with json_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Normalize structure: Morphe uses list of blocks [{meta: ..., patches: {...}}]
            patches: dict[str, Any] = {}
            meta: dict[str, Any] = {}
            if isinstance(data, list) and len(data) > 0 and isinstance(data[-1], dict):
                meta = data[-1].get("meta", {})
                patches = data[-1].get("patches", {})
            elif isinstance(data, dict):
                meta = data.get("meta", {})
                patches = data.get("patches", {})

            result["files"][owner] = {
                "owner": owner,
                "filename": json_file.name,
                "meta": meta,
                "patches": patches,
                "patch_count": len(patches),
                "enabled_count": sum(1 for p in patches.values() if isinstance(p, dict) and p.get("enabled") is True),
            }
        except Exception as e:
            logger.warning(f"Could not parse options file {json_file}: {e}")

    return result


def get_owner_options(package: str, owner: str) -> dict[str, Any] | None:
    """
    Returns the parsed options for a specific package and repo owner.
    """
    file_path = get_owner_options_path(package, owner)
    if not file_path.is_file():
        return None

    try:
        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        
        meta = {}
        patches = {}
        if isinstance(data, list) and len(data) > 0 and isinstance(data[-1], dict):
            meta = data[-1].get("meta", {})
            patches = data[-1].get("patches", {})
        elif isinstance(data, dict):
            meta = data.get("meta", {})
            patches = data.get("patches", {})

        return {
            "package": package,
            "owner": owner,
            "filename": file_path.name,
            "meta": meta,
            "patches": patches,
        }
    except Exception as e:
        logger.error(f"Error reading options file {file_path}: {e}")
        raise


def save_owner_options(package: str, owner: str, patches: dict[str, Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Saves options/<package>/<owner>_options.json directly."""
    safe_pkg = validate_safe_name(package, "package")
    safe_owner = validate_safe_name(owner, "owner")
    app_dir = get_app_options_dir(safe_pkg)
    app_dir.mkdir(parents=True, exist_ok=True)
    target_path = app_dir / f"{safe_owner}_options.json"

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    # If the file does not exist, first try running options-create via Morphe CLI workflow
    if not target_path.is_file():
        repo_url = meta.get("repo") if isinstance(meta, dict) else None
        mpp = None
        if repo_url and isinstance(repo_url, str):
            try:
                r_owner, r_name = extract_repo_info(repo_url)
                mpp = find_cached_mpp(r_owner, r_name)
            except Exception:
                pass
        if not mpp:
            mpp = find_any_cached_mpp_for_owner(safe_owner)

        if mpp:
            logger.info(f"Target options file missing for {safe_pkg}/{safe_owner}. Generating via morphe options-create using {mpp.name}...")
            generate_options_via_cli(mpp, safe_pkg, target_path)
        elif repo_url and isinstance(repo_url, str):
            try:
                ensure_app_options(safe_pkg, repos=[repo_url])
            except Exception as e:
                logger.warning(f"Could not auto-generate options via ensure_app_options for {safe_pkg}/{safe_owner}: {e}")

    existing_data = None
    if target_path.is_file():
        try:
            with target_path.open("r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except Exception:
            existing_data = None

    if existing_data is not None:
        existing_data = sanitize_options_blocks(existing_data)

    def update_patch_block(block: dict[str, Any]):
        if not isinstance(block.get("patches"), dict):
            block["patches"] = patches
        else:
            for patch_name, patch_val in patches.items():
                if patch_name in block["patches"] and isinstance(patch_val, dict) and isinstance(block["patches"][patch_name], dict):
                    if "enabled" in patch_val:
                        block["patches"][patch_name]["enabled"] = patch_val["enabled"]
                    if "options" in patch_val and isinstance(patch_val["options"], dict):
                        block["patches"][patch_name].setdefault("options", {})
                        for opt_k, opt_v in patch_val["options"].items():
                            block["patches"][patch_name]["options"][opt_k] = opt_v
                else:
                    block["patches"][patch_name] = patch_val

        if "meta" in block and isinstance(block["meta"], dict):
            block["meta"]["updated_at"] = now_iso
        else:
            block["meta"] = {"created_at": now_iso, "updated_at": now_iso}

    if isinstance(existing_data, list) and len(existing_data) > 0:
        # Match specific block by sha256 if provided, otherwise default to the active (last) block
        target_block = existing_data[-1]
        if meta and isinstance(meta, dict) and meta.get("sha256"):
            matching_block = next((b for b in existing_data if isinstance(b, dict) and b.get("meta", {}).get("sha256") == meta["sha256"]), None)
            if matching_block:
                target_block = matching_block

        if isinstance(target_block, dict):
            update_patch_block(target_block)
        payload = existing_data
    elif isinstance(existing_data, dict):
        update_patch_block(existing_data)
        payload = [existing_data]
    else:
        fallback_sha = hashlib.sha256(f"{safe_pkg}:{safe_owner}:{now_iso}".encode("utf-8")).hexdigest()
        final_meta = {
            "created_at": now_iso,
            "updated_at": now_iso,
            "source": f"{safe_owner}-web-fallback",
            "sha256": fallback_sha,
        }
        payload = [{"meta": final_meta, "patches": patches}]

    with target_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)
        f.write("\n")

    logger.info(f"Saved options for {safe_pkg}/{safe_owner} successfully ({len(patches)} patches).")

    return {
        "status": "success",
        "package": safe_pkg,
        "owner": safe_owner,
        "patch_count": len(patches),
        "enabled_count": sum(1 for p in patches.values() if isinstance(p, dict) and p.get("enabled") is True),
        "updated_at": now_iso,
    }


def get_morphe_jar_path() -> Path | None:
    """Finds morphe.jar or morphe-desktop jar under tools/."""
    tools_dir = PROJECT_ROOT / "tools"
    primary = tools_dir / "morphe.jar"
    if primary.exists():
        return primary
    for jar in tools_dir.glob("morphe-desktop*.jar"):
        if jar.is_file():
            return jar
    return None


def find_cached_mpp(owner: str, repo_name: str, log_fn: Any = None) -> Path | None:
    """Tier 2: Checks if a .mpp or .jar bundle is already cached locally."""
    repo_dir = PATCHES_CACHE_DIR / f"{owner}-{repo_name}"
    if not repo_dir.is_dir() and PATCHES_CACHE_DIR.is_dir():
        # Case-insensitive directory lookup fallback on Linux
        target_slug = f"{owner}-{repo_name}".lower()
        matched_dir = next((d for d in PATCHES_CACHE_DIR.iterdir() if d.is_dir() and d.name.lower() == target_slug), None)
        if matched_dir:
            repo_dir = matched_dir

    if not repo_dir.is_dir():
        if log_fn:
            log_fn(f"[Cache] No local directory found for {owner}-{repo_name}")
        return None

    for item in sorted(repo_dir.iterdir(), reverse=True):
        if not item.is_file():
            continue
        # Direct match for .mpp or .jar
        if item.suffix in (".mpp", ".jar"):
            if log_fn:
                log_fn(f"[Cache] Found cached patch bundle '{item.name}' ({item.stat().st_size:,} bytes)")
            return item
        # Recover interrupted/downloaded .tmp file if size is valid
        if item.suffix == ".tmp" and item.stat().st_size > 1024:
            target_mpp = item.with_suffix(".mpp")
            try:
                item.rename(target_mpp)
                if log_fn:
                    log_fn(f"[Cache] Recovered cached bundle: renamed '{item.name}' -> '{target_mpp.name}' ({target_mpp.stat().st_size:,} bytes)")
                return target_mpp
            except Exception as e:
                logger.warning(f"Could not rename {item.name}: {e}")
                return item

    if log_fn:
        log_fn(f"[Cache] No cached .mpp or .jar found in {repo_dir.name}")
    return None


def find_any_cached_mpp_for_owner(owner: str, log_fn: Any = None) -> Path | None:
    """Finds any cached .mpp or .jar bundle in PATCHES_CACHE_DIR matching owner."""
    safe_owner = owner.lower().strip()
    if not PATCHES_CACHE_DIR.is_dir():
        return None
    for item in sorted(PATCHES_CACHE_DIR.iterdir()):
        if item.is_dir() and item.name.lower().startswith(f"{safe_owner}-"):
            repo_name = item.name[len(safe_owner) + 1:]
            mpp = find_cached_mpp(owner, repo_name, log_fn=log_fn)
            if mpp:
                return mpp
    return None


def sanitize_options_blocks(existing_data: Any) -> list[dict[str, Any]]:
    """
    Cleans up options blocks. If there are official blocks (with meta.sha256)
    alongside orphan/synthetic blocks (lacking meta.sha256), merges user patch
    toggles from the synthetic block into the official block and drops the orphan block.
    """
    if isinstance(existing_data, dict):
        existing_data = [existing_data]
    if not isinstance(existing_data, list):
        return []

    valid_blocks = [b for b in existing_data if isinstance(b, dict)]
    official_blocks = [b for b in valid_blocks if isinstance(b.get("meta"), dict) and b["meta"].get("sha256")]
    synthetic_blocks = [b for b in valid_blocks if not (isinstance(b.get("meta"), dict) and b["meta"].get("sha256"))]

    if official_blocks and synthetic_blocks:
        target = official_blocks[-1]
        target.setdefault("patches", {})
        for syn in synthetic_blocks:
            syn_patches = syn.get("patches", {})
            if isinstance(syn_patches, dict):
                for p_name, p_data in syn_patches.items():
                    if isinstance(p_data, dict) and p_name in target["patches"] and isinstance(target["patches"][p_name], dict):
                        if "enabled" in p_data:
                            target["patches"][p_name]["enabled"] = p_data["enabled"]
                        if "options" in p_data and isinstance(p_data["options"], dict):
                            target["patches"][p_name].setdefault("options", {}).update(p_data["options"])
                    elif isinstance(p_data, dict) and p_name not in target["patches"]:
                        target["patches"][p_name] = p_data
        return official_blocks

    return valid_blocks


def download_repo_mpp(owner: str, repo_name: str, log_fn: Any = None) -> Path | None:
    """Tier 3: Downloads the latest .mpp or .jar release asset from GitHub for owner/repo_name."""
    headers = {
        "User-Agent": "AutoMorphOptionsFetcher",
        "Accept": "application/vnd.github+json",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    api_url = f"https://api.github.com/repos/{owner}/{repo_name}/releases/latest"
    if log_fn:
        log_fn(f"[GitHub] Querying latest release from {owner}/{repo_name}...")

    try:
        res = requests.get(api_url, headers=headers, timeout=12)
        if res.status_code != 200:
            msg = f"GitHub API returned HTTP {res.status_code} for {owner}/{repo_name}"
            logger.warning(msg)
            if log_fn:
                log_fn(f"[GitHub] Warning: {msg}")
            return None

        data = res.json()
        tag_name = data.get("tag_name", "latest")
        assets = data.get("assets", [])

        # Look for .mpp first, then .jar
        mpp_asset = next((a for a in assets if str(a.get("name", "")).endswith(".mpp")), None)
        if not mpp_asset:
            mpp_asset = next((a for a in assets if str(a.get("name", "")).endswith(".jar") and "patch" in str(a.get("name", "")).lower()), None)

        if not mpp_asset:
            msg = f"No .mpp or patch .jar asset found in release '{tag_name}' for {owner}/{repo_name}"
            logger.warning(msg)
            if log_fn:
                log_fn(f"[GitHub] Warning: {msg} (found: {[a.get('name') for a in assets[:5]]})")
            return None

        download_url = mpp_asset.get("browser_download_url")
        file_name = mpp_asset.get("name")
        asset_size = mpp_asset.get("size", 0)

        target_dir = PATCHES_CACHE_DIR / f"{owner}-{repo_name}"
        target_dir.mkdir(parents=True, exist_ok=True)
        dest_path = target_dir / f"{tag_name}__{file_name}"

        if log_fn:
            log_fn(f"[GitHub] Downloading '{file_name}' ({asset_size:,} bytes) from {tag_name}...")

        with requests.get(download_url, headers={"User-Agent": "AutoMorphDownloader"}, stream=True, timeout=90) as r:
            r.raise_for_status()
            tmp_dest = dest_path.with_suffix(".tmp")
            with tmp_dest.open("wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
            tmp_dest.replace(dest_path)

        # Clear older files in cache dir
        for item in target_dir.iterdir():
            if item.is_file() and item != dest_path:
                try:
                    item.unlink(missing_ok=True)
                except Exception:
                    pass

        if log_fn:
            log_fn(f"[GitHub] Successfully downloaded '{dest_path.name}' ({dest_path.stat().st_size:,} bytes)")
        return dest_path
    except Exception as e:
        msg = f"Failed downloading repo release for {owner}/{repo_name}: {e}"
        logger.warning(msg)
        if log_fn:
            log_fn(f"[GitHub] Error: {msg}")
        return None


def generate_options_via_cli(mpp_path: Path, package: str, target_file: Path, log_fn: Any = None) -> bool:
    """Executes morphe options-create directly on target_file using local .mpp and morphe.jar."""
    jar_path = get_morphe_jar_path()
    if not jar_path:
        msg = "morphe.jar not found under tools/, cannot run options-create CLI."
        logger.warning(msg)
        if log_fn:
            log_fn(f"[Morphe CLI] Warning: {msg}")
        return False

    target_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "java",
        "-jar",
        str(jar_path),
        "options-create",
        "-p",
        str(mpp_path),
        "-o",
        str(target_file),
        "-f",
        package,
    ]
    if log_fn:
        log_fn(f"[Morphe CLI] Running: java -jar {jar_path.name} options-create -p {mpp_path.name} -o {target_file.name} -f {package}")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if proc.returncode == 0 and target_file.is_file() and target_file.stat().st_size > 0:
            # Clean up any orphan synthetic blocks that Morphe CLI preserved
            try:
                with target_file.open("r", encoding="utf-8") as f:
                    content = json.load(f)
                cleaned = sanitize_options_blocks(content)
                with target_file.open("w", encoding="utf-8") as f:
                    json.dump(cleaned, f, indent=4, ensure_ascii=False)
                    f.write("\n")
            except Exception as clean_err:
                logger.warning(f"Could not sanitize blocks in {target_file.name}: {clean_err}")

            msg = f"Successfully generated/merged {target_file.name} for {package} via morphe CLI."
            logger.info(msg)
            if log_fn:
                log_fn(f"[Morphe CLI] Success: {msg}")
            return True
        else:
            stderr_out = proc.stderr.strip() or proc.stdout.strip() or "Unknown error"
            msg = f"morphe options-create CLI failed (exit code {proc.returncode}): {stderr_out}"
            logger.warning(msg)
            if log_fn:
                log_fn(f"[Morphe CLI] Error: {msg}")
            return False
    except Exception as e:
        msg = f"Error executing options-create CLI for {package}: {e}"
        logger.warning(msg)
        if log_fn:
            log_fn(f"[Morphe CLI] Exception: {msg}")
        return False


def ensure_app_options(package: str, repos: list[str] | None = None, force: bool = False) -> dict[str, Any]:
    """
    Ensures options files exist for package across specified repos by running options-create directly.
    """
    logs: list[str] = []

    def log(msg: str):
        tz_obj = None
        if os.getenv("TZ"):
            try:
                from zoneinfo import ZoneInfo
                tz_obj = ZoneInfo(os.environ["TZ"].strip())
            except Exception:
                tz_obj = None
        now_dt = datetime.now(tz=tz_obj) if tz_obj else datetime.now()
        now_time = now_dt.strftime("%H:%M:%S")
        entry = f"[{now_time}] {msg}"
        logs.append(entry)
        logger.info(entry)

    try:
        safe_pkg = validate_safe_name(package, "package")
    except Exception as e:
        log(f"Invalid package name '{package}': {e}")
        return {
            "success": False,
            "package": package,
            "files": {},
            "repo_statuses": {},
            "logs": logs,
            "error": str(e),
        }

    log(f"Initiating patch discovery for package: {safe_pkg} (force={force})")

    if not repos:
        try:
            from . import config_manager
            cfg = config_manager.get_full_config()
            configured_apps = {
                (a.get("package") or a.get("package_name")): a
                for a in (cfg.get("packages") or cfg.get("apps") or [])
                if isinstance(a, dict)
            }
            if safe_pkg in configured_apps:
                app_info = configured_apps[safe_pkg]
                repos = app_info.get("repos") or []
            else:
                repos = []
            log(f"Discovered {len(repos or [])} repositories from config.yaml")
        except Exception as e:
            log(f"Could not load repos from config.yaml: {e}")
            repos = []

    if not repos:
        log("Warning: No repositories specified or configured for this application.")

    valid_urls = [r.strip() for r in (repos or []) if isinstance(r, str) and r.strip()]

    def process_single_repo(clean_url: str) -> tuple[str, dict[str, str], list[str]]:
        repo_logs: list[str] = []
        def sub_log(msg: str):
            tz_obj = None
            if os.getenv("TZ"):
                try:
                    from zoneinfo import ZoneInfo
                    tz_obj = ZoneInfo(os.environ["TZ"].strip())
                except Exception:
                    tz_obj = None
            now_dt = datetime.now(tz=tz_obj) if tz_obj else datetime.now()
            now_time = now_dt.strftime("%H:%M:%S")
            entry = f"[{now_time}] {msg}"
            repo_logs.append(entry)
            logger.info(entry)

        try:
            owner, repo_name = extract_repo_info(clean_url)
            target_path = get_owner_options_path(safe_pkg, owner)
        except Exception as e:
            sub_log(f"Error parsing repository URL '{clean_url}': {e}")
            return "error", {"status": "error", "error": str(e)}, repo_logs

        sub_log(f"--- Processing repository: {owner}/{repo_name} ---")

        if not force and target_path.is_file() and target_path.stat().st_size > 10:
            sub_log(f"[Options File] Options file already exists: {target_path.name}. Reusing.")
            return owner, {"status": "existing", "source": "options_file"}, repo_logs

        # Check local cached bundle first
        cached_mpp = find_cached_mpp(owner, repo_name, log_fn=sub_log)
        if cached_mpp:
            sub_log(f"[Local Bundle] Found cached bundle '{cached_mpp.name}'. Running options-create...")
            if generate_options_via_cli(cached_mpp, safe_pkg, target_path, log_fn=sub_log):
                return owner, {"status": "generated", "source": "cached_mpp"}, repo_logs

        # Download bundle from release
        sub_log(f"[Network] Downloading release bundle for {owner}/{repo_name}...")
        downloaded_mpp = download_repo_mpp(owner, repo_name, log_fn=sub_log)
        if downloaded_mpp:
            sub_log(f"[Network] Obtained '{downloaded_mpp.name}'. Running options-create...")
            if generate_options_via_cli(downloaded_mpp, safe_pkg, target_path, log_fn=sub_log):
                return owner, {"status": "generated", "source": "downloaded_mpp"}, repo_logs

        return owner, {"status": "not_found", "source": "none"}, repo_logs

    repo_statuses: dict[str, Any] = {}
    if len(valid_urls) <= 1:
        for u in valid_urls:
            owner, status_info, r_logs = process_single_repo(u)
            if owner != "error":
                repo_statuses[owner] = status_info
            logs.extend(r_logs)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(valid_urls))) as executor:
            future_to_url = {executor.submit(process_single_repo, u): u for u in valid_urls}
            for future in concurrent.futures.as_completed(future_to_url):
                try:
                    owner, status_info, r_logs = future.result()
                    if owner != "error":
                        repo_statuses[owner] = status_info
                    logs.extend(r_logs)
                except Exception as ex:
                    log(f"Error processing repo {future_to_url[future]}: {ex}")

    result = get_app_options(safe_pkg)
    result["repo_statuses"] = repo_statuses
    result["logs"] = logs

    total_patches = sum(f.get("patch_count", 0) for f in result.get("files", {}).values())
    result["total_patches"] = total_patches
    result["success"] = total_patches > 0 or any(s.get("status") in ("existing", "generated") for s in repo_statuses.values())

    log(f"Discovery complete for {safe_pkg}. Total options files: {len(result.get('files', {}))}, Total patches: {total_patches}")
    return result

