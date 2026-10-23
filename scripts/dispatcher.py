"""
Email dispatcher module for AutoMorph / Morphe Patcher.

Constructs multi-service download links (Cloudflare, Pangolin, Tailscale, Local),
loads pre-computed metadata from state/patched.json, records run reports for
the web portal, and dispatches responsive dual-MIME (HTML + Plain Text)
notifications styled in the Morphe Obsidian Cyber Synthetics design system.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
import html
import json
import logging
import os
from pathlib import Path
import smtplib
import time
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
import yaml

try:
    from . import email_template
except ImportError:
    import email_template

try:
    from . import ntfy_notify
except ImportError:
    import ntfy_notify

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration & Tunnel Discovery
# ---------------------------------------------------------------------------

def load_config(project_root: Path | None = None) -> dict:
    """Loads config.yaml and resolves environment variables."""
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    config_path = project_root / "tools" / "automorph-data" / "config.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"Missing config.yaml at {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    env_rel = config.get("env_location", ".env")
    env_path = (project_root / env_rel).resolve()
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path)

    email_conf = config.setdefault("email", {})
    email_conf["username"] = (
        email_conf.get("username")
        or (os.getenv(email_conf.get("username_env")) if email_conf.get("username_env") else None)
        or os.getenv("SMTP_USERNAME")
        or os.getenv("SMTP_USER")
        or email_conf.get("sender_email", "")
    )
    email_conf["password"] = (
        email_conf.get("password")
        or (os.getenv(email_conf.get("password_env")) if email_conf.get("password_env") else None)
        or os.getenv("SMTP_PASSWORD")
        or os.getenv("SMTP_PASS")
        or ""
    )

    config["project_root"] = project_root
    return config


def normalize_base_url(url: str | None) -> str:
    """Ensures URL starts with http:// or https:// without trailing slash."""
    if not url:
        return ""
    clean = str(url).strip()
    if not clean:
        return ""
    if not clean.startswith("http://") and not clean.startswith("https://"):
        clean = "https://" + clean
    return clean.rstrip("/")


def resolve_tunnel_endpoints(config: dict | None = None) -> list[dict[str, str]]:
    """
    Detects and normalizes all active tunnel endpoints independently:
      - Cloudflare: CLOUDFLARE_TUNNEL_URL or 'cloudflare' in COMPOSE_PROFILES (#F38020)
      - Pangolin:   PANGOLIN_URL or 'pangolin' in COMPOSE_PROFILES (#DC2626)
      - Tailscale:  TAILSCALE_FUNNEL_URL, TAILSCALE_URL, or config tailscale.base_url (#121316)
      - Local:      http://localhost:8080 (fallback if none configured)
    """
    config = config or {}
    endpoints: list[dict[str, str]] = []

    profiles_env = os.getenv("COMPOSE_PROFILES", "").strip().lower()
    active_profiles = [p.strip() for p in profiles_env.split(",") if p.strip()]

    # 1. Cloudflare Tunnel
    cf_url_raw = os.getenv("CLOUDFLARE_TUNNEL_URL", "").strip()
    if not cf_url_raw and "cloudflare" in active_profiles:
        cf_url_raw = os.getenv("CLOUDFLARE_URL", "").strip()
    if cf_url_raw:
        endpoints.append({
            "id": "cloudflare",
            "label": "Cloudflare",
            "badge": "CF",
            "icon": "☁️",
            "color": "#F38020",
            "border_color": "#ea580c",
            "base_url": normalize_base_url(cf_url_raw),
        })

    # 2. Pangolin Tunnel
    pangolin_url_raw = os.getenv("PANGOLIN_URL", "").strip()
    if not pangolin_url_raw and "pangolin" in active_profiles:
        pangolin_url_raw = os.getenv("PANGOLIN_ENDPOINT", "").strip()
    if pangolin_url_raw:
        endpoints.append({
            "id": "pangolin",
            "label": "Pangolin",
            "badge": "Pangolin",
            "icon": "🌐",
            "color": "#DC2626",
            "border_color": "#b91c1c",
            "base_url": normalize_base_url(pangolin_url_raw),
        })

    # 3. Tailscale Funnel (runs independently of compose profiles)
    ts_conf = config.get("tailscale")
    ts_config_base = str(ts_conf.get("base_url") or "") if isinstance(ts_conf, dict) else ""
    ts_url_raw = (
        os.getenv("TAILSCALE_FUNNEL_URL", "").strip()
        or os.getenv("TAILSCALE_URL", "").strip()
        or os.getenv("TAILSCALE_BASE_URL", "").strip()
        or ts_config_base.strip()
    )
    if ts_url_raw:
        endpoints.append({
            "id": "tailscale",
            "label": "Tailscale",
            "badge": "Tailscale",
            "icon": "🔒",
            "color": "#121316",
            "border_color": "#333A48",
            "base_url": normalize_base_url(ts_url_raw),
        })

    # 4. Fallback to Local Origin if no tunnels are configured
    if not endpoints:
        endpoints.append({
            "id": "local",
            "label": "Local",
            "badge": "Local",
            "icon": "⚡",
            "color": "#4A7FC8",
            "border_color": "#3b69a6",
            "base_url": "http://localhost:8080",
        })

    return endpoints


# ---------------------------------------------------------------------------
# State & Data Ingestion
# ---------------------------------------------------------------------------

def load_patched_registry(state_dir: Path) -> dict[str, Any]:
    """Loads the pre-computed patched.json registry."""
    patched_file = state_dir / "patched.json"
    if not patched_file.is_file():
        return {}
    try:
        return json.loads(patched_file.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning(f"Could not parse {patched_file}: {e}")
        return {}


def build_app_success_record(
    pkg_name: str,
    raw_val: Any,
    patched_registry: dict[str, Any],
    endpoints: list[dict[str, str]],
    patched_dir: Path,
    global_arch: str,
) -> dict[str, Any]:
    """
    Builds a complete, normalized success app record using patched.json as the
    primary source of truth (anticipating pre-calculated file_size and sha256).
    """
    raw_dict = raw_val if isinstance(raw_val, dict) else {"dest": raw_val}
    dest_path_raw = raw_dict.get("dest") or raw_dict.get("path")
    resolved_apk = Path(dest_path_raw).resolve() if dest_path_raw else None

    # Check patched.json registry first
    pkg_registry = patched_registry.get(pkg_name, {})
    latest_ver = None
    reg_ver_info = {}

    if isinstance(pkg_registry, dict) and pkg_registry:
        # If APK path specifies a version directory, use that; else latest recorded
        candidate_ver = resolved_apk.parent.name if (resolved_apk and resolved_apk.parent) else None
        if candidate_ver and candidate_ver in pkg_registry:
            latest_ver = candidate_ver
            reg_ver_info = pkg_registry[candidate_ver]
        else:
            latest_ver = list(pkg_registry.keys())[-1]
            reg_ver_info = pkg_registry[latest_ver]

    version_str = latest_ver or (resolved_apk.parent.name if resolved_apk and resolved_apk.parent else "Unknown")
    arch = reg_ver_info.get("arch") or raw_dict.get("arch") or global_arch

    # File size: Check patched.json first (anticipation), fallback to stat() if not yet populated
    file_size_val = reg_ver_info.get("file_size") or reg_ver_info.get("size_bytes")
    if file_size_val:
        file_size_str = email_template.format_file_size(file_size_val)
    elif resolved_apk and resolved_apk.is_file():
        file_size_str = email_template.format_file_size(resolved_apk.stat().st_size)
    else:
        file_size_str = "Unknown size"

    # Pre-computed SHA-256
    checksum_str = reg_ver_info.get("sha256") or raw_dict.get("sha256")

    # Repositories & Patches
    repos = reg_ver_info.get("repos") or raw_dict.get("applied_repos") or raw_dict.get("scanned_repos") or []
    applied_patches_raw = reg_ver_info.get("applied_patches") or {}
    applied_patches = []
    if isinstance(applied_patches_raw, dict):
        for p_name, p_repo in applied_patches_raw.items():
            applied_patches.append({"name": p_name, "repo": str(p_repo)})
    elif isinstance(applied_patches_raw, list):
        for item in applied_patches_raw:
            if isinstance(item, dict):
                applied_patches.append(item)
            elif isinstance(item, str):
                applied_patches.append({"name": item, "repo": ""})

    # Strategy & Attempt Resolution
    strat_num = reg_ver_info.get("strat") or raw_dict.get("strategy") or 1
    att_num = reg_ver_info.get("attempt") or raw_dict.get("attempt") or 1
    strat_short = f"Strategy {strat_num}"
    strat_long = f"Strategy {strat_num} ({'All Patches' if str(strat_num) == '1' else 'Version-Compatible'})"
    winning_badge = f"{strat_short} • Attempt {att_num}"
    winning_full = f"{strat_long} • Attempt {att_num}"

    # Generate Download URLs for each active endpoint
    download_urls = {}
    if resolved_apk:
        for ep in endpoints:
            b_url = ep.get("base_url")
            if not b_url:
                continue
            try:
                rel_apk = resolved_apk.relative_to(patched_dir)
                d_url = f"{b_url.rstrip('/')}/files/{rel_apk.as_posix()}"
            except Exception:
                d_url = f"{b_url.rstrip('/')}/files/{resolved_apk.name}"
            download_urls[ep["id"]] = {
                **ep,
                "url": d_url,
            }

    filename = resolved_apk.name if resolved_apk else f"{pkg_name}.apk"

    # Minimal result.json check strictly for result file attachment
    result_file = resolved_apk.parent / "result.json" if resolved_apk else None

    return {
        "package": pkg_name,
        "version": version_str,
        "arch": arch,
        "filename": filename,
        "file_size": file_size_str,
        "checksum": checksum_str,
        "applied_repos": repos,
        "winning_badge": winning_badge,
        "winning_full": winning_full,
        "download_urls": download_urls,
        "result_file": result_file if (result_file and result_file.is_file()) else None,
        "patch_info": {
            "applied_patches": applied_patches,
            "failed_patches": [],
            "unapplied_patches": [],
        },
    }


def build_app_failure_record(pkg_name: str, fail_info: dict, global_arch: str) -> dict[str, Any]:
    """
    Builds a lightweight failure record directly from pipeline arguments.
    No file size calculation or disk walking is performed for failed builds.
    """
    raw_targets = fail_info.get("target_versions") or []
    target_versions = [
        str(t[0]) if isinstance(t, (tuple, list)) and len(t) > 0 else str(t)
        for t in (list(raw_targets) if isinstance(raw_targets, (list, tuple, set)) else [raw_targets])
        if t is not None
    ]
    arch = fail_info.get("arch") or global_arch
    attempts = fail_info.get("attempts") or []

    grouped: dict[str, list[dict]] = {}
    for att in attempts:
        raw_v = att.get("version") or att.get("target_version")
        ver = str(raw_v[0]) if isinstance(raw_v, (list, tuple)) and raw_v else str(raw_v or "Unknown")
        grouped.setdefault(ver, []).append(att)

    return {
        "package": pkg_name,
        "target_versions": target_versions,
        "arch": arch,
        "attempts": attempts,
        "grouped_attempts": grouped,
    }


# ---------------------------------------------------------------------------
# Email Generation & Delivery
# ---------------------------------------------------------------------------

def generate_email_content(
    patch_success: dict[str, Path | dict],
    failures: dict[str, dict] | None,
    config: dict,
    project_root: Path | None = None,
    log_content: str | Path | None = None,
    duration: str | float | int | None = None,
    build_flags: dict | None = None,
) -> tuple[str, str, str, list[Path]]:
    """
    Orchestrates data aggregation and renders Subject, Plain Text, and HTML
    via email_template module.
    """
    project_root = project_root or config.get("project_root") or Path(__file__).resolve().parent.parent
    email_conf = config.get("email") or {}
    patched_dir = (project_root / "apps" / "patched").resolve()

    raw_state_loc = config.get("state_location")
    if raw_state_loc:
        s_path = Path(raw_state_loc)
        state_dir = s_path if s_path.is_absolute() else (project_root / s_path).resolve()
    else:
        state_dir = (project_root / "tools" / "automorph-data" / "state").resolve()

    reports_dir = state_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / "build_report.html"

    global_arch = (build_flags or {}).get("architecture", config.get("architecture", "arm64-v8a"))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    duration_str = email_template.format_duration(duration)

    # 1. Resolve Active Tunnel Endpoints (Cloudflare, Pangolin, Tailscale, Local)
    endpoints = resolve_tunnel_endpoints(config)
    ep_ids = {ep["id"] for ep in endpoints}

    if "cloudflare" in ep_ids and "tailscale" in ep_ids:
        security_badge_text = "Cloudflare & Tailscale Tunnels"
    elif "cloudflare" in ep_ids:
        security_badge_text = "Cloudflare Tunnel"
    elif "pangolin" in ep_ids:
        security_badge_text = "Pangolin Tunnel"
    elif "tailscale" in ep_ids:
        security_badge_text = "Tailscale Funnel"
    else:
        security_badge_text = "Local Server"

    valid_eps = [ep for ep in endpoints if ep.get("base_url")]
    host_display = " • ".join(
        urlparse(ep["base_url"]).netloc or ep["base_url"].replace("https://", "").replace("http://", "")
        for ep in valid_eps
    ) if valid_eps else "localhost:8080"

    web_report_endpoints = [
        {"id": ep["id"], "label": f"{ep['label']} Portal", "url": f"{ep['base_url'].rstrip('/')}/"}
        for ep in endpoints if ep.get("base_url")
    ]

    # 2. Ingest Data using patched.json
    patched_registry = load_patched_registry(state_dir)
    success_records: list[dict[str, Any]] = []
    attachments: list[Path] = []
    attach_json = email_conf.get("attach_result_json", False)

    for pkg_name, raw_val in (patch_success or {}).items():
        record = build_app_success_record(
            pkg_name=pkg_name,
            raw_val=raw_val,
            patched_registry=patched_registry,
            endpoints=endpoints,
            patched_dir=patched_dir,
            global_arch=global_arch,
        )
        success_records.append(record)
        if attach_json and record.get("result_file"):
            attachments.append(record["result_file"])

    failure_records: list[dict[str, Any]] = []
    for pkg_name, fail_info in (failures or {}).items():
        if isinstance(fail_info, dict):
            failure_records.append(build_app_failure_record(pkg_name, fail_info, global_arch))

    raw_log_str = None
    if isinstance(log_content, Path) and log_content.is_file():
        attachments.append(log_content)
        try:
            raw_log_str = log_content.read_text(encoding="utf-8")
        except Exception:
            pass
    elif isinstance(log_content, str) and log_content.strip():
        raw_log_str = log_content.strip()

    # 3. Status Badges & Subject Line
    prefix = email_conf.get("subject_prefix", "[AutoMorph]")
    total_success = len(success_records)
    total_failures = len(failure_records)

    if total_failures > 0:
        if total_success == 0:
            subject = f"{prefix} ❌ Build Failed: All {total_failures} App{'s' if total_failures != 1 else ''} Failed"
            badge_text = "Build Failed"
            badge_bg = "rgba(239, 68, 68, 0.15)"
            badge_border = "rgba(239, 68, 68, 0.35)"
            badge_dot = "#ef4444"
            badge_color = "#fca5a5"
        else:
            subject = f"{prefix} ⚠️ Build Report: {total_success} Succeeded, {total_failures} Failed"
            badge_text = "Partial Pass"
            badge_bg = "rgba(245, 158, 11, 0.15)"
            badge_border = "rgba(245, 158, 11, 0.35)"
            badge_dot = "#f59e0b"
            badge_color = "#fbbf24"
    elif total_success == 0:
        subject = f"{prefix} ℹ️ All Configured Packages Up to Date"
        badge_text = "Up to Date"
        badge_bg = "rgba(16, 185, 129, 0.15)"
        badge_border = "rgba(16, 185, 129, 0.35)"
        badge_dot = "#10b981"
        badge_color = "#34d399"
    else:
        subject = f"{prefix} 🚀 {total_success} App{'s' if total_success != 1 else ''} Patched Successfully"
        badge_text = "All Passed"
        badge_bg = "rgba(16, 185, 129, 0.15)"
        badge_border = "rgba(16, 185, 129, 0.35)"
        badge_dot = "#10b981"
        badge_color = "#34d399"

    # Layout Mode (compact vs full cards)
    layout_mode = str(email_conf.get("layout_mode", "auto")).lower()
    compact_threshold = int(email_conf.get("compact_threshold", 15))
    total_apps = total_success + total_failures
    is_compact = (layout_mode == "compact") or (layout_mode == "auto" and total_apps > compact_threshold)

    # 4. Generate Full HTML Document for Web Report
    full_report_html = email_template.assemble_email_html(
        subject=subject,
        badge_text=badge_text,
        badge_bg=badge_bg,
        badge_border=badge_border,
        badge_dot=badge_dot,
        badge_color=badge_color,
        now_str=now_str,
        host_display=host_display,
        total_success=total_success,
        total_failures=total_failures,
        duration_str=duration_str,
        success_records=success_records,
        failure_records=failure_records,
        build_flags=build_flags,
        web_report_endpoints=web_report_endpoints,
        is_compact=False,
        security_badge_text=security_badge_text,
        raw_log_str=raw_log_str,
    )

    try:
        report_path.write_text(full_report_html, encoding="utf-8")
        logger.info(f"Saved full build report to: {report_path}")
    except Exception as e:
        logger.warning(f"Failed to write build report to {report_path}: {e}")

    # 5. Generate Email HTML & Plain Text
    email_html = email_template.assemble_email_html(
        subject=subject,
        badge_text=badge_text,
        badge_bg=badge_bg,
        badge_border=badge_border,
        badge_dot=badge_dot,
        badge_color=badge_color,
        now_str=now_str,
        host_display=host_display,
        total_success=total_success,
        total_failures=total_failures,
        duration_str=duration_str,
        success_records=success_records,
        failure_records=failure_records,
        build_flags=build_flags,
        web_report_endpoints=web_report_endpoints,
        is_compact=is_compact,
        security_badge_text=security_badge_text,
        raw_log_str=raw_log_str,
    )

    plain_text = email_template.assemble_plain_text(
        now_str=now_str,
        host_display=host_display,
        total_success=total_success,
        total_failures=total_failures,
        duration_str=duration_str,
        success_records=success_records,
        failure_records=failure_records,
        build_flags=build_flags,
        web_report_endpoints=web_report_endpoints,
        raw_log_str=raw_log_str,
    )

    return subject, plain_text, email_html, attachments


def send_email(
    subject: str,
    text_content: str,
    html_content: str,
    config: dict,
    attachments: list[Path] | None = None,
) -> None:
    """Delivers dual-MIME email message via authenticated SMTP (TLS/SSL)."""
    email_conf = config.get("email") or {}
    smtp_server = email_conf.get("smtp_server")
    smtp_port = email_conf.get("smtp_port")
    use_tls = email_conf.get("use_tls", True)
    use_ssl = email_conf.get("use_ssl", False)
    sender_name = email_conf.get("sender_name", "AutoMorph")
    sender_email = email_conf.get("sender_email")
    recipients = email_conf.get("recipients", [])
    username = email_conf.get("username", "")
    password = email_conf.get("password", "")

    if not recipients:
        raise ValueError("Cannot send email: No recipients specified in config.yaml")
    if not sender_email:
        raise ValueError("Cannot send email: No sender_email specified in config.yaml")

    msg = MIMEMultipart("mixed")
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender_email.split("@")[-1] if "@" in sender_email else None)

    alt_part = MIMEMultipart("alternative")
    alt_part.attach(MIMEText(text_content, "plain", "utf-8"))
    alt_part.attach(MIMEText(html_content, "html", "utf-8"))
    msg.attach(alt_part)

    for att in (attachments or []):
        if att and att.is_file():
            try:
                part = MIMEApplication(att.read_bytes(), Name=att.name)
                part["Content-Disposition"] = f'attachment; filename="{att.name}"'
                msg.attach(part)
            except Exception as e:
                logger.warning(f"Could not attach {att.name}: {e}")

    logger.info(f"Connecting to SMTP server {smtp_server}:{smtp_port} (TLS={use_tls}, SSL={use_ssl})...")
    if use_ssl:
        server: smtplib.SMTP = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)

    try:
        server.ehlo()
        if use_tls and not use_ssl:
            server.starttls()
            server.ehlo()
        if username and password:
            server.login(username, password)
        server.send_message(msg, from_addr=sender_email, to_addrs=recipients)
        logger.info(f" └── [✓] Email successfully sent to {', '.join(recipients)}")
    finally:
        try:
            server.quit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main Entrypoint & CLI
# ---------------------------------------------------------------------------

def portal_base(config: dict) -> str | None:
    """
    Public base URL of the portal (first configured tunnel). Returns None when
    only the local fallback exists, a localhost URL is useless in a push
    notification, so the notification config decides instead.
    """
    try:
        endpoints = resolve_tunnel_endpoints(config)
        base = next((e["base_url"] for e in endpoints if e.get("id") != "local"), None)
        return base.rstrip("/") if base else None
    except Exception:
        return None


def portal_link(patch_success: dict[str, Any], config: dict) -> str | None:
    """
    Builds the link opened from the notification: the patched APK itself when
    exactly one app was built, otherwise the portal root.
    """
    try:
        base = portal_base(config)
        if not base:
            return None
        if len(patch_success) != 1:
            return base
        pkg, data = next(iter(patch_success.items()))
        dest = data.get("dest") if isinstance(data, dict) else data
        if not dest:
            return base
        patched_root = Path(config.get("project_root", ".")) / "apps" / "patched"
        rel = Path(str(dest)).resolve().relative_to(patched_root.resolve())
        return f"{base}/files/{rel.as_posix()}"
    except Exception:
        return None

def main(
    patch_success: dict[str, Path | dict],
    failures: dict[str, dict] | None = None,
    log_content: str | Path | None = None,
    duration: str | float | int | None = None,
    dry_run: bool = False,
    project_root: Path | None = None,
    build_flags: dict | None = None,
) -> tuple[Path | None, str]:
    """
    Main dispatch entrypoint:
      1. Aggregates data & generates email content.
      2. Persists last_run_report.json and package_runs/<pkg>.json.
      3. Sends email via SMTP (or writes preview file in dry-run mode).
    """
    config = load_config(project_root)
    root = config["project_root"]
    failures = failures or {}

    subject, text_body, html_body, attachments = generate_email_content(
        patch_success=patch_success,
        failures=failures,
        config=config,
        project_root=root,
        log_content=log_content,
        duration=duration,
        build_flags=build_flags,
    )

    # Persist last_run_report and package_runs for Web Dashboard
    try:
        raw_state_loc = config.get("state_location")
        state_dir = Path(raw_state_loc) if raw_state_loc else (root / "tools" / "automorph-data" / "state")
        state_dir = state_dir if state_dir.is_absolute() else (root / state_dir).resolve()

        serialized_success: dict[str, Any] = {}
        for k, v in patch_success.items():
            if isinstance(v, dict):
                serialized_success[k] = {
                    key: (str(val) if isinstance(val, Path) else val)
                    for key, val in v.items()
                }
            else:
                serialized_success[k] = {"dest": str(v), "attempts": []}

        def _clean_for_json(obj: Any) -> Any:
            if isinstance(obj, Path):
                return str(obj)
            if isinstance(obj, dict):
                return {k: _clean_for_json(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple, set)):
                return [_clean_for_json(x) for x in obj]
            return obj

        report_data = {
            "timestamp": time.time(),
            "duration": duration,
            "success": serialized_success,
            "failures": _clean_for_json(failures),
        }
        report_file = state_dir / "logs" / "last_run_report.json"
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

        pkg_runs_dir = state_dir / "package_runs"
        pkg_runs_dir.mkdir(parents=True, exist_ok=True)
        now_ts = time.time()
        for pkg_name, s_data in serialized_success.items():
            try:
                (pkg_runs_dir / f"{pkg_name}.json").write_text(json.dumps({
                    "package": pkg_name,
                    "status": "success",
                    "timestamp": now_ts,
                    "duration": duration,
                    **s_data,
                }, indent=2), encoding="utf-8")
            except Exception:
                pass
        for pkg_name, f_data in failures.items():
            try:
                clean_f = _clean_for_json(f_data) if isinstance(f_data, dict) else {"reason": str(f_data)}
                (pkg_runs_dir / f"{pkg_name}.json").write_text(json.dumps({
                    "package": pkg_name,
                    "status": "failed",
                    "timestamp": now_ts,
                    "duration": duration,
                    **clean_f,
                }, indent=2), encoding="utf-8")
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Note: Could not save last_run_report.json: {e}")

    if dry_run:
        preview_file = root / "test_email.html"
        preview_file.write_text(html_body, encoding="utf-8")
        logger.info("\n" + "=" * 60)
        logger.info(" [DRY-RUN MODE] Zero SMTP or network calls made.")
        logger.info(f" HTML email preview written to: {preview_file}")
        logger.info("=" * 60 + "\n")
        logger.info(text_body)
        return preview_file, text_body

    if not config.get("email", {}).get("enabled", False):
        logger.info("Email notification is disabled in config.yaml. Skipping send.")
    else:
        send_email(subject, text_body, html_body, config, attachments)

    # ntfy runs independently of the SMTP dispatcher and has its own enable flag,
    # so notification works on hosts without a mail server as well.
    try:
        ntfy_notify.notify_run(
            patch_success=patch_success,
            failures=failures,
            config=config,
            duration=duration,
            click_url=portal_link(patch_success, config),
            download_base=portal_base(config),
        )
    except Exception as exc:  # a notification must never fail a build
        logger.warning(f"ntfy notification skipped: {exc}")

    return None, text_body


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AutoMorph Email Dispatcher")
    parser.add_argument("--dry-run", action="store_true", help="Generate and preview locally without sending")
    parser.add_argument("--duration", type=str, default=None, help="Elapsed duration string")
    args = parser.parse_args()

    # Test fixture with realistic apps
    project_root = Path(__file__).resolve().parent.parent
    test_success = {}

    patched_apks = list(project_root.glob("apps/patched/*/*/*_patched.apk"))
    for apk in patched_apks[:3]:
        pkg = apk.parent.parent.name
        test_success[pkg] = {
            "dest": apk,
            "arch": "arm64-v8a",
            "strategy": 1,
            "attempt": 1,
            "attempts": [],
        }

    test_failures = {
        "com.google.android.youtube": {
            "target_versions": ["19.16.39"],
            "arch": "arm64-v8a",
            "attempts": [
                {
                    "version": "19.16.39",
                    "run": 1,
                    "stage": "DOWNLOAD",
                    "reason": "Base APK download/verification failed (arch arm64-v8a unavailable)",
                }
            ],
        }
    }

    test_flags = {
        "striplibs": True,
        "architecture": "arm64-v8a",
        "low_spec_mode": True,
    }

    print("[dispatcher] Running AutoMorph dispatcher fixture...")
    main(
        test_success,
        failures=test_failures,
        duration=args.duration or "1m 45s",
        dry_run=args.dry_run,
        build_flags=test_flags,
    )
