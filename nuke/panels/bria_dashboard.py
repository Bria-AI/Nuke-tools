"""Bria dashboard panel for Nuke (Qt widget)."""

from __future__ import annotations

import os
from pathlib import Path

import nuke  # type: ignore

try:
    from PySide6 import QtCore, QtWidgets  # type: ignore  # Nuke 17+
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

from bria_core.dashboard_qt import BriaDashboardWidget
from bria_core.utils import resolve_temp_dir
from project_path_defaults import default_bria_project_path, sync_all_nodes_to_global


def _sync_all_bria_nodes_project_toggle(use_project: bool) -> None:
    """Propagate the dashboard 'Use Global Project Path' toggle to all Bria nodes in the scene."""
    sync_all_nodes_to_global(use_project)


def _set_project_favorite(path_value: str, enabled: bool = True) -> None:
    try:
        remove_fav = getattr(nuke, "removeFavoriteDir", None)
        if callable(remove_fav):
            remove_fav("Project")
    except Exception:
        pass

    if not enabled:
        return

    txt = str(path_value or "").strip()
    if not txt:
        return

    try:
        project_path = Path(os.path.expandvars(os.path.expanduser(txt))).resolve()
    except Exception:
        project_path = Path(txt).expanduser()

    try:
        project_path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    try:
        nuke.addFavoriteDir("Project", str(project_path))
    except Exception:
        pass


def _pick_directory(start_path: str) -> str | None:
    try:
        selected = nuke.getFilename("Select Bria Project Folder", start_path)
    except Exception:
        selected = None
    if not selected:
        return None

    selected_path = Path(str(selected)).expanduser()
    if selected_path.is_file():
        selected_path = selected_path.parent

    _set_project_favorite(str(selected_path), enabled=True)
    return str(selected_path)


class BriaPanel(QtWidgets.QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._fav_state: tuple[str, str] | None = None

        try:
            widget = BriaDashboardWidget(
                parent=self,
                dcc="nuke",
                include_project_paths=True,
                temp_dir_provider=lambda: resolve_temp_dir(nuke),
                folder_picker=_pick_directory,
                project_path_callback=self._sync_project_favorite,
            )
            layout.addWidget(widget)
        except Exception as exc:
            try:
                nuke.tprint(f"[Bria] Failed to initialize Qt dashboard widget: {exc}")
            except Exception:
                pass
            fallback = QtWidgets.QLabel(f"Bria dashboard failed to load:\n{exc}")
            fallback.setWordWrap(True)
            layout.addWidget(fallback)

    def _sync_project_favorite(self, use_project: bool, project_path: str) -> None:
        if not project_path:
            project_path = default_bria_project_path()
        state = ("1" if use_project else "0", project_path)
        if state == self._fav_state:
            return

        self._fav_state = state
        _set_project_favorite(project_path, enabled=use_project)
        _sync_all_bria_nodes_project_toggle(use_project)
