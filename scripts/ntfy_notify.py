"""
ntfy notification transport for AutoMorph.

Sends a compact push notification to an `ntfy` topic (ntfy.sh or any self-hosted
instance) after every pipeline run. It complements the SMTP dispatcher and is
independent of it, so AutoMorph can notify on hosts without a mail server.

Configuration (config.yaml)::

    ntfy:
      enabled: true
      server: "https://ntfy.example.com"   # base URL, no topic
      topic: "automorph"
      token: ""                            # optional, Bearer token
      priority: "default"                  # min|low|default|high|max (or 1-5)
      tags: "robot,package"                # optional, comma separated
      click_url: "https://automorph.example.com"   # optional, opened on tap
      notify_on: "always"                  # always | success | failure

Every value can also come from the environment, which wins over config.yaml:
``NTFY_ENABLED``, ``NTFY_SERVER``, ``NTFY_TOPIC``, ``NTFY_TOKEN``,
``NTFY_USERNAME``, ``NTFY_PASSWORD``, ``NTFY_PRIORITY``, ``NTFY_TAGS``,
``NTFY_CLICK_URL``. ``NTFY_URL`` may be either a bare server URL or a full
full `https://server/topic` URL; the last path segment is then used as the topic.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRUTHY = {"1", "true", "yes", "on"}
VALID_PRIORITIES = {"min", "low", "default", "high", "max", "1", "2", "3", "4", "5"}
PRIORITY_MAP = {"min": 1, "low": 2, "default": 3, "high": 4, "max": 5}


def priority_value(value: Any) -> int:
    """
    Converts a priority name (or 1-5) into the integer the ntfy JSON API expects.
    Some ntfy versions reject the string form ("default") with HTTP 400, so the
    payload always carries the number.
    """
    raw = str(value if value is not None else "default").strip().lower()
    if raw in PRIORITY_MAP:
        return PRIORITY_MAP[raw]
    try:
        num = int(raw)
        return min(5, max(1, num))
    except (TypeError, ValueError):
        return PRIORITY_MAP["default"]

# Well-known public instances offered as presets in the web portal. Any other
# ntfy server can be entered manually; nothing here is required.
SERVER_PRESETS = [
    {"url": "https://ntfy.sh", "label": "ntfy.sh (official, free)"},
    {"url": "https://ntfy.envs.net", "label": "ntfy.envs.net (community)"},
    {"url": "https://ntfy.adminforge.de", "label": "ntfy.adminforge.de (community)"},
]


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUTHY


def _split_base_topic(url: str) -> tuple[str, str]:
    """Splits ``https://host:port/topic`` into ``('https://host:port', 'topic')``."""
    raw = (url or "").strip()
    if not raw:
        return "", ""
    if "://" not in raw:
        raw = "https://" + raw
    scheme, rest = raw.split("://", 1)
    if "/" in rest:
        host, path = rest.split("/", 1)
        return f"{scheme}://{host}", path.strip("/")
    return f"{scheme}://{rest}", ""


def resolve_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merges config.yaml `ntfy:` with NTFY_* environment variables."""
    conf = (config or {}).get("ntfy") or {}
    if not isinstance(conf, dict):
        conf = {}

    url_env = str(os.getenv("NTFY_URL") or "").strip()
    base_from_url, topic_from_url = _split_base_topic(url_env)

    server = str(os.getenv("NTFY_SERVER") or conf.get("server") or base_from_url or "").strip().rstrip("/")
    topic = str(os.getenv("NTFY_TOPIC") or conf.get("topic") or topic_from_url or "").strip().strip("/")
    token = str(os.getenv("NTFY_TOKEN") or conf.get("token") or "").strip()
    username = str(os.getenv("NTFY_USERNAME") or conf.get("username") or "").strip()
    password = str(os.getenv("NTFY_PASSWORD") or conf.get("password") or "").strip()

    priority = str(os.getenv("NTFY_PRIORITY") or conf.get("priority") or "default").strip().lower()
    if priority not in VALID_PRIORITIES:
        priority = "default"

    tags_raw = str(os.getenv("NTFY_TAGS") or conf.get("tags") or "").strip()
    tags = [t.strip() for t in tags_raw.replace(";", ",").split(",") if t.strip()]

    click_url = str(os.getenv("NTFY_CLICK_URL") or conf.get("click_url") or "").strip()
    notify_on = str(conf.get("notify_on") or os.getenv("NTFY_NOTIFY_ON") or "always").strip().lower()
    if notify_on not in ("always", "success", "failure"):
        notify_on = "always"

    enabled = _as_bool(os.getenv("NTFY_ENABLED"), _as_bool(conf.get("enabled"), False))

    return {
        "enabled": enabled,
        "server": server or "https://ntfy.sh",
        "topic": topic,
        "token": token,
        "username": username,
        "password": password,
        "priority": priority,
        "tags": tags,
        "click_url": click_url,
        "notify_on": notify_on,
    }


def _apk_version(path: Path | str | None) -> str:
    """Reads the version from ``apps/patched/<pkg>/<version>/<file>.apk``."""
    try:
        return Path(str(path)).parent.name
    except Exception:
        return ""


def _download_url(base: str | None, dest: Any) -> str:
    """
    Builds the portal download URL for a patched APK. `dest` is the path inside
    apps/patched/<package>/<version>/<file>.apk, so the last three segments give
    the /files/ route.
    """
    if not base or not dest:
        return ""
    parts = Path(str(dest)).parts
    if len(parts) < 4:
        return ""
    rel = "/".join(parts[-3:])
    return f"{str(base).rstrip('/')}/files/{rel}"


def _display_names(config: dict[str, Any] | None) -> dict[str, str]:
    """Maps package names to the optional `display_name` from config.yaml."""
    names: dict[str, str] = {}
    for key in ("packages", "apps"):
        for item in (config or {}).get(key) or []:
            if not isinstance(item, dict):
                continue
            pkg = str(item.get("package_name") or item.get("package") or "").strip()
            name = str(item.get("display_name") or "").strip()
            if pkg and name:
                names[pkg] = name
    return names


def _patch_count(dest: Any) -> int:
    """Reads the applied patch count from the result.json next to the built APK."""
    try:
        result_file = Path(str(dest)).parent / "result.json"
        if result_file.is_file():
            data = json.loads(result_file.read_text(encoding="utf-8"))
            return len(data.get("appliedPatches") or [])
    except Exception:
        pass
    return 0


def _short_reason(reason: str, limit: int = 60) -> str:
    """One short line for a failure, because clients cut the rest anyway."""
    reason = " ".join((reason or "").split())
    if len(reason) <= limit:
        return reason
    cut = reason[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return f"{cut}..."



def build_message(
    patch_success: dict[str, Any],
    failures: dict[str, dict] | None = None,
    config: dict[str, Any] | None = None,
    duration: str | float | int | None = None,
    click_url: str | None = None,
    download_base: str | None = None,
    subject_prefix: str | None = None,
) -> dict[str, Any]:
    """Builds the ntfy payload for one pipeline run."""
    failures = failures or {}
    settings = resolve_settings(config)
    prefix = subject_prefix or ((config or {}).get("email") or {}).get("subject_prefix") or "[AutoMorph]"
    link_base = (download_base or settings.get("click_url") or "").strip()

    names = _display_names(config)

    ok_lines: list[str] = []
    for pkg, data in patch_success.items():
        dest = data.get("dest") if isinstance(data, dict) else data
        version = _apk_version(dest)
        label = names.get(pkg) or pkg
        patches = _patch_count(dest)
        line = f"✓ **{label}**" + (f" {version}" if version else "")
        if patches:
            line += f" - {patches} patches"
        link = _download_url(link_base, dest)
        if link:
            line += f"\n[Download APK]({link})"
        ok_lines.append(line)

    fail_lines: list[str] = []
    for pkg, info in failures.items():
        reason = ""
        if isinstance(info, dict):
            attempts = info.get("attempts") or []
            if attempts and isinstance(attempts[0], dict):
                reason = str(attempts[0].get("reason") or "")
            reason = reason or str(info.get("reason") or "")
        else:
            reason = str(info)
        label = names.get(pkg) or pkg
        short = _short_reason(reason) or "failed"
        hint = " (see report)" if (click_url or settings["click_url"]) else ""
        fail_lines.append(f"✗ **{label}**: {short}{hint}")

    duration_str = ""
    if isinstance(duration, (int, float)):
        total = int(duration)
        duration_str = f"{total // 60}m {total % 60}s" if total >= 60 else f"{total}s"
    elif duration:
        duration_str = str(duration)

    summary = f"{len(patch_success)} patched, {len(fail_lines)} failed"
    if duration_str:
        summary += f", {duration_str}"

    body_lines: list[str] = []
    for line in ok_lines + fail_lines:
        body_lines.append(line)
        body_lines.append("")
    body_lines.append(summary)
    title = f"{prefix} {len(patch_success)} patched" + (f", {len(fail_lines)} failed" if fail_lines else "")
    if len(title) > 120:
        title = title[:117] + "…"

    tags = list(settings["tags"])
    priority = settings["priority"]
    if fail_lines:
        tags = tags or ["warning"]
        if priority == "default":
            priority = "high"
    elif not tags:
        tags = ["white_check_mark"]

    return {
        "title": title,
        "message": "\n".join(body_lines).strip(),
        "priority": priority,
        "tags": tags,
        "click": (click_url or settings["click_url"] or "").strip(),
        # ntfy renders the body as Markdown (bold names, clickable download links)
        "markdown": True,
        "topic": settings["topic"],
        "server": settings["server"],
    }


def send_notification(payload: dict[str, Any], settings: dict[str, Any] | None = None,
                      timeout: int = 15) -> dict[str, Any]:
    """Posts a JSON message to the ntfy server. Never raises; returns a result dict."""
    settings = settings or resolve_settings()
    server = str(payload.get("server") or settings["server"]).rstrip("/")
    topic = str(payload.get("topic") or settings["topic"]).strip()
    if not topic:
        return {"success": False, "error": "no ntfy topic configured"}

    body: dict[str, Any] = {
        "topic": topic,
        "title": payload.get("title") or "AutoMorph",
        "message": payload.get("message") or "",
        "priority": priority_value(payload.get("priority") or settings["priority"]),
    }
    if payload.get("tags"):
        body["tags"] = payload["tags"]
    if payload.get("click"):
        body["click"] = payload["click"]
    if payload.get("markdown"):
        body["markdown"] = True

    headers = {"Content-Type": "application/json", "User-Agent": "AutoMorph"}
    if settings.get("token"):
        headers["Authorization"] = f"Bearer {settings['token']}"
    elif settings.get("username"):
        import base64

        cred = f"{settings['username']}:{settings.get('password', '')}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(cred).decode("ascii")

    request = urllib.request.Request(server, data=json.dumps(body).encode("utf-8"),
                                     headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            logger.info(f" └── [✓] ntfy notification sent to {server}/{topic}")
            return {"success": True, "status": response.status, "topic": topic, "server": server}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        logger.warning(f" └── [!] ntfy rejected the notification: HTTP {exc.code} {detail}")
        return {"success": False, "status": exc.code, "error": detail or f"HTTP {exc.code}"}
    except Exception as exc:  # network errors, DNS, TLS, timeouts
        logger.warning(f" └── [!] ntfy notification failed: {exc}")
        return {"success": False, "error": str(exc)}


def notify_run(
    patch_success: dict[str, Any],
    failures: dict[str, dict] | None = None,
    config: dict[str, Any] | None = None,
    duration: str | float | int | None = None,
    click_url: str | None = None,
    download_base: str | None = None,
) -> dict[str, Any]:
    """
    Entry point used by the pipeline. Honours `ntfy.notify_on` and never raises,
    so a broken notification endpoint cannot fail a build.
    """
    settings = resolve_settings(config)
    if not settings["enabled"] or not settings["topic"]:
        if settings["enabled"]:
            logger.warning("ntfy is enabled but no topic is configured, skipping.")
        return {"success": False, "skipped": True}

    has_success = bool(patch_success)
    has_failures = bool(failures)
    if settings["notify_on"] == "success" and not has_success:
        return {"success": False, "skipped": True}
    if settings["notify_on"] == "failure" and not has_failures:
        return {"success": False, "skipped": True}

    try:
        payload = build_message(patch_success, failures, config, duration, click_url, download_base)
        return send_notification(payload, settings)
    except Exception as exc:  # never break the pipeline because of a notification
        logger.warning(f" └── [!] ntfy notification skipped: {exc}")
        return {"success": False, "error": str(exc)}


if __name__ == "__main__":  # manual smoke test: python3 scripts/ntfy_notify.py
    import argparse

    parser = argparse.ArgumentParser(description="Send a test notification via ntfy")
    parser.add_argument("--dry-run", action="store_true", help="Print the payload, send nothing")
    args = parser.parse_args()

    demo_success = {
        "com.example.app": {"dest": "apps/patched/com.example.app/1.2.3/com.example.app_1.2.3_patched.apk", "strategy": 1},
    }
    demo_failures = {"com.example.broken": {"attempts": [{"reason": "no compatible version"}]}}
    demo_cfg = {"ntfy": {"enabled": True}, "email": {"subject_prefix": "[AutoMorph]"}}
    demo_payload = build_message(demo_success, demo_failures, demo_cfg, duration=125, click_url="https://example.invalid")

    if args.dry_run:
        print(json.dumps(demo_payload, indent=2, ensure_ascii=False))
    else:
        print(send_notification(demo_payload))
