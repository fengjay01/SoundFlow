"""Project-local default paths for worker processes.

Why this exists
---------------
The Tauri shell injects authoritative ``PYMSS_*`` paths when it spawns a worker
(``src-tauri/src/python/worker.rs``). A worker can also be started without that
launcher -- directly from Python, from pytest, or by a third-party library. The
upstream fallbacks used the user home directory in that case, which put caches
and model registries on the system drive. This module makes *the code default* itself
project-local: any process resolves its storage under the checkout, with no
environment setup required.

Layering contract (see ``docs/environment.md``)
----------------------------------------------
``<repo>/.venv``          environment layer (rebuildable, never holds weights)
``<repo>/data``           runtime state + models (portable, backup-able)
``<repo>/_debug_output``  ignored debug artifacts (``scripts/debug_paths.py``)

Explicit environment overrides are still honoured: portable builds, CI and
custom layouts can deliberately point ``PYMSS_STUDIO_DATA_ROOT`` elsewhere.
All paths are derived from ``__file__``; no drive letter or machine path is
hard-coded, so a clone works on any machine and any OS.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_ROOT = PROJECT_ROOT / ".venv"


def _override(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def data_root() -> Path:
    return _override("PYMSS_STUDIO_DATA_ROOT") or PROJECT_ROOT / "data"


def models_dir() -> Path:
    return _override("PYMSS_MODEL_DIR") or data_root() / "models"


def settings_dir() -> Path:
    return data_root() / "settings"


def debug_dir() -> Path:
    return _override("PYMSS_STUDIO_DEBUG_DIR") or data_root() / "debug"


def user_models_file() -> Path:
    return _override("PYMSS_USER_MODELS") or settings_dir() / "user_models.json"


def plugins_dir() -> Path:
    return _override("PYMSS_PLUGINS_DIR") or data_root() / "plugins"


def runtime_envs_dir() -> Path:
    return (
        _override("PYMSS_STUDIO_RUNTIME_ENVS_DIR")
        or _override("PYMSS_STUDIO_RUNTIME_ENVS_OVERRIDE")
        or ENV_ROOT / "python" / "runtime-envs"
    )


def scratch_dir() -> Path:
    """Directory for short-lived files, e.g. ``TemporaryDirectory(dir=...)``.

    Falls back to the OS temp only when the project tree is not writable
    (signed app bundles, read-only installations).
    """
    candidate = data_root() / "temp"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        return Path(tempfile.gettempdir())


def is_project_local(path: Path | str) -> bool:
    """True when *path* resolves inside the checkout (used by the env self-check)."""
    try:
        Path(path).resolve().relative_to(PROJECT_ROOT)
        return True
    except ValueError:
        return False


def project_local_or(candidate: Path, fallback: Path | None = None) -> Path:
    """Keep *candidate* when it is inside the checkout, else use a local default.

    Several upstream helpers derive runtimes from ``sys.executable``. That is
    correct for a dev venv or a packaged bundle (both live inside the tree),
    but points at the system Python when someone runs a worker directly after
    cloning. This wrapper preserves the original value in the normal case and
    only redirects the stray one.
    """
    return candidate if is_project_local(candidate) else (fallback or runtime_envs_dir())


def default_paths() -> dict[str, Path]:
    """All locally derived defaults, for diagnostics and ``pnpm env:paths``."""
    return {
        "data_root": data_root(),
        "models_dir": models_dir(),
        "settings_dir": settings_dir(),
        "debug_dir": debug_dir(),
        "plugins_dir": plugins_dir(),
        "user_models_file": user_models_file(),
        "runtime_envs_dir": runtime_envs_dir(),
        "scratch_dir": scratch_dir(),
    }
