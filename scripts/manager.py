from pathlib import Path
from urllib.parse import urlparse
import time
import logging
import json
import itertools
import shutil

logger = logging.getLogger(__name__)

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

            sorted_versions = sorted(details.keys(), key=lambda v: details[v]['package'].get('patched_version_code', details[v]['package'].get('source_version_code', 0)), reverse=True)
            latest_version = sorted_versions[0]
            latest_details = details[latest_version]
            patched_package_name = latest_details['package'].get('patched_package_name', package_name)
            additional_settings = {'appIdOrName': patched_package_name, 'versionExtractionRegEx': r'\d+(\.\d+)+', 'pickHighestVersionCode': True}
            stored_url = latest_details['file']['url']
            apk_path = urlparse(stored_url).path if stored_url else ""
            apk_url = f"{self.base_url.rstrip('/')}{apk_path}" if apk_path else stored_url
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
            
            for version, v_details in details.items():
                patched_package_name = v_details['package'].get('patched_package_name', package_name)
                time = v_details['metadata'].get('time_ms')
          
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
                                'en-US': 'Patched with AutoMorph'
                            },
                            'authorName': 'AutoMorph',
                            'changelog': 'Patches automatically applied'  
                        },
                        'versions': {}
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
                        'nativeCode': [
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

def clean(max_original_packages: int, max_patched_packages:int, state_dir: Path, original_dir: Path, patched_dir: Path, patched_json: Path) -> None:
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