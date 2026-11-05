"""
Proxy configuration and utilities for upstream network connections.
Supports HTTP and SOCKS5 proxies configured strictly via the PROXY Docker environment variable.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# Track the last applied proxy URL to detect changes
_CURRENT_PROXY: str | None = None


def normalize_proxy_url(proxy: str | None) -> str | None:
    """
    Normalizes a proxy URL string:
    - Strips leading/trailing whitespace.
    - If no scheme is provided, defaults to http://.
    - If socks5:// is provided, upgrades to socks5h:// so DNS queries
      for upstream domains (APKPure, APKMirror, etc.) are resolved
      remotely by the SOCKS5 proxy instead of leaking or failing on local DNS.
    """
    if not proxy:
        return None
    p = proxy.strip()
    if not p:
        return None

    # If no protocol scheme is given (e.g. "127.0.0.1:8080" or "proxy.lan:1080")
    if "://" not in p:
        p = f"http://{p}"

    # Map socks5:// -> socks5h:// for remote DNS resolution in requests/httpx
    # (keeps socks5h://, socks4://, socks4a://, http://, https:// intact)
    if p.lower().startswith("socks5://"):
        p = "socks5h://" + p[len("socks5://"):]
    elif p.lower().startswith("socks4://"):
        p = "socks4a://" + p[len("socks4://"):]

    return p


def obfuscate_proxy_url(proxy: str | None) -> str:
    """Masks credentials in proxy URL for safe logging."""
    if not proxy:
        return "None"
    try:
        parts = urlsplit(proxy)
        if parts.password:
            user = parts.username or ""
            host = parts.hostname or ""
            port = f":{parts.port}" if parts.port else ""
            netloc = f"{user}:***@{host}{port}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        pass
    return proxy


def get_proxy() -> str | None:
    """
    Retrieves and normalizes the PROXY environment variable.
    Returns None if PROXY is not set, empty, or whitespace.
    """
    raw = os.getenv("PROXY")
    return normalize_proxy_url(raw)


def get_requests_proxies() -> dict[str, str] | None:
    """
    Returns a proxies dict suitable for requests.get(..., proxies=...) or session.proxies.
    Returns None if no proxy is configured.
    """
    proxy = get_proxy()
    if not proxy:
        return None
    return {
        "http": proxy,
        "https": proxy,
        "all": proxy,
    }


def setup_proxy(proxy_url: str | None = None) -> str | None:
    """
    Applies upstream proxy configuration to process environment variables.
    If proxy_url is not passed, reads the PROXY environment variable.
    If proxy is configured, sets:
      - PROXY, HTTP_PROXY, HTTPS_PROXY, ALL_PROXY (and lowercase variants)
      - NO_PROXY / no_proxy (defaults to localhost,127.0.0.1 if unset)
    If proxy is not set or empty, removes any proxy variables that were set
    so that direct connections are made.
    """
    global _CURRENT_PROXY

    if proxy_url is not None:
        target_proxy = normalize_proxy_url(proxy_url)
    else:
        target_proxy = get_proxy()

    proxy_env_vars = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    )

    if target_proxy:
        os.environ["PROXY"] = target_proxy
        for var in proxy_env_vars:
            os.environ[var] = target_proxy

        # Ensure local loopback connections do not route through proxy
        if not os.getenv("NO_PROXY") and not os.getenv("no_proxy"):
            os.environ["NO_PROXY"] = "localhost,127.0.0.1"
            os.environ["no_proxy"] = "localhost,127.0.0.1"

        if target_proxy != _CURRENT_PROXY:
            logger.info(f"[*] Upstream proxy configured: {obfuscate_proxy_url(target_proxy)}")
            _CURRENT_PROXY = target_proxy
        return target_proxy
    else:
        # Clear all proxy env vars so direct connections are made
        for var in proxy_env_vars:
            os.environ.pop(var, None)
        os.environ.pop("PROXY", None)
        if _CURRENT_PROXY is not None:
            logger.info("[*] Upstream proxy disabled; direct connections will be made.")
            _CURRENT_PROXY = None
        return None


def get_session(session: Any = None) -> Any:
    """
    Returns a requests.Session configured with proxy settings if active.
    """
    import requests

    s = session or requests.Session()
    s.trust_env = True
    proxies = get_requests_proxies()
    if proxies:
        s.proxies.update(proxies)
    return s
