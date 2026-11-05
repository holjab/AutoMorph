"""
FlareSolverr integration for bypassing Cloudflare protection on APKMirror, APKCombo, etc.
Configured via the FLARESOLVERR_URL environment variable (e.g. http://flaresolverr:8191/v1).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def get_flaresolverr_url() -> str | None:
    """
    Returns normalized FlareSolverr endpoint URL ending in /v1, or None if unset.
    """
    raw = os.getenv("FLARESOLVERR_URL", "").strip()
    if not raw:
        return None
    url = raw.rstrip("/")
    if not url.endswith("/v1"):
        url = f"{url}/v1"
    return url


def is_flaresolverr_enabled() -> bool:
    """Returns True if FLARESOLVERR_URL is configured."""
    return bool(get_flaresolverr_url())


def fetch_via_flaresolverr(
    url: str,
    method: str = "GET",
    post_data: dict | str | None = None,
    timeout: int = 60,
    proxy_url: str | None = None,
) -> str | None:
    """
    Submits a request to FlareSolverr to solve any Cloudflare challenge and retrieve the page HTML.
    Note: Connection from AutoMorph to FlareSolverr is made directly, bypassing any upstream proxy.
    """
    endpoint = get_flaresolverr_url()
    if not endpoint:
        return None

    try:
        import requests
    except ImportError:
        logger.error("[FlareSolverr] 'requests' library not installed.")
        return None

    cmd = "request.post" if method.upper() == "POST" else "request.get"
    payload: dict[str, Any] = {
        "cmd": cmd,
        "url": url,
        "maxTimeout": max(15000, timeout * 1000),
    }

    if method.upper() == "POST" and post_data is not None:
        if isinstance(post_data, dict):
            import urllib.parse
            payload["postData"] = urllib.parse.urlencode(post_data)
        else:
            payload["postData"] = str(post_data)

    if proxy_url:
        payload["proxy"] = {"url": proxy_url}

    try:
        # Create session with trust_env=False to guarantee direct connection to FlareSolverr
        with requests.Session() as session:
            session.trust_env = False
            resp = session.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=timeout + 15,
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") == "ok":
            solution = data.get("solution", {})
            html = solution.get("response", "")
            status_code = solution.get("status", 200)
            if status_code in (200, 201) and html and len(html) > 100:
                logger.info(f"  [FlareSolverr] Solved Cloudflare challenge for: {url}")
                return html
            else:
                logger.warning(
                    f"  [FlareSolverr] Solved with status {status_code} for {url} (response length {len(html)})"
                )
        else:
            logger.warning(f"  [FlareSolverr] Response status not ok for {url}: {data.get('message')}")
    except Exception as e:
        logger.warning(f"  [FlareSolverr] Failed connecting to FlareSolverr at {endpoint} for {url}: {e}")

    return None
