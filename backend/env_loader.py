"""Tiny, optional .env loader for app entrypoints.

Deliberately dependency-free (no python-dotenv). Reads KEY=VALUE lines from
<repo-root>/.env (falling back to backend/.env) into os.environ using
setdefault semantics, so real environment variables always take precedence.

Opt-in only: modules that must prove key-free operation (e.g. check_cold_start.py)
never call this, so the no-keys cold-start guarantee stays verifiable even with a
populated .env on disk.
"""

import os
from pathlib import Path

_LOADED = False


def load_env_file(path: str | Path | None = None) -> None:
    """Load a .env file (default <repo-root>/.env or backend/.env) if present.

    Existing environment variables are never overwritten.
    """
    global _LOADED
    if _LOADED and path is None:
        return
    _LOADED = True

    candidates = []
    if path is not None:
        candidates = [Path(path)]
    else:
        root = Path(__file__).resolve().parent.parent
        candidates = [root / ".env", root / "backend" / ".env"]

    for env_file in candidates:
        if not env_file.is_file():
            continue
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            key, sep, value = line.partition("=")
            if not sep:
                continue
            key = key.strip().strip("'\"")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            os.environ.setdefault(key, value)
        return