import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import APP_STATE_DIR


LOG_PATH = APP_STATE_DIR / "geoclaw.log"
_CONFIGURED = False


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    APP_STATE_DIR.mkdir(exist_ok=True)
    root = logging.getLogger("geoclaw")
    root.setLevel(logging.INFO)
    root.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    file_handler = RotatingFileHandler(Path(LOG_PATH), maxBytes=1_000_000, backupCount=3)
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.handlers = [file_handler, stream_handler]
    _CONFIGURED = True


def get_logger(name: str = "app") -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"geoclaw.{name}")
