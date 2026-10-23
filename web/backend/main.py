"""
AutoMorph FastAPI Application.
Provides REST and SSE endpoints for dashboard metrics, configuration management,
and real-time patching execution.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import json
import logging
import os
from pathlib import Path
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from . import config_manager, mutex, options_manager, update_checker
from .runner import runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("automorph.api")

app = FastAPI(title="AutoMorph Management API", version="2.1.0")

# Allow CORS for development or external ingress
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path.lower()
    if path.startswith("/api/") or path.endswith((".js", ".css", ".html")) or path in ("/", ""):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

from dotenv import load_dotenv

PROJECT_ROOT = Path(os.getenv("AUTOMORPH_ROOT", Path(__file__).resolve().parent.parent.parent))
if (PROJECT_ROOT / ".env").is_file():
    load_dotenv(PROJECT_ROOT / ".env")


# ---------------------------------------------------------------------------
# Helper Models & Calculations
# ---------------------------------------------------------------------------

class AppConfigRequest(BaseModel):
    package: str
    package_name: str | None = None
    repos: list[str] = []
    patch_aliases: list[dict[str, Any]] = []


class AppToggleRequest(BaseModel):
    package: str
    enabled: bool


class RunRequest(BaseModel):
    package: str | None = None
    source: str = "manual"


class RepoRequest(BaseModel):
    repo_url: str


class OptionsSaveRequest(BaseModel):
    patches: dict[str, Any]
    meta: dict[str, Any] | None = None


class DownloadOptionsRequest(BaseModel):
    repos: list[str] = []
    force: bool = False


class SystemSettingsRequest(BaseModel):
    architecture: str | None = None
    striplibs: bool | None = None
    low_spec_mode: bool | None = None
    schedule_time: str | None = None
    run_on_startup: bool | None = None
    timezone: str | None = None
    max_logs: int | None = None
    max_original_packages: int | None = None
    max_patched_packages: int | None = None


class TunnelsSettingsRequest(BaseModel):
    tailscale: dict[str, Any] | None = None


class EmailSettingsRequest(BaseModel):
    enabled: bool | None = None
    smtp_server: str | None = None
    smtp_port: int | None = None
    use_tls: bool | None = None
    use_ssl: bool | None = None
    sender_name: str | None = None
    sender_email: str | None = None
    recipients: list[str] | str | None = None
    subject_prefix: str | None = None
    attach_result_json: bool | None = None
    layout_mode: str | None = None
    compact_threshold: int | None = None


class TestEmailRequest(BaseModel):
    email_config: dict[str, Any] | None = None
    recipient: str | None = None


class NtfySettingsRequest(BaseModel):
    enabled: bool | None = None
    server: str | None = None
    topic: str | None = None
    token: str | None = None
    username: str | None = None
    password: str | None = None
    clear_token: bool | None = None
    clear_password: bool | None = None
    priority: str | None = None
    tags: str | None = None
    click_url: str | None = None
    notify_on: str | None = None


class TestNtfyRequest(BaseModel):
    ntfy_config: dict[str, Any] | None = None


def format_bytes(bytes_count: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_count < 1024.0:
            return f"{bytes_count:.2f} {unit}"
        bytes_count /= 1024.0
    return f"{bytes_count:.2f} PB"


# Module-level tracking for scheduler execution target: (target_date, schedule_time_str)
last_executed_target: tuple[date, str] | None = None


def rearm_scheduler() -> None:
    """Resets the scheduled run marker so a new or updated schedule can trigger today."""
    global last_executed_target
    last_executed_target = None


def resolve_schedule_target() -> tuple[str, int, int, Any, datetime]:
    """Resolves configured schedule time, target hour/minute, timezone, and current datetime."""
    tz_obj = None
    try:
        sys_settings = config_manager.get_all_settings().get("system", {})
        schedule_str = str(sys_settings.get("schedule_time") or os.getenv("SCHEDULE_TIME", "03:00")).strip()
        tz_str = str(sys_settings.get("timezone") or os.getenv("TZ", "Etc/UTC")).strip()
        try:
            from zoneinfo import ZoneInfo
            tz_obj = ZoneInfo(tz_str)
        except Exception:
            tz_obj = None
    except Exception:
        schedule_str = os.getenv("SCHEDULE_TIME", "03:00").strip()

    try:
        parts = schedule_str.split(":")
        target_hour = int(parts[0])
        target_minute = int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        target_hour, target_minute = 3, 0

    if target_hour >= 24:
        target_hour, target_minute = divmod(target_hour, 60)

    now = datetime.now(tz=tz_obj) if tz_obj else datetime.now()
    return schedule_str, target_hour, target_minute, tz_obj, now


def get_next_run_info() -> dict[str, Any]:
    global last_executed_target
    schedule_str, target_hour, target_minute, _, now = resolve_schedule_target()

    formatted_target_str = f"{target_hour:02d}:{target_minute:02d}"
    target_today = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

    current_target = (now.date(), formatted_target_str)
    if last_executed_target == current_target or now >= target_today:
        next_dt = target_today + timedelta(days=1)
    else:
        next_dt = target_today

    delta = next_dt - now
    total_sec = max(0, int(delta.total_seconds()))
    hours = int(total_sec // 3600)
    minutes = int((total_sec % 3600) // 60)

    return {
        "schedule_time": schedule_str,
        "next_run_iso": next_dt.isoformat(),
        "seconds_until": total_sec,
        "countdown_formatted": f"in {hours}h {minutes}m" if hours > 0 else f"in {minutes}m",
        "display_time": next_dt.strftime("%b %d, %H:%M"),
    }


_last_storage_stats: dict[str, Any] = {}
_last_storage_stats_time: float = 0.0


def get_storage_stats() -> dict[str, Any]:
    global _last_storage_stats, _last_storage_stats_time
    now = time.time()
    if _last_storage_stats and (now - _last_storage_stats_time < 15.0):
        return _last_storage_stats

    patched_dir = PROJECT_ROOT / "apps" / "patched"
    if not patched_dir.is_dir():
        return {"apk_count": 0, "total_bytes": 0, "formatted_size": "0 B"}

    total_bytes = 0
    apk_count = 0
    seen_apks: set[Path] = set()

    # 1. Fast lookup from patched.json registry
    try:
        registry = config_manager.load_patched_registry()
        for pkg, versions in registry.items():
            if isinstance(versions, dict):
                for ver, info in versions.items():
                    if isinstance(info, dict):
                        loc_str = str(info.get("location") or "")
                        loc = Path(loc_str) if loc_str else None
                        if not loc or not loc.is_file():
                            candidate = patched_dir / pkg / ver / f"{pkg}_{ver}_patched.apk"
                            if candidate.is_file():
                                loc = candidate
                        if loc and loc.is_file():
                            sz = loc.stat().st_size
                            total_bytes += sz
                            apk_count += 1
                            seen_apks.add(loc.resolve())
    except Exception:
        pass

    # 2. Check for any legacy or unindexed APKs on disk (shallow 2-level scan)
    try:
        for pkg_entry in patched_dir.iterdir():
            if pkg_entry.is_dir():
                for ver_entry in pkg_entry.iterdir():
                    if ver_entry.is_dir():
                        for apk_file in ver_entry.glob("*.apk"):
                            r_apk = apk_file.resolve()
                            if r_apk not in seen_apks:
                                try:
                                    total_bytes += r_apk.stat().st_size
                                    apk_count += 1
                                    seen_apks.add(r_apk)
                                except Exception:
                                    pass
    except Exception:
        pass

    result = {
        "apk_count": apk_count,
        "total_bytes": total_bytes,
        "formatted_size": format_bytes(total_bytes),
    }
    _last_storage_stats = result
    _last_storage_stats_time = now
    return result


# ---------------------------------------------------------------------------
# Background Scheduler & Startup Runner
# ---------------------------------------------------------------------------

async def scheduler_background_loop() -> None:
    global last_executed_target
    logger.info("[*] Pipeline scheduler initialized in background.")

    while True:
        try:
            _, sched_hour, sched_minute, _, now = resolve_schedule_target()
            formatted_sched_str = f"{sched_hour:02d}:{sched_minute:02d}"
            current_target = (now.date(), formatted_sched_str)

            if (
                now.hour == sched_hour
                and now.minute == sched_minute
                and last_executed_target != current_target
            ):
                logger.info(f"[*] Scheduled time reached ({sched_hour:02d}:{sched_minute:02d}). Launching automated pipeline...")
                try:
                    await runner.trigger_run(source="scheduler")
                    last_executed_target = current_target
                except Exception as e:
                    logger.warning(f"[!] Scheduled run trigger skipped or deferred: {e}")

            await asyncio.sleep(25)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Error in scheduler background loop: {e}")
            await asyncio.sleep(25)


@app.on_event("startup")
async def startup_event() -> None:
    logger.info("Initializing AutoMorph backend...")

    # Ensure required apps and data directories exist
    apps_dir = PROJECT_ROOT / "apps"
    for d in [
        apps_dir,
        apps_dir / "original",
        apps_dir / "patched",
        PROJECT_ROOT / "tools" / "automorph-data",
        PROJECT_ROOT / "tools" / "automorph-data" / "options",
        PROJECT_ROOT / "tools" / "automorph-data" / "state",
        PROJECT_ROOT / "tools" / "automorph-data" / "state" / "logs",
        PROJECT_ROOT / "tools" / "automorph-data" / "state" / "reports",
        PROJECT_ROOT / "tools" / "morphe-data",
    ]:
        d.mkdir(parents=True, exist_ok=True)

    # Ensure default config.yaml and app_sources.yaml exist
    config_manager.ensure_config_exists()

    # Apply configured system timezone
    config_manager.apply_timezone()

    # Start background scheduler
    asyncio.create_task(scheduler_background_loop())

    # Check RUN_ON_STARTUP from config
    settings = config_manager.get_all_settings().get("system", {})
    run_on_startup = settings.get("run_on_startup", False)
    if run_on_startup:
        async def initial_startup_run() -> None:
            await asyncio.sleep(3)  # Brief delay to allow uvicorn to fully bind port 8000
            logger.info("[*] RUN_ON_STARTUP is enabled. Launching initial pipeline execution...")
            try:
                await runner.trigger_run(source="startup")
            except Exception as e:
                logger.warning(f"[!] Initial startup run skipped: {e}")

        asyncio.create_task(initial_startup_run())


def get_app_version() -> dict[str, Any]:
    """Resolves build/version code (e.g. 0.01) from web/version.json or env."""
    version_file = PROJECT_ROOT / "web" / "version.json"
    if version_file.is_file():
        try:
            with version_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
                ver = data.get("version") or data.get("commit") or "0.01"
                branch = data.get("branch")
                commit = data.get("commit")
                run_number = data.get("run_number")
                return {
                    "version": str(ver),
                    "branch": str(branch) if branch else None,
                    "run_number": run_number,
                    "commit": str(commit)[:7] if commit else None,
                }
        except Exception:
            pass
    return {"version": os.getenv("APP_VERSION", "0.01"), "branch": None, "run_number": None, "commit": None}


@app.get("/api/version")
async def get_version() -> dict[str, Any]:
    return get_app_version()


@app.get("/api/updates/check")
async def check_updates(force: bool = False) -> dict[str, Any]:
    """
    Checks GitHub Actions for published image updates on the container's active branch.
    Includes ETag support and 60-second cooldown on force=True.
    """
    return await asyncio.to_thread(update_checker.check_for_updates, force=force)


@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    active_lock = mutex.get_active_lock()
    last_run = runner.get_last_run_info()

    # Read configured apps and distinct repos count
    try:
        cfg = config_manager.get_full_config()
        packages = cfg.get("packages", []) or cfg.get("apps", [])
        configured_apps_count = len(packages)
        all_repos = set()
        for p in packages:
            if isinstance(p, dict):
                for r in p.get("repos", []):
                    if r and isinstance(r, str):
                        all_repos.add(r.strip())
        repos_count = len(all_repos)
    except Exception:
        configured_apps_count = 0
        repos_count = 0
    active_run = None
    if runner.is_running:
        active_run = {
            "run_id": runner.active_run_id,
            "source": runner.active_source,
            "package": runner.active_package,
            "started_at": runner.start_time,
        }
    elif active_lock:
        active_run = {
            "run_id": active_lock.get("run_id"),
            "source": active_lock.get("source"),
            "package": active_lock.get("package"),
            "started_at": active_lock.get("started_at"),
        }

    return {
        "is_running": active_lock is not None or runner.is_running,
        "active_lock": active_lock,
        "active_run": active_run,
        "next_run": get_next_run_info(),
        "last_run": last_run,
        "storage": get_storage_stats(),
        "stats": {
            "configured_apps": configured_apps_count,
            "connected_repos": repos_count,
        },
        "version": get_app_version(),
    }


@app.get("/api/catalog")
async def get_catalog(refresh: bool = False) -> Response:
    """
    Serves the Morphe bundles catalog (store, bundles, compatibilities) enriched
    with official Morphe default applications and the MorpheApp/morphe-patches bundle.
    """
    try:
        import sys
        scripts_dir = str(PROJECT_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import apkpure_fetcher

        data = apkpure_fetcher.get_morphe_catalog(force_refresh=refresh)
        return JSONResponse(data)
    except Exception as e:
        logger.error(f"Error fetching catalog: {e}", exc_info=True)
        try:
            import apkpure_fetcher
            catalog_file = PROJECT_ROOT / "tools" / "automorph-data" / "state" / "morphe_catalog.json"
            if catalog_file.is_file():
                with open(catalog_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    return JSONResponse(apkpure_fetcher.enrich_morphe_catalog(raw))
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    try:
        return config_manager.get_full_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    try:
        return config_manager.get_all_settings()
    except Exception as e:
        logger.error(f"Error fetching settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/settings/system")
async def update_system_settings(req: SystemSettingsRequest) -> dict[str, Any]:
    try:
        updated = config_manager.update_system_settings(req.dict(exclude_unset=True))
        rearm_scheduler()
        return {"status": "success", "settings": updated}
    except Exception as e:
        logger.error(f"Error updating system settings: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/settings/tunnels")
async def update_tunnels_settings(req: TunnelsSettingsRequest) -> dict[str, Any]:
    try:
        updated = config_manager.update_tunnels_settings(req.dict(exclude_unset=True))
        return {"status": "success", "tunnels": updated}
    except Exception as e:
        logger.error(f"Error updating tunnels settings: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))



@app.post("/api/settings/email")
async def update_email_settings(req: EmailSettingsRequest) -> dict[str, Any]:
    try:
        updated = config_manager.update_email_settings(req.dict(exclude_unset=True))
        return {"status": "success", "email": updated}
    except Exception as e:
        logger.error(f"Error updating email settings: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/settings/email/test")
async def test_email(req: TestEmailRequest | None = None) -> dict[str, Any]:
    try:
        cfg = req.email_config if req else None
        rcpt = req.recipient if req else None
        res = config_manager.send_test_email(override=cfg, recipient=rcpt)
        if not res.get("success"):
            return JSONResponse(status_code=400, content=res)
        return res
    except Exception as e:
        logger.error(f"Error sending test email: {e}", exc_info=True)
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})


@app.get("/api/settings/ntfy/presets")
async def get_ntfy_presets() -> dict[str, Any]:
    """Public ntfy instances offered as presets; custom servers are always allowed."""
    import sys

    scripts_dir = str(PROJECT_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        import ntfy_notify

        return {"presets": ntfy_notify.SERVER_PRESETS}
    except Exception as e:
        logger.error(f"Error loading ntfy presets: {e}", exc_info=True)
        return {"presets": [{"url": "https://ntfy.sh", "label": "ntfy.sh (official, free)"}]}


@app.post("/api/settings/ntfy")
async def update_ntfy_settings(req: NtfySettingsRequest) -> dict[str, Any]:
    try:
        updated = config_manager.update_ntfy_settings(req.dict(exclude_unset=True))
        return {"status": "success", "ntfy": updated}
    except Exception as e:
        logger.error(f"Error updating ntfy settings: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/settings/ntfy/test")
async def test_ntfy(req: TestNtfyRequest | None = None) -> Any:
    try:
        cfg = req.ntfy_config if req else None
        res = config_manager.send_test_ntfy(override=cfg)
        if not res.get("success"):
            return JSONResponse(status_code=400, content=res)
        return res
    except Exception as e:
        logger.error(f"Error sending ntfy test notification: {e}", exc_info=True)
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})


@app.post("/api/config/app")
async def add_or_update_app(req: AppConfigRequest) -> dict[str, Any]:
    try:
        result = config_manager.add_or_update_app(req.dict())
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/config/app/toggle")
async def toggle_app(req: AppToggleRequest) -> dict[str, Any]:
    try:
        success = config_manager.toggle_app_enabled(req.package, req.enabled)
        if not success:
            raise HTTPException(status_code=404, detail=f"Package '{req.package}' not found in configuration.")
        return {"status": "success", "package": req.package, "enabled": req.enabled}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/config/app/{package}")
async def delete_app(package: str) -> dict[str, Any]:
    success = config_manager.delete_app(package)
    if not success:
        raise HTTPException(status_code=404, detail="App package not found in config.")
    return {"status": "success", "package": package}


@app.get("/api/config/repos")
async def get_repos() -> list[str]:
    return config_manager.get_patch_repos()


@app.post("/api/config/repos")
async def add_repo(req: RepoRequest) -> dict[str, Any]:
    added = config_manager.add_patch_repo(req.repo_url)
    if not added:
        raise HTTPException(status_code=400, detail="Repo already exists or URL invalid.")
    return {"status": "success", "repo_url": req.repo_url}


@app.delete("/api/config/repos")
async def delete_repo(req: RepoRequest) -> dict[str, Any]:
    removed = config_manager.delete_patch_repo(req.repo_url)
    if not removed:
        raise HTTPException(status_code=404, detail="Repo URL not found.")
    return {"status": "success", "repo_url": req.repo_url}


# ---------------------------------------------------------------------------
# Morphe Patch Options Management (/api/options)
# ---------------------------------------------------------------------------

@app.get("/api/options/{package}")
async def get_app_options(package: str) -> dict[str, Any]:
    try:
        return options_manager.get_app_options(package)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/options/{package}/{owner}")
async def get_owner_options(package: str, owner: str) -> dict[str, Any]:
    try:
        opt = options_manager.get_owner_options(package, owner)
        if opt is None:
            raise HTTPException(status_code=404, detail=f"No options found for {package}/{owner}")
        return opt
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Dedicated unambiguous route + static download-server route
# MUST be defined before parameterized @app.post("/api/options/{package}/{owner}")
@app.post("/api/options-fetch/{package}")
@app.post("/api/options/{package}/download-server")
async def download_server_options(package: str, req: DownloadOptionsRequest | None = None) -> dict[str, Any]:
    try:
        repos = req.repos if req else []
        force = req.force if req else False
        res = options_manager.ensure_app_options(package, repos=repos, force=force)
        return res
    except Exception as e:
        logger.error(f"Error ensuring options on server for {package}: {e}", exc_info=True)
        return {
            "success": False,
            "package": package,
            "files": {},
            "repo_statuses": {},
            "logs": [f"[{datetime.now().strftime('%H:%M:%S')}] Server Error: {str(e)}"],
            "error": str(e),
        }


@app.post("/api/options/{package}/{owner}")
async def save_owner_options(package: str, owner: str, req: dict[str, Any]) -> dict[str, Any]:
    try:

        patches = req.get("patches", {}) if isinstance(req, dict) else {}
        meta = req.get("meta") if isinstance(req, dict) else None
        return options_manager.save_owner_options(package, owner, patches, meta)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



async def _trigger_pipeline(package: str | None = None, source: str = "manual") -> dict[str, Any]:
    uncommented = False
    if package:
        try:
            uncommented = config_manager.ensure_app_enabled(package)
        except Exception as e:
            logger.warning(f"Failed to ensure app '{package}' is enabled in config: {e}")
    try:
        res = await runner.trigger_run(source=source, package=package)
        if isinstance(res, dict):
            res["uncommented"] = uncommented
        return res
    except RuntimeError as e:
        try:
            lock_info = json.loads(str(e))
        except Exception:
            lock_info = {"error": str(e)}

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "A patching pipeline execution is already in progress.",
                "active_lock": lock_info,
                "uncommented": uncommented,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/run")
async def trigger_run(req: RunRequest | None = None) -> dict[str, Any]:
    target_pkg = req.package if req else None
    src = req.source if req else "manual"
    return await _trigger_pipeline(package=target_pkg, source=src)


@app.post("/api/run/stop")
async def stop_run() -> dict[str, Any]:
    """
    Stops the active pipeline process (SIGINT to process group, like Ctrl+C)
    and releases the execution lock.
    """
    try:
        return await runner.stop_run()
    except Exception as e:
        logger.error(f"Error stopping run: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/run/{package}")
async def trigger_single_package_run(package: str) -> dict[str, Any]:
    return await _trigger_pipeline(package=package, source="manual")


@app.get("/api/run/logs")
async def get_run_logs() -> dict[str, Any]:
    """
    Returns historical and latest log lines for the last pipeline execution.
    """
    return runner.get_last_run_logs()


@app.get("/api/run/stream")
async def stream_logs() -> StreamingResponse:
    """
    Streams pipeline stdout/stderr line-by-line via Server-Sent Events.
    Explicit anti-buffering directives ensure real-time chunk delivery over Ingress & Tunnels.
    """
    sse_headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        runner.log_stream_generator(),
        media_type="text/event-stream",
        headers=sse_headers,
    )


# ---------------------------------------------------------------------------
# Patched APK Directory Browser & File API (/api/files, /files)
# ---------------------------------------------------------------------------

@app.get("/api/files")
async def api_list_patched_files(path: str = "") -> dict[str, Any]:
    """
    Returns structured directory contents of apps/patched/ for the
    Control Center file browser.
    """
    patched_root = (PROJECT_ROOT / "apps" / "patched").resolve()
    target_path = (patched_root / path.strip("/")).resolve()

    # Security check: directory traversal protection
    try:
        target_path.relative_to(patched_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Directory not found")

    if not target_path.is_dir():
        raise HTTPException(status_code=400, detail="Target path is not a directory")

    dirs = []
    files = []
    total_bytes = 0
    apk_count = 0

    tz = config_manager.get_current_timezone()
    try:
        for entry in target_path.iterdir():
            try:
                st = entry.stat()
                rel = str(entry.relative_to(patched_root))
                if entry.is_dir():
                    # Count child items if accessible
                    child_count = 0
                    try:
                        child_count = sum(1 for _ in entry.iterdir())
                    except Exception:
                        pass
                    dirs.append({
                        "name": entry.name,
                        "path": rel,
                        "url": f"/files/{rel}",
                        "item_count": child_count,
                        "mtime": datetime.fromtimestamp(st.st_mtime, tz=tz).strftime("%Y-%m-%d %H:%M"),
                    })
                else:
                    total_bytes += st.st_size
                    is_apk = entry.suffix.lower() == ".apk"
                    is_result_json = entry.name.lower() == "result.json"
                    if is_apk:
                        apk_count += 1
                    files.append({
                        "name": entry.name,
                        "path": rel,
                        "size": format_bytes(st.st_size),
                        "raw_size": st.st_size,
                        "url": f"/files/{rel}",
                        "mtime": datetime.fromtimestamp(st.st_mtime, tz=tz).strftime("%Y-%m-%d %H:%M"),
                        "is_apk": is_apk,
                        "is_result_json": is_result_json,
                    })
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Error reading directory {target_path}: {e}")

    dirs.sort(key=lambda x: x["name"].lower())
    files.sort(key=lambda x: (not x["is_apk"], x["name"].lower()))

    rel_path = ""
    breadcrumbs = [{"name": "root", "path": ""}]
    if target_path != patched_root:
        rel_path = str(target_path.relative_to(patched_root))
        parts = [p for p in rel_path.split("/") if p and p != "."]
        acc = ""
        for p in parts:
            acc = f"{acc}/{p}".strip("/")
            breadcrumbs.append({"name": p, "path": acc})

    parent_path = None
    if target_path != patched_root:
        parent_rel = str(target_path.parent.relative_to(patched_root))
        parent_path = "" if parent_rel == "." else parent_rel

    return {
        "current_path": rel_path,
        "parent_path": parent_path,
        "breadcrumbs": breadcrumbs,
        "dirs": dirs,
        "files": files,
        "total_bytes": total_bytes,
        "formatted_total_bytes": format_bytes(total_bytes),
        "apk_count": apk_count,
    }


@app.get("/files")
@app.get("/files/{subpath:path}")
async def serve_patched_files(request: Request, subpath: str = "") -> Response:
    """
    Serves files from apps/patched/.
    - Directories: Redirects browser requests into the Control Center's unified Files view (/#files/...).
    - Files: Serves APKs as downloadable attachments and JSON files inline for viewing
      (or attachment if ?download=1).
    """
    patched_root = (PROJECT_ROOT / "apps" / "patched").resolve()
    target_path = (patched_root / subpath.strip("/")).resolve()

    # Security check: directory traversal protection
    try:
        target_path.relative_to(patched_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not target_path.exists():
        raise HTTPException(status_code=404, detail="File or folder not found")

    if target_path.is_file():
        is_download = request.query_params.get("download") in ("1", "true", "yes")

        if target_path.suffix.lower() == ".apk":
            return FileResponse(
                target_path,
                filename=target_path.name,
                media_type="application/vnd.android.package-archive",
            )
        elif target_path.suffix.lower() == ".json":
            if is_download:
                return FileResponse(
                    target_path,
                    filename=target_path.name,
                    media_type="application/json",
                )
            else:
                return FileResponse(
                    target_path,
                    media_type="application/json",
                )
        else:
            if is_download:
                return FileResponse(target_path, filename=target_path.name)
            return FileResponse(target_path)

    # Directory access: Seamlessly redirect into dashboard's unified file browser in current window
    rel_path = ""
    if target_path != patched_root:
        rel_path = str(target_path.relative_to(patched_root))

    redirect_target = f"/#/files/{rel_path}" if rel_path else "/#/files"
    return RedirectResponse(url=redirect_target, status_code=status.HTTP_302_FOUND)



# ---------------------------------------------------------------------------
# Static Frontend Serving (Fallback and Standalone Development)
# ---------------------------------------------------------------------------
from fastapi.staticfiles import StaticFiles

class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Any) -> Response:
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp

FRONTEND_DIR = PROJECT_ROOT / "web" / "frontend"

@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    ico_path = FRONTEND_DIR / "favicon.svg"
    if ico_path.is_file():
        return FileResponse(ico_path, media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="Favicon not found")


@app.get("/report", include_in_schema=True)
@app.get("/build_report.html", include_in_schema=False)
@app.get("/api/run/report", include_in_schema=True)
async def get_build_report():
    """
    Serves the latest full HTML build report generated by the dispatcher.
    """
    report_file = PROJECT_ROOT / "tools" / "automorph-data" / "state" / "reports" / "build_report.html"
    if report_file.is_file():
        return FileResponse(report_file, media_type="text/html")
    raise HTTPException(status_code=404, detail="Build report not found")

LOGO_DIR = PROJECT_ROOT / "Logo"
if LOGO_DIR.is_dir():
    app.mount("/logo", StaticFiles(directory=str(LOGO_DIR)), name="logo")

if FRONTEND_DIR.is_dir():
    app.mount("/", NoCacheStaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
