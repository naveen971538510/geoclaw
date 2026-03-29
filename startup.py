#!/usr/bin/env python3
"""GeoClaw startup health check. Run before starting server."""

import os
import py_compile
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parent
CHECKS = []


def _port_free(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", int(port))) != 0


def check(label, fn):
    try:
        result = bool(fn())
        status = "✓" if result else "✗"
        CHECKS.append((status, label))
        print(f"  {status}  {label}")
        return result
    except Exception as exc:
        CHECKS.append(("✗", f"{label} — {exc}"))
        print(f"  ✗  {label} — {exc}")
        return False


def _compile_ok(path: str) -> bool:
    py_compile.compile(str(ROOT / path), doraise=True)
    return True


def _migration_ok() -> bool:
    result = subprocess.run(
        [sys.executable, "migration.py"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _table_exists(name: str) -> bool:
    conn = sqlite3.connect(str(ROOT / "geoclaw.db"))
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
            (str(name),),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def main() -> int:
    print("\nGeoClaw startup check")
    print("─" * 40)
    check("Python >= 3.9", lambda: sys.version_info >= (3, 9))
    check("venv active", lambda: "venv" in sys.prefix or "VIRTUAL_ENV" in os.environ)
    check("geoclaw.db exists", lambda: (ROOT / "geoclaw.db").exists())
    check("main.py compiles", lambda: _compile_ok("main.py"))
    check("config.py compiles", lambda: _compile_ok("config.py"))
    check("migration.py runs", _migration_ok)
    check("agent_theses exists", lambda: _table_exists("agent_theses"))
    check("OPENAI_API_KEY set", lambda: bool(os.environ.get("OPENAI_API_KEY")))
    check("Port 8000 free", lambda: _port_free(8000))

    failed = [item for item in CHECKS if item[0] == "✗"]
    critical = [item for item in failed if "Python" in item[1] or "db" in item[1] or "main.py" in item[1]]

    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if critical:
        print("CRITICAL failures — fix before starting server")
        return 1
    if failed:
        print("WARNING: some checks failed — server may start but features limited")
        return 0
    print("All good. Start with: uvicorn main:app --port 8000 --reload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
