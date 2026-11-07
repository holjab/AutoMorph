import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from ruamel.yaml import YAML
import schedule

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

try:
    import proxy_utils
    proxy_utils.setup_proxy()
except Exception as e:
    logging.warning(f"Could not configure upstream proxy in scheduler: {e}")

def _load_config_values() -> tuple[str, bool]:
    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "tools" / "automorph-data" / "config.yaml"
    sched_time = os.getenv("SCHEDULE_TIME", "03:00")
    run_startup = os.getenv("RUN_ON_STARTUP", "false").lower() in ("true", "1", "yes")
    
    if config_path.is_file():
        try:
            yaml = YAML()
            with config_path.open("r", encoding="utf-8") as f:
                data = yaml.load(f) or {}
            if "schedule_time" in data and data["schedule_time"] is not None:
                sched_time = str(data["schedule_time"]).strip()
            if "run_on_startup" in data and data["run_on_startup"] is not None:
                val = data["run_on_startup"]
                run_startup = val if isinstance(val, bool) else str(val).lower() in ("true", "1", "yes")
            if "timezone" in data and data["timezone"] is not None:
                tz_val = str(data["timezone"]).strip()
                os.environ["TZ"] = tz_val
                try:
                    time.tzset()
                except Exception:
                    pass
        except Exception as e:
            logging.warning(f"Failed to read schedule from {config_path}: {e}")
    return sched_time, run_startup

RUN_TIME, RUN_ON_STARTUP = _load_config_values()


def get_lock_file() -> Path:
  apps_dir = Path("apps")
  apps_dir.mkdir(parents=True, exist_ok=True)
  return apps_dir / ".automorph.lock"


def check_and_acquire_lock() -> bool:
  lock = get_lock_file()
  if lock.is_file():
    try:
      data = json.loads(lock.read_text(encoding="utf-8"))
      updated_at = data.get("updated_at") or data.get("started_at", 0)
      if time.time() - updated_at <= 300:
        logging.warning(
            f"[!] Pipeline already running (run_id: {data.get('run_id')}, source: {data.get('source')}). Skipping scheduled run."
        )
        return False
      else:
        logging.warning("[!] Found stale lock in scheduler. Clearing.")
    except Exception:
      pass

  now = time.time()
  lock_data = {
      "run_id": f"scheduler-{int(now)}",
      "source": "scheduler",
      "container": "patcher",
      "started_at": now,
      "updated_at": now,
  }
  try:
    lock.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")
    return True
  except Exception as e:
    logging.warning(f"[!] Failed to write scheduler lock: {e}")
    return False


def release_lock() -> None:
  try:
    get_lock_file().unlink(missing_ok=True)
  except Exception:
    pass


def job() -> None:
  logging.info("[*] Scheduled time reached. Verifying execution lock...")
  if not check_and_acquire_lock():
    return
  try:
    logging.info("[*] Launching main.py...")
    res = subprocess.run([sys.executable, "scripts/main.py"], check=False)
    if res.returncode == 0:
      logging.info("[✓] Pipeline run finished successfully.")
    else:
      logging.warning(f"[!] Pipeline exited with code {res.returncode}.")
  finally:
    release_lock()


if __name__ == "__main__":
  logging.info("[*] Scheduler daemon initialized.")

  if RUN_ON_STARTUP:
    logging.info("[*] RUN_ON_STARTUP is enabled. Running initial job...")
    job()

  schedule.every().day.at(RUN_TIME).do(job)
  logging.info(f"[*] Patcher scheduled to run daily at {RUN_TIME}.")

  while True:
    schedule.run_pending()
    time.sleep(30)