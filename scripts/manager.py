from pathlib import Path
from urllib.parse import urlparse
import time
import logging
import json
import itertools
import shutil
import hashlib
from zipfile import ZipFile
from pyaxmlparser.axmlprinter import AXMLPrinter
import re
import apkpure_fetcher
import html

try:
    from migrate_patched_json import migrate_patched_json
except ImportError:
    try:
        from scripts.migrate_patched_json import migrate_patched_json
    except ImportError:
        migrate_patched_json = None

logger = logging.getLogger(__name__)

class Patched:
    def __init__(self, repo_details, detailed_patch_list, base_url: str, morphe_location: Path, patched_data: dict, arch: str, enabled_patches_per_app: dict) -> None:
        self.repo_details = repo_details
        self.detailed_patch_list = detailed_patch_list
        self.base_url = base_url
        self.morphe_location = morphe_location
        self.patched_data = patched_data
        self.arch = arch
        self.enabled_patches_per_app = enabled_patches_per_app

    def make_patched_data(self, package_name: str, details: dict, morphe_catalogue: dict):
        applied_patches = {}
        patch_aliases = {}
        target_shas = {}

        bundles = read_json(details['options'])
        results = read_json(details['results_dest'])

        if results:
            applied_patches_res = results.get('appliedPatches')
            
            if applied_patches_res:  
                if bundles:
                    for r in details['repos']:
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
            logger.warning(f"Unable to open results file for {package_name} v{details['version_name']}")    

        for p, alias in self.detailed_patch_list[package_name].get("patch_aliases", {}).items():
            if alias:
                patch_aliases.setdefault(alias, []).append(p)    

        package_sha = self.make_sha(details['output_dest'])
        package_size = self.get_size(details['output_dest'])
        vc = details['package'][package_name][details['version_name']]['version_code']
        version_code = int(vc) if vc is not None else 0
        min_sdk = None
        target_sdk = None
        patched_package_name = package_name
        patched_version_code = version_code
        patched_version_name = details['version_name']

        catalogue_entry = (morphe_catalogue.get('store', {}) or {}).get(package_name) if morphe_catalogue else None
        if not isinstance(catalogue_entry, dict):
            catalogue_entry = {}

        clean_name = catalogue_entry.get('name') or package_name
        icon_url = catalogue_entry.get('iconUrl') or ""
        summary = html.unescape(catalogue_entry.get('description', '').strip()) or "Patched with AutoMorph"


        try:
            with ZipFile(details['output_dest'], 'r') as z:
                root = AXMLPrinter(z.read("AndroidManifest.xml")).get_xml_obj()
                patched_package_name = root.attrib.get("package", package_name)
                patched_version_code = root.attrib.get("{http://schemas.android.com/apk/res/android}versionCode", version_code)
                patched_version_name = root.attrib.get("{http://schemas.android.com/apk/res/android}versionName", details['version_name'])

                uses_sdk = root.find("uses-sdk")
                if uses_sdk is not None:
                    raw_min = str(uses_sdk.attrib.get("{http://schemas.android.com/apk/res/android}minSdkVersion"))
                    raw_target = str(uses_sdk.attrib.get("{http://schemas.android.com/apk/res/android}targetSdkVersion"))
                    min_sdk = int(raw_min) if raw_min and raw_min.isdigit() else None
                    target_sdk = int(raw_target) if raw_target and raw_target.isdigit() else None
            
        except Exception as e:
            logger.critical(f"\n [!] Failed to read package name from {details['output_dest'].name} - may not function with Obtanium: {e}")
            patched_package_name = package_name
            patched_version_code = version_code

        relative_path = details['output_dest'].as_posix().split('/patched/', 1)[-1]
        url = f"{self.base_url}/files/{relative_path}"

        morphe = re.search(r"(\d+\.\d+\.\d+)", self.morphe_location.resolve().name) 
        morphe_version = morphe.group(1) if morphe else "Unknown"

        self.patched_data.setdefault(package_name, {})[patched_version_name] = {
            'metadata': {
                **details['time'],
                'icon_url': icon_url,
                'summary': summary
            },
            'package': {
                'clean_name': clean_name,
                'source_package_name': package_name,
                'patched_package_name': patched_package_name,
                'patched_version_name': str(patched_version_name),
                'source_version_name': str(details['version_name']),
                'patched_version_code': int(patched_version_code),
                'source_version_code': int(version_code),
                'arch': self.arch,
                'min_sdk': min_sdk,
                'target_sdk': target_sdk,
            },
            'file': {
                'file_name': details['output_dest'].name,
                'location': str(details['output_dest']),
                'url': url,
                **package_size,
                'sha256': package_sha
            },
            'patch': {
                'applied_patches': applied_patches,
                'patch_aliases': patch_aliases,
                'repos': list(details['repos']),
                'strat': details['strat'],
                'attempt': details['attempt'],
                'morphe_desktop_version': morphe_version
            }
        }
        
    def find_repo(self, patch: str, bundles: list[dict], target_shas: dict[str, str]) -> str:
        for bundle in bundles:
            patch_data = bundle.get("patches", {}).get(patch)
            if patch_data and patch_data.get("enabled"):
                found_sha = bundle.get("meta", {}).get("sha256")
                options = patch_data.get("options")
                if found_sha in target_shas:
                    return target_shas[found_sha], options
            
        return "Unable to find repo", None
    
    def make_sha(self, item: Path | dict) -> str:
        sha = hashlib.sha256()

        if isinstance(item, Path):
            with open(item, "rb") as f:
                while chunk := f.read(65536):
                    sha.update(chunk)
        elif isinstance(item, dict):
            sha.update(json.dumps(item, sort_keys=True, default=str).encode("utf-8"))
        else:
            raise TypeError(f"make_sha expects Path or dict, got {type(item).__name__}")

        return sha.hexdigest()

    def get_size(self, package: Path) -> dict[str, int | str]:
        size_in_bytes = package.stat().st_size
        size = {'size_bytes': size_in_bytes}

        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_in_bytes < 1024:
                size['file_size'] = f"{size_in_bytes:.2f} {unit}"
                return size
            size_in_bytes /= 1024
    
class Cleaner:
    def __init__(self, patched_results: dict, patched_dir: Path, original_dir: Path, patched_json: Path, max_original_packages: int, max_patched_packages: int) -> None:
        self.patched_results = patched_results
        self.patched_dir = patched_dir
        self.original_dir = original_dir
        self.patched_json = patched_json
        self.max_original_packages = max_original_packages
        self.max_patched_packages = max_patched_packages

    def clear_old_packages(self) -> None:
        for package_name in list(self.patched_results.keys()):
            logger.info(f"\n[*] {self.max_original_packages} unpatched and {self.max_patched_packages} package(s) allowed on disk. Checking for {package_name}...")
            details = {'patched': {'directory': self.patched_dir / package_name, 'max_packages': self.max_patched_packages}, 'original': {'directory': self.original_dir / package_name, 'max_packages': self.max_original_packages}}

            for key, values in details.items():
                if not values['max_packages']:
                    continue

                directory = values['directory']
                if not directory.is_dir():
                    continue

                version_folders = [d for d in directory.iterdir() if d.is_dir()]

                used_folders = [
                folder for folder in version_folders 
                if any(itertools.chain(folder.glob("*.xapk"), folder.rglob("*.apk")))
                ]

                if key == 'patched':
                    self.check_patched_json(package_name, used_folders)

                self.check_packages(package_name, values['max_packages'], key, version_folders, used_folders)

    def check_patched_json(self, package_name: str, used_folders: list) -> None:
        versions = self.patched_results.get(package_name)
        if versions:
            used_versions = [x.name for x in used_folders]
            remove_from_json = [version for version in versions if version not in used_versions]
            
            if remove_from_json:
                self.rm_from_patched_json(remove_from_json, package_name)    

    def check_packages(self, package_name: str, max_packages: int, id: str, version_folders: list, used_folders: list) -> None:
        if len(version_folders) <= max_packages:
            logger.info(f" └── [✓] {len(version_folders)} {id} package(s) found. No deletions required.")
            return

        if len(used_folders) <= max_packages:
            logger.info(f" └── [✓] {len(used_folders)} {id} package(s) found. No deletions required.")
            return

        used_folders.sort(key=lambda folder: tuple(int(d) for d in folder.name.lstrip('v').split('.') if d.isdigit()))
        remove_from_json = []

        while len(used_folders) > max_packages:
            used = len(used_folders)
            oldest_package = used_folders.pop(0)
            branch = " └── " if len(used_folders) == max_packages else " ├── "
            logger.info(f"{branch}[!] {used} {id} packages found, removing the oldest: v{oldest_package.name.lstrip('v')}")
            shutil.rmtree(oldest_package)
            if id == 'patched':
                remove_from_json.append(oldest_package.name)

        if id == 'patched' and remove_from_json:
            self.rm_from_patched_json(remove_from_json, package_name)

    def rm_from_patched_json(self, version_names: list, package_name: str) -> None:
        package_data = self.patched_results.get(package_name)

        if not isinstance(package_data, dict):
            logger.warning(f"Package '{package_name}' not found or invalid format.")
            return

        to_remove = False

        for version_name in version_names:
            if package_data.pop(version_name, None) is not None:
                to_remove = True

        if to_remove:
            if not package_data:
                self.patched_results.pop(package_name, None)

            write_json(self.patched_results, self.patched_json)
        
class Obtanium:
    def __init__(self, base_url: str, patched_results: dict):
        self.patched_results = patched_results
        self.base_url = base_url

    def add_package(self):
        ob_export = {'schemaVersion': 2, 'apps': []}

        for package_name, details in self.patched_results.items():
            if not details:
                continue

            sorted_versions = sorted(details.keys(), key=lambda v: details[v]['package'].get('source_version_code', details[v]['package'].get('patched_version_code', 0)), reverse=True)
            latest_version = sorted_versions[0]
            latest_details = details[latest_version]
            patched_package_name = latest_details['package'].get('patched_package_name', package_name)
            additional_settings = {'appIdOrName': patched_package_name, 'versionExtractionRegEx': r'\d+(\.\d+)+'}
            relative_url = urlparse(latest_details['file']['url']).path.lstrip("/")
            apk_url = f"{self.base_url}/{relative_url}"
            apk_list = [[latest_details['file']['file_name'], apk_url]]

            app = {
                'id': patched_package_name,
                'name': latest_details['package'].get('clean_name', package_name),
                'author': 'AutoMorph',
                'url': f"{self.base_url}?appId={patched_package_name}",
                'latestVersion': latest_version,
                'preferredApkIndex': 0,
                'overrideSource': 'FDroidRepo',
                'additionalSettings': json.dumps(additional_settings),
                'apkUrls': json.dumps(apk_list)
            }

            ob_export['apps'].append(app)

        return ob_export

class FDroid:
    def __init__(self, base_url: str, patched_results: dict, indexv2: dict):
        self.time = int(time.time() * 1000)
        self.patched_results = patched_results
        self.base_url = base_url
        self.existing_index = indexv2 or {}

    def add_package(self):
        index = {
            'repo': {
                'name': {
                    'en-US': "AutoMorph"
                },
                'description': {
                    'en-US': "Custom Patched Android Applications Using AutoMorph and Morphe Desktop"
                    },
                'address': self.base_url,
                'timestamp': self.time
            },
            'packages': {},
        }
        
        for package_name, details in self.patched_results.items():
            if not details:
                continue

            sorted_details = sorted(details.items(), key=lambda item: item[1]['metadata'].get('time_ms', 0), reverse=True)
            
            for version, v_details in sorted_details:
                patched_package_name = v_details['package'].get('patched_package_name', package_name)
                time = v_details['metadata'].get('time_ms')
                icon_url = v_details['metadata'].get('icon_url')
          
                if patched_package_name not in index['packages']:
                    added = self.existing_index.get('packages', {}).get(patched_package_name, {}).get('metadata', {}).get('added')
                    
                    index['packages'][patched_package_name] = {
                        'metadata': {
                            'added': added or time,
                            'lastUpdated': time,
                            'name': {
                                'en-US': v_details['package'].get('clean_name', package_name)
                            },
                            'summary': {
                                'en-US': v_details['metadata'].get('summary') or "Patched with AutoMorph"
                            },
                            'authorName': 'AutoMorph',
                            'changelog': 'Patches automatically applied',
                        },
                        'versions': {}
                    }
                    
                    if icon_url:
                        index['packages'][patched_package_name]['metadata']['icon'] = {
                            'en-US': {
                                'name': icon_url
                            }
                        }

                index['packages'][patched_package_name]['versions'][f"v{version.replace('.', '_')}"] = {
                    'added': time,
                    'file': {
                        'name': urlparse(v_details['file']['url']).path.lstrip("/"),
                        'sha256': v_details['file'].get('sha256'),
                        'size': v_details['file'].get('size_bytes')
                    },
                    'manifest': {
                        'versionName': version,
                        'versionCode': v_details['package'].get('patched_version_code', v_details['package'].get('source_version_code', 0)),
                        'nativecode': [
                            v_details['package'].get('arch')
                        ],
                        "usesSdk": {
                            "minSdkVersion": v_details['package'].get('min_sdk'),
                            "targetSdkVersion": v_details['package'].get('target_sdk')
                        },
                    },
                    'releaseChannels': [
                        'stable'
                    ],
                    'whatsNew': {
                        'en-US': 'new patches'
                    }
                }        

        return index

def main(patched: dict, repo_details: dict, detailed_patch_list, base_url: str, morphe_location: Path, state_dir: Path, arch: str, enabled_patches_per_app: dict):
    patched_data = {}
    patched_json = state_dir / "patched.json"
    morphe_catalogue = state_dir / "morphe_catalog.json"

    if patched_json.is_file():
        if migrate_patched_json:
            migrate_patched_json(patched_json)
        patched_data = read_json(patched_json) or {}
    
    p = Patched(repo_details, detailed_patch_list, base_url, morphe_location, patched_data, arch, enabled_patches_per_app)
    
    if not morphe_catalogue.is_file():
        apkpure_fetcher.get_morphe_catalog(force_refresh=True)
    
    catalogue = read_json(morphe_catalogue)

    for package_name, details in patched.items():
        p.make_patched_data(package_name, details, catalogue)

    write_json(p.patched_data, patched_json)

def clean(max_original_packages: int, max_patched_packages:int, state_dir: Path, original_dir: Path, patched_dir: Path, patched_json: Path) -> None:
    if migrate_patched_json and patched_json.is_file():
        migrate_patched_json(patched_json)
    patched_results = read_json(patched_json)
    cleaner = Cleaner(patched_results, patched_dir, original_dir, patched_json, max_original_packages, max_patched_packages)

    if patched_results and (max_original_packages or max_patched_packages):
        cleaner.clear_old_packages()

def clear_logs(log_dir: Path, max_logs: int):
    current_logs = list(log_dir.glob("automorph_*.log"))
    current_logs.sort(key=lambda l: l.name)

    while len(current_logs) > max_logs:
        branch = " └── " if len(current_logs) == max_logs else " ├── "
        logger.info(f"{branch}[!] {len(current_logs)} logs found, removing the oldest")
        oldest_log = current_logs.pop(0)
        oldest_log.unlink(missing_ok=True)

def build_all_jsons(state_dir: Path, patched_json: Path, base_url: str) -> None:
    to_write = {}
    indexv2 = state_dir / "index-v2.json"

    if migrate_patched_json and patched_json.is_file():
        migrate_patched_json(patched_json)
    patched_results = read_json(patched_json)
    existing_index = read_json(indexv2)

    if patched_results: 
        fdroid = FDroid(base_url, patched_results, existing_index)
        indexv2_data = fdroid.add_package()
        to_write['index-v2_data'] = {'data': indexv2_data, 'file': indexv2}

        obtanium = Obtanium(base_url, patched_results)
        ob_export = obtanium.add_package()
        to_write['ob_export'] = {'data': ob_export, 'file': state_dir / "obtainium-export.json"}

        for i in to_write.values():
            write_json(i['data'], i['file'])

def read_json(file: Path) -> dict | None:
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

def write_json(data: dict, file: Path) -> None:
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)