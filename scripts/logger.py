import logging
from pathlib import Path
from datetime import datetime

class CleanRecord(logging.Filter):

    def filter(self, record: logging.LogRecord) -> bool:
        tags = ("[✓]", "[!]", "[*]", "[✗]")
        message = record.getMessage().lstrip()
        message = message.lstrip("├└│─ ").lstrip()

        for tag in tags:
            if message.startswith(tag):
                message = message.removeprefix(tag).lstrip()
                break

        record.clean_message = message
        
        return True

def logger_init(project_root: Path, name: str = "main") -> tuple[logging.Logger, Path]:
    log_dir = project_root / "tools" / "automorph-data" / "state" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = log_dir / f"automorph_{timestamp}.log"
    logger = logging.getLogger(name)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    file_handler = logging.FileHandler(filename, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(CleanRecord())
    file_handler.setFormatter(logging.Formatter(fmt="%(asctime)s [%(levelname)s] %(name)s: %(clean_message)s", datefmt="%H:%M:%S"))

    logging.basicConfig(level=logging.DEBUG, handlers=[console_handler, file_handler], force=True)
    logging.getLogger("pyaxmlparser").setLevel(logging.WARNING)

    return logger, log_dir