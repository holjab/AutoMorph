"""Run and supervise both AutoMorph HTTP listeners in the container."""

import signal
import subprocess
import sys
import time


def main() -> int:
    stopping = False

    def request_shutdown(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    children = {}
    try:
        for name, app, port in (
            ("dashboard", "app", "8080"),
            ("delivery", "delivery_app", "8081"),
        ):
            if stopping:
                break
            children[name] = subprocess.Popen(
                [
                    sys.executable, "-m", "uvicorn", f"web.backend.main:{app}",
                    "--host", "0.0.0.0", "--port", port,
                    "--timeout-graceful-shutdown", "5",
                ]
            )

        while not stopping:
            for name, child in children.items():
                code = child.poll()
                if code is not None and not stopping:
                    print(f"AutoMorph {name} listener exited with status {code}.", file=sys.stderr)
                    return code if code > 0 else 1
            time.sleep(0.2)
        return 0
    finally:
        for child in children.values():
            if child.poll() is None:
                child.terminate()

        deadline = time.monotonic() + 8
        for child in children.values():
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    sys.exit(main())
