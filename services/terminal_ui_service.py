from pathlib import Path

from config import UI_DIR


def _read_text(name: str) -> str:
    path = UI_DIR / name
    return path.read_text(encoding="utf-8")


def render_terminal_page() -> str:
    return _read_text("terminal.html")


def render_terminal_asset(name: str) -> str:
    return _read_text(name)


def terminal_asset_path(name: str) -> Path:
    return UI_DIR / name
