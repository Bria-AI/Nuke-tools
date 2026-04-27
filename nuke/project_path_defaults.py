"""Shared defaults for Bria Nuke project-path behavior."""

from __future__ import annotations

import os
from pathlib import Path


def _is_true_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def default_use_global_project_path() -> bool:
    env_value = os.getenv("USE_BRIA_PROJECT_PATH")
    if env_value is not None:
        return _is_true_env(env_value)

    try:
        from bria_core.config import load_config

        cfg = load_config()
        return bool(cfg.use_bria_project_path)
    except Exception:
        return False


def default_bria_project_path() -> str:
    env_value = str(os.getenv("BRIA_PROJECT_PATH") or "").strip()
    if env_value:
        return env_value

    try:
        from bria_core.config import load_config

        cfg = load_config()
        project_path = str(getattr(cfg, "nuke_output_dir", "") or "").strip()
        if project_path:
            return project_path
    except Exception:
        pass

    return ""


def rehydrate_project_path_env() -> tuple[bool, str]:
    use_project = default_use_global_project_path()
    project_path = default_bria_project_path()

    os.environ["USE_BRIA_PROJECT_PATH"] = "1" if use_project else "0"
    if use_project and project_path:
        try:
            normalized = str(Path(os.path.expandvars(os.path.expanduser(project_path))).resolve())
        except Exception:
            normalized = project_path
        os.environ["BRIA_PROJECT_PATH"] = normalized
        return True, normalized

    if not project_path:
        os.environ.pop("BRIA_PROJECT_PATH", None)
    return use_project, str(os.getenv("BRIA_PROJECT_PATH") or "").strip()
