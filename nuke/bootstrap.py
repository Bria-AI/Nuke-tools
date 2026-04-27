"""Nuke bootstrap.

Runs on package load to validate config and log status.
This module should not modify the script or create nodes.
"""

from __future__ import annotations

import os
from pathlib import Path

from bria_core import get_status, ensure_tls_cert_env
from bria_core.config import load_config
from bria_core.logging import configure_logging, get_logger
from bria_core.errors import BriaConfigError


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _rehydrate_project_output_env() -> None:
    cfg = load_config()

    if os.getenv("BRIA_PROJECT_PATH") is None:
        cfg_path = (cfg.nuke_output_dir or "").strip()
        if cfg_path:
            try:
                os.environ["BRIA_PROJECT_PATH"] = str(Path(cfg_path).expanduser())
            except Exception:
                os.environ["BRIA_PROJECT_PATH"] = cfg_path

    if os.getenv("USE_BRIA_PROJECT_PATH") is None:
        use_cfg = bool(cfg.use_bria_project_path)
        os.environ["USE_BRIA_PROJECT_PATH"] = "1" if use_cfg else "0"

    use_now = _is_true(os.getenv("USE_BRIA_PROJECT_PATH"))
    if not use_now:
        return

    project_path = (os.getenv("BRIA_PROJECT_PATH") or "").strip()
    if not project_path:
        return

    try:
        Path(project_path).mkdir(parents=True, exist_ok=True)
    except Exception:
        # Keep startup fail-soft; write failures are handled at node runtime.
        pass


def init() -> None:
    configure_logging()
    logger = get_logger("nuke.bootstrap")

    try:
        ensure_tls_cert_env(logger)
        _rehydrate_project_output_env()

        do_ping = os.getenv("BRIA_BOOTSTRAP_PING", "").strip().lower() in ("1", "true", "yes")
        status = get_status(dcc="nuke", check_network=do_ping, timeout_s=3)
        if status["ok"]:
            logger.info(
                "Bria Nuke bootstrap OK | endpoint=%s | env_override=%s",
                status["resolved_endpoint"],
                status["using_env_override"],
            )
            print(f"[Bria] Nuke bootstrap OK | endpoint={status['resolved_endpoint']}")
        else:
            logger.warning("Bria Nuke bootstrap: %s (%s)", status["message"], status["reason"])
            print(f"[Bria] Nuke bootstrap WARNING | {status['message']} ({status['reason']})")
    except BriaConfigError as exc:
        logger.warning("Bria Nuke bootstrap config error: %s", exc)
        print(f"[Bria] Nuke bootstrap WARNING | {exc}")
    except Exception as exc:
        logger.exception("Bria Nuke bootstrap failed (non-fatal): %s", exc)
