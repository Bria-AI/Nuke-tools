"""Bria Nuke GUI auto-load template.

Copy this file to your Nuke init script.

- macOS/Linux: ~/.nuke/init.py
- Windows: %USERPROFILE%\\.nuke\\init.py

Single-path setup:
- Set BRIA_EXTENSION_ROOT below to your local extension root.
"""

import os
import runpy
import sys
import traceback
from pathlib import Path
from typing import Optional

# -----------------------------------------------------------------------------
# USER SETUP (single path)
# -----------------------------------------------------------------------------
# Set this to your extension root. Examples:
# - Windows: r"E:/work/bria/apps/extension"
# - macOS/Linux: "~/work/bria/apps/extension"
BRIA_EXTENSION_ROOT = ""


def _resolve_repo_root() -> Optional[Path]:
    # 1) Single-path setup in this file.
    if BRIA_EXTENSION_ROOT.strip():
        root = Path(BRIA_EXTENSION_ROOT).expanduser()
        if (root / "nuke" / "init.py").exists():
            return root

    # 2) Env fallback (for studio launchers).
    env_root = os.environ.get("BRIA_EXTENSION_ROOT", "").strip()
    if env_root:
        root = Path(env_root).expanduser()
        if (root / "nuke" / "init.py").exists():
            return root

    # 3) If this template runs from inside the repo, infer automatically.
    try:
        here = Path(__file__).resolve()
        inferred = here.parents[2]  # repo/nuke/startup/user_init.py -> repo
        if (inferred / "nuke" / "init.py").exists():
            return inferred
    except Exception:
        pass

    return None


def _norm_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value or ""))


REPO_ROOT = _resolve_repo_root()
if REPO_ROOT is None:
    print(
        "[Bria] Startup skipped: could not resolve extension root. "
        "Set BRIA_EXTENSION_ROOT at top of ~/.nuke/init.py "
        "or export BRIA_EXTENSION_ROOT before launching Nuke."
    )
else:
    NUKE_ROOT = REPO_ROOT / "nuke"

    os.environ.setdefault("BRIA_EXTENSION_ROOT", str(REPO_ROOT))
    os.environ.setdefault("BRIA_NUKE_ROOT", str(NUKE_ROOT))
    os.environ.setdefault("NUKE_BRIA", str(REPO_ROOT))
    os.environ.setdefault("BRIA_EXAMPLES", str((REPO_ROOT / "examples").resolve()))

    existing_nuke_path = os.environ.get("NUKE_PATH", "")
    nuke_path_parts = [part for part in existing_nuke_path.split(os.pathsep) if part]
    if _norm_path(str(NUKE_ROOT)) not in {_norm_path(part) for part in nuke_path_parts}:
        os.environ["NUKE_PATH"] = os.pathsep.join([str(NUKE_ROOT), *nuke_path_parts])

    if str(REPO_ROOT) not in sys.path:
        sys.path.append(str(REPO_ROOT))
    if str(NUKE_ROOT) not in sys.path:
        sys.path.append(str(NUKE_ROOT))

    print(f"[Bria] Startup root: {REPO_ROOT}")

    try:
        runpy.run_path(str(NUKE_ROOT / "init.py"), run_name="__bria_nuke_init__")
    except Exception as exc:
        print(f"[Bria] Failed to load Nuke extension: {exc}")
        traceback.print_exc()

    try:
        register_ui = None
        menu_module = sys.modules.get("_bria_nuke_menu")
        if menu_module is not None:
            register_ui = getattr(menu_module, "register_ui", None)
        else:
            # Load menu symbols without auto-register side effects.
            menu_globals = runpy.run_path(str(NUKE_ROOT / "menu.py"), run_name="__bria_nuke_menu_export__")
            register_ui = menu_globals.get("register_ui")

        def _attempt_register_ui() -> bool:
            if not callable(register_ui):
                return False
            try:
                result = register_ui()
                if isinstance(result, dict):
                    panel_ok = bool(result.get("panel"))
                    menu_ok = bool(result.get("menu"))
                    print(f"[Bria] UI registration status: panel={panel_ok} menu={menu_ok}")
                    return panel_ok or menu_ok
                print("[Bria] UI registration completed")
                return True
            except Exception as exc:
                print(f"[Bria] UI registration attempt failed: {exc}")
                return False

        registered_now = _attempt_register_ui()

        if not registered_now:
            try:
                import nuke  # type: ignore

                if hasattr(nuke, "addOnScriptLoad"):
                    nuke.addOnScriptLoad(_attempt_register_ui)
                    print("[Bria] Deferred UI registration via addOnScriptLoad")
            except Exception:
                pass
    except Exception as exc:
        print(f"[Bria] Failed to load Nuke menu: {exc}")
        traceback.print_exc()
