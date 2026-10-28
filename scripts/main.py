from pathlib import Path
import yaml
import os
import re
from downloader import Downloader
from morphe import Morphe
from dotenv import load_dotenv
from logger import logger_init
import dispatcher
import time
import sys
import apkpure_fetcher
import logging
import manager

logger = logging.getLogger(__name__)

def main(project_root: Path, conf: dict) -> None:
    start_time = time.perf_counter()
    patch_success = {}
    patch_failures = {}
    init_completed = False

    interrupted = False
    try:
        original_dir, patched_dir, architecture, striplibs, detailed_app_list, repo_details, low_spec_mode, max_original_packages, max_patched_packages = init(project_root, conf)
        endpoints = dispatcher.resolve_tunnel_endpoints(conf)
        base_url = endpoints[0]['base_url']
        init_completed = True
        force = False

        target_pkg = os.getenv("TARGET_PACKAGE") or (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None)
        if target_pkg:
            force = True

            if target_pkg in detailed_app_list:
                target_repos = detailed_app_list[target_pkg]['repos']
                detailed_app_list = {target_pkg: detailed_app_list[target_pkg]}
                repo_details = {r: details for r, details in repo_details.items() if r in target_repos}
            else:
                logger.warning(f"[!] Target package '{target_pkg}' not found in configured apps list.")
        
        morphe = Morphe(project_root, original_dir, patched_dir, architecture, detailed_app_list, repo_details, low_spec_mode, force, base_url)
        downlader = Downloader(original_dir, architecture, project_root)
        
        result = morphe.package_req_main()

        if not result:
            return

        package_version_req, enabled_patches = result
        sources = project_root / "tools" / "automorph-data" / "app_sources.yaml"
        download_list = check_patched_version(package_version_req, patched_dir, architecture, sources, force)

        if target_pkg:
            download_list = {k: v for k, v in download_list.items() if k == target_pkg}

        for package_name, details in download_list.items():
            scanned_repos = detailed_app_list[package_name]['repos']
            result, dest, attempt_details, applied_repos, run, winning_attempt = patch_piepline(package_name, details, downlader, morphe, enabled_patches, patched_dir, striplibs, force)            
        
            resolved_applied = list(applied_repos) if applied_repos else list(scanned_repos)
            if not result:
                logger.warning(f"[✗] Skipping {package_name}: patching could not be completed.")      
                patch_failures[package_name] = {'target_versions': details.get('target_versions', []), 'arch': architecture, 'scanned_repos': scanned_repos, 'applied_repos': resolved_applied, 'attempts': attempt_details}
                continue       

            patch_success[package_name] = {'dest': dest, 'arch': architecture, 'scanned_repos': scanned_repos, 'applied_repos': resolved_applied, 'strategy': run, 'attempt': winning_attempt, 'attempts': attempt_details}

            # clear_old_packages(package_name, max_original_packages, original_dir / package_name, "unpatched", project_root)
            # clear_old_packages(package_name, max_patched_packages, patched_dir / package_name, "patched", project_root)
    
    except KeyboardInterrupt:
        interrupted = True
        logger.critical("\n[!] Execution cancelled by user (SIGINT).")
        sys.exit(130)
    finally:
        if init_completed and not interrupted:
            duration_seconds = time.perf_counter() - start_time
            state_dir = project_root / "tools" / "automorph-data" / "state"
            patched_json = state_dir / "patched.json"

            manager.clean(max_original_packages, max_patched_packages, state_dir, original_dir, patched_dir, patched_json)
            manager.build_all_jsons(state_dir, patched_json, base_url)

            dispatcher.main(
                patch_success,
                patch_failures,
                duration=duration_seconds,
                dry_run=False,
                project_root=project_root,
                build_flags={"striplibs": striplibs, "architecture": architecture, "low_spec_mode": low_spec_mode},
            )

def init(project_root: Path, conf: dict) -> tuple:
    original_dir, patched_dir = make_paths(project_root, conf)
    check_env(project_root)
    architecture, striplibs, detailed_app_list, repo_details, low_spec_mode, max_original_packages, max_patched_packages = parse_yaml(conf)
    return original_dir, patched_dir, architecture, striplibs, detailed_app_list, repo_details, low_spec_mode, max_original_packages, max_patched_packages
    
def make_paths(project_root: Path, conf: dict) -> tuple[Path, Path]:

    def resolve_path(raw_path: Path) -> Path:
        if raw_path.is_absolute():
            return raw_path
        return (project_root / raw_path).resolve()

    original_dir = resolve_path(Path(conf.get("unpatched_app_location", "apps/original")))
    patched_dir = resolve_path(Path(conf.get("patched_app_location", "apps/patched")))
    
    for dir in [original_dir, patched_dir]:
        dir.mkdir(parents=True, exist_ok=True)

    return original_dir, patched_dir

def check_env(project_root: Path):
    try:
        env_file = project_root / ".env"

        if env_file.exists():
            load_dotenv(dotenv_path=env_file)
        else:
            load_dotenv()

    except PermissionError as e:
        logger.error(f"PermissionError loading .env: {e}")
    except Exception as e:
        logger.error(f"Failed to load environment file: {e}")

    if not os.getenv("SMTP_USERNAME") or not os.getenv("SMTP_PASSWORD"):
        logger.warning(f"[*] No SMTP_USERNAME, or SMTP_PASSWORD specified in enviroment. Unable to send email.")

def parse_yaml(conf: dict) -> tuple: 
    low_spec_mode = conf.get("low_spec_mode", False)
    architecture = conf["architecture"]
    striplibs = conf["striplibs"]
    max_original_packages = conf.get('max_original_packages')
    max_patched_packages = conf.get('max_patched_packages')

    detailed_app_list = {}
    repo_details = {}

    for package in conf.get("packages") or []:

        if not package:
            continue

        aliase_map = {}
        package_name = package.get('package_name')
        repos = package.get('repos') or []

        patch_aliases = package.get('patch_aliases') or []

        for alias in patch_aliases:
            if not alias:
                continue
            aliase_name = alias.get('name')
            aliases = alias.get('aliases') or []
            for al in aliases:
                if al:
                    aliase_map[al] = aliase_name

        for repo in repos:
            match = re.findall(r"https://(.+?).com/(.+?)/(.+)", repo)

            if not match:
                logger.warning(f"[!] Invalid GitHub or GitLab URL: {repo}")
                continue
            
            repo_type = match[0][0]
            owner = match[0][1]
            repo_name = match[0][2]
            repo_details[repo] = {'owner': owner, 'repo_name': repo_name, 'repo_type': repo_type}

        detailed_app_list.setdefault(package_name, {}).update({'repos': repos, 'patch_aliases': aliase_map}) 

    return architecture, striplibs, detailed_app_list, repo_details, low_spec_mode, max_original_packages, max_patched_packages

def check_patched_version(package_req: list, patched: Path, arch: str, sources: Path, force: bool):
    logger.debug(f"\npackage_req:\n{package_req}\n")
    needs_patching = {}

    for package_name, version_list in package_req.items():
        patched_package_path = patched / package_name
        first_it = True

        target_versions_set = set()
        for version in version_list:
            version_tup = version, False

            logger.info(f"\n[*] {package_name} | v{version}")

            if version is None or str(version).strip().lower() in ("0.0.0", "any", "latest", "any / latest"):
                logger.info(f" ├── [!] No version requirement for {package_name}. Looking for most up-to-date version for download...")
                version_dict = apkpure_fetcher.get_latest_version(package_name, arch, sources)
                version = version_dict['version']
                version_tup = version_dict['version'], True

            if not version:
                logger.warning(f" └── [!] Could not resolve version for {package_name}. Skipping...")
                continue

            if force:
                logger.info(" └── Forced via automorph console. Added to needs patching list..")
                target_versions_set.add(version_tup)
                first_it = False
                continue

            patched_version_path = patched_package_path / version
            
            if not patched_version_path.is_dir():
                logger.info(" └── Version directory does not exist. Added to needs patching list.")
                target_versions_set.add(version_tup)
                first_it = False
                continue
        
            logger.info(" ├── Version directory exists. Checking for apk...")

            filename = f"{package_name}_{version}_patched.apk"
            file_path = patched_version_path / filename
            if file_path.is_file():
                
                if first_it:
                    logger.info(f" └── [✓] {filename} latest version {version} already patched. Skipping package...")
                    target_versions_set.clear()
                    break

                logger.info(f" └── [✓] {filename} already exists - not proceeding with patch.")
                first_it = False
                continue

            logger.info(f" └── {filename} does not exist. Added to needs patching list.")
            target_versions_set.add(version_tup)

        if target_versions_set:
            needs_patching[package_name] = {'target_versions': sorted(target_versions_set, key=lambda v: v[0], reverse=True), 'patch_attempt': 0}


    logger.info("\n[*] SUMMARY: Packages in download list:")
    if needs_patching:
        for pkg, needs_patching_details in needs_patching.items():
            for i, (version, _) in enumerate(needs_patching_details['target_versions']):
                if i == 0:
                    logger.info(f" └── '{pkg}' 'v{version}'")
                else:
                    logger.info(f"       └── 'Backup no. {i}:' 'v{version}'")
    else:
        logger.info(" └── [✓] None! All packages are up to date.")

    return needs_patching

def patch_piepline(package_name, details, downloader, morphe, enabled_patches, patched_dir, striplibs: bool, force: bool) -> tuple[bool, Path | None, list[dict], set | None, int | None, int | None]:
    targets = details['target_versions']
    patch_attempt = details.get('patch_attempt', 0)
    attempt_details = []
    failed_downloads = set()

    while patch_attempt < (len(targets) * 2):
        if patch_attempt < len(targets):
            run = 1
            target_tuple = targets[patch_attempt]
            target_version = target_tuple[0]
            patched_package = patched_dir / package_name / target_version / f"{package_name}_{target_version}_patched.apk"
        else:
            run = 2
            target_tuple = targets[(patch_attempt - len(targets))]
            target_version = target_tuple[0]
            patched_package = patched_dir / package_name / target_version / f"{package_name}_{target_version}_patched.apk"

            if len(failed_downloads) == len(targets):
                break
            if target_version in failed_downloads:
                patch_attempt += 1
                continue

        if patched_package.is_file() and not force:
            logger.info(f" └── [✓] {package_name} v{target_version} is already patched.")
            return True, patched_package, attempt_details, None, None, None 

        patch_list, dl_reason, package = downloader.download_package_main(package_name, target_version)

        if not patch_list:
            logger.warning(f" └── [!] Base APK download/verification failed for v{target_version}.")
            failed_downloads.add(target_version)
            attempt_details.append({"version": target_version, "run": run, "stage": "Download / Verification", "reason": dl_reason or f"Base APK download/verification failed for v{target_version}"})
            patch_attempt += 1
            continue

        try:
            repos, options_to_use = morphe.check_options_to_use(package_name, target_version, target_tuple[1], enabled_patches, run)

        except ValueError as e:
            logger.error(f" └── [!] Options resolution failed ({e}). Trying backup version...")
            attempt_details.append({'version': target_version, 'run': run, 'stage': "Options Resolution", 'reason': f"Options resolution failed ({e})"})
            patch_attempt += 1
            continue

        result, dest, patch_reason = morphe.patch(package_name, target_version, repos, options_to_use, run, patch_attempt, striplibs, package)
        
        if result:
            winning_attempt = (patch_attempt + 1) if run == 1 else (patch_attempt - len(targets) + 1)
            return True, dest, attempt_details, repos, run, winning_attempt

        attempt_details.append({'version': target_version, 'run': run, 'stage': "Morphe Patching", 'reason': patch_reason or ("Incompatible bytecode" if run == 1 else "Fallback patch failure")})

        if run == 1:
            if dest == "one_repo" and len(targets) == 1:
                logger.warning(f"\n[✗] Only attempt failed for {package_name} when attempting all enabled patches. No other versions to try.")
                return False, None, attempt_details, None, None, None
            
            if patch_attempt < len(targets) - 1:
                logger.info(f" └── [!] Patch failed on v{target_version} with all enabled patches attempted. Falling back to next version...")
            else:
                logger.info(f" └── [!] Patch failed on v{target_version} with all enabled patches attempted. No more versions to try.")
                logger.info(f"      └── Will attempt to patch {targets[0]} with only version compatible patches enabled...")

        else:
            if patch_attempt < (len(targets) * 2) - 1:
                logger.info(f" └── [!] Patch failed on v{target_version} with version specific patches selected. Falling back to next version...")
 
        patch_attempt += 1

    logger.warning(f"\n[✗] All attempts failed for {package_name} when attempting to patch.")
    return False, None, attempt_details, None, None, None

if __name__ == "__main__":
    project_root = Path(os.getenv("AUTOMORPH_ROOT", Path(__file__).resolve().parent.parent))
    config_file = project_root / "tools" / "automorph-data" / "config.yaml"

    if not config_file.is_file():
        raise FileNotFoundError(f"config.yaml not found at: {config_file}")
    
    with config_file.open("r", encoding="utf-8") as f:
      conf = yaml.safe_load(f) or {}

    max_logs = conf.get("max_logs")

    logger, log_dir = logger_init(project_root)

    try:
        logger.info("Starting pipeline...")
        main(project_root, conf)
    except Exception as e:
        logger.exception("Pipeline crashed with an unhandled error: %s", e)
        raise
    finally:
        if max_logs:
            manager.clear_logs(log_dir, max_logs)
