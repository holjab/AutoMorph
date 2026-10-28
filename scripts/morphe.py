import json
import os
import re
import requests
import hashlib
import subprocess
import urllib.parse
from pathlib import Path
import logging
from datetime import datetime
from pyaxmlparser.axmlprinter import AXMLPrinter
from zipfile import ZipFile

logger = logging.getLogger(__name__)

class Morphe:

    def __init__(self, project_root: Path, original_dir: Path, patched_dir: Path, architecture: str, detailed_app_list: dict[str, dict], repo_details: dict[str, dict], low_spec_mode: bool, force: bool, base_url: str) -> None:
        self.project_root = project_root
        self.repo_details = repo_details
        self.arch = architecture
        self.low_spec_mode = low_spec_mode
        self.detailed_patch_list = detailed_app_list
        self.force = force
        self.base_url = base_url
        
        self.options_dir = project_root / "tools" / "automorph-data" / "options"
        self.original_dir = original_dir
        self.patched_dir = patched_dir
        self.state_dir = project_root / "tools" / "automorph-data" / "state" 
        self.morphe_location = project_root / "tools" / "morphe.jar"
        
        self.repo_versions_json = self.state_dir / "repo_versions.json"
        self.options_checksum_json = self.state_dir / "options_checksum.json"

        self.repo_search: dict[str, set[str]] = {}
        self.enabled_patches_per_app: dict[str, dict[str, dict[str, str]]]

    def get_cached_repo(self, repo: str) -> Path | None:
        owner = self.repo_details[repo]['owner']
        repo_name = self.repo_details[repo]['repo_name']
        cached_repo_dir = self.project_root / "tools" / "morphe-data" / "patches" / f"{owner}-{repo_name}"

        if not cached_repo_dir.is_dir():
            logger.error(f"\n[!] Skipping options for '{owner}/{repo_name}': cache directory does not exist.")
            return None

        cached_repo = next(cached_repo_dir.iterdir(), None)
        
        return cached_repo

#################################
#             MAIN              #
#################################

    def package_req_main(self) -> tuple[dict[str, list[str]], dict] | None:
        self.repo_search = self.scan_updates()

        if self.force:
            (package_name, details), = self.detailed_patch_list.items()
            self.repo_search = dict.fromkeys(details["repos"], {package_name})
        
        if not self.repo_search:
            logger.info("\n[✓] No updated option files or new/updated repos found.\n Exiting...")
            return None
        
        enabled_patches_per_app, all_enabled_repos = self.options_main()
        package_version_req, enabled_patches_per_app_versions = self.get_patch_versions_main(enabled_patches_per_app, all_enabled_repos)
        return package_version_req, enabled_patches_per_app_versions

#################################
#    FIND NEW/UPDATED REPOS     #
#################################

    def scan_updates(self) -> dict[str, set[str]]:
        new_repo_versions = self.find_repo_version()
        updated_repos = self.check_repo_update(new_repo_versions)

        if updated_repos:
            self.build_repo_cache(updated_repos)  
            self.write_repo_versions_json(new_repo_versions)

        if all(repo in updated_repos for repo in self.repo_details):
            return updated_repos
        
        return self.check_options_change(updated_repos)

    def find_repo_version(self) -> dict[str, list[str]] | None:
        repo_version = {}

        for repo in self.repo_details:
            owner = self.repo_details[repo]['owner']
            repo_name = self.repo_details[repo]['repo_name']
            repo_type = self.repo_details[repo]['repo_type']

            if not owner or not repo_name:
                logger.error(f"Unable to find owner or repo of {repo}")
                return None

            if repo_type == 'github' or repo_type == 'gitlab':
                headers = {
                    "User-Agent": "MorpheUpdater",
                    "Accept": "application/json",
                }

            if repo_type == 'github':
                api_url = f"https://api.github.com/repos/{owner}/{repo_name}/releases/latest"      

                token = os.getenv("GITHUB_TOKEN")
                if token:
                    headers["Authorization"] = f"Bearer {token}"

            elif repo_type == 'gitlab':
                project_path = urllib.parse.quote(f"{owner}/{repo_name}", safe="")
                api_url = f"https://gitlab.com/api/v4/projects/{project_path}/releases/permalink/latest"
            
            else:
                logger.warning(f" └── [✗] Unknown repo type: {repo_type} for {repo}")
                continue

            self.repo_details[repo].update({'api_url': api_url, 'headers': headers}) 

            try:
                response = requests.get(api_url, headers=headers, timeout=10)
                if response.status_code == 200:
                    repo_version[repo] = re.findall(r"\d+", response.json().get("tag_name"))
                    # repo_version[repo] = tuple(int(n) for n in re.findall(r"\d+", response.json().get("tag_name")))

                elif response.status_code == 404:
                    logger.warning(" └── [!] 404 error")
                response.raise_for_status()

            except requests.exceptions.RequestException as e:
                logger.error(f" └── [!] Failed checking release for {owner}/{repo}: {e}")

        return repo_version

    def check_repo_update(self, new_repo_versions: dict[str, list[str]]) -> dict[str, set[str]]:
        repo_search = {} 

        if self.repo_versions_json.is_file():
            logger.info("[*] Checking for repo updates...")
            with open(self.repo_versions_json, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
                for repo in self.repo_details:
                    owner = self.repo_details[repo]['owner']
                    if repo not in cached_data:
                        logger.info(f" └── [!] {owner} repo is new. Will add to updated repos list")
                        repo_search[repo] = self.check_repo(repo)    
                    elif repo not in new_repo_versions:
                        logger.warning(f" └── [✗] {owner} repo missing from parsed versions. Skipping.")
                    else:
                        cached_version = tuple(int(n) for n in cached_data[repo])
                        new_version = tuple(int(n) for n in new_repo_versions[repo])

                        if new_version > cached_version:
                            logger.info(f" └── [!] New repo version for {owner} repo. Will add to updated repos list.")
                            repo_search[repo] = self.check_repo(repo)
                        else:
                            logger.info(f" └── [✓] Latest version of {owner} already used.") 
      
        else:
            logger.info(f"[*] No repo_versions.json found. Adding all repos to updated repos list...")
            for repo in self.repo_details:
                repo_search[repo] = self.check_repo(repo)

        return repo_search

    def build_repo_cache(self, updated_repos: dict[str, set[str]]) -> None:
        for repo in updated_repos:
            owner = self.repo_details[repo]['owner']
            repo_name = self.repo_details[repo]['repo_name']
            repo_type = self.repo_details[repo]['repo_type']
            api_url = self.repo_details[repo]['api_url']
            headers = self.repo_details[repo]['headers']

            try:
                res = (requests.get(api_url, headers=headers, timeout=15))
                res.raise_for_status()
                response = res.json()
            except requests.exceptions.RequestException as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status == 404:
                    logger.error(f" └── [!] Repo or release not found (404) for '{owner}/{repo_name}'. Skipping...")
                else:
                    logger.error(f" └── [!] Failed checking release for '{owner}/{repo_name}': {e}")
                continue

            if repo_type == 'github':
                asset_list = response.get("assets", [])
            elif repo_type == 'gitlab':
                asset_list = response.get("assets", {}).get("links", [])

            mpp_asset = next((a for a in asset_list if a.get("name", "").endswith(".mpp")), None)

            if mpp_asset:
                if repo_type == 'github':
                    download_url = mpp_asset["browser_download_url"]
                elif repo_type == 'gitlab':
                    download_url = mpp_asset["direct_asset_url"]
                file_name = mpp_asset["name"]
            else:
                logger.error("No .mpp asset found in release.")
                continue    

            tag_name = response.get('tag_name', "")
            dest = self.project_root / "tools" / "morphe-data" / "patches" / f"{owner}-{repo_name}" / f"{tag_name}__{file_name}"
            dest.parent.mkdir(parents=True, exist_ok=True)

            for item in dest.parent.iterdir():
                if item.is_file():
                    item.unlink(missing_ok=True)

            dl_headers = {"User-Agent": "MorpheDownloader"}
            try:
                with requests.get(download_url, headers=dl_headers, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    tmp_path = dest.with_suffix(".tmp")
                    with open(tmp_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=128 * 1024):
                            if chunk:
                                f.write(chunk)
                    tmp_path.replace(dest)
                    logger.info(f" └── [✓] Download complete for '{owner}': {dest.name}")
                    self.repo_details[repo]['cached_repo'] = {'cached_mpp_path': dest}
            except requests.exceptions.RequestException as e:
                logger.error(f" └── [!] Download failed for '{file_name}': {e}")
                if "tmp_path" in locals() and tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                continue

    def check_options_change(self, updated_repos: dict[str, set[str]]) -> dict[str, set[str]]:
        logger.info(f"\n[*] Checking for updates to options files...")

        repo_options_list = []
        for app, details in self.detailed_patch_list.items():
            app_options_dir = self.options_dir / app
            expected_repos = details.get('repos', [])

            for repo in expected_repos:
                if repo in self.repo_details:
                    expected_options = app_options_dir / f"{self.repo_details[repo]['owner']}_options.json"
                    if not expected_options.is_file():
                        updated_repos.setdefault(repo, set()).add(app)
            
            repo_options_list.extend(app_options_dir.glob('*.json'))

        checksums = self.generate_checksum(repo_options_list)

        try: 
            with open(self.options_checksum_json) as f:
                cached_checksums = json.load(f)

        except FileNotFoundError:
            logger.info(f" └── [!] No checksum json found. Will add all repos to update repos list...")
            for repo in self.repo_details:
                updated_repos[repo] = self.check_repo(repo)
            return updated_repos

        for options_json, checksum in checksums.items():
            cached_checksum = cached_checksums.get(options_json)
            if cached_checksum is None:
                for k, v in cached_checksums.items():
                    if k.endswith(options_json):
                        cached_checksum = v
                        break

            if cached_checksum is not None:
                if not checksum == cached_checksum:
                    options_file = options_json.split("/")[-1]
                    package_name = options_json.split("/")[-2]
                    options_owner = options_json.split("/")[-1].split("_options.json")[0]
                    owner_to_repo = {d['owner']: r for r, d in self.repo_details.items()}

                    logger.info(f" └── [!] Options file '{package_name}/{options_file}' has changed. Will add to update repos list.")
          
                    if options_owner not in owner_to_repo:
                        logger.warning(f" └── [!] Could not locate repo for '{options_owner}'. Skipping...")
                        continue
                    matched_repo = owner_to_repo[options_owner]
                    updated_repos.setdefault(matched_repo, set()).add(package_name)
        return updated_repos
                    
    def write_repo_versions_json(self, new_repo_versions: dict[str, list[str]]) -> None:
        logger.info("\n[*] Updating/creating repo version cache...")

        try:
            with open(self.repo_versions_json, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.info(" └── [!] Updating repo version cache...")
            self.repo_versions_json.parent.mkdir(parents=True, exist_ok=True)
            data = {}

        data.update(new_repo_versions)

        try:
            with open(self.repo_versions_json, "w", encoding="utf-8") as fi:
                fi.write("{\n")
                entries = [f'  {json.dumps(repo)}: {json.dumps(version)}' for repo, version in data.items()]
                fi.write(",\n".join(entries))
                fi.write("\n}\n") 
            logger.info(f" └── [✓] '{self.repo_versions_json}' successfully updated.")

        except OSError as e:
            logger.error(f" └── [!] Failed to write {self.repo_versions_json.name}: {e}")

    def check_repo(self, repo: str | None = None) -> set[str]:
        app_set = set()

        helper = {package_name: details['repos'] for package_name, details in self.detailed_patch_list.items()}

        for package, repos in helper.items():
            if repo in repos:
                app_set.add(package)

        return app_set

    def get_cached_repo_path(self, owner: str, repo_name: str, repo: str) -> Path | None:
        mpp_path = self.repo_details.get(repo, {}).get("cached_repo", {}).get("cached_mpp_path")

        if not mpp_path:
            cached_dir = self.project_root / "tools" / "morphe-data" / "patches" / f"{owner}-{repo_name}"
            if cached_dir.is_dir():
                mpp_path = next((f for f in cached_dir.iterdir() if f.is_file() and f.name.endswith(".mpp")), None)
                if mpp_path:
                    self.repo_details.setdefault(repo, {}).setdefault("cached_repo", {})["cached_mpp_path"] = mpp_path
                else:
                    logger.warning(f" └── [!] Unable to find cached repo mpp for {owner}")
                    return None
            else:
                return None

        return mpp_path if mpp_path else None

#################################
#           OPTIONS             #
#################################

    def options_main(self) -> tuple[dict[str, dict[str, dict[str, str]]], set[str]]:
        repo_options_locations = self.make_options()
        checksums = self.generate_checksum(repo_options_locations)
        self.write_options_checksum_json(checksums)
        self.enabled_patches_per_app, all_enabled_repos = self.filter_repos()

        return self.enabled_patches_per_app, all_enabled_repos
        
    def make_options(self) -> list[Path]:
        repo_options_locations = []

        for repo in self.repo_search:     
            cached_repo = self.get_cached_repo(repo)  

            if not cached_repo:
                continue

            owner = self.repo_details[repo]['owner']

            logger.info(f"\n[*] Creating options json(s) from cached repo: '{cached_repo.parent.name}/{cached_repo.name}'")  

            for package_name in self.repo_search[repo]:
                app_options_dir = self.options_dir / package_name
                app_options_dir.mkdir(parents=True, exist_ok=True)
                repo_options = app_options_dir / f"{owner}_options.json" 

                result = subprocess.run(
                    [
                        "java",
                        "-jar",
                        str(self.morphe_location),
                        "options-create",
                        "-p",
                        cached_repo,
                        "-o",
                        str(repo_options),
                        "-f",
                        package_name,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                
                if result.returncode in (130, 143, -2, -15, -9) or result.returncode < 0:
                    logger.critical("\n └── [!] Options creation cancelled by user.")
                    raise KeyboardInterrupt("Cancelled by user")

                if result.returncode == 0:
                    logger.info(f" └── [✓] Created options file: '{repo_options.name}' for '{package_name}'.")
                    repo_options_locations.append(repo_options)
                else:
                    logger.error(f" └── [✗] Failed to create options for '{package_name}' from '{repo}'.")
                    if result.stderr and result.stderr.strip():
                        logger.error(result.stderr.strip())

        return repo_options_locations

    def filter_repos(self) -> tuple[dict[str, dict[str, dict[str, str]]], set[str]]:
        enabled_patches_per_app = {}
        all_enabled_repos = set()

        for app, details in self.detailed_patch_list.items():
            app_options_dir = self.options_dir / app
            for repo in details.get('repos', []):
                owner = self.repo_details.get(repo, {}).get('owner')
                options_file_name = app_options_dir / f"{owner}_options.json"

                if not options_file_name.is_file():
                    continue

                with open(options_file_name, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)

                    if isinstance(cached_data, list) and cached_data:
                        patches_dict = cached_data[-1].get('patches', {})
                    elif isinstance(cached_data, dict):
                        patches_dict = cached_data.get('patches', {})
                    else:
                        patches_dict = {}

                    for patch, values in patches_dict.items():
                        alias = patch
                        alias_map = self.detailed_patch_list[app].get('patch_aliases', {})
                        if patch in alias_map:
                            alias = alias_map[patch]

                        if isinstance(values, dict) and values.get('enabled'):
                            enabled_patches_per_app.setdefault(app, {}).setdefault(alias, {})[repo] = patch
                            all_enabled_repos.add(repo)

        return enabled_patches_per_app, all_enabled_repos

    def generate_checksum(self, repo_options_locations: list[Path]) -> dict[str, str]:
        checksums = {}

        for options_path in repo_options_locations:
            try:
                rel_path = options_path.relative_to(self.options_dir).as_posix()
            except ValueError:
                rel_path = str(options_path)

            try:
                with open(options_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # get just the last block to avoid only time changes
                if isinstance(data, list) and data:
                    patch_data = data[-1].get("patches", {})
                elif isinstance(data, dict):
                    patch_data = data.get("patches", {})
                else:
                    patch_data = {}

                normalised = json.dumps(patch_data, sort_keys=True, separators=(",", ":"))
                checksums[rel_path] = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
            except Exception as e:
                logger.error(f" └── [!] Failed computing checksum for {options_path}: {e}")

        return checksums
        
    def write_options_checksum_json(self, checksums: dict[str, str]) -> None:
        logger.info("\n[*] Updating option checksum cache...")

        existing_checksums = {}

        if self.options_checksum_json.is_file():
            try:
                with open(
                    self.options_checksum_json, "r", encoding="utf-8"
                ) as f:
                    existing_checksums = json.load(f)
            except Exception as e:
                logger.error(f" └── [!] Failed reading checksum cache: {e}")

        existing_checksums.update(checksums)

        self.options_checksum_json.parent.mkdir(parents=True, exist_ok=True)
        with open(self.options_checksum_json, "w", encoding="utf-8") as f:
            json.dump(existing_checksums, f, indent=2, sort_keys=True)

    def merge_target_repos(self, package_name: str, repos: set[str], version_name: str) -> Path:
        if len(repos) == 1:
            logger.debug(f" └── Only one repo ('{next(iter(repos))}') for '{version_name}', package: '{package_name}'")
            options_to_use = self.options_dir / package_name / f"{self.repo_details[next(iter(repos))]['owner']}_options.json"
            return options_to_use

        app_options_dir = self.options_dir / package_name
        output = app_options_dir / f"{package_name}_merge_options_{version_name}.json"
        merged_bundles = []
        seen_shas = set()
        logger.info(f"\n[*] Creating merged options.json for {package_name}...")

        for repo in repos:
            logger.info(f" ├── Merging {repo}...")
            owner = self.repo_details[repo]['owner']
            options_file = app_options_dir / f"{owner}_options.json"

            with open(options_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for block in data:
                sha = block.get("meta", {}).get("sha256")
                if sha in seen_shas:
                    logger.info(f"    [!] Skipping duplicate bundle (sha256={sha}) from {owner}")
                    continue
                seen_shas.add(sha)
                merged_bundles.append(block)  # keep meta + patches exactly as generated

        with open(output, "w", encoding="utf-8") as f:
            json.dump(merged_bundles, f, indent=2)

        total_patches = sum(len(b.get("patches", {})) for b in merged_bundles)
        logger.info(f" └── [✓] Merged {len(merged_bundles)} bundles "
            f"({total_patches} patches total) into: {output}")

        return output

    def check_aliase_clash(self, package_name: str, target_version: str, enabled_patches_per_app_versions: dict, options_to_use: Path) -> None:
        with open(options_to_use, "r", encoding="utf-8") as f:
            data = json.load(f)

        entries = [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])

        for alias, details in enabled_patches_per_app_versions.items():
            if len(details) > 1:
                logger.info(f" └── [!] Alias found {alias}")
                for repo, patch_info in details.items():
                    shas = self.get_sha(repo, package_name)
                    patch_name = patch_info[0]
                    patch_version_name = patch_info[2]

                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        sha = entry.get('meta', {}).get('sha256')

                        if not sha or sha not in shas:
                            continue

                        patches = entry.get('patches', {})
                        if isinstance(patches, dict) and patch_name in patches:
                            is_enabled = patches[patch_name].get('enabled', False)

                            if target_version != patch_version_name and is_enabled:
                                logger.info(f"       └── [!] Disabling {patch_name} in {repo} as version does not match target.")
                                patches[patch_name]['enabled'] = False

                            elif target_version == patch_version_name and not is_enabled:
                                logger.info(f"       └── [!] Enabling {patch_name} in {repo} as version matches target.")
                                patches[patch_name]['enabled'] = True

        with open(options_to_use, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get_sha(self, repo: str, package_name: str) -> set[str]:
        shas = set()
        owner = self.repo_details[repo]['owner']
        options_file = self.options_dir / package_name / f"{owner}_options.json"

        with open(options_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        for entry in data:
            if isinstance(entry, dict):
                sha = entry.get('meta', {}).get('sha256')
                if sha:
                    shas.add(sha)

        return shas

#################################
#      GET PATCH VERSOINS       #
#################################

    def get_patch_versions_main(self, enabled_patches_per_app: dict[str, dict[str, dict[str, str]]], enabled_repos: set[str]) -> tuple[dict[str, list[str]], dict[str, dict[str, dict[str, tuple[str, tuple[int, ...], str]]]]]:
        enabled_patches_per_app_versions = self.get_patch_versions(enabled_repos, enabled_patches_per_app)
        package_req = self.find_package_req(enabled_patches_per_app_versions)
        return package_req, enabled_patches_per_app_versions

    def get_patch_versions(self, enabled_repos: set[str], enabled_patches_per_app: dict[str, dict[str, dict[str, str]]]) -> dict[str, dict[str, dict[str, tuple[str, tuple[int, ...], str]]]]: 
        logger.info(f"\n[*] Searching for patches in {len(enabled_repos)} repos:")
        enabled_patches_per_app_versions = {}

        for repo in enabled_repos:
            logger.info(f" ├── {repo}")

            cached_repo = self.get_cached_repo(repo)

            if not cached_repo:
                continue

            result = subprocess.run(
                [
                    "java",
                    "-jar",
                    str(self.morphe_location),
                    "list-patches",
                    "--patches",
                    cached_repo,
                    "--with-packages",
                    "--with-versions",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode in (130, 143, -2, -15, -9) or result.returncode < 0:
                logger.critical("\n │     └── [!] Patch listing cancelled by user.")
                raise KeyboardInterrupt("Cancelled by user")

            if result.returncode == 0:
                logger.info(f" │     └── [✓] Success!")
                for block in result.stdout.split('Index: ')[1:]:
                    patch_search = re.search(r"Name:\s*(.+)", block)
                    if not patch_search:
                        continue

                    patch_name = patch_search.group(1).strip()
                    package_sections = re.split(r"Package name:\s*", block)[1:]

                    # 1. Parse versions (take the first listed compatible version or default to (0, 0, 0))
                    # 2. Package-specific patches
                    if package_sections:
                        for section in package_sections:
                            pkg = section.split()[0]
                            version_search = re.search(r"Compatible versions:\s*(.+)", section)
                            first_ver_line = version_search.group(1).strip().splitlines()[0] if version_search else ""
                            version_digits = re.findall(r"\d+", first_ver_line)
                            tup_ver = tuple(int(d) for d in version_digits) if version_digits else (0, 0, 0)
                            ver_str = first_ver_line.strip() if version_digits else "0.0.0"
                            if pkg in enabled_patches_per_app:
                                for alias, details in enabled_patches_per_app[pkg].items():
                                    if repo in details and details[repo] == patch_name:
                                        enabled_patches_per_app_versions.setdefault(pkg, {}).setdefault(alias, {})[repo] = (patch_name, tup_ver, ver_str)
                    # 3. Genuine universal patches (check if any configured app wants this patch)
                    else:
                        for app_pkg, aliases in enabled_patches_per_app.items():
                            for alias, details in aliases.items():
                                if repo in details and details[repo] == patch_name:
                                    enabled_patches_per_app_versions.setdefault(app_pkg, {}).setdefault(alias, {})[repo] = (patch_name, (0, 0, 0), '0.0.0')

            else:
                err_msg = result.stderr.strip().splitlines()[-1] if result.stderr else "Unknown error"
                logger.error(f" │     └── [✗] Repo search failed (exit code {result.returncode}): {err_msg}")

        return enabled_patches_per_app_versions
     
    def find_package_req(self, enabled_patches_per_app_versions: dict[str, dict[str, dict[str, tuple[str, tuple[int, ...], str]]]]) -> dict[str, list[str]]:
        package_req_tup = {}

        for package_name, aliases  in enabled_patches_per_app_versions.items():
            version = set()
            for aliase_details in aliases.values():
                for repo_detials in aliase_details.values():
                    tup_ver = repo_detials[1]
                    str_ver = repo_detials[2]
                    version.add((tup_ver, str_ver))

            sorted_versions = sorted(version, key=lambda item: item[0], reverse=True)

            package_req_tup[package_name] = [ver_str for _, ver_str in sorted_versions]

        return package_req_tup

#################################
#            PATCH              #
#################################

    def check_options_to_use(self, package_name: str, target_version: str, latest: bool, enabled_patches_per_app_versions: dict, run: int) -> tuple[set[str], Path]:
        if run == 1:
            repos = self.check_repo_reqs(enabled_patches_per_app_versions[package_name], target_version)
            options_to_use = self.merge_target_repos(package_name, repos, target_version)
            self.check_aliase_clash(package_name, target_version, enabled_patches_per_app_versions[package_name], options_to_use)
            logger.debug("run == 1")
        if run == 2:
            target_tuple = tuple(int(x) for x in target_version.split("."))
            repo_per_version = self.check_repo_per_version(enabled_patches_per_app_versions[package_name])
            logger.debug(f"Check options to use, repo per version: {repo_per_version}")
            if latest:
                repos = repo_per_version[(0, 0, 0)] 
            else:
                repos = repo_per_version[target_tuple]
            options_to_use = self.merge_target_repos(package_name, repos, target_version)
            self.check_aliase_clash(package_name, target_version, enabled_patches_per_app_versions[package_name], options_to_use)
            logger.debug("run == 2")

        if len(repos) == 0:
            logger.error(f" └── [✗] No repos found for target version: {target_version}")
            raise ValueError(f"No repos found for target version: {target_version}")

        logger.debug(f"Check options to use: repo_versions/repos:\n{target_version} {repos}")

        return repos, options_to_use

    def check_repo_reqs(self, aliase_details: dict[str, dict[str, tuple[str, tuple[int, ...], str]]], target_version: str) -> set[str]:
        repos = set()
        for alias, repo_details in aliase_details.items():
            for repo, (patch_name, version_tuple, version_string) in repo_details.items():
                if version_string == target_version:
                    repos.add(repo)
                    break
                else:
                    best_candidate = max(repo_details.items(), key=lambda item: item[1][1])
                    chosen_repo = best_candidate[0]
                    if chosen_repo:
                        repos.add(chosen_repo)
                    else:
                        logger.error(f" └── [!] No repo found for alias: {alias}")
        return repos

    def check_repo_per_version(self, aliase_details: dict[str, dict[str, tuple[str, tuple[int, ...], str]]]) -> dict[tuple[int, ...], set[str]]:
        output = {}

        for repo_details in aliase_details.values():
            for repo, val in repo_details.items():
                output.setdefault(val[1], set()).add(repo)

        return output 

    def patch(self, package_name: str, version_name: str, repos: set[str], options_to_use: Path, strat: int, attempt: int, striplibs: bool, package: dict) -> tuple[bool, Path | str | None, str | None]:
        dest = None

        cmd = ["java"]

        if self.low_spec_mode:
            tmp_dir = self.project_root / "tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            cmd += ["-Xms128m", "-Xmx1536m", f"-Djava.io.tmpdir={tmp_dir}"]

        cmd += ["-jar", str(self.morphe_location), "patch"]

        if not repos:
            logger.error(" └── [!] Repo list is empty.")
            return False, dest, "Repo list is empty."

        if len(repos) == 1:
            dest = "one_repo" 

        for repo in repos:
            owner = self.repo_details[repo]['owner']
            repo_name = self.repo_details[repo]['repo_name']
            mpp_path = self.get_cached_repo_path(owner, repo_name, repo)

            if not mpp_path:
                raise FileNotFoundError(f"No cached .mpp bundle found for {owner}/{repo_name}")

            cmd += ["--patches", str(mpp_path)]

        if striplibs:
            cmd += ["--striplibs", self.arch]

        output_dest = self.patched_dir / package_name / version_name / f"{package_name}_{version_name}_patched.apk"
        output_dest.parent.mkdir(parents=True, exist_ok=True)
        result_dest = output_dest.parent / "result.json"
        version_dir = self.original_dir / package_name / version_name
        candidate_names = (
            f"{package_name}.xapk",
            f"{package_name}.apk",
            f"{package_name}-{version_name}.xapk",
            f"{package_name}-{version_name}.apk",
        )
        input_package = next((version_dir / name for name in candidate_names if (version_dir / name).is_file()), None)

        if input_package is None:
            logger.error(f"No apk or xapk found for {package_name}, version: {version_name}")
            return False, dest, f"No APK or XAPK found for {package_name} v{version_name}"

        cmd += ["--result-file", str(result_dest), "--options-file", str(options_to_use)]

        if strat == 1:
            cmd += ["--force"]

        cmd += ["--out", str(output_dest), str(input_package)]

        try:
            logger.info(f"[*] Attempting to patch package: {package_name}, version: {version_name}...")
            logger.debug(f"Patch command:\n{cmd}\n")
            subprocess.run(cmd, capture_output=True, text=True, check=True)

            if output_dest.is_file():
                logger.info(f" └── [✓] Successfully patched {package_name} v{version_name}")
                self.write_patched_json(package_name, version_name, repos, strat, attempt, options_to_use, output_dest, result_dest, package)
                return True, output_dest, None
            else:
                logger.error(" └── [!] Java exited with code 0, but output APK was not found.")
                return False, dest, "Java exited with code 0, but output APK was not found."

        except subprocess.CalledProcessError as e:
            if e.returncode in (130, 143, -2, -15, -9) or e.returncode < 0:
                logger.critical("\n └── [!] Patch process cancelled by user.")
                raise KeyboardInterrupt("Patching interrupted by user")
            error_msg = next((line.strip() for line in e.stderr.splitlines() if line.strip()), "Unknown CLI error") if e.stderr else "Unknown CLI error"
            logger.error(f" └── [!] Morphe CLI failed (exit code {e.returncode}): {error_msg}")
            return False, dest, f"Morphe CLI failed (exit code {e.returncode}): {error_msg}"

        except FileNotFoundError:
            logger.error(" └── [!] Java executable not found. Ensure Java is installed and on your PATH.")
            return False, dest, "Java executable not found."

        except KeyboardInterrupt:
            logger.critical("\n └── [!] Patch process cancelled by user.")
            raise

#################################
#      Write Patched Json       #
#################################

    def write_patched_json(self, package_name: str, version_name: str, repos: set[str], strat: int, attempt: int, options_to_use: Path, output: Path, result_dset: Path, package: dict) -> None:
        attempt += 1
        patched_json = self.state_dir / "patched.json"
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        time_ms = int(now.timestamp() * 1000)
        patched_data = {}
        applied_patches = {}
        patch_aliases = {}
        target_shas = {}

        bundles = self.read_json(options_to_use)
        results = self.read_json(result_dset)

        if results:
            applied_patches_res = results.get('appliedPatches')
            
            if applied_patches_res:  
                if bundles:
                    for r in repos:
                        cached_mpp_path = self.repo_details[r]['cached_repo']['cached_mpp_path']
                        sha = self.repo_details[r].get("sha")
                        if not sha:
                            sha = self.make_sha(cached_mpp_path)
                            self.repo_details[r]["sha"] = sha
                        target_shas[sha] = r

                for patch_name in applied_patches_res:
                    repo = None
                    alias = "Unable to detemine alias"
                    patch_options = "Unable to determine patch options"

                    if bundles:
                        repo, patch_options = self.find_repo(patch_name, bundles, target_shas)

                    aliases = self.enabled_patches_per_app.get(package_name)
                    if repo and aliases:
                        for al, rs in aliases.items():
                            if rs.get(repo) == patch_name:
                                alias = al

                    applied_patches[patch_name] = {
                        'repo': repo,
                        'options': patch_options,
                        'alias': alias
                    }

        else:
            logger.warning(f"Unable to open results file for {package_name} v{version_name}")
        
        if patched_json.is_file():
            patched_data = self.read_json(patched_json) or {}

        for p, alias in self.detailed_patch_list[package_name].get("patch_aliases", {}).items():
            if alias:
                patch_aliases.setdefault(alias, []).append(p)

        package_sha = self.make_sha(output)
        package_size = self.get_size(output)
        vc = package[package_name][version_name]['version_code']
        version_code = int(vc) if vc is not None else 0
        min_sdk = None
        target_sdk = None
        clean_name = package_name
        patched_package_name = package_name
        patched_version_code = version_code
        patched_version_name = version_name
        
        try:
            with ZipFile(output, 'r') as z:
                root = AXMLPrinter(z.read("AndroidManifest.xml")).get_xml_obj()
                patched_package_name = root.attrib.get("package", package_name)
                patched_version_code = root.attrib.get("{http://schemas.android.com/apk/res/android}versionCode", version_code)
                patched_version_name = root.attrib.get("{http://schemas.android.com/apk/res/android}versionName", version_name)

                uses_sdk = root.find("uses-sdk")
                if uses_sdk is not None:
                    raw_min = str(uses_sdk.attrib.get("{http://schemas.android.com/apk/res/android}minSdkVersion"))
                    raw_target = str(uses_sdk.attrib.get("{http://schemas.android.com/apk/res/android}targetSdkVersion"))
                    min_sdk = int(raw_min) if raw_min and raw_min.isdigit() else None
                    target_sdk = int(raw_target) if raw_target and raw_target.isdigit() else None
                    
                app_elem = root.find("application")
                if app_elem is not None:
                    label = app_elem.attrib.get("{http://schemas.android.com/apk/res/android}label")
                    if label and not label.startswith("@"):
                        clean_name = label
            
        except Exception as e:
            logger.critical(f"\n [!] Failed to read package name from {output.name} - may not function with Obtanium: {e}")
            patched_package_name = package_name
            patched_version_code = version_code

        relative_path = output.as_posix().split('/patched/', 1)[-1]
        url = f"{self.base_url}/files/{relative_path}"

        morphe = re.search(r"(\d+\.\d+\.\d+)", self.morphe_location.resolve().name) 
        morphe_version = morphe.group(1) if morphe else "Unknown"

        patched_data.setdefault(package_name, {})[version_name] = {
            'metadata': {
                'time': timestamp,
                'time_ms': time_ms
            },
            'package': {
                'clean_name': clean_name,
                'source_package_name': package_name,
                'patched_package_name': patched_package_name,
                'patched_version_name': str(patched_version_name),
                'patched_version_code': int(patched_version_code),
                'source_version_code': int(version_code),
                'arch': self.arch,
                'min_sdk': min_sdk,
                'target_sdk': target_sdk
            },
            'file': {
                'file_name': output.name,
                'location': str(output),
                'url': url,
                **package_size,
                'sha256': package_sha
            },
            'patch': {
                'applied_patches': applied_patches,
                'patch_aliases': patch_aliases,
                'repos': list(repos),
                'strat': strat,
                'attempt': attempt,
                'morphe_desktop_version': morphe_version
            }
        }
            
        with open(patched_json, "w", encoding="utf-8") as f:
            json.dump(patched_data, f, indent=2)

    def find_repo(self, patch: str, bundles: list[dict], target_shas: dict[str, str]) -> str:
        for bundle in bundles:
            patch_data = bundle.get("patches", {}).get(patch)
            if patch_data and patch_data.get("enabled"):
                found_sha = bundle.get("meta", {}).get("sha256")
                options = patch_data.get("options")
                if found_sha in target_shas:
                    return target_shas[found_sha], options
            
        return "Unable to find repo", None

    def make_sha(self, item: Path) -> str:
        sha = hashlib.sha256()

        with open(item, "rb") as f:
            while chunk := f.read(65536):
                sha.update(chunk)

        return sha.hexdigest()

    def get_size(self, package: Path) -> dict[str, int | str]:
        size_in_bytes = package.stat().st_size
        size = {'size_bytes': size_in_bytes}

        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_in_bytes < 1024:
                size['file_size'] = f"{size_in_bytes:.2f} {unit}"
                return size
            size_in_bytes /= 1024

    def read_json(self, file: Path) -> dict | None:
        try:
            with open(file, "r", encoding="utf-8") as r:
                results = json.load(r)
                return results
        except FileNotFoundError:
            logger.warning(f"File not found: {file}")
            return
        except json.JSONDecodeError:
            logger.warning(f"Unable to parse invalid JSON in {file}")
            return