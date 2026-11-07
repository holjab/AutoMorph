"""
FlareSolverr integration for bypassing Cloudflare protection on APKMirror, APKCombo, etc.
Configured via the FLARESOLVERR_URL environment variable (e.g. http://flaresolverr:8191/v1).
Supports routing FlareSolverr through PROXY_URL when both are configured.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def get_flaresolverr_url() -> str | None:
    """
    Returns normalized FlareSolverr endpoint URL ending in /v1, or None if unset.
    Checks FLARESOLVERR_URL (or FLARESOLVERR).
    """
    raw = (os.getenv("FLARESOLVERR_URL") or os.getenv("FLARESOLVERR") or "").strip()
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
    If PROXY_URL is configured (or proxy_url passed), FlareSolverr will route its request through the proxy.
    The connection from AutoMorph to FlareSolverr is made directly (bypassing any upstream proxy).
    """
    endpoint = get_flaresolverr_url()
    if not endpoint:
        return None

    try:
        import requests
    except ImportError:
        logger.error("[FlareSolverr] 'requests' library not installed.")
        return None

    # If proxy not explicitly provided, detect active PROXY_URL
    effective_proxy = proxy_url
    if not effective_proxy:
        try:
            import proxy_utils
            effective_proxy = proxy_utils.get_proxy()
        except Exception:
            effective_proxy = os.getenv("PROXY_URL") or os.getenv("PROXY")

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

    if effective_proxy:
        payload["proxy"] = {"url": effective_proxy}

    def _execute_flaresolverr(req_payload: dict[str, Any]) -> str | None:
        with requests.Session() as session:
            # AutoMorph connects to FlareSolverr directly (never through upstream proxy)
            session.trust_env = False
            resp = session.post(
                endpoint,
                json=req_payload,
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
        return None

    try:
        html = _execute_flaresolverr(payload)
        if html:
            return html
        # If FlareSolverr failed while using a proxy, retry once without proxy through FlareSolverr
        if effective_proxy and "proxy" in payload:
            logger.info(f"  [FlareSolverr] Retrying {url} directly without proxy through FlareSolverr...")
            no_proxy_payload = dict(payload)
            no_proxy_payload.pop("proxy", None)
            return _execute_flaresolverr(no_proxy_payload)
    except Exception as e:
        logger.warning(f"  [FlareSolverr] Failed connecting to FlareSolverr at {endpoint} for {url}: {e}")
        # Retry without proxy if proxy caused connection failure
        if effective_proxy and "proxy" in payload:
            try:
                logger.info(f"  [FlareSolverr] Retrying {url} directly without proxy through FlareSolverr...")
                no_proxy_payload = dict(payload)
                no_proxy_payload.pop("proxy", None)
                return _execute_flaresolverr(no_proxy_payload)
            except Exception as e2:
                logger.warning(f"  [FlareSolverr] Direct retry also failed: {e2}")

    return None
