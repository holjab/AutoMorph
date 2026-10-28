import subprocess
import apkpure_fetcher
import json
import shutil
import requests
import filecmp
import re
import os
from pathlib import Path
from zipfile import ZipFile, BadZipFile, ZIP_DEFLATED
from pyaxmlparser import APK
import logging

logger = logging.getLogger(__name__)

class Downloader:
    def __init__(self, org_path: Path, architecture: str, project_root: Path) -> None:
        self.org_path = org_path
        self.architecture = architecture
        self.sources_file = (project_root / "tools" / "automorph-data" / "app_sources.yaml") if (project_root / "tools" / "automorph-data" / "app_sources.yaml").is_file() else (project_root / "tools" / "app_sources.yaml")

#################################
#        DOWNLOAD LOGIC         #
#################################

    def download_package_main(self, package_name: str, target_version: str) -> tuple[dict | None, str | None]: 
        logger.info(f"\n[*] {package_name} | v{target_version} ({self.architecture})")

        package = {}
        outer_dir = self.make_dir(package_name, target_version)
        split_dir = outer_dir / f"{package_name}"

        exists, version_code = self.check_exists(outer_dir, self.architecture, target_version, package_name, split_dir)

        if exists:
            patch_details = {"package_name": package_name, "version_name": target_version, "arch": self.architecture, 'patch_attempt': 0}
            logger.info(f" └── [✓] {package_name} v{target_version} already exists with correct arch.")
            package = {package_name: {target_version: {'version_code': version_code, 'arch': self.architecture}}}
            return patch_details, f"{package_name} already exists", package

        result = apkpure_fetcher.process_package(package_name, target_version, self.architecture, self.sources_file)
        package = self.package_details(result)
        patch_list, reason = self.download_package(result, outer_dir, split_dir)
        return patch_list, reason, package

    def package_details(self, result: dict):
        package = {
            result['package_name']: {
                result['versionname']: {
                    'version_code': result['versioncode'],
                    'arch': result['arch']
                }
            }
        }

        return package

    def download_package(self, result: dict, outer_dir: Path, split_dir: Path) -> tuple[dict | None, str | None]: 
        success, reason = False, None
        package_name, version_name, version_code, arch, sources, apkmirror_versioncode, apkmirror_url = result['package_name'], result['versionname'], result['versioncode'], result['arch'], result['sources'], result['apkmirror_versioncode'], result['apkmirror_url']
        version_codes = [v for v in [version_code, apkmirror_versioncode] if v is not None]

        for source, link in sources.items():
            if source != "gplay" and not link:
                logger.warning(f" └── [!] No {source} download URL provided for {package_name}")
                continue

            success, reason = self.try_source(source, package_name, link, outer_dir, version_name, arch, split_dir, version_codes)
            
            if success:
                logger.info(" └── [✓] Ready to patch.")
                patch_details = {"package_name": package_name, "version_name": version_name, "arch": arch, 'patch_attempt': 0}
                return patch_details, reason

        logger.warning(f" └── [!] Giving up on {package_name}: {reason}")
        
        return None, reason

    def try_source(self, dl_source: str, package_name: str, link: str | None, outer_dir: Path, version_name: str, arch: str, split_dir: Path, version_codes: list) -> tuple[bool, str | None]:
        if not dl_source:
            logger.error(f"Critical error: No dl_source {dl_source}. Exiting...")
            return False, "incorrect dl_source"
        
        elif dl_source != "gplay":
            success, response = self.link_dl(package_name, link, dl_source, outer_dir, version_name)
        else:
            success, response = self.gplay_dl(package_name, arch, version_codes, version_name, outer_dir, split_dir)

        if not success:
            return False, response
        if response == "already_exists":
            return True, "already_exists"

        matched_xapk = next(outer_dir.glob("*.xapk"), None)
        result, reason = False, "verification_failed"

        if matched_xapk:
            result, reason = self.check_xapk(matched_xapk, arch, version_name, package_name)
            
            if reason == "is_apk":
                new_apk = matched_xapk.with_suffix(".apk")
                matched_xapk.replace(new_apk)
                logger.info(f" └── Mislabeled XAPK detected: Renamed to {new_apk.name}")

        matched_apk = next(outer_dir.glob("*.apk"), None)
        if not result and matched_apk:
            result, reason = self.check_apk(matched_apk, arch, version_name, package_name)

        if not matched_apk and not matched_xapk:
            return False, "no_file_found"

        if result:
            return True, "success"
        elif reason is not None and "incorrect_version" in reason:
            i_version = reason.split("-")[1]
            logger.warning(f" ├── [!] Incorrect version detected ({i_version}). Moving directory...")
            self.move_dir(package_name, outer_dir, i_version)

        return False, reason

#################################
#          DOWNLOADERS          #
#################################

    def gplay_dl(self, package_name: str, arch: str, version_codes: list, version_name: str, outer_dir: Path, split_dir: Path) -> tuple[bool, str | None]:
        goopdl_arch = {"arm64-v8a": "arm64", "armeabi-v7a": "armv7", "arm-v7a": "armv7", "x86": "x86", "x86_64": "x86_64",}
        
        if arch not in goopdl_arch:
            logger.error(f" └── [!] Unsupported architecture: {arch}")
            return False, "unsupported goopdl arch"
        
        arch = goopdl_arch[arch]

        if version_codes:
            found, vc, vn = self.goopdl_inspect(package_name, arch, version_codes, version_name)
        else:
            found, vc, vn = self.goopdl_latest(package_name, arch)
        
        if found and vn == version_name:
            result, reason = self.goopdl(package_name, arch, vc, outer_dir, split_dir)
            
            if result:
                return result, "success"

            else:
                logger.error(" └── [✗] goopdl failed.")
                return False, reason

        else:
            return False, vc

    def goopdl_inspect(self, package_name: str, arch: str, versioncodes: list, version_name: str) -> tuple[bool, str | None, str | None]:
            logger.info(" ├── Inspecting Google Play package...")

            try:
                for versioncode in versioncodes:
                    if versioncode != None:
                        logger.info(f" │    ├── Inspecting version code {versioncode}...")
                        cmd = ["goopdl", "inspect-delivery", package_name, "--arch", arch, "--version", versioncode, "--json"]

                    else:
                        logger.info(" │    ├── Inspecting latest version...")
                        cmd = ["goopdl", "inspect-delivery", package_name, "--arch", arch, "--json"]
                        
                    result = subprocess.run(cmd, text=True, capture_output=True)

                    if result.returncode in (130, 143, -2, -15, -9) or result.returncode < 0:
                        logger.critical("\n │    └── [!] Inspection cancelled by user.")
                        raise KeyboardInterrupt("Cancelled by user")
                    if result.returncode == 4:
                        logger.info(" │    │   not available, skipping")
                        continue
                    if result.returncode == 5: 
                        logger.warning(f" │    └── [!] Google Play rate limited: {result.stderr.strip()}")
                        return False, "rate limited", None
                    if result.returncode != 0:
                        logger.error(f" │    └── [!] goopdl failed (exit {result.returncode}): {result.stderr.strip()}")
                        return False, "subprocess error", None

                    try:
                        found = json.loads(result.stdout)
                    except json.JSONDecodeError:
                        logger.error(f" │    └── [!] Could not parse goopdl output: {result.stdout!r}")
                        return False, "json error", None

                    if found['package'] == package_name and found['architecture'] == arch:
                            logger.info(" │    └── [✓] Found package matches name, version and arch...")
                            return True, found['version']['code'], version_name
                
                logger.warning(f" └── [✗] Failed to locate matching package on Google Play for {package_name}")
                
                return False, "none found", None

            except FileNotFoundError:
                logger.warning(" └── [!] goopdl is not installed, falling back to next source...")
                return False, "goopdl not found", None
            except KeyboardInterrupt:
                logger.critical(" └── [!] Download cancelled by user.")
                raise

    def goopdl_latest(self, package_name: str, arch: str) -> tuple[bool, str | None, str | None]:
        logger.info(" ├── Inspecting latest Google Play package...")

        cmd = ["goopdl", "latest", package_name, "--arch", arch]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            match = re.search(r"Latest:\s*(\S+)\s*\((\d+)\)", result.stdout)
            if match:
                versionname = match.group(1)
                versioncode = match.group(2)
                return True, versioncode, versionname
            else:
                logger.warning(" └── [!] No match found")
                return False, "no match found", None
        except FileNotFoundError:
            logger.warning(" └── [!] goopdl is not installed, falling back to next source...")
            return False, "goopdl not found", None

    def goopdl(self, package_name: str, arch: str, versioncode: str, outer_dir: Path, split_dir: Path) -> tuple[bool, str]:
        logger.info(" ├── Downloading Google Play package...")

        cmd = ["goopdl", "download", package_name, "--output", str(split_dir), "--arch", arch, "--version", str(versioncode), "--splits"]

        try:
            result = subprocess.run(cmd, check=True, text=True, capture_output=True)
            
            has_split = split_dir.is_dir() and any(split_dir.glob("*.apk"))
            no_split = any(outer_dir.glob("*.apk"))

            if has_split:
                self.make_xapk(split_dir, package_name)

            elif (not has_split and not no_split):
                logger.warning(" └── [!] No APKs downloaded. Google Play skipped or failed. Falling back to APKPure...")
                return False, "no_download"

            return True, "success"

        except FileNotFoundError:
            logger.warning(" └── [!] goopdl is not installed")
            return False, "goopdl not found"

        except subprocess.CalledProcessError as e:
                if e.returncode in (130, 143, -2, -15, -9) or e.returncode < 0:
                    logger.critical("\n └── [!] Download cancelled by user.")
                    raise KeyboardInterrupt("Download interrupted by user")
                error_msg = e.stderr.strip() if e.stderr else "Unknown CLI error"
                logger.error(f" └── [!] goopdl failed (exit code {e.returncode}): {error_msg}")
                return False, error_msg

        except KeyboardInterrupt:
            logger.critical(" └── [!] Download cancelled by user")
            raise

    def link_dl(self, package_name: str, url: str | None, source: str, outer_dir: Path, version_name: str, chunk_size: int = 128 * 1024) -> tuple[bool, str]:
        logger.info(f" ├── Downloading from {source}...") 

        outer_dir.mkdir(parents=True, exist_ok=True)

        if source == "apkpure":
            is_xapk = "/XAPK/" in url
            dest_file = outer_dir / f"{package_name}.{'xapk' if is_xapk else 'apk'}"
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101"
                    " Firefox/120.0"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": "https://apkpure.com/",
                "Connection": "keep-alive",
            }

        elif source == "apkcombo":
            dest_file = outer_dir / f"{package_name}.xapk"
            headers = {
                "User-Agent": "curl/8.0.1",
                "Accept": "*/*",
                "Connection": "keep-alive",
            }

        elif source == "GitHub":
            dest_file = outer_dir / f"{package_name}.apk"
            headers = {"User-Agent": "MorpheDownloader"}
            token = os.getenv("GITHUB_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"

        elif source =="uptodown":
            dest_file = outer_dir / f"{package_name}.xapk"
            headers = {
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 16; Pixel 8 Pro Build/BP4A.260205.001)",
                "Accept": "*/*",
            }

        temp_file = dest_file.with_suffix(".tmp")

        try:
            logger.info(f" └── Downloading {dest_file.name} from {source}...")
            with requests.get(url, headers=headers, stream=True, timeout=60, allow_redirects=True) as r:
                r.raise_for_status()
                with open(temp_file, "wb") as f:
                    for chunk in r.iter_content(chunk_size=chunk_size):
                        if chunk:
                            f.write(chunk)
            temp_file.replace(dest_file)
            return True, "success"
        
        except Exception as e:
            error_msg = str(e)
            logger.error(f" └── [!] {source} download failed: {error_msg}")

            if temp_file.exists():
                temp_file.unlink()

            return False, error_msg

#################################
#            CHECKS             #
#################################

    def check_exists(self, outer_dir: Path, arch: str, version_name: str, package_name: str, split_dir: Path) -> tuple[bool, str]:
        xapks = list(outer_dir.glob("*.xapk"))

        if split_dir.exists() and not xapks:
            self.make_xapk(split_dir, package_name)
            xapks = list(outer_dir.glob("*.xapk"))

        if xapks:
            for xapk in xapks:
                xapk_result, xapk_reason = self.check_xapk(xapk, arch, version_name, package_name)
                if xapk_reason == "is_apk":
                    new_apk = xapk.with_suffix(".apk")
                    xapk.replace(new_apk)
                    xapk = new_apk
                if xapk_result:
                    return True, xapk_reason           
                logger.warning(f" └── [!] XAPK check failed: {xapk_reason}")
        
        apks = list(outer_dir.glob("*.apk"))

        if apks:
            for apk in apks:
                apk_result, apk_reason = self.check_apk(apk, arch, version_name, package_name)
                if apk_result:
                    return True, apk_reason
                logger.warning(f" └── [!] APK check failed: {apk_reason}")
        
        return False, None
        
    def check_xapk(self, xapk: Path, arch: str, version_name: str, package_name: str) -> tuple[bool, str]:
        is_apk, reason = self.is_apk(xapk)

        if is_apk:
            return False, reason

        arch_result, reason = self.check_xapk_arch(xapk, arch)
        
        if arch_result:
            version_result, reason = self.check_xapk_version(xapk, version_name, package_name)
            if version_result:
                return True, reason

        return False, reason 

    def is_apk(self, xapk: Path) -> tuple[bool, str]:
        try:
            with ZipFile(xapk, "r") as z:  #
                has_manifest = (
                    next((name for name in z.namelist() if name == "AndroidManifest.xml"), None)
                    is not None
                )
                if has_manifest:
                    return True, "is_apk"
                return False, "is_xapk"
            
        except BadZipFile:
            return False, "bad_zip"

    def check_xapk_arch(self, xapk: Path, arch: str) -> tuple[bool, str | None]:
        normalised_arch = arch.replace('-', '_')
        inner_arch = arch.replace('_', '-') if "x86_64" not in arch else "x86_64"

        try:
            with ZipFile(xapk, "r") as outer_xapk:
                inner_files = outer_xapk.namelist()
                candidates = [
                                name for name in inner_files
                                if name.endswith(f"config.{normalised_arch}.apk")
                                or name.endswith(f"{normalised_arch}.apk")
                                or (name.endswith(".apk") and "config" not in name)
                            ]


                for apk_name in candidates:
                    apk_bytes = outer_xapk.read(apk_name)
                    apk = APK(apk_bytes, raw=True)
                    inner_names = apk.get_files()

                    has_so = any(f.startswith(f"lib/{inner_arch}/") and f.endswith(".so") for f in inner_names)

                    if has_so:
                        logger.info(f" ├── Valid {inner_arch} libs verified in nested {apk_name}.")
                        return True, None
            return False, "incorrect_arch_libs"

        except (BadZipFile, OSError):
                return False, "no_xapk"

    def check_xapk_version(self, xapk: Path, version_name: str, package_name: str) -> tuple[bool, str | None]:
        try:
            with ZipFile(xapk, "r") as outer_xapk:
                inner_files = outer_xapk.namelist()
                candidates = [name for name in inner_files
                    if name == f"{package_name}.apk" 
                    or name == f"{package_name}.base.apk"
                    or (name.endswith(".apk") and "config" not in name)
                ]

                if not candidates:
                    logger.warning(" └── [!] No base APK found in xapk")
                    return False, "no apk found in xapk"

                detected_version = None
                
                for candidate in candidates:
                    try:
                        apk_bytes = outer_xapk.read(candidate)
                        detected_version = APK(apk_bytes, raw=True).version_name

                        normalised_detected_version = self.normalise_version_name(detected_version)
                        
                        if detected_version == version_name or normalised_detected_version == version_name:
                            logger.info(f" ├── Base APK matches target version ({version_name})")
                            version_code = APK(apk_bytes, raw=True).version_code
                            return True, version_code
                    except Exception:
                        continue

                if detected_version is None:
                    logger.error(f" ├── [✗] Failed to parse manifest from APK candidates in {xapk.name}")
                    return False, "unparseable_version"
                
                logger.warning(f" ├── [✗] Base APK target version ({version_name}) does not match (found: {detected_version})")
                return False, f"incorrect_version-{detected_version}"
            
        except (BadZipFile, OSError):
            return False, "no_xapk"

    def check_apk(self, apk: Path, arch: str, version_name: str, package_name: str) -> tuple[bool, str | None]:
        arch_result, reason = self.apk_arch(apk, arch)
        if arch_result:
            version_result, reason = self.apk_version(apk, version_name)
            if version_result:
                return True, reason
        return False, reason

    def apk_arch(self, apk: Path, arch: str) -> tuple[bool, str | None]:
        normalised_arch = arch.replace('_', '-') if "x86_64" not in arch else "x86_64"

        try:
            with ZipFile(apk, "r") as apk_zip:
                inner_names = apk_zip.namelist()

                has_so = any(f.startswith(f"lib/{normalised_arch}/") and f.endswith(".so") for f in inner_names)

                if has_so:
                    logger.info(f" ├── [✓] Valid {normalised_arch} libs verified in {apk.name}.")
                    return True, None
                return False, "no_libs"

        except (BadZipFile, OSError):
                return False, "no_apk"

    def apk_version(self, package: Path, target_version: str) -> tuple[bool, str | None]:
        detected_version = APK(package).version_name

        normalise_detected_version = self.normalise_version_name(detected_version)

        if detected_version == target_version or normalise_detected_version == target_version:
            logger.info(f" ├── [✓] Base APK matches target version ({target_version})")
            version_code = APK(package).version_code
            return True, version_code
        if detected_version is None or normalise_detected_version is None:
            logger.error(f" ├── [✗] Failed to parse manifest for {package.name}")
            return False, "unparseable_version"
        else:
            logger.warning(f" ├── [✗] Base APK target version ({target_version}) does not match (found: {detected_version} / {normalise_detected_version})")
            return False, f"incorrect_version-{detected_version}"

#################################
#            UTILITY            #
#################################

    def make_xapk(self, split_dir: Path, package_name: str) -> Path:
        output_xapk = split_dir.parent / f"{package_name}.xapk"

        with ZipFile(output_xapk, "w", compression=ZIP_DEFLATED) as z:
                for file_path in split_dir.rglob("*"):
                    if file_path.is_file():
                        relative_path = file_path.relative_to(split_dir)
                        z.write(file_path, arcname=relative_path)
                
        logger.info(f" └── [✓] Successfully created XAPK: {output_xapk}")
        return output_xapk

    def move_dir(self, package_name: str, download_dir: Path, actual_version: str) -> None:
        source_dir = download_dir / package_name
        target_dir = self.org_path / package_name / actual_version / package_name

        if not source_dir.is_dir():
            logger.warning(f" └── [!] Source directory {source_dir} not found. Skipping move.")
            return

        if target_dir.exists():
            logger.info(f" └── [!] Target {target_dir} already exists. Cleaning up...")
            shutil.rmtree(target_dir, ignore_errors=True)

        destination_dir = self.make_dir(package_name, actual_version)

        logger.info(f" └── Moving {source_dir} -> {destination_dir}...")
        shutil.move(source_dir, destination_dir)
            
    def make_dir(self, package_name: str, version: str) -> Path:
        download_dir = self.org_path / package_name / version
        download_dir.mkdir(parents=True, exist_ok=True)
        return Path(download_dir)

    def normalise_version_name(self, version: str) -> str:
        if not version:
            return ""
        version = re.sub(r"^[vV]", "", version.strip())
        match = re.match(r"^(\d+(?:[.-]\d+)+)", version)
        if match:
            core = match.group(1)
            return core.replace("-", ".").rstrip(".")
        return re.sub(r"[^0-9a-zA-Z.]+$", "", version)
