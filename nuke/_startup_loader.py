"""Helpers for loading Bria bootstrap from Nuke startup scripts."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def resolve_nuke_root() -> Path:
    env_nuke_root = os.getenv("BRIA_NUKE_ROOT", "").strip()
    if env_nuke_root:
        return Path(env_nuke_root).resolve()
    return Path(__file__).resolve().parent


def resolve_repo_root() -> Path:
    env_root = os.getenv("BRIA_EXTENSION_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()

    nuke_root = resolve_nuke_root()

    # If BRIA_NUKE_ROOT points to repo root, use it directly.
    if (nuke_root / "bria_core").exists() and (nuke_root / "nuke").exists():
        return nuke_root

    # Typical case: BRIA_NUKE_ROOT points at repo/nuke.
    candidate = nuke_root.parent
    if (candidate / "bria_core").exists():
        return candidate

    # Fallback for local repo layout where this file is in repo/nuke.
    return Path(__file__).resolve().parents[1]


def ensure_paths(repo_root: Path) -> None:
    nuke_root = resolve_nuke_root()
    panels_dir = nuke_root / "panels"
    nodes_dir = nuke_root / "nodes"
    gizmos_dir = nuke_root / "gizmos"

    # Append to avoid shadowing Nuke's built-in `nuke` module.
    if str(repo_root) not in sys.path:
        sys.path.append(str(repo_root))

    # Allow Nuke startup/menu scripts and helpers to import sibling modules.
    if str(nuke_root) not in sys.path:
        sys.path.append(str(nuke_root))

    # Allow Nuke UI scripts to import panel/node helper modules by name.
    # Use prepend to avoid collisions with third-party modules that share names
    # (for example: `rmbg`).
    for path in (panels_dir, nodes_dir, gizmos_dir):
        if path.exists() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


def load_bootstrap(repo_root: Path):
    bootstrap_path = repo_root / "nuke" / "bootstrap.py"
    spec = importlib.util.spec_from_file_location("bria_nuke_bootstrap", str(bootstrap_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load bootstrap spec: {bootstrap_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_bootstrap_init() -> None:
    repo_root = resolve_repo_root()
    ensure_paths(repo_root)
    mod = load_bootstrap(repo_root)
    if hasattr(mod, "init"):
        mod.init()
