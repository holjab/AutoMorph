"""
Safe YAML configuration manager for AutoMorph using ruamel.yaml.
Preserves existing comments, whitespace, and formatting across round-trip edits.
Supports parsing both active and disabled (commented-out) apps, app toggling,
and attaching patch audit results from apps/patched/<package>/<version>/result.json.
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
from typing import Any
from dotenv import load_dotenv
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

logger = logging.getLogger("automorph.config")

PROJECT_ROOT = Path(os.getenv("AUTOMORPH_ROOT", Path(__file__).resolve().parent.parent.parent))
if (PROJECT_ROOT / ".env").is_file():
    load_dotenv(PROJECT_ROOT / ".env")

CONFIG_PATH = PROJECT_ROOT / "tools" / "automorph-data" / "config.yaml"
BACKUP_PATH = CONFIG_PATH.parent / "config.yaml.bak"
PATCHED_DIR = PROJECT_ROOT / "apps" / "patched"
STATE_DIR = PROJECT_ROOT / "tools" / "automorph-data" / "state"
PATCHED_JSON_PATH = STATE_DIR / "patched.json"

DEFAULTS_DIR = PROJECT_ROOT / "defaults"
DEFAULT_CONFIG_PATH = DEFAULTS_DIR / "config.yaml"
DEFAULT_SOURCES_PATH = DEFAULTS_DIR / "app_sources.yaml"
SOURCES_PATH = CONFIG_PATH.parent / "app_sources.yaml"


def _extract_repo_owner(repo_url: str) -> str:
    """Extracts a clean repository owner / vendor identifier from a repo URL."""
    if not repo_url or not isinstance(repo_url, str):
        return ""
    repo_url = repo_url.strip()
    match = re.search(r"https?://[^/]+/([^/]+)/", repo_url)
    if match:
        return match.group(1)
    parts = repo_url.strip("/").split("/")
    if len(parts) >= 2:
        return parts[-2]
    return repo_url


def load_patched_registry() -> dict[str, Any]:
    """
    Loads tools/automorph-data/state/patched.json containing the registry
    of successfully patched packages, versions, and applied patches.
    """
    target = PATCHED_JSON_PATH
    if not target.is_file():
        alt = PROJECT_ROOT / "tools" / "state" / "patched.json"
        if alt.is_file():
            target = alt
        else:
            return {}
    try:
        with target.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.debug(f"Error loading patched.json: {e}")
        return {}


def ensure_config_exists() -> Path:
    """
    Ensures tools/automorph-data directory, config.yaml, and app_sources.yaml exist.
    If missing or empty, auto-seeds them from the defaults/ template directory.
    """
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 1. Seed config.yaml if missing or 0 bytes
    if not CONFIG_PATH.is_file() or CONFIG_PATH.stat().st_size == 0:
        if DEFAULT_CONFIG_PATH.is_file():
            shutil.copy2(DEFAULT_CONFIG_PATH, CONFIG_PATH)
            logger.info(f"Initialized config.yaml from {DEFAULT_CONFIG_PATH}")
        else:
            with CONFIG_PATH.open("w", encoding="utf-8") as f:
                f.write("packages:\n")
            logger.info("Initialized minimal config.yaml")

    # 2. Seed app_sources.yaml if missing or 0 bytes
    if not SOURCES_PATH.is_file() or SOURCES_PATH.stat().st_size == 0:
        if DEFAULT_SOURCES_PATH.is_file():
            shutil.copy2(DEFAULT_SOURCES_PATH, SOURCES_PATH)
            logger.info(f"Initialized app_sources.yaml from {DEFAULT_SOURCES_PATH}")

    return CONFIG_PATH


def get_current_timezone(tz_name: str | None = None):
    """Returns a ZoneInfo object for the specified or configured timezone, falling back to None."""
    if tz_name is None:
        tz_name = os.getenv("TZ", "Etc/UTC")
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(str(tz_name).strip())
    except Exception:
        return None


def apply_timezone(tz_name: str | None = None) -> str:
    """
    Applies the configured or given timezone to os.environ['TZ'] and invokes time.tzset().
    Validates timezone name using zoneinfo.ZoneInfo, falling back to Etc/UTC if invalid.
    Returns the resolved active timezone string.
    """
    if tz_name is None:
        try:
            _, data = load_config_raw()
            tz_val = data.get("timezone")
        except Exception:
            tz_val = None
        if tz_val is None:
            tz_val = os.getenv("TZ", "Etc/UTC")
        tz_name = str(tz_val).strip()

    resolved_tz = "Etc/UTC"
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(tz_name)
            resolved_tz = tz_name
        except Exception as e:
            logger.warning(f"Invalid timezone '{tz_name}', falling back to Etc/UTC: {e}")
            resolved_tz = "Etc/UTC"

    os.environ["TZ"] = resolved_tz
    if hasattr(time, "tzset"):
        try:
            time.tzset()
            logger.info(f"System timezone successfully set to: {resolved_tz}")
        except Exception as e:
            logger.warning(f"Failed to execute time.tzset() for {resolved_tz}: {e}")

    return resolved_tz


def get_yaml_instance() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def create_backup() -> Path | None:
    if not CONFIG_PATH.is_file():
        return None
    try:
        shutil.copy2(CONFIG_PATH, BACKUP_PATH)
        logger.info(f"Created config backup at {BACKUP_PATH}")
        return BACKUP_PATH
    except Exception as e:
        logger.error(f"Failed to create config backup: {e}")
        return None


def load_config_raw() -> tuple[YAML, CommentedMap]:
    ensure_config_exists()
    yaml = get_yaml_instance()
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.load(f)
    if data is None:
        data = CommentedMap()
    return yaml, data


def save_config_raw(yaml: YAML, data: CommentedMap) -> None:
    create_backup()
    # Direct in-place write preserves Linux Docker single-file inode bind mounts
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)
    logger.info("Saved changes to config.yaml safely.")


def save_raw_lines(lines: list[str]) -> None:
    create_backup()
    content = "".join(lines)
    # Direct in-place write preserves Linux Docker single-file inode bind mounts
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        f.write(content)
    logger.info("Saved raw lines to config.yaml safely.")

def _extract_patch_details(item: Any) -> tuple[str, str]:
    """Extracts a normalized (patch_name, reason) tuple from raw patch result entry."""
    if isinstance(item, str):
        return item.strip(), "Patch failed during execution"
    if not isinstance(item, dict):
        return "Patch", str(item)

    raw_name = (
        item.get("name")
        or item.get("patch")
        or item.get("patchName")
        or item.get("patch_name")
        or item.get("title")
        or item.get("id")
    )
    if isinstance(raw_name, dict):
        raw_name = raw_name.get("name") or raw_name.get("title")

    name_str = str(raw_name).strip() if raw_name else ""
    reason_str = str(
        item.get("reason")
        or item.get("error")
        or item.get("message")
        or item.get("exception")
        or "Patch failed during execution"
    ).strip()

    # If patch name is generic, deduce it from the fingerprint/class name
    if not name_str or name_str.lower() in ("patch", "unknown patch", "unknown", "failed patch"):
        fp_match = re.search(r"([a-zA-Z0-9_]+)Fingerprint", reason_str)
        if fp_match:
            name_str = re.sub(r"([a-z])([A-Z])", r"\1 \2", fp_match.group(1))
        else:
            patch_match = re.search(r"patches\.[a-zA-Z0-9_.]+\.([a-zA-Z0-9_]+)(?:Patch)?", reason_str)
            if patch_match:
                name_str = re.sub(r"([a-z])([A-Z])", r"\1 \2", patch_match.group(1).removesuffix("Patch"))
            else:
                name_str = "Failed Patch"

    return name_str, reason_str


def get_last_patch_result(package: str, patched_registry: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """
    Retrieves latest patch status for a package.
    Primary Fast Path: Uses tools/automorph-data/state/patched.json for successfully built apps,
    eliminating redundant options parsing, result.json inspection, and disk crawls.
    Fallback Path: Reads package_runs/<package>.json, last_run_report.json, and result.json on disk
    for failure diagnostics or unindexed legacy builds.
    """
    # 1. Check for run report: package-specific report first, then last_run_report.json
    pkg_run_file = PROJECT_ROOT / "tools" / "automorph-data" / "state" / "package_runs" / f"{package}.json"
    last_report_file = PROJECT_ROOT / "tools" / "automorph-data" / "state" / "logs" / "last_run_report.json"

    report_timestamp = 0.0
    report_attempts: list[dict[str, Any]] = []
    report_fail_info: dict[str, Any] | None = None
    report_succ_info: dict[str, Any] | None = None
    winning_strategy = None
    winning_attempt_idx = None

    # Load from package_runs/<pkg>.json if available
    if pkg_run_file.is_file():
        try:
            with pkg_run_file.open("r", encoding="utf-8") as f:
                pkg_data = json.load(f)
            report_timestamp = float(pkg_data.get("timestamp") or 0.0)
            if pkg_data.get("status") == "success":
                report_succ_info = pkg_data
            else:
                report_fail_info = pkg_data
            report_attempts = list(pkg_data.get("attempts") or [])
            winning_strategy = pkg_data.get("strategy")
            winning_attempt_idx = pkg_data.get("attempt")
        except Exception as e:
            logger.debug(f"Error reading {pkg_run_file}: {e}")

    # Fallback to last_run_report.json if pkg_run_file was not found or if last_run_report is newer
    if last_report_file.is_file():
        try:
            with last_report_file.open("r", encoding="utf-8") as f:
                rep_data = json.load(f)
            lr_timestamp = float(rep_data.get("timestamp") or 0.0)
            failures_map = rep_data.get("failures") or {}
            success_map = rep_data.get("success") or {}
            if lr_timestamp >= report_timestamp:
                if package in failures_map:
                    report_timestamp = lr_timestamp
                    report_fail_info = failures_map[package]
                    report_succ_info = None
                    report_attempts = list(report_fail_info.get("attempts") or [])
                    winning_strategy = None
                    winning_attempt_idx = None
                elif package in success_map:
                    report_timestamp = lr_timestamp
                    report_succ_info = success_map[package]
                    report_fail_info = None
                    report_attempts = list(report_succ_info.get("attempts") or [])
                    winning_strategy = report_succ_info.get("strategy")
                    winning_attempt_idx = report_succ_info.get("attempt")
        except Exception as e:
            logger.debug(f"Error reading {last_report_file}: {e}")

    # 2. Fast Path: Check tools/automorph-data/state/patched.json
    if patched_registry is None:
        patched_registry = load_patched_registry()

    pkg_entry = patched_registry.get(package)
    valid_versions: list[tuple[float, str, dict[str, Any], Path]] = []
    if isinstance(pkg_entry, dict):
        for ver_name, ver_info in pkg_entry.items():
            if not isinstance(ver_info, dict):
                continue
            loc_str = str(ver_info.get("location") or "")
            loc = Path(loc_str) if loc_str else None
            if not loc or not loc.is_file():
                # Fallback to standard app location if path moved
                candidate = PATCHED_DIR / package / ver_name / f"{package}_{ver_name}_patched.apk"
                if candidate.is_file():
                    loc = candidate
            if loc and loc.is_file():
                t_str = ver_info.get("time", "")
                t_val = 0.0
                if t_str:
                    try:
                        t_val = datetime.strptime(t_str, "%Y-%m-%d %H:%M:%S").timestamp()
                    except Exception:
                        t_val = loc.stat().st_mtime
                else:
                    t_val = loc.stat().st_mtime
                valid_versions.append((t_val, ver_name, ver_info, loc))

    if valid_versions:
        valid_versions.sort(key=lambda x: x[0], reverse=True)
        best_time, best_ver, best_info, best_loc = valid_versions[0]
        has_newer_failure = (report_fail_info is not None and report_timestamp > best_time)

        # If there is no more recent failure, return the fast-path result directly!
        if not has_newer_failure:
            applied_dict = best_info.get("applied_patches") or {}
            patch_owners = {}
            norm_applied = []
            for p_name, r_url in applied_dict.items():
                owner = _extract_repo_owner(r_url)
                patch_owners[p_name] = owner
                norm_applied.append({
                    "name": str(p_name).strip(),
                    "owner": owner,
                    "options": None,
                })

            strat = best_info.get("strat", 1)
            strat_num = int(strat) if str(strat).isdigit() else 1
            strat_name = "Strategy 1 (All Repos)" if strat_num == 1 else "Strategy 2 (Version-Compatible)"
            attempt_num = best_info.get("attempt", 1)
            attempt_val = int(attempt_num) if str(attempt_num).isdigit() else 1
            repos = [str(r).strip() for r in (best_info.get("repos") or []) if str(r).strip()]

            detailed_attempts = []
            if report_attempts:
                for idx, att in enumerate(report_attempts):
                    att_ver = str(att.get("version") or att.get("target_version") or best_ver)
                    att_strat = att.get("strategy") or att.get("run") or 1
                    att_strat_name = att.get("strategy_name") or ("Strategy 1 (All Repos)" if att_strat == 1 else "Strategy 2 (Version-Compatible)")
                    att_status = att.get("status") or ("success" if idx == len(report_attempts) - 1 else "failed")
                    detailed_attempts.append({
                        "attempt_number": idx + 1,
                        "version": att_ver,
                        "strategy": att_strat,
                        "strategy_name": att_strat_name,
                        "stage": att.get("stage") or "Morphe Patching",
                        "reason": att.get("reason") or "",
                        "status": att_status,
                        "repos": [str(r).strip() for r in (att.get("repos") or repos) if str(r).strip()],
                        "applied_patches": norm_applied if att_status == "success" else [],
                        "failed_patches": [],
                        "applied_count": len(norm_applied) if att_status == "success" else 0,
                        "failed_count": 0,
                    })
            else:
                detailed_attempts.append({
                    "attempt_number": attempt_val,
                    "version": best_ver,
                    "strategy": strat_num,
                    "strategy_name": strat_name,
                    "stage": "Morphe Patching",
                    "reason": "",
                    "status": "success",
                    "repos": repos,
                    "applied_patches": norm_applied,
                    "failed_patches": [],
                    "applied_count": len(norm_applied),
                    "failed_count": 0,
                })

            effective_time = max(best_time, report_timestamp)
            tz = get_current_timezone()
            dt = datetime.fromtimestamp(effective_time, tz=tz) if effective_time else datetime.now(tz=tz)

            return {
                "version": best_ver,
                "status": "success",
                "is_pipeline_failure": False,
                "failure_stage": "Pipeline",
                "failure_reason": "",
                "total_attempts": len(detailed_attempts),
                "winning_strategy": strat_num,
                "winning_strategy_name": strat_name,
                "winning_attempt_number": attempt_val,
                "download_url": f"/files/{package}/{best_ver}/{best_loc.name}",
                "apk_name": best_loc.name,
                "attempts": detailed_attempts,
                "patch_owners": patch_owners,
                "applied_patches": norm_applied,
                "failed_patches": [],
                "applied_count": len(norm_applied),
                "failed_count": 0,
                "sha256": best_info.get("sha256"),
                "timestamp": int(effective_time),
                "timestamp_formatted": dt.strftime("%b %d, %H:%M"),
            }

    # 3. Fallback Path: Build patch -> repo owner map from options/<package>/*.json
    patch_owners: dict[str, str] = {}
    options_app_dir = PROJECT_ROOT / "tools" / "automorph-data" / "options" / package
    if options_app_dir.is_dir():
        for opt_file in options_app_dir.glob("*_options.json"):
            owner = opt_file.name.replace("_options.json", "")
            try:
                with opt_file.open("r", encoding="utf-8") as f:
                    opt_data = json.load(f)
                patches_dict = {}
                if isinstance(opt_data, list) and opt_data and isinstance(opt_data[-1], dict):
                    patches_dict = opt_data[-1].get("patches", {})
                elif isinstance(opt_data, dict):
                    patches_dict = opt_data.get("patches", {})
                for p_name in patches_dict.keys():
                    patch_owners[p_name] = owner
            except Exception:
                pass

    # 3. Read all version result.json files on disk
    pkg_dir = PATCHED_DIR / package
    version_results: dict[str, dict[str, Any]] = {}
    candidate_results: list[tuple[float, str, Path]] = []

    if pkg_dir.is_dir():
        try:
            for ver_entry in pkg_dir.iterdir():
                if ver_entry.is_dir():
                    res_file = ver_entry / "result.json"
                    apk_file = ver_entry / f"{package}_{ver_entry.name}_patched.apk"
                    mtime = 0.0
                    if res_file.is_file():
                        mtime = res_file.stat().st_mtime
                    elif apk_file.is_file():
                        mtime = apk_file.stat().st_mtime

                    if mtime > 0.0:
                        candidate_results.append((mtime, ver_entry.name, res_file))

                    if res_file.is_file():
                        try:
                            with res_file.open("r", encoding="utf-8") as f:
                                rdata = json.load(f)

                            raw_applied = rdata.get("appliedPatches") or []
                            raw_failed = rdata.get("failedPatches") or []
                            steps = rdata.get("stepResults") or rdata.get("patchingSteps") or []
                            ver = ver_entry.name
                            pkg_ver = str(rdata.get("packageVersion") or ver_entry.name)

                            norm_applied = []
                            for p in raw_applied:
                                if isinstance(p, dict):
                                    pname = p.get("name") or p.get("patch") or p.get("patchName") or p.get("title") or "Patch"
                                    if isinstance(pname, dict):
                                        pname = pname.get("name") or pname.get("title")
                                    norm_applied.append({
                                        "name": str(pname).strip(),
                                        "options": p.get("options") or None,
                                        "owner": patch_owners.get(str(pname).strip()) or ""
                                    })
                                elif isinstance(p, str) and p.strip():
                                    norm_applied.append({
                                        "name": p.strip(),
                                        "options": None,
                                        "owner": patch_owners.get(p.strip()) or ""
                                    })

                            norm_failed = []
                            for p in raw_failed:
                                pname, preason = _extract_patch_details(p)
                                norm_failed.append({
                                    "name": pname,
                                    "reason": preason,
                                    "owner": patch_owners.get(pname) or ""
                                })

                            steps_succeeded = all(s.get("success", False) for s in steps) if steps else True
                            has_applied = len(norm_applied) > 0
                            has_failed = len(norm_failed) > 0

                            if not steps_succeeded or (not has_applied and has_failed):
                                vstatus = "failed"
                            elif has_applied and has_failed:
                                vstatus = "partial"
                            else:
                                vstatus = "success"

                            v_entry = {
                                "version": ver,
                                "package_version": pkg_ver,
                                "status": vstatus,
                                "applied_patches": norm_applied,
                                "failed_patches": norm_failed,
                                "applied_count": len(norm_applied),
                                "failed_count": len(norm_failed),
                                "steps": steps,
                                "mtime": mtime,
                            }
                            version_results[ver] = v_entry
                            if pkg_ver and pkg_ver != ver:
                                version_results[pkg_ver] = v_entry
                        except Exception as e:
                            logger.debug(f"Error parsing result.json for {package}/{ver_entry.name}: {e}")
        except Exception as e:
            logger.debug(f"Error scanning patched dir for {package}: {e}")

    # Sort candidate results newest first
    candidate_results.sort(key=lambda x: x[0], reverse=True)
    latest_apk_mtime = candidate_results[0][0] if candidate_results else 0.0

    # 4. Construct enriched attempts list
    detailed_attempts: list[dict[str, Any]] = []

    if report_attempts:
        for idx, att in enumerate(report_attempts):
            att_ver = str(att.get("version") or att.get("target_version") or "target")
            att_strat = att.get("strategy") or att.get("run") or 1
            att_strat_name = att.get("strategy_name") or ("Strategy 1 (All Repos)" if att_strat == 1 else "Strategy 2 (Version-Compatible)")
            att_stage = att.get("stage") or "Morphe Patching"
            att_reason = att.get("reason") or ""
            att_status = att.get("status")
            att_repos = att.get("repos") or (report_fail_info.get("applied_repos") if report_fail_info else None) or []

            clean_repos = [str(r).strip() for r in att_repos if str(r).strip()]

            # Determine attempt status
            if not att_status:
                if att_reason:
                    att_status = "failed"
                elif report_succ_info and idx == len(report_attempts) - 1:
                    att_status = "success"
                else:
                    att_status = "failed"

            att_applied = []
            att_failed = []

            if att_status == "success":
                # Winning attempt has applied patches from result.json on disk
                if att_ver in version_results:
                    vr = version_results[att_ver]
                    att_applied = vr["applied_patches"]
                    att_failed = vr["failed_patches"]
            else:
                # Failed attempt
                if att_ver in version_results and version_results[att_ver]["status"] == "failed":
                    att_failed = version_results[att_ver]["failed_patches"]
                if not att_failed and att_reason:
                    pname, preason = _extract_patch_details({"name": "Patch Failure", "reason": att_reason})
                    att_failed = [{"name": pname, "reason": preason, "owner": ""}]

            detailed_attempts.append({
                "attempt_number": idx + 1,
                "version": att_ver,
                "strategy": att_strat,
                "strategy_name": att_strat_name,
                "stage": att_stage,
                "reason": att_reason,
                "status": att_status,
                "repos": clean_repos,
                "applied_patches": att_applied,
                "failed_patches": att_failed,
                "applied_count": len(att_applied),
                "failed_count": len(att_failed),
            })

        # If the run succeeded overall, append the winning build as the successful final attempt
        if report_succ_info:
            has_success = any(a.get("status") == "success" for a in detailed_attempts)
            if not has_success:
                dest_str = str(report_succ_info.get("dest") or "")
                winning_ver = "latest"
                if dest_str:
                    try:
                        winning_ver = Path(dest_str).parent.name
                    except Exception:
                        pass
                if winning_ver in ("latest", package) and candidate_results:
                    winning_ver = candidate_results[0][1]

                winning_strat = report_succ_info.get("strategy") or 1
                winning_strat_num = int(winning_strat) if str(winning_strat).isdigit() else 2
                winning_strat_name = "Strategy 1 (All Repos)" if winning_strat_num == 1 else "Strategy 2 (Version-Compatible)"
                winning_repos = [str(r).strip() for r in (report_succ_info.get("applied_repos") or []) if str(r).strip()]

                vr = version_results.get(winning_ver, {})
                win_applied = vr.get("applied_patches") or []
                win_failed = vr.get("failed_patches") or []

                detailed_attempts.append({
                    "attempt_number": len(detailed_attempts) + 1,
                    "version": winning_ver,
                    "strategy": winning_strat_num,
                    "strategy_name": winning_strat_name,
                    "stage": "Morphe Patching",
                    "reason": "",
                    "status": "success",
                    "repos": winning_repos,
                    "applied_patches": win_applied,
                    "failed_patches": win_failed,
                    "applied_count": len(win_applied),
                    "failed_count": len(win_failed),
                })
    elif candidate_results:
        # Fallback when no run report exists: treat the newest valid build on disk as 1 installed build
        mtime, ver, _ = candidate_results[0]
        vr = version_results.get(ver, {})
        vstatus = vr.get("status", "failed")
        detailed_attempts.append({
            "attempt_number": 1,
            "version": ver,
            "strategy": 1,
            "strategy_name": "Installed Build",
            "stage": "Verification",
            "reason": "",
            "status": vstatus,
            "repos": [],
            "applied_patches": vr.get("applied_patches", []),
            "failed_patches": vr.get("failed_patches", []),
            "applied_count": vr.get("applied_count", 0),
            "failed_count": vr.get("failed_count", 0),
        })

    if not detailed_attempts and not candidate_results:
        return None

    # 5. Determine winning attempt for card title and direct download link
    winning_attempt = next((a for a in reversed(detailed_attempts) if a.get("status") == "success"), None)

    if winning_attempt:
        card_version = winning_attempt["version"]
        card_status = "success"
        card_primary = winning_attempt
    else:
        card_primary = detailed_attempts[-1] if detailed_attempts else {}
        card_version = card_primary.get("version") or (candidate_results[0][1] if candidate_results else "latest")
        card_status = "failed" if (report_fail_info or detailed_attempts) else "success"

    # Direct download link for the succeeded version
    download_url = None
    apk_name = None
    apk_file = PATCHED_DIR / package / card_version / f"{package}_{card_version}_patched.apk"
    if apk_file.is_file():
        download_url = f"/files/{package}/{card_version}/{package}_{card_version}_patched.apk"
        apk_name = f"{package}_{card_version}_patched.apk"
    elif candidate_results:
        for _, cver, _ in candidate_results:
            capk = PATCHED_DIR / package / cver / f"{package}_{cver}_patched.apk"
            if capk.is_file():
                download_url = f"/files/{package}/{cver}/{package}_{cver}_patched.apk"
                apk_name = f"{package}_{cver}_patched.apk"
                break

    effective_time = max(report_timestamp, latest_apk_mtime)
    tz = get_current_timezone()
    dt = datetime.fromtimestamp(effective_time, tz=tz) if effective_time else datetime.now(tz=tz)

    return {
        "version": card_version,
        "status": card_status,
        "is_pipeline_failure": (card_status == "failed" and card_primary.get("stage") not in ("Morphe Patching", None)),
        "failure_stage": card_primary.get("stage", "Pipeline"),
        "failure_reason": card_primary.get("reason", ""),
        "total_attempts": len(detailed_attempts),
        "winning_strategy": card_primary.get("strategy") if card_status == "success" else None,
        "winning_strategy_name": card_primary.get("strategy_name") if card_status == "success" else None,
        "winning_attempt_number": card_primary.get("attempt_number") if card_status == "success" else None,
        "download_url": download_url,
        "apk_name": apk_name,
        "attempts": detailed_attempts,
        "patch_owners": patch_owners,
        "applied_patches": card_primary.get("applied_patches", []),
        "failed_patches": card_primary.get("failed_patches", []),
        "applied_count": card_primary.get("applied_count", 0),
        "failed_count": card_primary.get("failed_count", 0),
        "timestamp": int(effective_time),
        "timestamp_formatted": dt.strftime("%b %d, %H:%M"),
    }


def _find_apps_blocks(lines: list[str]) -> tuple[int, int, list[dict[str, Any]]]:
    """
    Finds the packages: (or legacy apps:) section and extracts all app blocks (both active and commented).
    Returns (apps_start_idx, apps_end_idx, list_of_blocks).
    Each block is a dict: {
        "package": str,
        "start": int,
        "end": int,
        "is_commented": bool,
    }
    """
    apps_start_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("packages:") or stripped.startswith("apps:"):
            apps_start_idx = i
            break
    if apps_start_idx == -1:
        return -1, len(lines), []

    pkg_pattern = re.compile(r"^\s*(?:#\s*)?-\s+(?:package_name|package):\s*([^\s#]+)")
    # A true top-level section key is an unindented string ending with ':' (e.g. 'tailscale:', 'email:')
    # NOT starting with '#' (comments) and NOT indented with spaces or tabs.
    top_key_pattern = re.compile(r"^[a-zA-Z0-9_-]+:\s*(?:#.*)?$")

    blocks: list[dict[str, Any]] = []
    current_block: dict[str, Any] | None = None
    apps_end_idx = len(lines)

    for i in range(apps_start_idx + 1, len(lines)):
        line = lines[i]

        # Check if an unindented, non-comment top-level key begins (excluding empty lines)
        if line.strip() and not line.startswith(" ") and not line.startswith("\t") and not line.lstrip().startswith("#"):
            if top_key_pattern.match(line):
                apps_end_idx = i
                if current_block is not None:
                    current_block["end"] = i
                    blocks.append(current_block)
                    current_block = None
                break

        m = pkg_pattern.match(line)
        if m:
            if current_block is not None:
                current_block["end"] = i
                blocks.append(current_block)
            current_block = {
                "package": m.group(1).strip(),
                "start": i,
                "end": len(lines),
                "is_commented": line.lstrip().startswith("#"),
            }

    if current_block is not None:
        current_block["end"] = apps_end_idx
        blocks.append(current_block)

    return apps_start_idx, apps_end_idx, blocks


def get_apps_list() -> list[dict[str, Any]]:
    """
    Returns all configured apps/packages from config.yaml in order of appearance,
    including both active apps (enabled=True) and commented-out apps (enabled=False),
    with their latest patch audit result attached.
    """
    if not CONFIG_PATH.is_file():
        return []

    try:
        yaml, data = load_config_raw()
    except Exception:
        yaml = get_yaml_instance()
        data = {}

    raw_active_apps = data.get("packages") or data.get("apps") or []
    active_apps_map: dict[str, dict[str, Any]] = {}
    for item in raw_active_apps:
        if isinstance(item, dict):
            pkg = str(item.get("package_name") or item.get("package") or "").strip()
            if pkg:
                active_apps_map[pkg] = item

    results: list[dict[str, Any]] = []
    seen_packages: set[str] = set()
    patched_registry = load_patched_registry()

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    apps_start_idx, apps_end_idx, blocks = _find_apps_blocks(lines)
    if apps_start_idx == -1:
        # Fallback to just active apps if no packages/apps section recognized
        for pkg, item in active_apps_map.items():
            results.append(_format_app_entry(pkg, item, is_enabled=True, patched_registry=patched_registry))
        return results

    for block in blocks:
        pkg = block["package"]
        if not pkg or pkg in seen_packages:
            continue
        seen_packages.add(pkg)

        is_enabled = not block["is_commented"]
        if is_enabled and pkg in active_apps_map:
            item = active_apps_map[pkg]
            results.append(_format_app_entry(pkg, item, is_enabled=True, patched_registry=patched_registry))
        else:
            # Parse block lines by stripping comment markers
            block_lines = lines[block["start"]:block["end"]]
            uncommented = []
            for l in block_lines:
                uncommented.append(re.sub(r"^(\s*)#\s?", r"\1", l))
            try:
                parsed_block = yaml.load("".join(uncommented))
                if isinstance(parsed_block, list) and len(parsed_block) > 0 and isinstance(parsed_block[0], dict):
                    item = parsed_block[0]
                elif isinstance(parsed_block, dict):
                    item = parsed_block
                else:
                    item = {"package_name": pkg}
            except Exception:
                item = {"package_name": pkg}

            results.append(_format_app_entry(pkg, item, is_enabled=is_enabled, patched_registry=patched_registry))

    return results


def _format_app_entry(
    pkg: str,
    item: dict[str, Any],
    is_enabled: bool,
    patched_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repos = item.get("repos") or []
    if isinstance(repos, str):
        repos = [repos]
    repos = [r.strip() for r in repos if isinstance(r, str) and r.strip()]

    aliases = item.get("patch_aliases") or []

    return {
        "package": pkg,
        "package_name": pkg,
        "display_name": str(item.get("display_name") or "").strip(),
        "repos": list(repos),
        "patch_aliases": list(aliases) if isinstance(aliases, list) else [],
        "enabled": is_enabled,
        "last_result": get_last_patch_result(pkg, patched_registry=patched_registry),
    }


def toggle_app_enabled(package: str, enabled: bool) -> bool:
    """
    Enables or disables an app in config.yaml by commenting or uncommenting
    its YAML block in-place, preserving ordering, comments, and structure.
    """
    package = package.strip()
    if not CONFIG_PATH.is_file():
        return False

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    apps_start_idx, apps_end_idx, blocks = _find_apps_blocks(lines)
    target_block = None
    for b in blocks:
        if b["package"] == package:
            target_block = b
            break

    if not target_block:
        return False

    start_idx = target_block["start"]
    end_idx = target_block["end"]

    # Trim trailing blank lines from the block
    while end_idx > start_idx and not lines[end_idx - 1].strip():
        end_idx -= 1

    new_lines = []
    for i in range(start_idx, end_idx):
        line = lines[i]
        if enabled:
            # Uncomment line: remove leading '# ' or '#' after indentation
            if re.match(r"^\s*#", line):
                uncommented = re.sub(r"^(\s*)#\s?", r"\1", line)
                new_lines.append(uncommented)
            else:
                new_lines.append(line)
        else:
            # Comment line: prefix with '  # ' or '# '
            if re.match(r"^\s*#", line):
                new_lines.append(line)
            elif line.strip():
                if line.startswith("  "):
                    new_lines.append("  # " + line[2:])
                else:
                    new_lines.append("# " + line)
            else:
                new_lines.append(line)

    lines[start_idx:end_idx] = new_lines
    save_raw_lines(lines)
    return True


def ensure_app_enabled(package: str) -> bool:
    """
    Ensures the specified app is enabled (uncommented) in config.yaml.
    If the app exists and is currently commented out, it uncomments it in-place.
    Returns True if the app was previously commented and is now uncommented, False otherwise.
    """
    package = package.strip()
    if not CONFIG_PATH.is_file():
        return False

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    _, _, blocks = _find_apps_blocks(lines)
    for b in blocks:
        if b["package"] == package:
            if b["is_commented"]:
                return toggle_app_enabled(package, True)
            return False

    return False



def add_or_update_app(app_data: dict[str, Any]) -> dict[str, Any]:
    """
    Safely adds a new app or updates an existing app in config.yaml under packages:.
    Places new active apps cleanly inside the active packages section above commented apps
    and section headers, preserving all existing comments and formatting.
    """
    package = (app_data.get("package_name") or app_data.get("package") or "").strip()
    if not package:
        raise ValueError("App package name is required.")

    repos = app_data.get("repos") or []
    if isinstance(repos, str):
        repos = [repos]
    repos = [r.strip() for r in repos if isinstance(r, str) and r.strip()]

    patch_aliases = app_data.get("patch_aliases") or []
    display_name = str(app_data.get("display_name") or "").strip()

    # Format the new app YAML block
    new_block_lines = [f"  - package_name: {package}\n"]
    if display_name:
        safe_name = display_name.replace('"', "'")
        new_block_lines.append(f'    display_name: "{safe_name}"\n')
    if repos:
        new_block_lines.append("    repos:\n")
        for r in repos:
            new_block_lines.append(f"      - {r}\n")
    if patch_aliases:
        new_block_lines.append("    patch_aliases:\n")
        for item in patch_aliases:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                new_block_lines.append(f"      - name: {name}\n")
                raw_aliases = item.get("aliases") or []
                if isinstance(raw_aliases, str):
                    raw_aliases = [raw_aliases]
                cleaned = [str(a).strip() for a in raw_aliases if str(a).strip()]
                if cleaned:
                    new_block_lines.append("        aliases:\n")
                    for a in cleaned:
                        new_block_lines.append(f"          - {a}\n")
    new_block_lines.append("\n")

    ensure_config_exists()

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    apps_start_idx, apps_end_idx, blocks = _find_apps_blocks(lines)
    if apps_start_idx == -1:
        lines.append("\npackages:\n")
        lines.extend(new_block_lines)
        save_raw_lines(lines)
        return {"action": "added", "package": package, "entry": app_data}

    # Check if this package already exists in blocks
    existing_block = None
    for b in blocks:
        if b["package"] == package:
            existing_block = b
            break

    if existing_block:
        start_idx = existing_block["start"]
        end_idx = existing_block["end"]
        while end_idx > start_idx and not lines[end_idx - 1].strip():
            end_idx -= 1
        lines[start_idx:end_idx] = new_block_lines
        action = "updated"
    else:
        # Insert as active app in the active apps region
        active_blocks = [b for b in blocks if not b["is_commented"]]
        if active_blocks:
            last_active = active_blocks[-1]
            insert_idx = last_active["end"]
            while insert_idx > last_active["start"] and not lines[insert_idx - 1].strip():
                insert_idx -= 1
            lines[insert_idx:insert_idx] = ["\n"] + new_block_lines
        elif blocks:
            # If all existing blocks are commented, insert before the first block
            insert_idx = blocks[0]["start"]
            lines[insert_idx:insert_idx] = new_block_lines + ["\n"]
        else:
            # No blocks at all, insert right after packages:
            insert_idx = apps_start_idx + 1
            lines[insert_idx:insert_idx] = new_block_lines

        action = "added"

    save_raw_lines(lines)
    return {"action": action, "package": package, "entry": app_data}


def delete_app(package: str) -> bool:
    package = package.strip()
    if not CONFIG_PATH.is_file():
        return False

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    apps_start_idx, apps_end_idx, blocks = _find_apps_blocks(lines)
    target_block = None
    for b in blocks:
        if b["package"] == package:
            target_block = b
            break

    if not target_block:
        return False

    start_idx = target_block["start"]
    end_idx = target_block["end"]
    while end_idx > start_idx and not lines[end_idx - 1].strip():
        end_idx -= 1
    if end_idx < len(lines) and not lines[end_idx].strip():
        end_idx += 1

    del lines[start_idx:end_idx]
    save_raw_lines(lines)
    return True


def get_patch_repos() -> list[str]:
    _, data = load_config_raw()
    repos = data.get("patch_repos") or []
    return [str(r).strip() for r in repos if str(r).strip()]


def add_patch_repo(repo_url: str) -> bool:
    repo_url = repo_url.strip()
    if not repo_url:
        return False
    yaml, data = load_config_raw()
    if "patch_repos" not in data or data["patch_repos"] is None:
        data["patch_repos"] = CommentedSeq()

    repos = data["patch_repos"]
    if repo_url in repos:
        return False

    repos.append(repo_url)
    save_config_raw(yaml, data)
    return True


def delete_patch_repo(repo_url: str) -> bool:
    repo_url = repo_url.strip()
    yaml, data = load_config_raw()
    repos = data.get("patch_repos")
    if not repos:
        return False

    initial_len = len(repos)
    data["patch_repos"] = [r for r in repos if str(r).strip() != repo_url]
    if len(data["patch_repos"]) < initial_len:
        save_config_raw(yaml, data)
        return True
    return False


def get_full_config() -> dict[str, Any]:
    apps_list = get_apps_list()
    settings = get_all_settings()
    sys_settings = settings.get("system", {})
    tunnels_settings = settings.get("tunnels", {})
    ts_url = tunnels_settings.get("tailscale", {}).get("base_url", "")

    return {
        **sys_settings,
        "patch_repos": get_patch_repos(),
        "packages": apps_list,
        "apps": apps_list,
        "tailscale": {"base_url": ts_url},
        "tunnels": tunnels_settings,
        "system": sys_settings,
        "email": settings.get("email", {}),
    }


def get_all_settings() -> dict[str, Any]:
    """
    Returns consolidated system, tunnel, and email settings from config.yaml
    with optional environment variable fallback defaults.
    """
    _, data = load_config_raw()

    email_data = data.get("email") or {}
    ntfy_data = data.get("ntfy") if isinstance(data.get("ntfy"), dict) else {}
    ts_data = data.get("tailscale") if isinstance(data.get("tailscale"), dict) else {}
    ts_url = str(
        os.getenv("TAILSCALE_FUNNEL_URL")
        or os.getenv("TAILSCALE_URL")
        or os.getenv("TAILSCALE_BASE_URL")
        or ts_data.get("base_url")
        or ""
    ).strip()

    raw_schedule = data.get("schedule_time")
    if raw_schedule is None:
        raw_schedule = os.getenv("SCHEDULE_TIME", "03:00")
    schedule_time = str(raw_schedule).strip()

    raw_startup = data.get("run_on_startup")
    if raw_startup is None:
        raw_startup = os.getenv("RUN_ON_STARTUP", "false")
    if isinstance(raw_startup, bool):
        run_on_startup = raw_startup
    else:
        run_on_startup = str(raw_startup).lower() in ("true", "1", "yes")

    raw_tz = data.get("timezone")
    if raw_tz is None:
        raw_tz = os.getenv("TZ", "Etc/UTC")
    timezone = str(raw_tz).strip()

    raw_max_logs = data.get("max_logs")
    if raw_max_logs is None:
        raw_max_logs = os.getenv("MAX_LOGS", 5)
    try:
        max_logs = int(raw_max_logs)
    except (ValueError, TypeError):
        max_logs = 5

    raw_max_orig = data.get("max_original_packages")
    try:
        max_original_packages = int(raw_max_orig) if raw_max_orig is not None else None
    except (ValueError, TypeError):
        max_original_packages = None

    ntfy_token_set = bool(str(ntfy_data.get("token") or "").strip() or os.getenv("NTFY_TOKEN"))
    ntfy_password_set = bool(str(ntfy_data.get("password") or "").strip() or os.getenv("NTFY_PASSWORD"))
    ntfy_token_cfg = str(ntfy_data.get("token") or "").strip()
    ntfy_user_cfg = str(ntfy_data.get("username") or "").strip()
    ntfy_token_env = bool(os.getenv("NTFY_TOKEN"))
    ntfy_user_env = bool(os.getenv("NTFY_USERNAME"))
    if ntfy_token_cfg or ntfy_token_env:
        ntfy_auth_mode = "bearer"
    elif ntfy_user_cfg or ntfy_user_env:
        ntfy_auth_mode = "basic"
    else:
        ntfy_auth_mode = "none"
    ntfy_auth_from_env = bool(
        (ntfy_token_env or ntfy_user_env) and not (ntfy_token_cfg or ntfy_user_cfg)
    )

    raw_max_patched = data.get("max_patched_packages")
    try:
        max_patched_packages = int(raw_max_patched) if raw_max_patched is not None else None
    except (ValueError, TypeError):
        max_patched_packages = None

    return {
        "system": {
            "architecture": str(data.get("architecture") or "arm64-v8a"),
            "striplibs": bool(data.get("striplibs", True)),
            "low_spec_mode": bool(data.get("low_spec_mode", True)),
            "schedule_time": schedule_time,
            "run_on_startup": run_on_startup,
            "timezone": timezone,
            "max_logs": max_logs,
            "max_original_packages": max_original_packages,
            "max_patched_packages": max_patched_packages,
        },
        "tunnels": {
            "tailscale": {
                "base_url": ts_url,
            },
        },
        "email": {
            "enabled": bool(email_data.get("enabled")),
            "smtp_server": str(email_data.get("smtp_server") or "smtp.gmail.com"),
            "smtp_port": int(email_data.get("smtp_port") or 587),
            "use_tls": bool(email_data.get("use_tls", True)),
            "use_ssl": bool(email_data.get("use_ssl")),
            "sender_name": str(email_data.get("sender_name") or "AutoMorph"),
            "sender_email": str(email_data.get("sender_email") or ""),
            "recipients": [str(r).strip() for r in (email_data.get("recipients") or []) if str(r).strip()],
            "subject_prefix": str(email_data.get("subject_prefix") or "[AutoMorph]"),
            "attach_result_json": bool(email_data.get("attach_result_json")),
            "layout_mode": str(email_data.get("layout_mode") or "auto"),
            "compact_threshold": int(email_data.get("compact_threshold") or 15),
        },
        "ntfy": {
            "enabled": bool(ntfy_data.get("enabled")),
            # Auth state for the UI: report whether credentials are configured
            # (from config.yaml or the NTFY_* env) without ever exposing them.
            "auth_mode": ntfy_auth_mode,
            "auth_from_env": ntfy_auth_from_env,
            "server": str(ntfy_data.get("server") or "https://ntfy.sh"),
            "topic": str(ntfy_data.get("topic") or ""),
            # Secrets are never returned: the portal has no login, so anyone on
            # the network could read the token. Only whether one is set.
            "token_set": ntfy_token_set,
            "password_set": ntfy_password_set,
            "username": str(ntfy_data.get("username") or ""),
            "priority": str(ntfy_data.get("priority") or "default"),
            "tags": str(ntfy_data.get("tags") or ""),
            "click_url": str(ntfy_data.get("click_url") or ""),
            "notify_on": str(ntfy_data.get("notify_on") or "always"),
        },
    }


def update_system_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Updates system/build settings and schedule/tz/startup triggers in config.yaml."""
    yaml, data = load_config_raw()

    if "architecture" in settings and settings["architecture"] is not None:
        data["architecture"] = str(settings["architecture"]).strip()
    if "striplibs" in settings and settings["striplibs"] is not None:
        data["striplibs"] = bool(settings["striplibs"])
    if "low_spec_mode" in settings and settings["low_spec_mode"] is not None:
        data["low_spec_mode"] = bool(settings["low_spec_mode"])
    if "schedule_time" in settings and settings["schedule_time"] is not None:
        data["schedule_time"] = str(settings["schedule_time"]).strip()
    if "run_on_startup" in settings and settings["run_on_startup"] is not None:
        data["run_on_startup"] = bool(settings["run_on_startup"])
    if "timezone" in settings and settings["timezone"] is not None:
        tz_str = str(settings["timezone"]).strip()
        data["timezone"] = tz_str
        apply_timezone(tz_str)
    if "max_logs" in settings and settings["max_logs"] is not None:
        try:
            val = int(settings["max_logs"])
            if val >= 0:
                data["max_logs"] = val
        except (ValueError, TypeError):
            pass
    if "max_original_packages" in settings:
        try:
            val = int(settings["max_original_packages"]) if settings["max_original_packages"] is not None else None
            data["max_original_packages"] = val if (val is not None and val > 0) else None
        except (ValueError, TypeError):
            data["max_original_packages"] = None

    if "max_patched_packages" in settings:
        try:
            val = int(settings["max_patched_packages"]) if settings["max_patched_packages"] is not None else None
            data["max_patched_packages"] = val if (val is not None and val > 0) else None
        except (ValueError, TypeError):
            data["max_patched_packages"] = None

    save_config_raw(yaml, data)
    return get_all_settings()["system"]


def update_tunnels_settings(tunnels: dict[str, Any]) -> dict[str, Any]:
    """Updates Tailscale Funnel settings in .env."""
    if isinstance(tunnels, dict) and "tailscale" in tunnels and isinstance(tunnels["tailscale"], dict):
        ts_data = tunnels["tailscale"]
        if "base_url" in ts_data:
            val = str(ts_data["base_url"]).strip() if ts_data["base_url"] is not None else ""
            os.environ["TAILSCALE_FUNNEL_URL"] = val
            env_file = PROJECT_ROOT / ".env"
            if env_file.is_file():
                try:
                    from dotenv import set_key
                    set_key(str(env_file), "TAILSCALE_FUNNEL_URL", val)
                except Exception as e:
                    logger.warning(f"Could not write TAILSCALE_FUNNEL_URL to .env: {e}")

    return get_all_settings()["tunnels"]


def update_email_settings(email_data: dict[str, Any]) -> dict[str, Any]:
    """Updates email dispatcher settings in config.yaml."""
    yaml, data = load_config_raw()
    if "email" not in data or not isinstance(data["email"], dict):
        data["email"] = CommentedMap()

    email_map = data["email"]

    if "enabled" in email_data and email_data["enabled"] is not None:
        email_map["enabled"] = bool(email_data["enabled"])
    if "smtp_server" in email_data and email_data["smtp_server"] is not None:
        email_map["smtp_server"] = str(email_data["smtp_server"]).strip()
    if "smtp_port" in email_data and email_data["smtp_port"] is not None:
        email_map["smtp_port"] = int(email_data["smtp_port"])
    if "use_tls" in email_data and email_data["use_tls"] is not None:
        email_map["use_tls"] = bool(email_data["use_tls"])
    if "use_ssl" in email_data and email_data["use_ssl"] is not None:
        email_map["use_ssl"] = bool(email_data["use_ssl"])
    if "sender_name" in email_data and email_data["sender_name"] is not None:
        email_map["sender_name"] = str(email_data["sender_name"]).strip()
    if "sender_email" in email_data and email_data["sender_email"] is not None:
        email_map["sender_email"] = str(email_data["sender_email"]).strip()
    if "recipients" in email_data and email_data["recipients"] is not None:
        raw_recipients = email_data["recipients"]
        if isinstance(raw_recipients, str):
            raw_recipients = [r.strip() for r in raw_recipients.replace(";", ",").split(",") if r.strip()]
        email_map["recipients"] = [str(r).strip() for r in raw_recipients if str(r).strip()]
    if "subject_prefix" in email_data and email_data["subject_prefix"] is not None:
        email_map["subject_prefix"] = str(email_data["subject_prefix"]).strip()
    email_map.pop("calculate_checksum", None)
    email_map.pop("include_checksum", None)
    if "attach_result_json" in email_data and email_data["attach_result_json"] is not None:
        email_map["attach_result_json"] = bool(email_data["attach_result_json"])
    if "layout_mode" in email_data and email_data["layout_mode"] is not None:
        email_map["layout_mode"] = str(email_data["layout_mode"]).strip().lower()
    if "compact_threshold" in email_data and email_data["compact_threshold"] is not None:
        email_map["compact_threshold"] = int(email_data["compact_threshold"])

    save_config_raw(yaml, data)
    return get_all_settings()["email"]


def update_ntfy_settings(ntfy_data: dict[str, Any]) -> dict[str, Any]:
    """
    Updates ntfy notification settings in config.yaml.

    Token and password are write-only: because they are never sent to the
    browser, an empty field means "keep what is stored" and `clear_token` /
    `clear_password` remove them.
    """
    yaml, data = load_config_raw()
    if "ntfy" not in data or not isinstance(data["ntfy"], dict):
        data["ntfy"] = CommentedMap()

    ntfy_map = data["ntfy"]
    for key in ("server", "topic", "username", "tags", "click_url", "priority", "notify_on"):
        if key in ntfy_data and ntfy_data[key] is not None:
            ntfy_map[key] = str(ntfy_data[key]).strip()
    # Secrets are write-only: an empty value keeps the stored one, so a normal
    # settings save cannot wipe a token the user never saw.
    for secret, clear_flag in (("token", "clear_token"), ("password", "clear_password")):
        if ntfy_data.get(clear_flag):
            ntfy_map[secret] = ""
        elif ntfy_data.get(secret):
            ntfy_map[secret] = str(ntfy_data[secret]).strip()
    if "enabled" in ntfy_data and ntfy_data["enabled"] is not None:
        ntfy_map["enabled"] = bool(ntfy_data["enabled"])

    save_config_raw(yaml, data)
    return get_all_settings()["ntfy"]


def send_test_ntfy(override: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Sends a test notification so server, topic and credentials can be verified
    from the web portal (mirrors send_test_email).
    """
    settings = dict(get_all_settings()["ntfy"])
    if override:
        settings.update({k: v for k, v in override.items() if v is not None})
    settings["enabled"] = True

    import sys

    scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import ntfy_notify

    resolved = ntfy_notify.resolve_settings({"ntfy": settings})
    if not resolved.get("topic"):
        return {"success": False, "error": "No ntfy topic configured"}

    payload = {
        "title": "[AutoMorph] Test notification",
        "message": "This test confirms server, topic and credentials are working.\n"
                   f"Sent from the AutoMorph web portal at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.",
        "priority": resolved["priority"],
        "tags": resolved["tags"] or ["white_check_mark"],
        "click": resolved.get("click_url") or "",
        "topic": resolved["topic"],
        "server": resolved["server"],
    }
    result = ntfy_notify.send_notification(payload, resolved)
    if result.get("success"):
        return {
            "success": True,
            "message": f"Test notification sent to {result['server']}/{result['topic']}",
        }
    return {"success": False, "error": result.get("error") or "ntfy refused the notification"}


def send_test_email(override: dict[str, Any] | None = None, recipient: str | None = None) -> dict[str, Any]:
    """
    Sends a test email to verify SMTP host, port, TLS/SSL encryption, and credentials.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formatdate, make_msgid

    settings = get_all_settings()
    email_conf = dict(settings["email"])
    if override:
        email_conf.update(override)

    smtp_server = str(email_conf.get("smtp_server") or "").strip()
    smtp_port = int(email_conf.get("smtp_port") or 587)
    use_tls = bool(email_conf.get("use_tls", True))
    use_ssl = bool(email_conf.get("use_ssl", False))
    sender_name = str(email_conf.get("sender_name") or "AutoMorph").strip()
    sender_email = str(email_conf.get("sender_email") or "").strip()
    username = str(
        email_conf.get("username")
        or (os.getenv(email_conf.get("username_env")) if email_conf.get("username_env") else None)
        or os.getenv("SMTP_USERNAME")
        or os.getenv("SMTP_USER")
        or sender_email
    ).strip()
    password = str(
        email_conf.get("password")
        or (os.getenv(email_conf.get("password_env")) if email_conf.get("password_env") else None)
        or os.getenv("SMTP_PASSWORD")
        or os.getenv("SMTP_PASS")
        or ""
    ).strip()

    target_recipients: list[str] = []
    if recipient and recipient.strip():
        target_recipients = [recipient.strip()]
    elif email_conf.get("recipients"):
        raw_recipients = email_conf["recipients"]
        if isinstance(raw_recipients, str):
            raw_recipients = [r.strip() for r in raw_recipients.replace(";", ",").split(",") if r.strip()]
        target_recipients = [str(r).strip() for r in raw_recipients if str(r).strip()]
    elif sender_email:
        target_recipients = [sender_email]

    if not smtp_server:
        raise ValueError("SMTP Server host is missing.")
    if not sender_email:
        raise ValueError("Sender email address is missing.")
    if not target_recipients:
        raise ValueError("No recipient specified for test email.")

    tz = get_current_timezone()
    now_dt = datetime.now(tz=tz) if tz else datetime.now()
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    subject = f"{email_conf.get('subject_prefix', '[AutoMorph]').strip()} 🧪 SMTP Test Notification ({now_str})"

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0d0e12; color: #f3f4f6; margin: 0; padding: 20px; }}
  .card {{ max-width: 560px; margin: 0 auto; background: #14161d; border: 1px solid #1f2330; border-radius: 14px; overflow: hidden; box-shadow: 0 8px 24px rgba(0,0,0,0.5); }}
  .header {{ background: linear-gradient(135deg, #4A7FC8 0%, #20CFCE 100%); padding: 20px 24px; color: #fff; }}
  .title {{ font-size: 20px; font-weight: 700; margin: 0; }}
  .body {{ padding: 24px; font-size: 14px; line-height: 1.6; }}
  .badge {{ display: inline-block; background: rgba(16, 185, 129, 0.2); color: #34d399; padding: 4px 10px; border-radius: 9999px; font-weight: 600; font-size: 12px; margin-bottom: 16px; border: 1px solid rgba(16, 185, 129, 0.3); }}
  .info-table {{ width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 13px; }}
  .info-table td {{ padding: 8px 12px; border-bottom: 1px solid rgba(255,255,255,0.06); }}
  .info-table td.label {{ color: #9ca3af; width: 35%; font-weight: 500; }}
  .info-table td.val {{ color: #f3f4f6; font-family: monospace; }}
  .footer {{ padding: 16px 24px; border-top: 1px solid rgba(255,255,255,0.06); font-size: 12px; color: #6b7280; text-align: center; }}
</style>
</head>
<body>
  <div class="card">
    <div class="header">
      <div class="title">⚡ AutoMorph SMTP Test</div>
    </div>
    <div class="body">
      <div class="badge">✓ SMTP Connection Successful</div>
      <p style="margin: 0 0 12px 0; color: #e5e7eb;">This test notification confirms that your AutoMorph email dispatcher configuration and SMTP credentials are fully functional.</p>
      
      <table class="info-table">
        <tr>
          <td class="label">SMTP Server:</td>
          <td class="val">{smtp_server}:{smtp_port}</td>
        </tr>
        <tr>
          <td class="label">Security:</td>
          <td class="val">{'Direct SSL' if use_ssl else ('STARTTLS' if use_tls else 'None')}</td>
        </tr>
        <tr>
          <td class="label">Sender:</td>
          <td class="val">{sender_name} &lt;{sender_email}&gt;</td>
        </tr>
        <tr>
          <td class="label">Authenticated As:</td>
          <td class="val">{username or '(anonymous)'}</td>
        </tr>
        <tr>
          <td class="label">Timestamp:</td>
          <td class="val">{now_str}</td>
        </tr>
      </table>
    </div>
    <div class="footer">
      AutoMorph Control Center • Automated Dispatcher Test
    </div>
  </div>
</body>
</html>"""

    text_content = f"""AutoMorph SMTP Test
=======================
✓ SMTP Connection Successful!

This test message confirms that your AutoMorph email dispatcher configuration and SMTP credentials are functioning properly.

- Server: {smtp_server}:{smtp_port}
- Security: {'Direct SSL' if use_ssl else ('STARTTLS' if use_tls else 'None')}
- Sender: {sender_name} <{sender_email}>
- Authenticated As: {username or '(anonymous)'}
- Timestamp: {now_str}
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = ", ".join(target_recipients)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=smtp_server if "." in smtp_server else "patcher.local")
    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    if use_ssl:
        server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=20)
    else:
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=20)

    try:
        if use_tls and not use_ssl:
            server.starttls()
        if username and password:
            server.login(username, password)
        server.send_message(msg, from_addr=sender_email, to_addrs=target_recipients)
        return {
            "success": True,
            "message": f"Test email successfully sent to {', '.join(target_recipients)} via {smtp_server}:{smtp_port}",
        }
    except Exception as e:
        logger.error(f"SMTP Test Error: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }
    finally:
        try:
            server.quit()
        except Exception:
            pass


# Initialize active timezone on module import
try:
    apply_timezone()
except Exception:
    pass

