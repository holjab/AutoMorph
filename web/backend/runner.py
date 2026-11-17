"""
Asynchronous subprocess execution and real-time Server-Sent Events (SSE) log runner.
Enforces mutual exclusion with heartbeat-based locks and maintains an in-memory ring buffer.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any, AsyncGenerator

from . import mutex

logger = logging.getLogger("automorph.runner")

PROJECT_ROOT = Path(os.getenv("AUTOMORPH_ROOT", Path(__file__).resolve().parent.parent.parent))
MAIN_SCRIPT = PROJECT_ROOT / "scripts" / "main.py"
APPS_DIR = PROJECT_ROOT / "apps"
LOGS_DIR = PROJECT_ROOT / "tools" / "automorph-data" / "state" / "logs"
LAST_RUN_FILE = LOGS_DIR / "last_run.json"
LAST_RUN_LOG_FILE = LOGS_DIR / "last_run.log"

RING_BUFFER_SIZE = 2000


def format_duration(duration: float | int | str | None) -> str:
    """Formats execution duration into human-readable hours, minutes, and seconds (e.g. 1h 2m 30s)."""
    if duration is None or duration == "":
        return "--"
    try:
        total_sec = int(round(float(duration)))
        mins, secs = divmod(total_sec, 60)
        hours, mins = divmod(mins, 60)
        if hours > 0:
            return f"{hours}h {mins}m {secs}s"
        if mins > 0:
            return f"{mins}m {secs}s"
        return f"{secs}s"
    except Exception:
        return f"{duration}s"


class PipelineRunner:
    def __init__(self) -> None:
        self.ring_buffer: deque[dict[str, Any]] = deque(maxlen=RING_BUFFER_SIZE)
        self.subscribers: set[asyncio.Queue] = set()
        self.current_process: asyncio.subprocess.Process | None = None
        self.current_pgid: int | None = None
        self.current_task: asyncio.Task | None = None
        self.is_running: bool = False
        self.active_run_id: str | None = None
        self.active_source: str = "manual"
        self.active_package: str | None = None
        self.start_time: float = 0.0
        self._heartbeat_task: asyncio.Task | None = None

    async def _heartbeat_loop(self) -> None:
        """Periodically refreshes the mutex lock heartbeat every 10 seconds."""
        try:
            while self.is_running:
                mutex.update_heartbeat()
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Error in heartbeat loop: {e}")

    async def trigger_run(self, source: str = "manual", package: str | None = None) -> dict[str, Any]:
        """
        Attempts to acquire the execution lock and spawn scripts/main.py.
        Returns run metadata or raises ValueError/RuntimeError if locked.
        """
        for d in [APPS_DIR, APPS_DIR / "original", APPS_DIR / "patched"]:
            d.mkdir(parents=True, exist_ok=True)

        run_id = f"run-{int(time.time())}"
        effective_source = f"{source}:{package}" if package else source
        acquired, lock_data = mutex.acquire_lock(
            source=source,
            package=package,
            container="automorph_web",
            run_id=run_id,
        )
        if not acquired:
            raise RuntimeError(json.dumps(lock_data or {"message": "Active run in progress"}))

        self.ring_buffer.clear()
        self.active_run_id = run_id
        self.active_source = source
        self.active_package = package
        self.is_running = True
        self.start_time = time.time()

        # Start heartbeat
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # Spawn background execution
        self.current_task = asyncio.create_task(self._execute_process(run_id, effective_source, package=package))

        return {
            "status": "started",
            "run_id": run_id,
            "source": effective_source,
            "package": package,
            "started_at": self.start_time,
        }

    async def _execute_process(self, run_id: str, source: str, package: str | None = None) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["AUTOMORPH_ROOT"] = str(PROJECT_ROOT)
        if package:
            env["TARGET_PACKAGE"] = package

        # Ensure configured timezone is set in env and applied
        tz_obj = None
        try:
            from . import config_manager
            tz_str = config_manager.apply_timezone()
            env["TZ"] = tz_str
            tz_obj = config_manager.get_current_timezone(tz_str)
        except Exception:
            if "TZ" in os.environ:
                env["TZ"] = os.environ["TZ"]

        captured_lines: list[str] = []

        # Broadcast initial line
        pkg_suffix = f" for package '{package}'" if package else ""
        now_dt = datetime.now(tz=tz_obj) if tz_obj else datetime.now()
        initial_msg_line = f"[*] AutoMorph pipeline triggered ({source}){pkg_suffix} at {now_dt.strftime('%Y-%m-%d %H:%M:%S')}"
        initial_msg = {
            "line": initial_msg_line,
            "timestamp": time.time(),
            "type": "stdout",
        }
        captured_lines.append(initial_msg_line)
        self._append_and_broadcast(initial_msg)

        returncode = -1
        try:
            self.current_process = await asyncio.subprocess.create_subprocess_exec(
                sys.executable,
                str(MAIN_SCRIPT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(PROJECT_ROOT),
                env=env,
                start_new_session=True,
            )
            self.current_pgid = self.current_process.pid

            # Stream stdout line by line
            while True:
                line_bytes = await self.current_process.stdout.readline()
                if not line_bytes:
                    break
                line_str = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                captured_lines.append(line_str)
                msg = {
                    "line": line_str,
                    "timestamp": time.time(),
                    "type": "stdout",
                }
                self._append_and_broadcast(msg)

            returncode = await self.current_process.wait()

        except asyncio.CancelledError:
            err_line = "\n[!] Pipeline execution cancelled by user request."
            captured_lines.append(err_line)
            err_msg = {
                "line": err_line,
                "timestamp": time.time(),
                "type": "stderr",
            }
            self._append_and_broadcast(err_msg)
            returncode = 130
        except Exception as e:
            err_line = f"[✗] Pipeline execution error: {e}"
            captured_lines.append(err_line)
            err_msg = {
                "line": err_line,
                "timestamp": time.time(),
                "type": "stderr",
            }
            self._append_and_broadcast(err_msg)
            returncode = 1
        finally:
            self.is_running = False
            duration = round(time.time() - self.start_time, 2)
            duration_formatted = format_duration(duration)

            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()

            # Record final status
            final_status = "success" if returncode == 0 else "failed"
            if returncode == 0:
                last_report_file = LOGS_DIR / "last_run_report.json"
                if last_report_file.is_file():
                    try:
                        rep_data = json.loads(last_report_file.read_text(encoding="utf-8"))
                        if rep_data.get("timestamp", 0) >= self.start_time - 10:
                            failures = rep_data.get("failures") or {}
                            successes = rep_data.get("success") or {}
                            if package:
                                if package in failures or (failures and package not in successes):
                                    final_status = "failed"
                            elif failures:
                                final_status = "failed"
                    except Exception as e:
                        logger.debug(f"Could not check last_run_report.json for status: {e}")

            summary = {
                "run_id": run_id,
                "status": final_status,
                "returncode": returncode,
                "duration_seconds": duration,
                "duration_formatted": duration_formatted,
                "finished_at": time.time(),
                "source": source,
                "package": package,
            }

            if final_status == "failed" and returncode == 0:
                completion_line = f"\n[!] Run completed with failures in {duration_formatted} (Code: {returncode})"
            else:
                completion_line = f"\n[✓] Run completed in {duration_formatted} (Code: {returncode})"
            captured_lines.append(completion_line)

            try:
                LOGS_DIR.mkdir(parents=True, exist_ok=True)
                LAST_RUN_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
                LAST_RUN_LOG_FILE.write_text("\n".join(captured_lines) + "\n", encoding="utf-8")
            except Exception as e:
                logger.warning(f"Could not save last_run files: {e}")

            # Release mutex lock
            mutex.release_lock()

            # Reset process tracking
            self.current_process = None
            self.current_pgid = None
            self.current_task = None

            # Emit completion event
            done_msg = {
                "event": "complete",
                "status": final_status,
                "returncode": returncode,
                "duration_seconds": duration,
                "duration_formatted": duration_formatted,
                "source": source,
                "package": package,
                "timestamp": time.time(),
            }
            self._append_and_broadcast(done_msg)

    def _kill_process_group(self, pgid: int | None, pid: int | None, sig: signal.Signals) -> bool:
        """Sends signal to process group and process directly. Returns True if any signal succeeded."""
        sent = False
        if pgid and pgid > 1:
            try:
                os.killpg(pgid, sig)
                sent = True
            except (ProcessLookupError, OSError) as e:
                logger.debug(f"os.killpg failed for group {pgid}: {e}")

        if pid and pid > 1:
            try:
                os.kill(pid, sig)
                sent = True
            except (ProcessLookupError, OSError) as e:
                logger.debug(f"os.kill failed for pid {pid}: {e}")

        return sent

    async def stop_run(self) -> dict[str, Any]:
        """
        Stops the running pipeline immediately, exactly like Ctrl+C in a terminal.
        Sends SIGINT to the process group, escalates to SIGKILL if needed,
        guarantees that the mutex lock is released, and returns only after the pipeline has halted.
        If an orphaned lock exists with no in-memory process, releases the lock.
        """
        # Always cancel the heartbeat loop first so it cannot refresh the lock timestamp
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()

        # Case 1: No process in memory and not marked running
        if not self.is_running and not self.current_process:
            lock_path = mutex.get_lock_path()
            if mutex.get_active_lock() is not None or lock_path.is_file():
                logger.info("stop_run called with orphaned lock on disk. Releasing lock.")
                mutex.release_lock()
                return {"status": "stopped", "message": "Orphaned execution lock released"}
            return {"status": "not_running", "message": "No active pipeline process to stop"}

        run_id = self.active_run_id

        try:
            proc = self.current_process
            pid = proc.pid if proc else None
            pgid = self.current_pgid or pid

            if proc and pid:
                logger.info(f"Stop requested for run {run_id} (PID: {pid}, PGID: {pgid})")
                stop_msg = {
                    "line": "\n[!] Stop requested by user (SIGINT)...",
                    "timestamp": time.time(),
                    "type": "stderr",
                }
                self._append_and_broadcast(stop_msg)

                # 1. Send SIGINT to process group and process (like terminal Ctrl+C)
                self._kill_process_group(pgid, pid, signal.SIGINT)

                # 2. Wait up to 1.0 second for clean exit
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1.0)
                except (asyncio.TimeoutError, Exception):
                    # 3. If still active after 1s, immediately SIGKILL
                    logger.warning(f"Process {pid} / group {pgid} still active after 1s; force killing (SIGKILL)")
                    self._kill_process_group(pgid, pid, signal.SIGKILL)
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=1.0)
                    except (asyncio.TimeoutError, Exception):
                        pass

            # 4. Cancel the background execution/reader task
            if self.current_task and not self.current_task.done():
                self.current_task.cancel()
                try:
                    await asyncio.wait_for(self.current_task, timeout=0.5)
                except (asyncio.CancelledError, asyncio.TimeoutError, BaseException):
                    pass

        except Exception as e:
            logger.error(f"Error during stop_run execution: {e}", exc_info=True)
        finally:
            # 5. UNCONDITIONALLY release the lock, reset state, and broadcast complete event
            mutex.release_lock()
            self.is_running = False

            duration = round(time.time() - self.start_time, 2) if self.start_time else 0
            duration_formatted = format_duration(duration)

            done_msg = {
                "event": "complete",
                "status": "failed",
                "returncode": 130,
                "duration_seconds": duration,
                "duration_formatted": duration_formatted,
                "source": self.active_source,
                "package": self.active_package,
                "timestamp": time.time(),
            }
            self._append_and_broadcast(done_msg)

            self.current_process = None
            self.current_pgid = None
            self.current_task = None

        return {
            "status": "stopped",
            "run_id": run_id,
            "message": "Pipeline process stopped and execution lock released",
        }

    def _append_and_broadcast(self, msg: dict[str, Any]) -> None:
        self.ring_buffer.append(msg)
        line = msg.get("line")
        if line:
            logger.info(f"[pipeline] {line}")

        for queue in list(self.subscribers):
            try:
                queue.put_nowait(msg)
            except Exception:
                pass

    async def log_stream_generator(self) -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(queue)

        try:
            # 1. Replay historical ring buffer first
            for item in list(self.ring_buffer):
                if item.get("event") == "complete":
                    yield f"event: complete\ndata: {json.dumps(item)}\n\n"
                else:
                    yield f"data: {json.dumps(item)}\n\n"

            # If not running and buffer was already completed, return immediately
            if not self.is_running:
                return

            # 2. Stream ongoing live lines
            while self.is_running:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    if msg.get("event") == "complete":
                        yield f"event: complete\ndata: {json.dumps(msg)}\n\n"
                        break
                    else:
                        yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    # SSE comment line as keep-alive ping to prevent proxy/browser idle timeouts
                    yield ": ping\n\n"
        finally:
            self.subscribers.discard(queue)

    def get_last_run_info(self) -> dict[str, Any] | None:
        target_file = LAST_RUN_FILE
        if target_file.is_file():
            try:
                data = json.loads(target_file.read_text(encoding="utf-8"))
                if "duration_formatted" not in data:
                    data["duration_formatted"] = format_duration(data.get("duration_seconds"))
                return data
            except Exception:
                pass

        # Fallback to last_run_report.json in logs directory if available
        last_report_file = LOGS_DIR / "last_run_report.json"
        if last_report_file.is_file():
            try:
                rep_data = json.loads(last_report_file.read_text(encoding="utf-8"))
                ts = rep_data.get("timestamp") or 0.0
                dur = rep_data.get("duration")
                failures = rep_data.get("failures") or {}
                status = "failed" if failures else "success"
                return {
                    "run_id": f"cli-{int(ts)}",
                    "status": status,
                    "returncode": 1 if failures else 0,
                    "duration_seconds": dur,
                    "duration_formatted": format_duration(dur),
                    "finished_at": ts,
                    "source": "cli",
                }
            except Exception:
                pass

        return None

    def get_last_run_logs(self) -> dict[str, Any]:
        """Returns the full log lines and execution summary for the last or currently active run."""
        run_info = self.get_last_run_info()

        # If currently running, fetch from active ring buffer
        if self.is_running:
            lines = [item.get("line") for item in list(self.ring_buffer) if item.get("line") is not None]
            return {
                "is_running": True,
                "run_id": self.active_run_id,
                "run_info": run_info,
                "lines": lines,
            }

        # If not running, check in-memory ring buffer first
        if self.ring_buffer:
            lines = [item.get("line") for item in list(self.ring_buffer) if item.get("line") is not None]
            if lines:
                return {
                    "is_running": False,
                    "run_info": run_info,
                    "lines": lines,
                }

        # Fallback to persistent last_run.log file on disk
        target_log = LAST_RUN_LOG_FILE
        if target_log.is_file():
            try:
                content = target_log.read_text(encoding="utf-8", errors="replace")
                lines = [line for line in content.splitlines()]
                return {
                    "is_running": False,
                    "run_info": run_info,
                    "lines": lines,
                }
            except Exception as e:
                logger.warning(f"Could not read last_run.log: {e}")

        return {
            "is_running": False,
            "run_info": run_info,
            "lines": [],
        }


runner = PipelineRunner()
