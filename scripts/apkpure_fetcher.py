#!/usr/bin/env python3
# ==============================================================================
# INPUT / OUTPUT FORMAT
# ==============================================================================
#
# Input: Python list of dictionaries. Each package requires:
#   - package_name: Android package name
#   - target_version: APKPure/APKCombo version name
#   - arch: target architecture/ABI
#
# Example:
# [
#   {'package_name': 'com.reddit.frontpage', 'target_version': '2026.14.0', 'arch': 'arm64-v8a'},
#   {'package_name': 'com.duolingo', 'target_version': '6.92.5', 'arch': 'armeabi-v7a'}
# ]
#
# Output: JSON array containing one result object per input package.
#
# Success:
# {
#   "package_name": "...",
#   "versionname": "...",
#   "versioncode": "...",
#   "arch": "...",
#   "apkpure_dl_link": "https://...",
#   "apkcombo_dl_link": "https://...",
#   "uptodown_dl_link": "https://...",
#   "aptoide_dl_link": "https://..."
# }
#
# Partial success (one source failed): same shape, with the failed link set to
# null and an "error" key describing what failed.
#
# Failure (sources failed, or version codes disagree):
# {
#   "package_name": "...",
#   "versionname": "...",
#   "versioncode": null,
#   "arch": "...",
#   "apkpure_dl_link": null,
#   "apkcombo_dl_link": null,
#   "uptodown_dl_link": null,
#   "aptoide_dl_link": null,
#   "error": "..."
# }
# ==============================================================================

import re
import urllib.request
import urllib.parse
import os
import base64
import yaml
import json
import time
import secrets
import hmac
import hashlib
from bs4 import BeautifulSoup
import requests
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import sys
import subprocess
import shutil
import concurrent.futures
import logging

logger = logging.getLogger(__name__)

APKPURE_API = "https://api.pureapk.com/m/v3/cms/app_version"
APKCOMBO_BASE = "https://apkcombo.com"

APK_MIRROR_HEADERS = {
    "User-Agent": "Obtainium/1.0.0 (+https://github.com/ImranR98/Obtainium)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

APKMIRROR_API_URL = "https://www.apkmirror.com/wp-json/apkm/v1/app_exists/"
APKMIRROR_API_AUTH = "Basic YXBpLWFwa3VwZGF0ZXI6cm01cmNmcnVVakt5MDRzTXB5TVBKWFc4"


HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

HEADERS_API = {
    "User-Agent": "Apkpure/3.20.0 (Linux; Android 14)",
    "x-sv": "34",
    "x-gp": "1",
    "x-cv": "3172501",
    "Connection": "close",
}

UPTODOWN_HMAC_KEY = b"MDGMXUMdvHJBG/vjdFgmqX6LUdy7ecfwvYNd0gyfOCs="
UPTODOWN_CLIENT_HEADERS = {
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 16; Pixel 8 Pro Build/BP4A.260205.001)",
    "Identificador": "Uptodown_Android",
    "Identificador-Version": "739",
    "Connection": "close",
}

APTOIDE_BASE_URLS = [
    "https://ws75.aptoide.com/api/7",
    "https://ws2.aptoide.com/api/7",
    "https://ws75-cache.aptoide.com/api/7",
]

APTOIDE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def normalise_arch(arch: str) -> str:
    replacements = {
        "arm64": "arm64-v8a",
        "armv8": "arm64-v8a",
        "armv8a": "arm64-v8a",
        "aarch64": "arm64-v8a",
        "arm64_v8a": "arm64-v8a",
        "arm64-v8a": "arm64-v8a",
        "armv7": "armeabi-v7a",
        "armv7a": "armeabi-v7a",
        "arm-v7a": "armeabi-v7a",
        "armeabi_v7a": "armeabi-v7a",
        "armeabi-v7a": "armeabi-v7a",
        "armeabi": "armeabi-v7a",
        "x86_64": "x86_64",
        "x86-64": "x86_64",
        "x86": "x86",
        "universal": "universal",
        "noarch": "universal",
        "all": "universal",
    }
    arch = arch.strip().lower()
    return replacements.get(arch, arch)


def parse_architecture(text: str) -> list[str]:
    """Convert an architecture string into a list of ABIs."""
    return [abi.strip() for abi in re.split(r"[,;\s\+/]+", text) if abi.strip()]


def match_architecture_tier(cand_archs: list[str], target_arch: str) -> int | None:
    """
    Check if candidate architectures match the target architecture.
    Returns:
        1: Exact native match (e.g. 'arm64-v8a')
        2: Universal / multi-arch match ('universal', 'noarch', 'all')
        3: Backward-compatible fallback (e.g. 'armeabi-v7a' for 'arm64-v8a')
        None: Incompatible
    """
    target = normalise_arch(target_arch)
    norm_cands = [normalise_arch(a) for a in cand_archs]

    if target in norm_cands:
        return 1

    if any(a in ("universal", "noarch", "all") for a in norm_cands):
        return 2

    if target == "arm64-v8a" and "armeabi-v7a" in norm_cands:
        return 3

    if target == "x86_64" and "x86" in norm_cands:
        return 3

    return None


def normalise_version(version: str) -> str:
    """normalise APK version strings for comparison and clean output without suffixes."""
    if not version:
        return ""
    version = str(version).strip()
    version = re.sub(r"^[><=~!]+", "", version).strip()
    version = re.sub(r"^[vV]+", "", version).strip()
    m = re.match(r"^(\d+(?:[.-]\d+)*)", version)
    if m:
        core = m.group(1)
        return core.replace("-", ".").rstrip(".")
    m_search = re.search(r"(\d+(?:\.\d+)+)", version)
    if m_search:
        return m_search.group(1).rstrip(".")
    return re.sub(r"[^0-9a-zA-Z.]+$", "", version)


def parse_version_constraint(version: str) -> tuple[str, str, tuple[int, ...] | None]:
    """
    Parses a version string or constraint into (operator, clean_version_str, tuple_version).
    Examples:
      'v>=4.2.81' -> ('>=', '4.2.81', (4, 2, 81))
      '>=4.2.81'  -> ('>=', '4.2.81', (4, 2, 81))
      '4.2.81'    -> ('==', '4.2.81', (4, 2, 81))
      'latest'    -> ('latest', '', None)
    """
    v = str(version).strip() if version else ""
    if not v or v.lower() in ("latest", "any", "any / latest"):
        return "latest", "", None

    m = re.match(r"^([><=~!]+)\s*(.*)$", v)
    if m:
        op = m.group(1)
        base = m.group(2)
    elif v.lower().startswith("v") and re.match(r"^v([><=~!]+)\s*(.*)$", v, re.IGNORECASE):
        m2 = re.match(r"^v([><=~!]+)\s*(.*)$", v, re.IGNORECASE)
        op = m2.group(1)
        base = m2.group(2)
    else:
        op = "=="
        base = v
    clean = normalise_version(base)
    nums = [int(p) for p in re.findall(r"\d+", clean)]
    tup = tuple(nums) if nums else None
    return op, clean, tup


def is_version_compatible(candidate_ver: str, op: str, target_tuple: tuple[int, ...] | None, target_str: str) -> bool:
    """Check if candidate_ver satisfies the operator and target version constraint."""
    if op in ("latest", "any"):
        return True
    m_cand = re.search(r"(\d+(?:\.\d+)+)", candidate_ver)
    c_ver_str = m_cand.group(1) if m_cand else candidate_ver
    c_clean = normalise_version(c_ver_str)
    t_clean = normalise_version(target_str)
    if c_clean and c_clean == t_clean:
        return True
    c_nums = [int(p) for p in re.findall(r"\d+", c_clean)]
    if not c_nums or not target_tuple:
        return False
    c_tup = tuple(c_nums)
    max_len = max(len(c_tup), len(target_tuple))
    c_padded = c_tup + (0,) * (max_len - len(c_tup))
    t_padded = target_tuple + (0,) * (max_len - len(target_tuple))
    if op in (">=", "v>="):
        return c_padded >= t_padded
    elif op == ">":
        return c_padded > t_padded
    elif op == "<=":
        return c_padded <= t_padded
    elif op == "<":
        return c_padded < t_padded
    elif op in ("==", "="):
        return c_padded == t_padded
    return False


def fetch_html(url: str, source: str | None = None) -> str:
    headers = APK_MIRROR_HEADERS if source == "apkmirror" else HEADERS_BROWSER
    try:
        resp = requests.get(url, headers=headers, timeout=25)
        if resp.ok and len(resp.text) > 200:
            return resp.text
    except Exception:
        pass

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")

def log(source: str, message: str) -> None:
    """One log format for every source: '  [Source] message'."""
    logger.info(f"  [{source}] {message}")


def log_candidates(source: str, candidates: list[dict]) -> None:
    """Print matching candidates the same way for every source."""
    log(source, f"{len(candidates)} matching candidate(s):")
    for i, c in enumerate(candidates, 1):
        parts = [
            f"arch: {', '.join(c['architectures'])}",
            f"version_code: {c['version_code']}",
        ]
        if c.get("sha1"):
            parts.append(f"SHA-1: {c['sha1']}")
        logger.info(f"    {i}. " + " | ".join(parts))


# ==============================================================================
# APKCOMBO
# ==============================================================================

def slugify(text: str) -> str:
    """Turn 'HypnoBox: Self Hypnosis, Sleep' into 'hypnobox-self-hypnosis-sleep'."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text).strip("-")
    return text


def resolve_app_title_google_play(package_name: str) -> str | None:
    """Fetch app title dynamically from Google Play Store web page (unauthenticated)."""
    url = f"https://play.google.com/store/apps/details?id={urllib.parse.quote(package_name)}&hl=en"
    try:
        resp = requests.get(url, headers=HEADERS_BROWSER, timeout=6)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()
                title = re.sub(r"\s*-\s*Apps on Google Play.*$", "", title, flags=re.IGNORECASE)
                title = re.sub(r"\s*-\s*Android Apps.*$", "", title, flags=re.IGNORECASE)
                if title:
                    return title
                    
            title_tag = soup.find("title")
            if title_tag:
                title = title_tag.get_text(strip=True)
                title = re.sub(r"\s*-\s*Apps on Google Play.*$", "", title, flags=re.IGNORECASE)
                title = re.sub(r"\s*-\s*Android Apps.*$", "", title, flags=re.IGNORECASE)
                if title:
                    return title
    except Exception:
        pass
    return None


# ==============================================================================
# MORPHE BUNDLES CATALOG
# ==============================================================================

MORPHE_CATALOG_URL = "https://morphe-patches.software/data/bundles.json"
_MORPHE_CATALOG_CACHE = None
_MORPHE_CATALOG_LOADED_AT = 0


def get_morphe_catalog_path() -> Path:
    env_root = os.environ.get("AUTOMORPH_ROOT")
    base = Path(env_root) if env_root else Path(__file__).resolve().parent.parent
    target_dir = base / "tools" / "automorph-data" / "state"
    if not (base / "tools" / "automorph-data").exists() and (base / "tools").exists():
        target_dir = base / "tools" / "state"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return target_dir / "morphe_catalog.json"


MORPHE_DEFAULT_APPS = {
    "com.google.android.youtube": {
        "name": "YouTube",
        "category": "Video",
        "iconUrl": "https://play-lh.googleusercontent.com/QNmuZQc9I6Zbe3mWnSr0hycnENqGFCI5p3yE29Hkxtf22T0IWS6zTrpxULLyyjWpB7ONAXDsDQXnXcVWokl3eg=s128-rw",
        "description": "Official YouTube app with ad-blocking, background playback, and customization options.",
    },
    "com.google.android.apps.youtube.music": {
        "name": "YouTube Music",
        "category": "Music & Audio",
        "iconUrl": "https://play-lh.googleusercontent.com/MyptZXmcCyF59phIWPsNCfHq6zSO_ojEdw_4rMYYWPOGdikSjqeOXcoszBZ053VYpINe5K7ZR9KV1nkFWgxdOw=s128-rw",
        "description": "Official YouTube Music streaming app with ad-free listening and background play.",
    },
    "com.reddit.frontpage": {
        "name": "Reddit",
        "category": "Social & Communication",
        "iconUrl": "https://play-lh.googleusercontent.com/8FVS0kvM60JwqZWRNzBIlwrZZxJa8vpli8IWaaTo8QXRe1PHmCNJ8UpWoXkGPV3jKEaTY2OVAeYoisOnGJXY=s128-rw",
        "description": "Discover why people are the best - through real & authentic conversations.",
    },
}

OFFICIAL_MORPHE_BUNDLE = {
    "name": "Morphe",
    "repo": "MorpheApp/morphe-patches",
    "source": "github",
    "targetApps": [
        "com.google.android.youtube",
        "com.google.android.apps.youtube.music",
        "com.reddit.frontpage",
    ],
    "patches": [],
}


def enrich_morphe_catalog(data: dict) -> dict:
    """
    Enriches catalog data with official Morphe default applications (YouTube, YouTube Music, Reddit)
    and the official Morphe repository bundle (MorpheApp/morphe-patches).
    Preserves all existing community bundles so third-party repos can support them too.
    """
    if not isinstance(data, dict):
        return data

    store = data.setdefault("store", {})
    bundles = data.setdefault("bundles", [])

    for pkg, info in MORPHE_DEFAULT_APPS.items():
        if pkg not in store:
            store[pkg] = dict(info)
        else:
            for k, v in info.items():
                if not store[pkg].get(k):
                    store[pkg][k] = v
            # Ensure official default apps have the up-to-date icon URL
            if info.get("iconUrl"):
                store[pkg]["iconUrl"] = info["iconUrl"]

    # Ensure official Morphe repository is present and targets default apps
    morphe_bundle = next(
        (b for b in bundles if isinstance(b, dict) and b.get("repo", "").lower() == "morpheapp/morphe-patches"),
        None,
    )
    if not morphe_bundle:
        bundles.insert(0, dict(OFFICIAL_MORPHE_BUNDLE))
    else:
        target_apps = morphe_bundle.setdefault("targetApps", [])
        for pkg in OFFICIAL_MORPHE_BUNDLE["targetApps"]:
            if pkg not in target_apps:
                target_apps.append(pkg)

    return data


def get_morphe_catalog(force_refresh: bool = False, max_age_hours: int = 24) -> dict:
    """
    Load Morphe bundles catalog from persistent state file or remote source.
    Caches the parsed catalog in memory and stores it in tools/automorph-data/state/morphe_catalog.json.
    """
    global _MORPHE_CATALOG_CACHE, _MORPHE_CATALOG_LOADED_AT
    now = time.time()
    if not force_refresh and _MORPHE_CATALOG_CACHE is not None and (now - _MORPHE_CATALOG_LOADED_AT) < (max_age_hours * 3600):
        return _MORPHE_CATALOG_CACHE

    catalog_file = get_morphe_catalog_path()

    # Check local disk cache validity
    if not force_refresh and catalog_file.is_file():
        try:
            mtime = catalog_file.stat().st_mtime
            if (now - mtime) < (max_age_hours * 3600):
                with open(catalog_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "store" in data:
                        _MORPHE_CATALOG_CACHE = enrich_morphe_catalog(data)
                        _MORPHE_CATALOG_LOADED_AT = now
                        return _MORPHE_CATALOG_CACHE
        except Exception as e:
            log("MorpheCatalog", f"Error reading cached catalog: {e}")

    # Fetch from remote
    try:
        resp = requests.get(MORPHE_CATALOG_URL, headers=HEADERS_BROWSER, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "store" in data:
                data = enrich_morphe_catalog(data)
                try:
                    temp_file = catalog_file.with_suffix(".tmp")
                    with open(temp_file, "w", encoding="utf-8") as f:
                        json.dump(data, f)
                    temp_file.replace(catalog_file)
                except Exception as we:
                    log("MorpheCatalog", f"Failed saving catalog to disk: {we}")
                _MORPHE_CATALOG_CACHE = data
                _MORPHE_CATALOG_LOADED_AT = now
                log("MorpheCatalog", f"Catalog updated ({len(data.get('store', {}))} store apps).")
                return _MORPHE_CATALOG_CACHE
    except Exception as e:
        log("MorpheCatalog", f"Failed to refresh catalog from remote: {e}")

    # Fallback to existing disk cache if remote fetch failed
    if catalog_file.is_file():
        try:
            with open(catalog_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _MORPHE_CATALOG_CACHE = enrich_morphe_catalog(data)
                    _MORPHE_CATALOG_LOADED_AT = now
                    return _MORPHE_CATALOG_CACHE
        except Exception:
            pass

    _MORPHE_CATALOG_CACHE = enrich_morphe_catalog({})
    return _MORPHE_CATALOG_CACHE


def resolve_app_title(package_name: str) -> str | None:
    """
    Resolve app display title from:
    1. Cached Morphe catalog (fast, local, works for delisted/custom apps like Boost for Reddit)
    2. Google Play web scraping
    """
    try:
        catalog = get_morphe_catalog()
        store = catalog.get("store", {})
        if package_name in store and isinstance(store[package_name], dict):
            name = store[package_name].get("name")
            if name and isinstance(name, str) and name.strip():
                return name.strip()
    except Exception:
        pass

    return resolve_app_title_google_play(package_name)


def resolve_apkcombo_slug_direct(package_name: str) -> str | None:
    """Probe APKCombo search directly for package link or redirection."""
    for base in ("https://apkcombo.com", "https://apkcombo.app"):
        url = f"{base}/search/{urllib.parse.quote(package_name)}"
        try:
            req = urllib.request.Request(url, headers=HEADERS_BROWSER)
            with urllib.request.urlopen(req, timeout=8) as resp:
                final_url = resp.geturl()
                m = re.search(r"apkcombo\.(?:app|com)/([^/]+)/" + re.escape(package_name) + r"(?:/|$)", final_url)
                if m and m.group(1) != "search":
                    return m.group(1)
                html = resp.read().decode("utf-8", errors="replace")
                
            m = re.search(r'href="[^"]*apkcombo\.(?:app|com)/([^/]+)/' + re.escape(package_name) + r'/?["/]', html)
            if not m:
                m = re.search(r'href="/([^/]+)/' + re.escape(package_name) + r'/?["/]', html)
            if m and m.group(1) not in ("search", "developer", "category"):
                return m.group(1)
        except Exception:
            pass
    return None


def get_apkcombo_slug(package_name: str) -> str:
    """Find the APKCombo app slug dynamically without hardcoding."""
    # 1. Direct APKCombo search (fastest and exact, handles redirects)
    slug = resolve_apkcombo_slug_direct(package_name)
    if slug:
        return slug

    # 2. App title -> slug candidate (from Morphe catalog or Google Play)
    app_title = resolve_app_title(package_name)
    if app_title:
        candidate_slug = slugify(app_title)
        if candidate_slug:
            return candidate_slug

    # 3. DuckDuckGo search fallback (supporting both .com and .app)
    query = urllib.parse.quote(f'site:apkcombo.com OR site:apkcombo.app "{package_name}"')
    url = f"https://html.duckduckgo.com/html/?q={query}"

    try:
        req = urllib.request.Request(url, headers=HEADERS_BROWSER)
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8", errors="ignore")

        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", class_="result__url"):
            href = a.get("href", "")
            if "uddg=" not in href:
                continue
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            if "uddg" not in parsed:
                continue
            actual_url = parsed["uddg"][0]
            match = re.search(
                r"apkcombo\.(?:app|com)/([^/]+)/" + re.escape(package_name),
                actual_url,
            )
            if match:
                return match.group(1)
    except Exception as e:
        log("APKCombo", f"Warning: DuckDuckGo slug lookup failed ({e}); using package name.")

    # 4. Reasonable fallback
    return package_name


def get_apkcombo_version_page_url(
    slug: str,
    package_name: str,
    version: str,
) -> str:
    """Construct the APKCombo version-specific download page URL."""
    if not version or version.lower() in ("latest", "any", "any / latest"):
        return f"{APKCOMBO_BASE}/{slug}/{package_name}/download/apk"
    clean_ver = normalise_version(version)
    return (
        f"{APKCOMBO_BASE}/{slug}/{package_name}/"
        f"download/phone-{urllib.parse.quote(clean_ver)}-apk"
    )


def get_apkcombo_variant_info(
    version_page_url: str,
    package_name: str,
    target_version: str,
    target_arch: str,
    slug: str | None = None,
    verbose: bool = True,
) -> dict:
    """
    Find the APKCombo variant matching the requested version
    and architecture.

    Returns:
        {
            "version_name": "...",
            "version_code": "...",
            "architectures": [...],
            "download_url": "https://..."
        }
    """
    target_arch = normalise_arch(target_arch)
    op, clean_ver, target_tuple = parse_version_constraint(target_version)

    html = fetch_html(version_page_url)
    soup = BeautifulSoup(html, "html.parser")

    variant_elements = soup.select("a.variant")

    # If no variants in static HTML, check for APKCombo's dynamic AJAX /dl endpoint
    if not variant_elements and slug:
        m_xid = re.search(r'xid\s*=\s*["\']([^"\']+)["\']', html)
        if m_xid:
            xid = m_xid.group(1)
            dl_endpoint = f"https://apkcombo.com/{slug}/{package_name}/{xid}/dl"
            try:
                resp = requests.post(
                    dl_endpoint,
                    data={"package_name": package_name, "version": clean_ver},
                    headers=HEADERS_BROWSER,
                    timeout=10,
                )
                if resp.status_code == 200:
                    ajax_soup = BeautifulSoup(resp.text, "html.parser")
                    ajax_variants = ajax_soup.select("a.variant")
                    if ajax_variants:
                        soup = ajax_soup
                        variant_elements = ajax_variants
            except Exception:
                pass

    # If still no variants, inspect /old-versions/ for compatible version releases
    if not variant_elements and slug:
        old_ver_url = f"https://apkcombo.com/{slug}/{package_name}/old-versions/"
        try:
            old_html = fetch_html(old_ver_url)
            old_soup = BeautifulSoup(old_html, "html.parser")
            for item in old_soup.select("ul.list-versions li a.ver-item, .ver-item"):
                href = item.get("href")
                if not href:
                    continue
                vername_el = item.select_one(".vername")
                ver_text = vername_el.get_text(strip=True) if vername_el else item.get_text(strip=True)
                m_v = re.search(r"(\d+(?:\.\d+)+)", ver_text)
                if not m_v:
                    continue
                cand_v = m_v.group(1)
                if is_version_compatible(cand_v, op, target_tuple, clean_ver):
                    candidate_page_url = f"https://apkcombo.com{href}" if href.startswith("/") else href
                    cand_html = fetch_html(candidate_page_url)
                    cand_soup = BeautifulSoup(cand_html, "html.parser")
                    cand_variants = cand_soup.select("a.variant")
                    if not cand_variants:
                        m_cxid = re.search(r'xid\s*=\s*["\']([^"\']+)["\']', cand_html)
                        if m_cxid:
                            cand_endpoint = f"https://apkcombo.com/{slug}/{package_name}/{m_cxid.group(1)}/dl"
                            c_resp = requests.post(
                                cand_endpoint,
                                data={"package_name": package_name, "version": cand_v},
                                headers=HEADERS_BROWSER,
                                timeout=10,
                            )
                            if c_resp.status_code == 200:
                                cand_ajax_soup = BeautifulSoup(c_resp.text, "html.parser")
                                cand_variants = cand_ajax_soup.select("a.variant")
                                if cand_variants:
                                    soup = cand_ajax_soup
                                    variant_elements = cand_variants
                                    clean_ver = cand_v
                                    break
                    else:
                        soup = cand_soup
                        variant_elements = cand_variants
                        clean_ver = cand_v
                        break
        except Exception:
            pass

    candidates = []

    for variant_link in variant_elements:
        # Walk up until we find the <li> containing the architecture.
        container = variant_link.find_parent("li")
        while container and not container.select_one("span.blur code"):
            container = container.find_parent("li")

        vername_el = variant_link.select_one(".vername")
        vercode_el = variant_link.select_one(".vercode")

        version_name = vername_el.get_text(" ", strip=True) if vername_el else ""
        if not version_name:
            version_name = clean_ver

        version_code_text = vercode_el.get_text(" ", strip=True) if vercode_el else ""

        version_code_match = re.search(r"\((\d+)\)", version_code_text)
        if not version_code_match:
            version_code_match = re.search(r"(\d+)", version_code_text)
        if not version_code_match:
            continue

        version_code = version_code_match.group(1)

        # Check version compatibility with constraint
        if not is_version_compatible(version_name, op, target_tuple, clean_ver):
            continue

        arch_el = container.select_one("span.blur code") if container else variant_link.select_one("span.blur code")
        if not arch_el:
            desc_el = variant_link.select_one(".description")
            arch_text = desc_el.get_text(" ", strip=True) if desc_el else "universal"
        else:
            arch_text = arch_el.get_text(" ", strip=True)

        architectures = [
            normalise_arch(a)
            for a in parse_architecture(arch_text)
        ]

        tier = match_architecture_tier(architectures, target_arch)
        if tier is None:
            continue

        href = variant_link.get("href")
        if not href:
            continue

        if href.startswith("/"):
            href = APKCOMBO_BASE + href

        dl_url = href_parse(href)

        candidates.append({
            "version_name": normalise_version(version_name) or clean_ver,
            "version_code": version_code,
            "architectures": architectures,
            "download_url": dl_url,
            "tier": tier,
        })

    if not candidates:
        raise RuntimeError(
            f"Could not find an APKCombo variant for "
            f"{package_name} v{target_version} matching "
            f"architecture '{target_arch}'"
        )

    if verbose:
        log_candidates("APKCombo", candidates)

    # Sort by tier (1: exact native, 2: universal, 3: compatible fallback),
    # then prefer the most specific architecture list among same tier.
    candidates.sort(key=lambda x: (x["tier"], len(x["architectures"])))

    return candidates[0]


def href_parse(url: str) -> str:
    parsed_url = urlparse(url)

    if parsed_url.path not in ("/r2", "/d"):
        raise ValueError(
            f"APKCombo URL is not a supported download URL: {parsed_url.path}"
        )

    u = parse_qs(parsed_url.query).get("u", [None])[0]
    if not u:
        raise ValueError(
            f"APKCombo {parsed_url.path} URL is missing the 'u' parameter"
        )

    if parsed_url.path == "/r2":
        return u

    try:
        return base64.b64decode(u).decode("utf-8")
    except Exception as e:
        raise ValueError(
            "APKCombo /d URL contains an invalid base64 'u' parameter"
        ) from e



# ==============================================================================
# APKPURE: PURE-PYTHON PROTOBUF WIRE DECODER & API FETCHER
# ==============================================================================

def decode_varint(buf: bytes, offset: int) -> tuple[int | None, int]:
    """Decodes a protobuf varint without external libraries."""
    res = 0
    shift = 0
    while True:
        if offset >= len(buf):
            return None, offset
        b = buf[offset]
        offset += 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80):
            return res, offset
        shift += 7


def parse_protobuf_fields(buf: bytes) -> list[tuple[int, int, bytes | int]]:
    """
    Parses raw protobuf bytes into a list of (field_number, wire_type, raw_value).
    Handles wire types 0 (varint), 1 (64-bit), 2 (length-delimited), 5 (32-bit).
    """
    fields = []
    offset = 0
    buf_len = len(buf)
    while offset < buf_len:
        key, offset = decode_varint(buf, offset)
        if key is None:
            break
        field_num = key >> 3
        wire_type = key & 7

        if wire_type == 0:  # Varint
            val, offset = decode_varint(buf, offset)
            if val is None:
                break
            fields.append((field_num, wire_type, val))
        elif wire_type == 1:  # 64-bit fixed
            if offset + 8 > buf_len:
                break
            fields.append((field_num, wire_type, buf[offset:offset + 8]))
            offset += 8
        elif wire_type == 2:  # Length-delimited (string, bytes, or sub-message)
            length, offset = decode_varint(buf, offset)
            if length is None or offset + length > buf_len:
                break
            fields.append((field_num, wire_type, buf[offset:offset + length]))
            offset += length
        elif wire_type == 5:  # 32-bit fixed
            if offset + 4 > buf_len:
                break
            fields.append((field_num, wire_type, buf[offset:offset + 4]))
            offset += 4
        else:
            break
    return fields


def parse_version_entry(entry_bytes: bytes) -> dict | None:
    """Extracts package, version_code, version_name, sha1, and download_url from an entry block."""
    entry_fields = parse_protobuf_fields(entry_bytes)
    pkg = None
    vc = None
    vn = None
    sha1 = None
    dl_url = None

    for f_num, w_type, val in entry_fields:
        if w_type != 2:
            continue
        if f_num == 4:
            pkg = val.decode("utf-8", errors="replace")
        elif f_num == 5:
            vc = val.decode("utf-8", errors="replace")
        elif f_num == 6:
            vn = val.decode("utf-8", errors="replace")
        elif f_num == 24:
            # Field 24 contains sub-message with download URL and SHA1
            sub_fields = parse_protobuf_fields(val)
            for sub_num, sub_type, sub_val in sub_fields:
                if sub_type == 2:
                    if sub_num == 3 and len(sub_val) == 40:
                        sha1 = sub_val.decode("utf-8", errors="replace").lower()
                    elif sub_num == 9:
                        url_str = sub_val.decode("utf-8", errors="replace")
                        if url_str.startswith("http"):
                            dl_url = url_str

    if pkg and vc and vn:
        clean_vn = normalise_version(vn) or vn
        return {
            "package_name": pkg,
            "version_name": clean_vn,
            "raw_version_name": vn,
            "version_code": vc,
            "sha1": sha1,
            "download_link": dl_url,
        }
    return None


def extract_versions(data: bytes, arch: str) -> list[dict]:
    """Recursively traverses protobuf message to find all version entries."""
    results = []

    def scan(buf: bytes) -> None:
        fields = parse_protobuf_fields(buf)
        for f_num, w_type, val in fields:
            if w_type == 2:
                entry = parse_version_entry(val)
                if entry:
                    entry["arch"] = arch
                    results.append(entry)
                else:
                    scan(val)

    scan(data)
    return results

def _match_apkpure_entry(all_entries: list[dict], target_version: str | None) -> dict | None:
    matched = None
    if target_version and target_version.lower() not in ("latest", "any / latest", "any", "0.0.0"):
        op, clean_ver, target_tuple = parse_version_constraint(target_version)
        norm_target = normalise_version(clean_ver)

        # 1. Exact match (prefer entry with download_link and highest version_code)
        exact_matches = [
            e for e in all_entries
            if (e["version_name"] == clean_ver or e["version_name"] == target_version)
        ]
        if exact_matches:
            dl_matches = [e for e in exact_matches if e.get("download_link")]
            if dl_matches:
                return max(dl_matches, key=lambda x: int(re.sub(r"\D", "", str(x.get("version_code") or "0")) or 0))
            return exact_matches[0]

        # 2. Normalized match fallback (prefer entry with download_link and highest version_code)
        norm_matches = [
            e for e in all_entries
            if normalise_version(e["version_name"]) == norm_target
        ]
        if norm_matches:
            dl_matches = [e for e in norm_matches if e.get("download_link")]
            if dl_matches:
                return max(dl_matches, key=lambda x: int(re.sub(r"\D", "", str(x.get("version_code") or "0")) or 0))
            return norm_matches[0]

        # 3. Compatible version fallback (if operator constraint was supplied)
        if op in (">=", ">"):
            compat_entries = [
                e for e in all_entries
                if is_version_compatible(e["version_name"], op, target_tuple, clean_ver)
            ]
            if compat_entries:
                dl_matches = [e for e in compat_entries if e.get("download_link")]
                target_pool = dl_matches if dl_matches else compat_entries
                return max(
                    target_pool,
                    key=lambda x: (
                        tuple(int(d) for d in re.findall(r"\d+", normalise_version(x["version_name"]))),
                        int(re.sub(r"\D", "", str(x.get("version_code") or "0")) or 0),
                    ),
                )
    elif all_entries:
        dl_matches = [e for e in all_entries if e.get("download_link")]
        target_pool = dl_matches if dl_matches else all_entries
        if target_pool:
            return max(
                target_pool,
                key=lambda x: (
                    tuple(int(d) for d in re.findall(r"\d+", normalise_version(x.get("version_name", "")))),
                    int(re.sub(r"\D", "", str(x.get("version_code") or "0")) or 0),
                ),
            )

    return matched


def query_apkpure_api(
    package_name: str,
    arch: str,
    target_version: str | None = None,
) -> dict:
    """
    Queries APKPure API for a given package and target architecture.
    Uses modern Android SDK 34 (Android 14) and falls back to SDK 29 if needed.

    Returns:
    {
        "package_name": str,
        "arch": str,
        "available_versions": list[str],
        "matched_version": {
            "package_name": str,
            "version_name": str,
            "version_code": str,
            "sha1": str,
            "download_link": str,
            "arch": str
        } | None
    }
    """
    norm_arch = normalise_arch(arch)
    all_entries = []
    seen_sigs = set()

    # Query modern Android SDK 34 first; fallback to legacy SDK 29 if target not found
    sdk_levels = ["34", "29"]
    for sdk in sdk_levels:
        headers = dict(HEADERS_API)
        headers["x-sv"] = sdk
        headers["x-abis"] = norm_arch
        headers["x-abi"] = norm_arch

        params = {
            "hl": "en-US",
            "package_name": package_name,
        }

        try:
            resp = requests.get(APKPURE_API, params=params, headers=headers, timeout=20)
            resp.raise_for_status()

            if resp.content.startswith(b"<") or "html" in resp.headers.get("content-type", "").lower():
                continue

            entries = extract_versions(resp.content, norm_arch)
            for e in entries:
                sig = (e["version_name"], e.get("version_code"))
                if sig not in seen_sigs:
                    seen_sigs.add(sig)
                    all_entries.append(e)

            matched = _match_apkpure_entry(all_entries, target_version)
            if matched and matched.get("download_link"):
                break
        except Exception:
            continue

    if not all_entries:
        raise RuntimeError(f"APKPure API returned no versions for {package_name}")

    available_versions = []
    seen_vns = set()
    for e in all_entries:
        vn = e["version_name"]
        if vn not in seen_vns:
            seen_vns.add(vn)
            available_versions.append(vn)

    matched = _match_apkpure_entry(all_entries, target_version)

    return {
        "package_name": package_name,
        "arch": norm_arch,
        "available_versions": available_versions,
        "matched_version": matched,
    }


# ==============================================================================
# UPTODOWN: AUTHENTICATED EAPI FETCHER
# ==============================================================================

class UptodownClient:
    _token = None
    _token_expiry = 0.0

    @classmethod
    def get_bearer_token(cls, force_refresh: bool = False) -> str:
        now = time.time()
        if not force_refresh and cls._token and now < (cls._token_expiry - 60):
            return cls._token

        timestamp = str(int(now))
        identifier = secrets.token_hex(8)
        sig = hmac.new(
            UPTODOWN_HMAC_KEY,
            timestamp.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers = dict(UPTODOWN_CLIENT_HEADERS)
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        data = {
            "identifier": identifier,
            "id_plataforma": "13",
            "lang": "en",
            "unixtime": timestamp,
            "hmac": sig,
        }

        # Retry once with short backoff in case of transient CDN rate-limiting
        last_err = None
        token = None
        for attempt in range(2):
            try:
                r = requests.post(
                    "https://www.uptodown.app/eapi/auth/token",
                    data=data,
                    headers=headers,
                    timeout=10,
                )
                r.raise_for_status()
                token = r.json().get("token")
                if token:
                    break
            except Exception as e:
                last_err = e
                if attempt == 0:
                    time.sleep(2)
                    continue
                raise last_err

        if not token:
            raise RuntimeError("Uptodown auth endpoint did not return a token")

        # Parse expiry from JWT payload
        try:
            parts = token.split(".")
            if len(parts) >= 2:
                payload = parts[1]
                padded = payload + "=" * (-len(payload) % 4)
                claims = json.loads(base64.urlsafe_b64decode(padded.encode()).decode("utf-8", "ignore"))
                cls._token_expiry = float(claims.get("exp", now + 1500))
            else:
                cls._token_expiry = now + 1500
        except Exception:
            cls._token_expiry = now + 1500

        cls._token = token
        return cls._token

    @classmethod
    def get_download_url(cls, app_id: str, file_id: str) -> str:
        for attempt in range(2):
            token = cls.get_bearer_token(force_refresh=(attempt > 0))
            auth_headers = dict(UPTODOWN_CLIENT_HEADERS)
            auth_headers["Authorization"] = f"Bearer {token}"
            resolver_url = f"https://www.uptodown.app/eapi/apps/{app_id}/file/{file_id}/downloadUrl"
            r = requests.get(resolver_url, headers=auth_headers, timeout=10)
            if r.status_code == 401 and attempt == 0:
                continue
            r.raise_for_status()
            res_json = r.json()
            download_url = res_json.get("data", {}).get("downloadURL")
            if not download_url:
                raise RuntimeError(f"Uptodown eapi did not return downloadURL: {res_json}")
            return download_url
        raise RuntimeError(f"Failed to resolve Uptodown download URL for app {app_id} / file {file_id}")


def extract_uptodown_package_name(soup: BeautifulSoup) -> str | None:
    """Extract verified Android package name from Uptodown technical information table."""
    for row in soup.select("#technical-information tr"):
        cells = [c.text.strip() for c in row.find_all(["th", "td"]) if c.text.strip()]
        if len(cells) >= 2 and cells[0].lower() == "package name":
            return cells[1]
    return None


def get_uptodown_app_url(package_name: str) -> str | None:
    """Find the Uptodown app URL using direct slug probing and search endpoint fallback."""
    parts = package_name.split(".")
    candidates = []
    search_queries = []

    app_title = resolve_app_title(package_name)
    if app_title:
        # Split title by common delimiters: e.g. "Water Tracker - Hydro Coach", "Ninja VPN: Fast & Secure"
        title_segments = [s.strip() for s in re.split(r"[-:|–—(]", app_title) if s.strip()]
        for seg in title_segments:
            seg_clean = re.sub(r"[^\w\s]", "", seg).strip()
            if seg_clean:
                seg_slug = slugify(seg_clean)
                if seg_slug:
                    candidates.append(f"https://{seg_slug}.en.uptodown.com/android")
                search_queries.append(seg_clean)

        title_slug = slugify(app_title)
        if title_slug:
            candidates.append(f"https://{title_slug}.en.uptodown.com/android")
            words = title_slug.split("-")
            if len(words) > 1:
                # Prefix combinations
                candidates.append(f"https://{words[0]}.en.uptodown.com/android")
                candidates.append(f"https://{'-'.join(words[:2])}.en.uptodown.com/android")
                if len(words) > 2:
                    candidates.append(f"https://{'-'.join(words[:3])}.en.uptodown.com/android")
                # Suffix combinations (for titles like "Water Tracker - Hydro Coach")
                candidates.append(f"https://{words[-1]}.en.uptodown.com/android")
                candidates.append(f"https://{'-'.join(words[-2:])}.en.uptodown.com/android")
                if len(words) > 2:
                    candidates.append(f"https://{'-'.join(words[-3:])}.en.uptodown.com/android")
                search_queries.append(" ".join(words[-2:]))
    else:
        # Fallback to APKCombo slug if Google Play title resolution was unavailable (e.g. delisted apps)
        combo_slug = get_apkcombo_slug(package_name)
        if combo_slug and combo_slug != package_name:
            candidates.append(f"https://{combo_slug}.en.uptodown.com/android")
            search_queries.append(combo_slug.replace("-", " "))
            search_queries.append(combo_slug)

    # Add meaningful package segments, avoiding generic stop words like 'android' or 'app'
    generic_stop = {"android", "app", "apps", "com", "net", "org", "mobile", "client", "free"}
    meaningful_parts = [p for p in parts if p.lower() not in generic_stop and len(p) >= 3]
    if meaningful_parts:
        candidates.append(f"https://{meaningful_parts[-1]}.en.uptodown.com/android")
        candidates.append(f"https://{meaningful_parts[0]}.en.uptodown.com/android")
        if len(meaningful_parts) >= 2:
            candidates.append(f"https://{meaningful_parts[0]}-{meaningful_parts[1]}.en.uptodown.com/android")
            candidates.append(f"https://{meaningful_parts[-2]}-{meaningful_parts[-1]}.en.uptodown.com/android")
        search_queries.append(meaningful_parts[-1])
        search_queries.append(meaningful_parts[0])

    candidates.append(f"https://{package_name.replace('.', '-')}.en.uptodown.com/android")

    # 1. Direct candidate URL probing with verified package name check
    for url in dict.fromkeys(candidates):
        try:
            r = requests.get(url, headers=HEADERS_BROWSER, timeout=5)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                pkg = extract_uptodown_package_name(soup)
                if pkg == package_name:
                    return url
        except Exception:
            pass

    # 2. Search endpoint fallback
    if app_title:
        # Also include cleaned title without punctuation to avoid confusing Uptodown's search engine
        clean_full_title = re.sub(r"[-:|–—()]+", " ", app_title).strip()
        search_queries.append(clean_full_title)
        search_queries.append(app_title)
    search_queries.append(package_name.replace(".", " "))

    for q in dict.fromkeys(search_queries):
        if not q or len(q) < 3:
            continue
        try:
            search_headers = dict(UPTODOWN_CLIENT_HEADERS)
            search_headers["Content-Type"] = "application/x-www-form-urlencoded"
            res = requests.post(
                "https://en.uptodown.com/android/en/s",
                data={"queryString": q},
                headers=search_headers,
                timeout=8,
            )
            if res.status_code == 200:
                apps = res.json().get("data", {}).get("apps", [])
                for app in apps:
                    app_url = app.get("url")
                    if not app_url:
                        continue
                    if ".en.uptodown.com" not in app_url:
                        app_url = re.sub(r"https?://([^/]+)\.uptodown\.com", r"https://\1.en.uptodown.com", app_url)
                    try:
                        pr = requests.get(app_url, headers=HEADERS_BROWSER, timeout=5)
                        if pr.status_code == 200:
                            soup = BeautifulSoup(pr.text, "html.parser")
                            pkg = extract_uptodown_package_name(soup)
                            if pkg == package_name:
                                return app_url
                    except Exception:
                        pass
        except Exception:
            pass

    return None


def query_uptodown(
    package_name: str,
    target_version: str | None = None,
    arch: str | None = None,
) -> tuple[str | None, str | None]:
    """
    Query Uptodown for an app download URL.
    Note: Uptodown does not expose architecture or version codes in its public web/eapi metadata.
    Download links serve as an unverified fallback, verified downstream by apkeep.py via check_apk/check_xapk.
    Returns: (download_url, error_message)
    """
    try:
        app_url = get_uptodown_app_url(package_name)
        if not app_url:
            return None, f"Uptodown: Could not find app page for '{package_name}'"

        # Uptodown's /versions and /apps/... endpoints 404 on the .en. language subdomain.
        # Normalize to the canonical subdomain/host URL.
        base_app_url = re.sub(r"https?://([^/]+)\.en\.uptodown\.com", r"https://\1.uptodown.com", app_url)
        base_app_url = re.sub(r"https?://en\.uptodown\.com", r"https://www.uptodown.com", base_app_url).rstrip("/")

        session = requests.Session()

        # Try candidate URLs (English /download first, then regional fallbacks)
        dl_candidates = [
            f"{app_url.rstrip('/')}/download",
            f"{base_app_url}/descargar",
            f"{app_url.rstrip('/')}/descargar",
            f"{base_app_url}/download",
        ]
        page_html = None
        dl_page_url = None
        for cand_url in dict.fromkeys(dl_candidates):
            try:
                cand_resp = session.get(cand_url, headers=HEADERS_BROWSER, timeout=6)
                if cand_resp.status_code == 200 and ("detail-download-button" in cand_resp.text or "detail-app-name" in cand_resp.text):
                    page_html = cand_resp.text
                    dl_page_url = cand_url
                    break
            except Exception:
                continue

        if not page_html or not dl_page_url:
            return None, f"Uptodown: Could not load download page for '{package_name}'"

        soup = BeautifulSoup(page_html, "html.parser")

        btn = soup.select_one("#detail-download-button")
        app_name_el = soup.select_one("#detail-app-name")

        app_id = None
        file_id = None
        if btn:
            app_id = btn.get("data-app-id")
            file_id = btn.get("data-file-id")
        if not app_id and app_name_el:
            app_id = app_name_el.get("data-code")
        if not file_id and app_name_el:
            file_id = app_name_el.get("data-file-id")

        version_div = soup.select_one("div.version")
        latest_ver = version_div.get_text(strip=True) if version_div else None

        is_specific_version = target_version and target_version.lower() not in ("latest", "any", "any / latest", "0.0.0")

        # If a specific target_version is requested, check if latest matches or search /versions
        if is_specific_version:
            op, clean_ver, target_tuple = parse_version_constraint(target_version)
            latest_matches = latest_ver and is_version_compatible(latest_ver, op, target_tuple, clean_ver)

            if not latest_matches:
                target_file_id = None

                # 1. Inspect static /versions page directly without heavy retry loops
                try:
                    ver_resp = session.get(f"{app_url.rstrip('/')}/versions", headers=HEADERS_BROWSER, timeout=6)
                    if ver_resp.status_code == 200:
                        ver_soup = BeautifulSoup(ver_resp.text, "html.parser")
                        if not app_id:
                            v_app_el = ver_soup.select_one("#detail-app-name")
                            if v_app_el:
                                app_id = v_app_el.get("data-code")

                        for row in ver_soup.select("#versions-items-list div[data-version-id], section.versions div[data-version-id], div[data-version-id], div[data-file-id]"):
                            v_span = row.select_one("span.version")
                            if v_span:
                                v_text = v_span.get_text(strip=True)
                                if is_version_compatible(v_text, op, target_tuple, clean_ver):
                                    target_file_id = row.get("data-version-id") or row.get("data-file-id")
                                    break
                except Exception as e:
                    logger.debug(f"Failed to fetch static versions page ({app_url}/versions): {e}")

                # 2. Paginate via Uptodown AJAX endpoint in concurrent batches if older version not on static page
                if not target_file_id and app_id:
                    ajax_headers = dict(HEADERS_BROWSER)
                    ajax_headers["X-Requested-With"] = "XMLHttpRequest"
                    ajax_headers["Accept"] = "application/json"

                    def fetch_versions_page(p: int):
                        url = f"{base_app_url}/apps/{app_id}/versions/{p}"
                        try:
                            r = session.get(url, headers=ajax_headers, timeout=5)
                            if r.status_code == 200:
                                return p, r.json()
                        except Exception:
                            pass
                        return p, None

                    batch_size = 5
                    max_pages = 15
                    stop_paging = False

                    with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
                        for batch_start in range(1, max_pages + 1, batch_size):
                            if stop_paging:
                                break
                            pages = list(range(batch_start, min(batch_start + batch_size, max_pages + 1)))
                            futures = {executor.submit(fetch_versions_page, p): p for p in pages}
                            batch_results = {}
                            for fut in concurrent.futures.as_completed(futures):
                                p, res = fut.result()
                                batch_results[p] = res

                            for p in sorted(batch_results.keys()):
                                res = batch_results[p]
                                if not res or not res.get("success") or not res.get("data"):
                                    stop_paging = True
                                    continue
                                for item in res["data"]:
                                    v_text = item.get("version")
                                    if v_text and is_version_compatible(v_text, op, target_tuple, clean_ver):
                                        target_file_id = str(item.get("fileID") or item.get("versionURL", {}).get("versionID"))
                                        break
                                if target_file_id:
                                    break
                            if target_file_id:
                                break

                if not target_file_id:
                    return None, f"Uptodown: Version '{target_version}' not found"

                file_id = target_file_id

        if not app_id or not file_id:
            return None, f"Uptodown: Could not extract app_id or file_id from {dl_page_url}"

        download_url = UptodownClient.get_download_url(app_id, file_id)
        return download_url, None

    except Exception as e:
        return None, f"Uptodown: {e}"


def query_aptoide(
    package_name: str,
    target_version: str | None = None,
    arch: str = "arm64-v8a",
    timeout: int = 12,
) -> tuple[str | None, str | None, str | None]:
    """
    Query Aptoide API v7 for versionCode and direct APK download URL.
    Returns: (version_code, download_url, error_message)
    """
    op, clean_ver, target_tuple = parse_version_constraint(target_version)
    clean_target = clean_ver or (normalise_version(target_version) if target_version else None)
    is_specific = op not in ("latest", "any") and bool(clean_target)

    session = requests.Session()
    session.headers.update(APTOIDE_HEADERS)

    for base_url in APTOIDE_BASE_URLS:
        try:
            # 1. Fetch latest metadata via app/getMeta
            resp = session.get(f"{base_url}/app/getMeta", params={"package_name": package_name}, timeout=timeout)
            if resp.status_code == 404:
                return None, None, f"Aptoide: App '{package_name}' not found"
            if resp.status_code != 200:
                continue

            payload = resp.json()
            data = payload.get("data") or payload.get("nodes", {}).get("meta", {}).get("data")
            if not data:
                continue

            file_info = data.get("file", {})
            latest_vername = file_info.get("vername", "")
            latest_clean_ver = normalise_version(latest_vername) or latest_vername
            latest_vercode = str(file_info.get("vercode")) if file_info.get("vercode") is not None else None
            latest_dl_url = file_info.get("path") or file_info.get("path_alt")

            # 1. Target is latest, any, or matches current build
            if not is_specific or (latest_clean_ver and is_version_compatible(latest_clean_ver, op, target_tuple, clean_target)):
                if latest_dl_url:
                    return latest_vercode, latest_dl_url, None

            # 2. Search historical releases list via listAppVersions with pagination
            matched_entry = None
            max_offset = 250  # Check up to 250 historical versions
            for offset in range(0, max_offset, 50):
                hist_resp = session.get(
                    f"{base_url}/listAppVersions",
                    params={"package_name": package_name, "limit": 50, "offset": offset},
                    timeout=timeout,
                )
                if hist_resp.status_code != 200:
                    break
                hist_data = hist_resp.json()
                version_list = hist_data.get("list", [])
                if not version_list:
                    break
                for entry in version_list:
                    e_file = entry.get("file", {})
                    v_raw = e_file.get("vername", "")
                    v_clean = normalise_version(v_raw) or v_raw
                    if v_clean and is_version_compatible(v_clean, op, target_tuple, clean_target):
                        matched_entry = entry
                        break
                if matched_entry:
                    break
                total = hist_data.get("info", {}).get("pagination", {}).get("total", 0)
                if offset + len(version_list) >= total:
                    break

            if matched_entry:
                e_file = matched_entry.get("file", {})
                vercode = str(e_file.get("vercode")) if e_file.get("vercode") is not None else None
                dl_url = e_file.get("path") or e_file.get("path_alt")

                # If download link requires resolving app_id
                if not dl_url and matched_entry.get("id"):
                    app_id = matched_entry["id"]
                    detail_resp = session.get(
                        f"{base_url}/app/getMeta",
                        params={"app_id": app_id},
                        timeout=min(timeout, 10),
                    )
                    if detail_resp.status_code == 200:
                        d_file = detail_resp.json().get("data", {}).get("file", {})
                        dl_url = d_file.get("path") or d_file.get("path_alt")
                        if not vercode and d_file.get("vercode") is not None:
                            vercode = str(d_file.get("vercode"))

                if dl_url:
                    return vercode, dl_url, None
                return vercode, None, f"Aptoide: Found version '{target_version}' but could not obtain download link"

            return None, None, f"Aptoide: Version '{target_version}' not found for '{package_name}'"

        except Exception as exc:
            logger.debug(f"[Aptoide] Base {base_url} failed for {package_name}: {exc}")
            continue

    return None, None, f"Aptoide: Lookup failed for {package_name}"


# ==========================================================================
# STEP 0: SOURCES
# ==========================================================================

def check_sources_yaml(package_name: str, target_version: str, sources_file: Path) -> dict | bool:
    if not sources_file.is_file():
        return False
    
    with open(sources_file, "r", encoding="utf-8") as f:
        sources = yaml.safe_load(f) or {}

    if package_name not in sources:
        return False

    logger.info(f"{package_name} exists in custom sources!")
    return sources

def html_scrape(target_url: str) -> list | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(target_url, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        logger.warning(f"Failed to fetch directory {target_url}: {e}")
        return None
    
    soup = BeautifulSoup(response.text, "html.parser")
    all_links = [a["href"] for a in soup.find_all("a", href=True)]
    return all_links


def make_html_index_dl_link(
    package_name: str,
    target_version: str,
    target_url: str,
    pattern: str,
    arch: str,
    sources_file: Path | None,
    pkg_conf: dict | None = None
) -> tuple[str | None, str]:
    if not target_url:
        logger.warning(f"No url provided for HTML source for package {package_name}")
        return None, target_version

    all_links = html_scrape(target_url)
    if not all_links:
        logger.warning(f"No links found on HTML source for package {package_name}")
        return None, target_version

    if pkg_conf is None and sources_file and Path(sources_file).is_file():
        try:
            with open(sources_file, "r", encoding="utf-8") as f:
                srcs = yaml.safe_load(f) or {}
                pkg_conf = srcs.get(package_name, {})
        except Exception:
            pkg_conf = {}
    elif pkg_conf is None:
        pkg_conf = {}

    is_most_recent = (
        str(target_version).strip().lower() in ("0.0.0", "latest", "any", "any / latest")
        or str(pkg_conf.get("version", "")).strip().lower() in ("most_recent", "latest", "any")
    )

    try:
        regex = re.compile(pattern)
    except re.error as e:
        logger.error(f"Invalid regex pattern '{pattern}' for {package_name}: {e}")
        return None, target_version

    candidates: list[tuple[str, str]] = []

    for link in all_links:
        filename = link.split("?")[0].split("/")[-1]
        match = regex.match(filename)
        if match:
            if "version" in match.groupdict():
                extracted_version = match.group("version")
            elif match.groups():
                extracted_version = match.group(1)
            else:
                extracted_version = filename
            candidates.append((extracted_version, link))

    if not candidates:
        logger.warning(f"No APK matching pattern '{pattern}' found at {target_url}")
        return None, target_version

    if is_most_recent:
        def version_key(cand: tuple[str, str]) -> tuple[int, ...]:
            digits = [int(n) for n in re.findall(r"\d+", cand[0])]
            return tuple(digits) if digits else (0,)

        best_cand = max(candidates, key=version_key)
        best_ver, best_link = best_cand
        resolved_url = urllib.parse.urljoin(target_url, best_link)
        logger.info(f" └── [✓] Found latest HTML asset: {best_link.split('/')[-1]} (v{best_ver})")
        return resolved_url, best_ver

    clean_target = str(target_version).strip().lstrip("vV")
    for ver_str, link in candidates:
        clean_cand = str(ver_str).strip().lstrip("vV")
        if clean_cand == clean_target or re.match(rf"^{re.escape(clean_target)}[a-zA-Z]+$", clean_cand):
            resolved_url = urllib.parse.urljoin(target_url, link)
            logger.info(f" └── [✓] Found matching HTML asset: {link.split('/')[-1]} (v{ver_str})")
            return resolved_url, ver_str

    logger.warning(f"Failed: No HTML asset matched version '{target_version}' at {target_url}")
    return None, target_version

def make_github_dl_link(package_name: str, target_version: str, repo: str, pattern: str, arch: str, sources_file: Path | None, pkg_conf: dict | None = None) -> tuple[str | None, str]:
    headers = {
        "User-Agent": "MorpheDownloader",
        "Accept": "application/vnd.github+json",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if not repo:
        log("GitHub", f"No repository configured for {package_name}.")
        return None, target_version

    if pkg_conf is None and sources_file and Path(sources_file).is_file():
        try:
            with open(sources_file, "r", encoding="utf-8") as f:
                srcs = yaml.safe_load(f) or {}
                pkg_conf = srcs.get(package_name, {})
        except Exception:
            pkg_conf = {}
    elif pkg_conf is None:
        pkg_conf = {}

    is_most_recent = (
        str(target_version).strip().lower() in ("0.0.0", "latest", "any", "any / latest")
        or str(pkg_conf.get("version", "")).strip().lower() in ("most_recent", "latest", "any")
    )

    resp = None
    if is_most_recent:
        log("GitHub", f"Fetching latest release for {package_name} from {repo}...")
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        try:
            res = requests.get(api_url, headers=headers, timeout=15)
            if res.status_code == 200:
                resp = res.json()
            else:
                log("GitHub", f"Failed: GitHub API returned status {res.status_code} for {repo}/releases/latest")
        except Exception as e:
            log("GitHub", f"Failed to fetch latest release: {e}")
    else:
        clean_ver = re.sub(r"^[vV]+", "", str(target_version).strip())
        tags_to_try = [f"v{clean_ver}", clean_ver]
        log("GitHub", f"Looking up release tag for {package_name} v{clean_ver} on {repo}...")
        for tag in tags_to_try:
            api_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
            try:
                res = requests.get(api_url, headers=headers, timeout=15)
                if res.status_code == 200:
                    resp = res.json()
                    break
            except Exception as e:
                log("GitHub", f"Failed querying tag '{tag}': {e}")
                break
        if not resp:
            log("GitHub", f"Failed: Release tag for version '{clean_ver}' not found on {repo}")

    if not resp or 'assets' not in resp:
        return None, target_version

    tag_name = resp.get("tag_name", "")
    resolved_version = normalise_version(target_version) or str(target_version)
    if is_most_recent or str(target_version).strip().lower() in ("0.0.0", "latest", "any", "any / latest"):
        resolved_version = normalise_version(tag_name)
        if not resolved_version:
            digits = re.findall(r"\d+", tag_name)
            resolved_version = ".".join(digits) if digits else tag_name.lstrip("vV")

    for asset in resp.get('assets', []):
        if re.search(pattern, asset["name"]):
            git_dl_link = asset['browser_download_url']
            log("GitHub", f"Found matching asset: {asset['name']} (v{resolved_version})")
            return git_dl_link, resolved_version

    log("GitHub", f"Failed: No assets in release '{tag_name}' matched pattern '{pattern}'")
    return None, target_version


# ==============================================================================
# APKMIRROR
# ==============================================================================

def query_apkmirror_app_exists(package_name: str) -> dict | None:
    """Query APKMirror's REST API for app info using Obtainium's credential."""
    try:
        headers = {
            "Authorization": APKMIRROR_API_AUTH,
            "User-Agent": APK_MIRROR_HEADERS["User-Agent"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        res = requests.post(
            APKMIRROR_API_URL,
            json={"pnames": [package_name]},
            headers=headers,
            timeout=15,
        )
        if res.ok:
            data = res.json()
            entries = data.get("data", [])
            if entries and isinstance(entries, list) and entries[0].get("exists"):
                return entries[0]
    except Exception as e:
        logger.debug(f"[APKMirror] app_exists query for {package_name} failed: {e}")
    return None


def get_apkmirror_feed_releases(app_url: str) -> list[dict]:
    """Parse releases from an APKMirror app page's RSS feed (/feed/)."""
    feed_url = app_url.rstrip("/") + "/feed/"
    try:
        html = fetch_html(feed_url, source="apkmirror")
        try:
            soup = BeautifulSoup(html, "xml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
        items = []
        for item in soup.find_all("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            if title_el and link_el:
                title = title_el.get_text(strip=True)
                link = link_el.get_text(strip=True) or (link_el.next_sibling.strip() if link_el.next_sibling else "")
                if title and link:
                    items.append({"title": title, "link": link})
        return items
    except Exception as e:
        logger.debug(f"[APKMirror] Feed fetch failed for {feed_url}: {e}")
        return []


def _match_apkmirror_feed_item(feed_items: list[dict], target_version: str, is_latest: bool) -> str | None:
    """Match a target version string against a list of APKMirror feed items."""
    for item in feed_items:
        if is_latest:
            return item["link"]
        match = re.search(r"(\d+(?:[.-]\d+)+(?:-[a-zA-Z0-9-]+)?)", item["title"])
        if match and normalise_version(match.group(1)) == target_version:
            return item["link"]
        v_slug = target_version.replace(".", "-")
        if f"-{v_slug}-" in item["link"]:
            return item["link"]
    return None


def get_apkmirror_version_url(
    package_name: str,
    version: str,
    custom_url: str | None = None,
) -> str | None:
    """Find the APKMirror release page matching the requested version name."""
    clean_version = re.sub(r"^[vV]+", "", version.strip())
    is_latest = clean_version.lower() in ("latest", "any", "any / latest", "")
    target_version = "" if is_latest else normalise_version(clean_version)

    # 1. Custom URL directly provided (e.g. from app_sources.yaml)
    if custom_url:
        c_url = custom_url.strip()
        if not c_url.startswith("http"):
            c_url = f"https://www.apkmirror.com/{c_url.lstrip('/')}"
        # Direct release or variant link
        if "-release/" in c_url or "-android-apk-download/" in c_url:
            return c_url
        # If it's an app overview URL, inspect its RSS feed
        matched = _match_apkmirror_feed_item(get_apkmirror_feed_releases(c_url), target_version, is_latest)
        if matched:
            return matched

    # 2. Check APKMirror REST API (Obtainium/APKUpdater endpoint)
    api_info = query_apkmirror_app_exists(package_name)
    if api_info:
        rel = api_info.get("release", {})
        rel_ver = rel.get("version", "")
        rel_link = rel.get("link", "")
        if rel_link and (is_latest or normalise_version(rel_ver) == target_version):
            return f"https://www.apkmirror.com{rel_link}" if rel_link.startswith("/") else rel_link

        # Check the primary app's RSS feed
        app_link = api_info.get("app", {}).get("link", "")
        if app_link:
            app_full = f"https://www.apkmirror.com{app_link}" if app_link.startswith("/") else app_link
            matched = _match_apkmirror_feed_item(get_apkmirror_feed_releases(app_full), target_version, is_latest)
            if matched:
                return matched

    # Meaningful keywords to verify that candidate releases actually belong to the package
    generic_stop = {"android", "app", "apps", "com", "net", "org", "google", "mobile", "client", "free", "inc", "ltd"}
    raw_keywords = re.findall(r"[a-zA-Z]{3,}", package_name)
    meaningful_keywords = {k.lower() for k in raw_keywords if k.lower() not in generic_stop}

    # Also resolve app title from Morphe catalog or Google Play if available
    app_title = resolve_app_title(package_name)
    clean_title = re.split(r"[-:|–—(]", app_title)[0].strip() if app_title else None
    if clean_title:
        title_keywords = {w.lower() for w in re.findall(r"[a-zA-Z]{3,}", clean_title) if w.lower() not in generic_stop}
        meaningful_keywords.update(title_keywords)

    if is_latest:
        queries = [f"{package_name}"]
        if clean_title:
            queries.append(clean_title)
    else:
        queries = [f"{package_name} {clean_version}"]
        parts = clean_version.split(".")
        if len(parts) > 3:
            queries.append(f"{package_name} {'.'.join(parts[:-1])}")
        queries.append(f"{package_name}")

        if clean_title:
            queries.append(f"{clean_title} {clean_version}")
            queries.append(clean_title)
            # Try reversed title words (e.g. 'Proton VPN' -> 'VPN Proton')
            title_words = clean_title.split()
            if len(title_words) >= 2:
                rev_title = " ".join(reversed(title_words))
                queries.append(f"{rev_title} {clean_version}")
                queries.append(rev_title)

    # Fallback to app name/slug if reverse domain, avoiding generic words like 'android' or 'app'
    pkg_parts = package_name.split(".")
    candidates_parts = [p for p in pkg_parts if p.lower() not in generic_stop and len(p) >= 3]
    if candidates_parts:
        if is_latest:
            queries.append(f"{candidates_parts[-1]}")
        else:
            queries.append(f"{candidates_parts[-1]} {clean_version}")
            queries.append(f"{candidates_parts[-1]}")

    # Deduplicate queries while preserving order
    queries = list(dict.fromkeys(queries))
    candidate_app_urls = []

    for i, query in enumerate(queries):
        if i > 0:
            time.sleep(1.0)  # Rate limiting between search requests
        encoded_query = urllib.parse.quote(query)
        url = (
            "https://www.apkmirror.com/"
            f"?post_type=app_release&searchtype=apk&s={encoded_query}"
        )

        try:
            html = fetch_html(url, source="apkmirror")
        except Exception as e:
            log("APKMirror", f"Search failed for '{query}': {e}")
            continue

        soup = BeautifulSoup(html, "html.parser")
        container = (
            soup.select_one(".search-area")
            or soup.select_one(".content-area")
            or soup.select_one("#primary")
            or soup
        )
        for item in container.select("div.appRow"):
            link = item.select_one("a.fontBlack")
            if not link:
                continue

            href = link.get("href")
            if not href:
                continue

            version_element = item.select_one(".appRowTitle")
            result_title = (
                version_element.get_text(" ", strip=True)
                if version_element
                else link.get_text(strip=True)
            )

            title_lower = result_title.lower()
            href_lower = href.lower()

            img_el = item.select_one("img")
            img_src = img_el.get("src", "").lower() if img_el else ""

            dev_el = item.select_one(".byDeveloper")
            dev_lower = dev_el.get_text(strip=True).lower() if dev_el else ""

            # Relevance sanity check
            is_relevant = False
            pkg_lower = package_name.lower()
            if pkg_lower in img_src or pkg_lower in href_lower or pkg_lower in title_lower:
                is_relevant = True
            elif meaningful_keywords and any(
                k in title_lower or k in href_lower or k in dev_lower
                for k in meaningful_keywords
            ):
                is_relevant = True

            if not is_relevant:
                continue

            # Record candidate app parent URLs for feed checking (e.g. /apk/developer/app-name/)
            parts_href = [p for p in href.strip("/").split("/") if p]
            if len(parts_href) >= 3 and parts_href[0] == "apk":
                app_parent = f"https://www.apkmirror.com/apk/{parts_href[1]}/{parts_href[2]}/"
                if app_parent not in candidate_app_urls:
                    candidate_app_urls.append(app_parent)

            if is_latest:
                if href.startswith("/"):
                    return f"https://www.apkmirror.com{href}"
                return href

            match = re.search(
                r"(\d+(?:[.-]\d+)+(?:-[a-zA-Z0-9-]+)?)(?:\s+(?:beta|alpha|rc\d*|\([^)]*\)))*$",
                result_title,
                re.IGNORECASE,
            )
            if not match:
                match = re.search(
                    r"(\d+(?:[.-]\d+)+(?:-[a-zA-Z0-9-]+)?)",
                    result_title,
                )

            if match:
                result_version = match.group(1)
                if normalise_version(result_version) == target_version:
                    if href.startswith("/"):
                        return f"https://www.apkmirror.com{href}"
                    return href

            # Fallback: check against release slug in href
            v_slug = target_version.replace(".", "-")
            if f"-{v_slug}-" in href or href.endswith(f"-{v_slug}-release/"):
                if href.startswith("/"):
                    return f"https://www.apkmirror.com{href}"
                return href

    # 4. Probe feeds of candidate apps discovered in search results
    for app_url in candidate_app_urls[:3]:
        matched = _match_apkmirror_feed_item(get_apkmirror_feed_releases(app_url), target_version, is_latest)
        if matched:
            return matched

    log(
        "APKMirror",
        f"Could not find version {version} for {package_name}",
    )
    return None


def get_apkmirror_details(
    package_name: str,
    target_version: str,
    target_arch: str,
    custom_url: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Lookup release page URL and versionCode from APKMirror.
    Returns: (apkmirror_url, version_code, error_message)
    """
    clean_target = re.sub(r"^[vV]+", "", target_version.strip())
    log(
        "APKMirror",
        f"Looking up version code for {package_name} v{clean_target}...",
    )

    try:
        url = get_apkmirror_version_url(package_name, target_version, custom_url=custom_url)
    except Exception as e:
        err = f"APKMirror: Search failed: {e}"
        log("APKMirror", err)
        return None, None, err

    if not url:
        err = f"APKMirror: Could not find release page for {package_name} v{clean_target}"
        log("APKMirror", err)
        return None, None, err

    try:
        rel_html = fetch_html(url, source="apkmirror")
    except Exception as e:
        err = f"APKMirror: Failed fetching release page ({url}): {e}"
        log("APKMirror", err)
        return url, None, err

    soup = BeautifulSoup(rel_html, "html.parser")
    canon_link = soup.find("link", rel="canonical")
    final_url = canon_link.get("href") if canon_link and canon_link.get("href") else url

    # Sanity check: verify release page belongs to the requested package
    raw_keywords = re.findall(r"[a-zA-Z]{3,}", package_name)
    generic_stop = {"android", "app", "apps", "com", "net", "org", "google", "mobile", "client", "free", "inc", "ltd"}
    meaningful_keywords = {k.lower() for k in raw_keywords if k.lower() not in generic_stop}
    rel_html_lower = rel_html.lower()
    if package_name.lower() not in rel_html_lower and not any(k in rel_html_lower for k in meaningful_keywords):
        err = f"APKMirror: Release page ({final_url}) did not match package '{package_name}'"
        log("APKMirror", err)
        return None, None, err

    norm_target_arch = normalise_arch(target_arch)

    # 0. Check direct variant download page or single-variant release (e.g. .appspec-value)
    appspec_values = [v.get_text(" ", strip=True) for v in soup.select(".appspec-value")]
    appspec_text = " ".join(appspec_values)

    direct_vc_match = (
        re.search(r"Version:\s*[\d.]+\s*\((\d+)\)", appspec_text, re.IGNORECASE)
        or re.search(r"\(([0-9]{5,10})\)", appspec_text)
        or re.search(r"Version:.*?\(([0-9]+)\)", rel_html)
        or re.search(r"-(\d{5,10})_minAPI", rel_html)
    )

    if direct_vc_match:
        cand_archs = []
        for val in appspec_values:
            parsed = parse_architecture(val)
            if parsed and any(normalise_arch(p) in ("arm64-v8a", "armeabi-v7a", "universal", "noarch", "all", "x86", "x86_64") for p in parsed):
                cand_archs.extend(parsed)
        if not cand_archs:
            cand_archs = parse_architecture(appspec_text) or ([norm_target_arch] if norm_target_arch in rel_html else ["universal"])

        page_tier = match_architecture_tier(cand_archs, target_arch)
        if page_tier is not None:
            return final_url, direct_vc_match.group(1), None

    # 1. Parse variant table directly on the release page
    candidates = []  # list of (tier, vc, v_link)
    for row in soup.select("div.table-row"):
        link = row.select_one('a[href*="-android-apk-download/"]')
        if not link:
            continue
        v_link = link.get("href")
        vc_el = row.select_one("span.colorLightBlack")
        vc = vc_el.get_text(strip=True) if vc_el else None

        cells = row.select(".table-cell")
        arch_text = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        cand_archs = [normalise_arch(a) for a in parse_architecture(arch_text)]
        tier = match_architecture_tier(cand_archs, target_arch)

        if not vc:
            m = re.search(r"\((\d+)\)", row.get_text(" ", strip=True))
            if m:
                vc = m.group(1)

        if vc and vc.isdigit() and tier is not None:
            candidates.append((tier, vc, v_link))

    if candidates:
        candidates.sort(key=lambda x: x[0])  # Sort by tier (exact first, then universal)
        return final_url, candidates[0][1], None

    # 2. Check for direct versionCode in the release page (if no variant table)
    variant_links = re.findall(
        r'href="(/apk/[^"]+-android-apk-download/)"',
        rel_html,
    )

    if not variant_links:
        direct_match = re.search(
            r'class="colorDark.*?">(\d+)<',
            rel_html,
        )
        if direct_match:
            return final_url, direct_match.group(1), None

    # 3. Fallback: inspect inner variant pages
    variant_links = list(dict.fromkeys(variant_links))
    for v_link in variant_links:
        try:
            var_html = fetch_html(
                f"https://www.apkmirror.com{v_link}",
                source="apkmirror",
            )
            var_archs = ["universal"] if "universal" in var_html.lower() or "noarch" in var_html.lower() else []
            if norm_target_arch in var_html:
                var_archs.append(norm_target_arch)
            if match_architecture_tier(var_archs, target_arch) is not None:
                code_match = re.search(r"\((\d+)\)", var_html)
                if code_match:
                    return final_url, code_match.group(1), None
        except Exception:
            continue

    err = f"APKMirror: Found release page ({final_url}) but no variant matching arch '{target_arch}' or universal"
    return final_url, None, err


def get_apkmirror_version_code(
    package_name: str,
    target_version: str,
    target_arch: str,
    custom_url: str | None = None,
) -> str | None:
    """Fallback: lookup versionCode from APKMirror when APKPure/APKCombo don't have it."""
    _, vc, _ = get_apkmirror_details(package_name, target_version, target_arch, custom_url=custom_url)
    return vc


# ==============================================================================
# MULTI-SOURCE LATEST VERSION RESOLUTION
# ==============================================================================

def is_beta_version(version_str: str) -> bool:
    """Check if a version string indicates a prerelease, beta, alpha, rc, or dev build."""
    if not version_str:
        return False
    pattern = r"(?:^|[\W_])(?:beta|alpha|rc|preview|dev|canary|nightly)(?:\d*|[\W_]|$)"
    return bool(re.search(pattern, version_str, re.IGNORECASE))


def extract_version_tuple(version_str: str) -> tuple[int, ...]:
    """Extract numeric components of a version string into an integer tuple."""
    if not version_str:
        return ()
    clean = re.sub(r"^[vV><=~!]+", "", version_str.strip())
    clean = re.sub(r"-(?:release|final)", "", clean, flags=re.IGNORECASE)
    nums = [int(p) for p in re.findall(r"\d+", clean)]
    return tuple(nums)


def pad_version_tuple(ver_tuple: tuple[int, ...], target_length: int) -> tuple[int, ...]:
    """Pad an integer tuple with trailing zeroes to target_length."""
    if len(ver_tuple) >= target_length:
        return ver_tuple
    return ver_tuple + (0,) * (target_length - len(ver_tuple))


def _probe_uptodown_latest(package_name: str, arch: str, allow_betas: bool, timeout: int) -> dict | None:
    try:
        app_url = get_uptodown_app_url(package_name)
        if not app_url:
            return None

        page_html = None
        base_app_url = re.sub(r"https?://([^/]+)\.en\.uptodown\.com", r"https://\1.uptodown.com", app_url)
        base_app_url = re.sub(r"https?://en\.uptodown\.com", r"https://www.uptodown.com", base_app_url).rstrip("/")
        for cand_url in dict.fromkeys([
            f"{app_url.rstrip('/')}/download",
            f"{base_app_url}/descargar",
            f"{app_url.rstrip('/')}/descargar",
            f"{base_app_url}/download",
        ]):
            try:
                cand_html = fetch_html(cand_url)
                if cand_html and ("detail-download-button" in cand_html or "detail-app-name" in cand_html):
                    page_html = cand_html
                    break
            except Exception:
                continue

        if not page_html:
            return None
        soup = BeautifulSoup(page_html, "html.parser")

        btn = soup.select_one("#detail-download-button")
        app_name_el = soup.select_one("#detail-app-name")

        app_id = (btn.get("data-app-id") if btn else None) or (app_name_el.get("data-code") if app_name_el else None)
        file_id = (btn.get("data-file-id") if btn else None) or (app_name_el.get("data-file-id") if app_name_el else None)

        if not app_id or not file_id:
            return None

        version_div = soup.select_one("div.version")
        latest_ver = version_div.get_text(strip=True) if version_div else None
        if not latest_ver:
            return None

        clean_ver = normalise_version(latest_ver)
        if not clean_ver:
            return None

        if not allow_betas and is_beta_version(latest_ver):
            return None

        return {
            "source": "uptodown",
            "version": clean_ver,
            "version_code": None,
            "raw_version": latest_ver,
        }
    except Exception as e:
        log("Uptodown", f"Latest version probe failed for {package_name}: {e}")
        return None


def _probe_apkcombo_latest(package_name: str, arch: str, allow_betas: bool, timeout: int) -> dict | None:
    try:
        slug = get_apkcombo_slug(package_name)
        if not slug:
            return None

        url = f"{APKCOMBO_BASE}/{slug}/{package_name}/download/apk"
        cand = get_apkcombo_variant_info(url, package_name, "latest", arch, slug=slug, verbose=False)
        if not cand or not cand.get("version_name") or not cand.get("download_url"):
            return None

        raw_ver = cand["version_name"]
        clean_ver = normalise_version(raw_ver)
        if not clean_ver:
            return None

        if not allow_betas and is_beta_version(raw_ver):
            return None

        return {
            "source": "apkcombo",
            "version": clean_ver,
            "version_code": cand.get("version_code"),
            "raw_version": raw_ver,
        }
    except Exception as e:
        log("APKCombo", f"Latest version probe failed for {package_name}: {e}")
        return None


def _probe_apkpure_latest(package_name: str, arch: str, allow_betas: bool, timeout: int) -> dict | None:
    try:
        data = query_apkpure_api(package_name, arch=arch, target_version=None)
        matched = data.get("matched_version")
        if not matched or not matched.get("download_link"):
            return None

        raw_ver = matched.get("version_name", "")
        clean_ver = normalise_version(raw_ver)
        if not clean_ver:
            return None

        if not allow_betas and is_beta_version(raw_ver):
            return None

        return {
            "source": "apkpure",
            "version": clean_ver,
            "version_code": matched.get("version_code"),
            "raw_version": raw_ver,
        }
    except Exception as e:
        log("APKPure", f"Latest version probe failed for {package_name}: {e}")
        return None


def _probe_aptoide_latest(package_name: str, arch: str, allow_betas: bool, timeout: int) -> dict | None:
    try:
        params = {"package_name": package_name}
        for base_url in APTOIDE_BASE_URLS:
            try:
                resp = requests.get(
                    f"{base_url}/app/getMeta",
                    params=params,
                    headers=APTOIDE_HEADERS,
                    timeout=timeout,
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    data = payload.get("data") or payload.get("nodes", {}).get("meta", {}).get("data")
                    if not data:
                        continue
                    file_info = data.get("file", {})
                    raw_ver = file_info.get("vername", "")
                    clean_ver = normalise_version(raw_ver)
                    if not clean_ver:
                        return None
                    if not allow_betas and is_beta_version(raw_ver):
                        return None
                    vc = str(file_info.get("vercode")) if file_info.get("vercode") is not None else None
                    return {
                        "source": "aptoide",
                        "version": clean_ver,
                        "version_code": vc,
                        "raw_version": raw_ver,
                    }
            except Exception:
                continue
        return None
    except Exception as e:
        log("Aptoide", f"Latest version probe failed for {package_name}: {e}")
        return None


def _probe_google_play_latest(package_name: str, arch: str, allow_betas: bool, timeout: int) -> dict | None:
    try:
        goopdl_bin = "goopdl"
        venv_bin = Path(sys.executable).parent / "goopdl"
        if venv_bin.is_file() and os.access(venv_bin, os.X_OK):
            goopdl_bin = str(venv_bin)
        elif not shutil.which("goopdl"):
            p_venv_bin = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "goopdl"
            if p_venv_bin.is_file() and os.access(p_venv_bin, os.X_OK):
                goopdl_bin = str(p_venv_bin)
            else:
                return {"error": "goopdl not installed"}

        goopdl_arch = {
            "arm64-v8a": "arm64",
            "armeabi-v7a": "armv7",
            "arm-v7a": "armv7",
            "x86": "x86",
            "x86_64": "x86_64",
        }
        norm_arch = normalise_arch(arch)
        g_arch = goopdl_arch.get(norm_arch)
        if not g_arch:
            return {"error": f"unsupported arch {arch}"}

        cmd_latest = [goopdl_bin, "latest", package_name, "--arch", g_arch, "--stable", "1"]
        try:
            res = subprocess.run(cmd_latest, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"error": "timeout"}

        if res.returncode != 0:
            err_msg = (res.stderr or "").strip()
            if "rate-limiting" in err_msg.lower() or "rate limit" in err_msg.lower():
                return {"error": "rate limited"}
            return {"error": f"latest exit {res.returncode}"}

        clean_stdout = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", res.stdout or "")
        m = re.search(r"Latest:\s*(.+?)\s*\((\d+)\)", clean_stdout)
        if not m:
            return {"error": "not found on Play Store"}

        raw_ver = m.group(1).strip()
        vc = m.group(2).strip()
        clean_ver = normalise_version(raw_ver)
        if not clean_ver:
            return {"error": f"invalid version '{raw_ver}'"}

        if not allow_betas and is_beta_version(raw_ver):
            return {"error": f"beta version filtered ({raw_ver})"}

        cmd_inspect = [goopdl_bin, "inspect-delivery", package_name, "--arch", g_arch, "--version", str(vc), "--json"]
        try:
            insp_res = subprocess.run(cmd_inspect, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            insp_res = None

        if insp_res is not None:
            if insp_res.returncode == 0:
                try:
                    data = json.loads(insp_res.stdout)
                    cand_arch = data.get("architecture") or "universal"
                    cand_archs = parse_architecture(cand_arch) if cand_arch else ["universal"]
                    if data.get("package") == package_name and match_architecture_tier(cand_archs, norm_arch):
                        return {
                            "source": "google_play",
                            "version": clean_ver,
                            "version_code": str(vc),
                            "raw_version": raw_ver,
                        }
                    else:
                        return {"error": f"arch mismatch ({cand_arch})"}
                except Exception as e:
                    return {"error": f"json parse error: {e}"}
            elif insp_res.returncode == 5:
                # Rate-limited on delivery inspection, but version and code confirmed from Google Play
                return {
                    "source": "google_play",
                    "version": clean_ver,
                    "version_code": str(vc),
                    "raw_version": raw_ver,
                }
            elif insp_res.returncode == 4:
                return {"error": "delivery unavailable (exit 4)"}
            else:
                return {"error": f"inspect exit {insp_res.returncode}"}

        return {
            "source": "google_play",
            "version": clean_ver,
            "version_code": str(vc),
            "raw_version": raw_ver,
        }
    except Exception as e:
        log("GooglePlay", f"Latest version probe failed for {package_name}: {e}")
        return {"error": str(e)}


def get_latest_version(
    package_name: str,
    arch: str = "arm64-v8a",
    sources_file: Path | None = None,
    allow_betas: bool = False,
    timeout: int = 15,
) -> dict:
    """
    Polls all available sources to find the true latest downloadable version.

    Returns:
    {
        "package_name": "com.duolingo",
        "version": "6.95.0",
        "version_code": "1458",
        "winning_source": "uptodown",
        "arch": "arm64-v8a",
        "sources_checked": {
            "uptodown": "6.95.0",
            "apkcombo": "6.95.0",
            "apkpure": "6.92.5"
        }
    }
    """
    source_labels = {
        "google_play": "Google Play",
        "uptodown": "Uptodown",
        "aptoide": "Aptoide",
        "apkcombo": "APKCombo",
        "apkpure": "APKPure",
        "github": "GitHub",
        "html": "HTML",
    }

    # Step 1: Check Custom Mappings First (e.g. GitHub releases in app_sources.yaml)
    if sources_file is None:
        default_sources = Path(__file__).resolve().parent.parent / "tools" / "automorph-data" / "app_sources.yaml"
        if not default_sources.is_file():
            default_sources = Path(__file__).resolve().parent.parent / "tools" / "app_sources.yaml"
        if default_sources.is_file():
            sources_file = default_sources

    if sources_file and Path(sources_file).is_file():
        try:
            with open(sources_file, "r", encoding="utf-8") as f:
                sources = yaml.safe_load(f) or {}
            if package_name in sources:
                src_conf = sources[package_name]
                if isinstance(src_conf, dict):
                    src_type = src_conf.get("type")
                    pattern = src_conf.get("asset_pattern", r"\.apk$")
                    target_arch = src_conf.get("arch") or arch

                    if src_type == "github":
                        repo = src_conf.get("repo")
                        git_link, git_ver = make_github_dl_link(
                            package_name=package_name,
                            target_version="0.0.0",
                            repo=repo,
                            pattern=pattern,
                            arch=target_arch,
                            sources_file=sources_file,
                        )
                        git_clean_ver = normalise_version(git_ver) or git_ver
                        if git_clean_ver and git_clean_ver != "0.0.0":
                            logger.info(f" └── [✓] Resolved latest GitHub release: v{git_clean_ver} ({repo})")
                            return {
                                "package_name": package_name,
                                "version": git_clean_ver,
                                "version_code": None,
                                "winning_source": "github",
                                "arch": arch,
                                "sources_checked": {
                                    "github": git_clean_ver,
                                },
                            }

                    elif src_type == "html":
                        target_url = src_conf.get("target_url") or src_conf.get("url")
                        html_link, html_ver = make_html_index_dl_link(
                            package_name=package_name,
                            target_version="0.0.0",
                            target_url=target_url,
                            pattern=pattern,
                            arch=target_arch,
                            sources_file=sources_file,
                            pkg_conf=src_conf,
                        )
                        html_clean_ver = normalise_version(html_ver) or html_ver
                        if html_clean_ver and html_clean_ver != "0.0.0":
                            logger.info(f" └── [✓] Resolved latest HTML release: v{html_clean_ver} ({target_url})")
                            return {
                                "package_name": package_name,
                                "version": html_clean_ver,
                                "version_code": None,
                                "winning_source": "html",
                                "arch": arch,
                                "sources_checked": {
                                    "html": html_clean_ver,
                                },
                            }
        except Exception as e:
            logger.warning(f"Custom sources lookup failed for {package_name}: {e}")

    # Step 2: Concurrent Multi-Source Probing
    logger.info(f" ├── Probing sources for latest {package_name} ({arch})...")
    candidates: list[dict] = []
    sources_checked: dict[str, str] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_source = {
            executor.submit(_probe_uptodown_latest, package_name, arch, allow_betas, timeout): "uptodown",
            executor.submit(_probe_apkcombo_latest, package_name, arch, allow_betas, timeout): "apkcombo",
            executor.submit(_probe_apkpure_latest, package_name, arch, allow_betas, timeout): "apkpure",
            executor.submit(_probe_aptoide_latest, package_name, arch, allow_betas, timeout): "aptoide",
            executor.submit(_probe_google_play_latest, package_name, arch, allow_betas, max(timeout, 25)): "google_play",
        }
        for future in concurrent.futures.as_completed(future_to_source):
            src_name = future_to_source[future]
            label = source_labels.get(src_name, src_name)
            try:
                cand = future.result()
                if cand and cand.get("version"):
                    sources_checked[src_name] = cand["version"]
                    candidates.append(cand)
                    vc_str = f" (vc: {cand['version_code']})" if cand.get("version_code") else ""
                    logger.info(f" │    ├── [{label}] v{cand['version']}{vc_str}")
                else:
                    reason = f" ({cand['error']})" if (isinstance(cand, dict) and cand.get("error")) else ""
                    logger.info(f" │    ├── [{label}] Not available{reason}")
            except Exception as e:
                log(src_name, f"Probe error for {package_name}: {e}")
                logger.warning(f" │    ├── [{label}] Skipped ({e})")

    if not candidates:
        logger.error(f" └── [✗] Could not resolve a downloadable version across any source.")
        return {
            "package_name": package_name,
            "version": None,
            "version_code": None,
            "winning_source": None,
            "arch": arch,
            "sources_checked": sources_checked,
        }

    # Step 3 & 4: Normalization, Padding, and Selection
    for c in candidates:
        c["tuple"] = extract_version_tuple(c["version"])

    max_len = max(len(c["tuple"]) for c in candidates) if candidates else 0
    for c in candidates:
        c["padded_tuple"] = pad_version_tuple(c["tuple"], max_len)

    # Source tie-breaking priority when version is identical:
    # Google Play > Uptodown > Aptoide > APKCombo > APKPure
    source_priority = {"google_play": 5, "uptodown": 4, "aptoide": 3, "apkcombo": 2, "apkpure": 1}

    def sort_key(cand: dict) -> tuple[tuple[int, ...], int]:
        src_rank = source_priority.get(cand["source"], 0)
        return (cand["padded_tuple"], src_rank)

    winner = max(candidates, key=sort_key)
    winning_version = normalise_version(winner["version"]) or winner["version"]
    winning_vc = winner.get("version_code")

    # If winning source did not provide a version code (e.g. Uptodown),
    # backfill from another source with the same version tuple
    if not winning_vc:
        matching_cands = [
            c for c in candidates
            if c["padded_tuple"] == winner["padded_tuple"] and c.get("version_code")
        ]
        if matching_cands:
            best_vc_cand = max(
                matching_cands,
                key=lambda x: int(re.sub(r"\D", "", str(x.get("version_code") or "0")) or 0),
            )
            winning_vc = best_vc_cand["version_code"]

    winner_label = source_labels.get(winner["source"], winner["source"])
    vc_summary = f" (vc: {winning_vc})" if winning_vc else ""
    logger.info(f" └── [✓] Resolved latest version: v{winning_version}{vc_summary} (via {winner_label})")

    return {
        "package_name": package_name,
        "version": winning_version,
        "version_code": winning_vc,
        "winning_source": winner["source"],
        "arch": arch,
        "sources_checked": sources_checked,
    }


# ==============================================================================
# MAIN PROCESSING
# ==============================================================================

def process_package(
    package_name: str,
    target_version: str,
    arch: str,
    sources_file: Path,
    check_apkmirror: bool = False,
) -> dict:
    v_prefix = "" if target_version.strip().lower().startswith("v") else "v"
    logger.info(f"\nProcessing {package_name} {v_prefix}{target_version} ({arch})...")
    op, clean_ver, target_tuple = parse_version_constraint(target_version)

    static_sources = check_sources_yaml(package_name, target_version, sources_file)

    git_dl_link = None
    html_dl_link = None
    resolved_version_name = None
    version_code = None
    apkpure_dl_link = None
    apkcombo_dl_link = None
    uptodown_dl_link = None
    aptoide_dl_link = None
    aptoide_version_code = None
    apkmirror_url = None
    apkmirror_version_code = None
    error_msg = None

    if static_sources:
        pkg_conf = static_sources[package_name]
        src_type = pkg_conf.get('type')
        pattern = pkg_conf.get("asset_pattern", r"\.apk$")
        arch = pkg_conf.get("arch") or arch

        if src_type == 'github':
            repo = pkg_conf.get("repo")
            git_dl_link, resolved_target_version = make_github_dl_link(
                package_name, target_version, repo, pattern, arch, sources_file, pkg_conf=pkg_conf
            )
            if git_dl_link:
                resolved_version_name = normalise_version(resolved_target_version) or resolved_target_version
            else:
                log("GitHub", f"Custom source lookup failed for {package_name}. Falling back to other sources...")

        elif src_type == 'html':
            target_url = pkg_conf.get('target_url') or pkg_conf.get('url')
            html_dl_link, resolved_target_version = make_html_index_dl_link(
                package_name, target_version, target_url, pattern, arch, sources_file, pkg_conf=pkg_conf
            )
            if html_dl_link:
                resolved_version_name = normalise_version(resolved_target_version) or resolved_target_version
            else:
                logger.warning(f"HTML source lookup failed for {package_name}. Falling back to other sources...")

        else:
            logger.warning(f"Unknown type '{src_type}' discovered in app_sources.yaml for {package_name}")

    custom_dl_link = git_dl_link or html_dl_link

    if not custom_dl_link:
        if target_version.lower() in ("latest", "any", "any / latest", "0.0.0"):
            logger.info(f"[*] Resolving latest version for {package_name} across sources...")
            latest_res = get_latest_version(package_name, arch=arch, sources_file=sources_file)
            if latest_res and latest_res.get("version"):
                target_version = latest_res["version"]
                op, clean_ver, target_tuple = parse_version_constraint(target_version)
                logger.info(f" └── [✓] Resolved latest version: v{target_version} ({latest_res.get('winning_source')})")

        apkpure_version_code = None
        apkcombo_version_code = None

        variant_info = None
        matched = None

        errors = []

        # ==========================================================================
        # STEP 1: APKPURE (Direct API)
        # ==========================================================================

        try:
            log("APKPure", f"Querying API for {package_name} ({arch})...")
            api_res = query_apkpure_api(package_name, arch, target_version)
            matched = api_res.get("matched_version")

            if matched:
                if matched.get("download_link"):
                    apkpure_version_code = str(matched["version_code"])
                    apkpure_dl_link = matched["download_link"]
                    log(
                        "APKPure",
                        f"Selected: version_code {apkpure_version_code} | "
                        f"version_name {matched['version_name']} | SHA-1: {matched.get('sha1')}",
                    )
                    log("APKPure", "Download link found.")
                else:
                    raise RuntimeError(
                        f"Found version '{matched.get('version_name', target_version)}' on APKPure "
                        f"but no download link was returned."
                    )
            else:
                avail = api_res.get("available_versions", [])
                avail_str = f" Available: {', '.join(avail[:5])}..." if avail else " No versions found on API."
                raise RuntimeError(f"Version '{target_version}' not found on APKPure.{avail_str}")

        except Exception as e:
            error = str(e)
            errors.append(f"APKPure: {error}")
            log("APKPure", f"Failed: {error}")

        # ==========================================================================
        # STEP 2: APKCOMBO
        # ==========================================================================

        try:
            log("APKCombo", "Looking up version...")

            slug = get_apkcombo_slug(package_name)

            version_page_url = get_apkcombo_version_page_url(slug, package_name, target_version)

            variant_info = get_apkcombo_variant_info(
                version_page_url,
                package_name,
                target_version,
                arch,
                slug=slug,
            )

            apkcombo_version_code = variant_info["version_code"]
            apkcombo_dl_link = variant_info["download_url"]

            log(
                "APKCombo",
                f"Selected: version_code {apkcombo_version_code} | "
                f"arch: {', '.join(variant_info['architectures'])}",
            )
            log("APKCombo", "Download link found.")

        except Exception as e:
            error = str(e)
            errors.append(f"APKCombo: {error}")
            log("APKCombo", f"Failed: {error}")

        # ==========================================================================
        # STEP 3: UPTODOWN
        # ==========================================================================

        try:
            log("Uptodown", f"Looking up {package_name} ({arch})...")
            uptodown_dl, uptodown_err = query_uptodown(package_name, target_version, arch)
            if uptodown_dl:
                uptodown_dl_link = uptodown_dl
                log("Uptodown", "Download link found.")
            elif uptodown_err:
                errors.append(uptodown_err)
                log("Uptodown", f"Failed: {uptodown_err}")
        except Exception as e:
            error = str(e)
            errors.append(f"Uptodown: {error}")
            log("Uptodown", f"Failed: {error}")

        # ==========================================================================
        # STEP 3.4: APTOIDE
        # ==========================================================================

        try:
            log("Aptoide", f"Looking up {package_name} ({arch})...")
            aptoide_vc, aptoide_dl, aptoide_err = query_aptoide(package_name, target_version, arch)
            if aptoide_dl:
                aptoide_dl_link = aptoide_dl
                aptoide_version_code = aptoide_vc
                log("Aptoide", f"Selected: version_code {aptoide_version_code}")
                log("Aptoide", "Download link found.")
            elif aptoide_vc:
                aptoide_version_code = aptoide_vc
                log("Aptoide", f"Selected: version_code {aptoide_version_code}")
                if aptoide_err:
                    errors.append(aptoide_err)
            elif aptoide_err:
                errors.append(aptoide_err)
                log("Aptoide", f"Failed: {aptoide_err}")
        except Exception as e:
            error = str(e)
            errors.append(f"Aptoide: {error}")
            log("Aptoide", f"Failed: {error}")

        # ==========================================================================
        # STEP 3.5: APKMIRROR LOOKUP (PROACTIVE OR FALLBACK / RESOLVER)
        # ==========================================================================

        custom_apkmirror_url = None
        if static_sources and isinstance(static_sources, dict) and package_name in static_sources:
            cfg = static_sources[package_name]
            if isinstance(cfg, dict):
                custom_apkmirror_url = cfg.get("apkmirror_url") or cfg.get("apkmirror_slug")

        version_codes = {
            vc
            for vc in (apkpure_version_code, apkcombo_version_code, aptoide_version_code)
            if vc is not None
        }

        # Query APKMirror if proactively requested or if version_codes need reconciliation / fallback
        need_apkmirror = check_apkmirror or (len(version_codes) != 1)
        if need_apkmirror:
            apkmirror_url, apkmirror_version_code, apkmirror_err = get_apkmirror_details(
                package_name, target_version, arch, custom_url=custom_apkmirror_url
            )
            if apkmirror_err:
                errors.append(apkmirror_err)
            elif apkmirror_version_code:
                log("APKMirror", f"Found version code: {apkmirror_version_code}")
                if not version_codes:
                    version_codes.add(apkmirror_version_code)

        # ==========================================================================
        # STEP 4: RECONCILE VERSION CODES
        # ==========================================================================

        if matched and matched.get("version_name"):
            resolved_version_name = normalise_version(matched.get("version_name")) or matched.get("version_name")
        elif variant_info and variant_info.get("version_name"):
            resolved_version_name = normalise_version(variant_info.get("version_name")) or variant_info.get("version_name")
        else:
            resolved_version_name = clean_ver or normalise_version(target_version) or target_version

        if len(version_codes) > 1:
            mismatch_parts = [
                f"APKPure={apkpure_version_code}" if apkpure_version_code else None,
                f"APKCombo={apkcombo_version_code}" if apkcombo_version_code else None,
                f"Aptoide={aptoide_version_code}" if aptoide_version_code else None,
                f"APKMirror={apkmirror_version_code}" if apkmirror_version_code else None,
            ]
            error_msg = f"Version code mismatch: {', '.join(p for p in mismatch_parts if p)}"
        elif version_codes:
            version_code = version_codes.pop()
        else:
            error_msg = "; ".join(errors) if errors else "No version code found across any source."

        if not error_msg and errors:
            error_msg = "; ".join(errors)

    # ==========================================================================
    # STEP 5: RESULT
    # ==========================================================================

    sources = {
        "html": html_dl_link,
        "GitHub": git_dl_link,
        "gplay": None,
        "uptodown": uptodown_dl_link,
        "apkcombo": apkcombo_dl_link,
        "apkpure": apkpure_dl_link,
        "aptoide": aptoide_dl_link,
    }

    result = {
        "package_name": package_name,
        "versionname": resolved_version_name,
        "versioncode": version_code,
        "arch": arch,
        "sources": sources,
        "html_dl_link": html_dl_link,
        "apkpure_dl_link": apkpure_dl_link,
        "apkcombo_dl_link": apkcombo_dl_link,
        "uptodown_dl_link": uptodown_dl_link,
        "git_dl_link": git_dl_link,
        "apkmirror_url": apkmirror_url,
        "apkmirror_versioncode": apkmirror_version_code,
        "aptoide_dl_link": aptoide_dl_link,
        "aptoide_versioncode": aptoide_version_code,
    }

    if error_msg:
        result["error"] = error_msg

    logger.debug(f"Process package result for {package_name}:\n{result}")
    return result

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    default_sources = Path(__file__).resolve().parent.parent / "tools/automorph-data/app_sources.yaml"

    parser = argparse.ArgumentParser(description="Fetch APK metadata and download links across scrapers.")
    parser.add_argument("package", nargs="?", help="Android package name (e.g. epixler.de.hypnobox_android)")
    parser.add_argument("version", nargs="?", help="Target version or constraint (e.g. 4.2.81, 'v>=4.2.81', latest)")
    parser.add_argument("arch", nargs="?", default="arm64-v8a", help="Target architecture (default: arm64-v8a)")
    parser.add_argument("-p", "--package-name", dest="flag_pkg", help="Alternative flag for package name")
    parser.add_argument("-v", "--version-name", dest="flag_ver", help="Alternative flag for target version")
    parser.add_argument("-a", "--arch", dest="flag_arch", default="arm64-v8a", help="Alternative flag for architecture")
    parser.add_argument("-l", "--latest", action="store_true", help="Resolve latest available version across sources")
    parser.add_argument("--allow-betas", action="store_true", help="Allow beta/prerelease versions during latest resolution")
    parser.add_argument("--sources", default=str(default_sources), help="Path to app_sources.yaml")
    parser.add_argument("--check-apkmirror", action="store_true", help="Proactively check APKMirror")

    args = parser.parse_args()
    pkg = args.package or args.flag_pkg
    ver = args.version or args.flag_ver
    arch = args.flag_arch if args.flag_pkg else args.arch
    sources_path = Path(args.sources)

    if pkg:
        if args.latest or ver in ("latest", "resolve-latest", "0.0.0"):
            print(f"\nResolving latest version for {pkg} ({arch})...")
            res = get_latest_version(pkg, arch=arch, sources_file=sources_path, allow_betas=args.allow_betas)
            print("\nLatest Version Result:")
            print(json.dumps(res, indent=2))
        else:
            target_ver = ver if ver else "latest"
            res = process_package(pkg, target_ver, arch, sources_path, check_apkmirror=args.check_apkmirror)
            print("\nFinal Result:")
            print(json.dumps(res, indent=2))
    else:
        process_package("com.accuweather.android", "21.1.15.3", "arm64-v8a", sources_path)
        process_package("com.chess", "4.9.49", "arm64-v8a", sources_path)