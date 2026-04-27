"""Shared Nuke node helper utilities for Bria node modules.

This implements the same DCC helper contract as Houdini for reuse in shared
Bria node logic.
"""

from __future__ import annotations

from typing import Any

from bria_core.dcc import DccNodeUtils, debug_logger
from bria_core.utils import resolve_temp_dir as _resolve_temp_dir


class NukeNodeUtils(DccNodeUtils):
    def _knob(self, node: Any, name: str):
        if node is None:
            return None
        try:
            return node.knob(name)
        except Exception:
            return None

    def opt_parm_str(self, node: Any, name: str) -> str | None:
        knob = self._knob(node, name)
        if knob is None:
            return None
        try:
            value = knob.value()
        except Exception:
            return None
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def opt_parm_menu_str(self, node: Any, name: str) -> str | None:
        knob = self._knob(node, name)
        if knob is None:
            return None

        values = None
        try:
            values = list(knob.values())  # Enumeration_Knob
        except Exception:
            values = None

        try:
            raw = knob.value()
        except Exception:
            return self.opt_parm_str(node, name)

        # Some Nuke enum knobs may return numeric index; map it to label.
        if values:
            try:
                if isinstance(raw, (int, float)):
                    idx = int(raw)
                    if 0 <= idx < len(values):
                        text = str(values[idx]).strip()
                        return text or None
                txt = str(raw).strip()
                if txt.isdigit():
                    idx = int(txt)
                    if 0 <= idx < len(values):
                        text = str(values[idx]).strip()
                        return text or None
            except Exception:
                pass

        if raw is None:
            return None
        txt = str(raw).strip()
        return txt or None

    def opt_parm_bool(self, node: Any, name: str) -> bool | None:
        knob = self._knob(node, name)
        if knob is None:
            return None
        try:
            return bool(knob.value())
        except Exception:
            return None

    def opt_parm_int(self, node: Any, name: str) -> int | None:
        knob = self._knob(node, name)
        if knob is None:
            return None
        try:
            return int(knob.value())
        except Exception:
            return None

    def opt_parm_float(self, node: Any, name: str) -> float | None:
        knob = self._knob(node, name)
        if knob is None:
            return None
        try:
            return float(knob.value())
        except Exception:
            return None

    def as_dcc_path(self, path: str) -> str:
        return str(path).replace("\\", "/")


_UTILS = NukeNodeUtils()


def opt_parm_str(node: Any, name: str) -> str | None:
    return _UTILS.opt_parm_str(node, name)


def opt_parm_menu_str(node: Any, name: str) -> str | None:
    return _UTILS.opt_parm_menu_str(node, name)


def opt_parm_bool(node: Any, name: str) -> bool | None:
    return _UTILS.opt_parm_bool(node, name)


def opt_parm_int(node: Any, name: str) -> int | None:
    return _UTILS.opt_parm_int(node, name)


def opt_parm_float(node: Any, name: str) -> float | None:
    return _UTILS.opt_parm_float(node, name)


def as_nuke_path(path: str) -> str:
    return _UTILS.as_dcc_path(path)


def resolve_temp_dir(nuke_module: Any = None, fallback: str = ".") -> str:
    """Resolve writable temp dir via shared core utility."""
    return _resolve_temp_dir(dcc_module=nuke_module, fallback=fallback)
