"""Shared Qt dashboard widget for DCC integrations."""

from __future__ import annotations

import os
import webbrowser
from pathlib import Path
from typing import Any, Callable

from .config import resolve_config_path
from .dashboard import (
    DASHBOARD_API_KEYS_URL,
    DASHBOARD_LOGIN_URL,
    clear_dashboard_tokens,
    dashboard_token_aliases,
    has_any_dashboard_token,
    read_dashboard_config,
    resolve_dashboard_token_value,
    save_dashboard_tokens,
    token_fields_for_dcc,
    write_dashboard_config,
)
from .status import get_status

_MASK = "*" * 32
_CONNECTION_KEYS = (
    "nuke_use_bearer_auth",
    "nuke_use_env_proxy",
    "nuke_http_proxy",
    "nuke_https_proxy",
)

try:
    from PySide6 import QtCore, QtWidgets  # type: ignore  # Nuke 17+
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore


try:
    _SP_EXPANDING = QtWidgets.QSizePolicy.Expanding
    _SP_PREFERRED = QtWidgets.QSizePolicy.Preferred
except Exception:
    _SP_EXPANDING = QtWidgets.QSizePolicy.Policy.Expanding
    _SP_PREFERRED = QtWidgets.QSizePolicy.Policy.Preferred

try:
    _ALIGN_LEFT = QtCore.Qt.AlignLeft
    _ALIGN_RIGHT = QtCore.Qt.AlignRight
    _ALIGN_VCENTER = QtCore.Qt.AlignVCenter
except Exception:
    _ALIGN_LEFT = QtCore.Qt.AlignmentFlag.AlignLeft
    _ALIGN_RIGHT = QtCore.Qt.AlignmentFlag.AlignRight
    _ALIGN_VCENTER = QtCore.Qt.AlignmentFlag.AlignVCenter

try:
    _MSGBOX_YES = QtWidgets.QMessageBox.Yes
    _MSGBOX_NO = QtWidgets.QMessageBox.No
except Exception:
    _MSGBOX_YES = QtWidgets.QMessageBox.StandardButton.Yes
    _MSGBOX_NO = QtWidgets.QMessageBox.StandardButton.No


STYLE = """
* { font-family: 'Courier New', monospace; font-size: 11px; color: #c8c8c8; }
QWidget { background: #2b2b2b; }
QWidget#status_bar { background: #232323; border-bottom: 1px solid #1a1a1a; }
QWidget#status_info { background: #232323; border: none; }
QWidget#config_bar { background: #262626; border-bottom: 1px solid #1e1e1e; }
QWidget#access_container { background: #272727; border-top: 1px solid #1e1e1e; border-bottom: 1px solid #1e1e1e; }
QLabel#status_ok { color: #4caf50; font-weight: bold; background: transparent; }
QLabel#status_warn { color: #ff9800; font-weight: bold; background: transparent; }
QLabel#status_err { color: #e05252; font-weight: bold; background: transparent; }
QLabel#status_url { color: #888; font-size: 10px; background: transparent; }
QLabel#config_label { color: #666; font-size: 10px; }
QLabel#config_path { color: #777; font-size: 10px; font-style: italic; }
QWidget#section_header { background: transparent; border: none; }
QLabel#section_label { color: #999; font-size: 10px; letter-spacing: 1px; }
QLabel#row_label { color: #888; font-size: 10px; }
QLineEdit { background: #222; border: 1px solid #1a1a1a; color: #ccc; padding: 2px 5px; }
QLineEdit:focus { border: 1px solid #555; background: #252525; }
QLineEdit:disabled { background: #1f1f1f; color: #666; border: 1px solid #1a1a1a; }
QPushButton { background: #3a3a3a; border: 1px solid #4a4a4a; color: #c0c0c0; padding: 2px 8px; min-height: 20px; }
QPushButton:hover { background: #484848; border-color: #666; color: #e0e0e0; }
QPushButton:disabled { background: #2b2b2b; border-color: #333; color: #666; }
QPushButton#btn_save { background: #3a4a3a; border-color: #4d6b4d; color: #8ec98e; }
QPushButton#btn_save:hover { background: #445244; border-color: #5e7e5e; color: #a8d8a8; }
QPushButton#btn_clear { background: #3e2e2e; border-color: #5e3e3e; color: #c08080; }
QPushButton#btn_clear:hover { background: #4a3030; border-color: #7a4040; color: #d89090; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: #242424; color: #777; padding: 4px 12px; border-right: 1px solid #1a1a1a; }
QTabBar::tab:selected { background: #2b2b2b; color: #ccc; border-bottom: 2px solid #5a8a5a; }
"""


class BriaDashboardWidget(QtWidgets.QWidget):
    def __init__(
        self,
        parent=None,
        *,
        dcc: str,
        include_project_paths: bool = False,
        temp_dir_provider: Callable[[], str] | None = None,
        folder_picker: Callable[[str], str | None] | None = None,
        message_fn: Callable[[str], None] | None = None,
        project_path_callback: Callable[[bool, str], None] | None = None,
    ):
        super().__init__(parent)
        self._dcc = (dcc or "").strip().lower()
        self._include_project_paths = bool(include_project_paths)
        self._temp_dir_provider = temp_dir_provider
        self._folder_picker = folder_picker
        self._message_fn = message_fn
        self._project_path_callback = project_path_callback

        self._config_path = resolve_config_path()
        self._token_fields = token_fields_for_dcc(self._dcc)
        self._label_width = 120
        self._token_inputs: dict[str, QtWidgets.QLineEdit] = {}
        self._token_show_buttons: dict[str, QtWidgets.QPushButton] = {}

        self.setMinimumWidth(420)
        self.setStyleSheet(STYLE)

        self._setup_ui()
        self._load_current_state()

    def _setup_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._status_frame = QtWidgets.QFrame()
        self._status_frame.setObjectName("status_bar")
        status_layout = QtWidgets.QHBoxLayout(self._status_frame)
        status_layout.setContentsMargins(8, 5, 8, 5)
        status_layout.setSpacing(6)

        status_info = QtWidgets.QWidget()
        status_info.setObjectName("status_info")
        status_info_layout = QtWidgets.QHBoxLayout(status_info)
        status_info_layout.setContentsMargins(0, 0, 0, 0)
        status_info_layout.setSpacing(6)

        self._status_indicator = QtWidgets.QLabel()
        self._status_indicator.setFixedSize(9, 9)
        status_info_layout.addWidget(self._status_indicator)

        self._status_label = QtWidgets.QLabel("Checking...")
        self._status_label.setObjectName("status_ok")
        status_info_layout.addWidget(self._status_label)

        self._status_url = QtWidgets.QLabel("")
        self._status_url.setObjectName("status_url")
        self._status_url.setSizePolicy(_SP_EXPANDING, _SP_PREFERRED)
        status_info_layout.addWidget(self._status_url)

        status_layout.addWidget(status_info, 1)

        check_net_btn = QtWidgets.QPushButton("Check Network")
        check_net_btn.clicked.connect(lambda: self._run_status_check(True))
        status_layout.addWidget(check_net_btn)

        check_cfg_btn = QtWidgets.QPushButton("Check Config")
        check_cfg_btn.clicked.connect(lambda: self._run_status_check(False))
        status_layout.addWidget(check_cfg_btn)

        status_layout.addStretch()

        layout.addWidget(self._status_frame)

        config_bar = QtWidgets.QWidget()
        config_bar.setObjectName("config_bar")
        config_layout = QtWidgets.QHBoxLayout(config_bar)
        config_layout.setContentsMargins(8, 4, 8, 4)
        config_layout.setSpacing(6)
        cfg_label = QtWidgets.QLabel("Config")
        cfg_label.setObjectName("config_label")
        cfg_label.setFixedWidth(40)
        config_layout.addWidget(cfg_label)
        self._path_label = QtWidgets.QLabel(str(self._config_path))
        self._path_label.setObjectName("config_path")
        config_layout.addWidget(self._path_label)
        layout.addWidget(config_bar)

        layout.addWidget(self._create_separator())

        access_container = QtWidgets.QWidget()
        access_container.setObjectName("access_container")
        access_container_layout = QtWidgets.QHBoxLayout(access_container)
        access_container_layout.setContentsMargins(8, 4, 8, 6)
        access_container_layout.setSpacing(6)

        access_label = QtWidgets.QLabel("ACCESS")
        access_label.setObjectName("section_label")
        access_label.setAlignment(_ALIGN_LEFT | _ALIGN_VCENTER)
        access_container_layout.addWidget(access_label, 1)

        access_btns = QtWidgets.QWidget()
        access_btns_layout = QtWidgets.QHBoxLayout(access_btns)
        access_btns_layout.setContentsMargins(0, 0, 0, 0)
        access_btns_layout.setSpacing(6)

        login_btn = QtWidgets.QPushButton("Open Bria Login")
        login_btn.clicked.connect(lambda: self._open_url(DASHBOARD_LOGIN_URL))
        access_btns_layout.addWidget(login_btn)

        keys_btn = QtWidgets.QPushButton("Open Bria Console")
        keys_btn.clicked.connect(lambda: self._open_url(DASHBOARD_API_KEYS_URL))
        access_btns_layout.addWidget(keys_btn)

        access_container_layout.addWidget(access_btns, 0)
        layout.addWidget(access_container)

        layout.addWidget(self._create_separator())

        tabs = QtWidgets.QTabWidget()
        tabs.setDocumentMode(True)
        tabs.addTab(self._build_api_tokens_tab(), "API Keys")
        if self._include_project_paths:
            tabs.addTab(self._build_project_paths_tab(), "Project Paths")
        self._tabs = tabs
        layout.addWidget(tabs)

        self._message_label = QtWidgets.QLabel()
        self._message_label.setWordWrap(True)
        self._message_label.setStyleSheet("padding: 4px 8px;")
        self._message_label.hide()
        layout.addWidget(self._message_label)

    def _build_api_tokens_tab(self) -> QtWidgets.QWidget:
        holder = QtWidgets.QWidget()
        token_layout = QtWidgets.QVBoxLayout(holder)
        token_layout.setContentsMargins(8, 8, 8, 8)
        token_layout.setSpacing(6)

        for label, key_name in self._token_fields:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(6)
            row_label = QtWidgets.QLabel(f"{label} Key")
            row_label.setObjectName("row_label")
            row_label.setAlignment(_ALIGN_LEFT | _ALIGN_VCENTER)
            row_label.setFixedWidth(self._label_width)
            row.addWidget(row_label)

            token_input = QtWidgets.QLineEdit()
            token_input.setPlaceholderText("(not set)")
            token_input.setEchoMode(QtWidgets.QLineEdit.Password)
            token_input.textChanged.connect(self._on_token_changed)
            row.addWidget(token_input)

            show_btn = QtWidgets.QPushButton("👁")
            show_btn.setFixedWidth(32)
            show_btn.clicked.connect(lambda _=None, name=key_name: self._toggle_token_visibility(name))
            row.addWidget(show_btn)

            self._token_inputs[key_name] = token_input
            self._token_show_buttons[key_name] = show_btn

            token_layout.addLayout(row)

        actions_row = QtWidgets.QHBoxLayout()
        actions_row.setSpacing(6)
        actions_row.addStretch()

        self._clear_btn = QtWidgets.QPushButton("Clear All")
        self._clear_btn.setObjectName("btn_clear")
        self._clear_btn.clicked.connect(self._clear_all)
        actions_row.addWidget(self._clear_btn)

        self._save_btn = QtWidgets.QPushButton("Save All")
        self._save_btn.setObjectName("btn_save")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save_all)
        actions_row.addWidget(self._save_btn)

        token_layout.addLayout(actions_row)
        token_layout.addStretch()
        return holder

    def _build_project_paths_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._temp_folder = QtWidgets.QLineEdit()
        self._temp_folder.setReadOnly(True)
        self._temp_folder.setPlaceholderText("Temp folder")

        temp_row = QtWidgets.QHBoxLayout()
        temp_row.setSpacing(6)
        self._temp_label = QtWidgets.QLabel("Temp Folder :")
        self._temp_label.setObjectName("row_label")
        self._temp_label.setAlignment(_ALIGN_RIGHT | _ALIGN_VCENTER)
        self._temp_label.setFixedWidth(self._label_width)
        temp_row.addWidget(self._temp_label)
        temp_row.addWidget(self._temp_folder)
        layout.addLayout(temp_row)

        checkbox_row = QtWidgets.QHBoxLayout()
        checkbox_row.setSpacing(6)
        checkbox_row.addSpacing(self._label_width)

        self._use_project = QtWidgets.QCheckBox("Choose Global Project Path")
        self._use_project.stateChanged.connect(self._on_use_project_changed)
        checkbox_row.addWidget(self._use_project)
        checkbox_row.addStretch()
        layout.addLayout(checkbox_row)

        self._project_path = QtWidgets.QLineEdit()
        self._project_path.textChanged.connect(self._on_project_changed)

        project_row = QtWidgets.QHBoxLayout()
        project_row.setSpacing(6)
        project_label = QtWidgets.QLabel("Output Folder :")
        project_label.setObjectName("row_label")
        project_label.setAlignment(_ALIGN_RIGHT | _ALIGN_VCENTER)
        project_label.setFixedWidth(self._label_width)
        project_row.addWidget(project_label)
        project_row.addWidget(self._project_path)

        browse_btn = QtWidgets.QPushButton("...")
        browse_btn.setFixedWidth(30)
        browse_btn.clicked.connect(self._browse_project_path)
        self._browse_btn = browse_btn
        project_row.addWidget(browse_btn)

        set_btn = QtWidgets.QPushButton("Set")
        set_btn.clicked.connect(self._set_project_path_now)
        self._set_btn = set_btn
        project_row.addWidget(set_btn)

        layout.addLayout(project_row)
        layout.addStretch()
        self._sync_project_controls()
        return page

    def _create_separator(self) -> QtWidgets.QFrame:
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        line.setStyleSheet("background:#222; border:none;")
        line.setFixedHeight(1)
        return line

    def _load_current_state(self) -> None:
        try:
            data = read_dashboard_config(self._config_path)
            self._status_url.setText("")
            using_houdini_fallback = False
            if self._dcc == "nuke":
                using_houdini_fallback = bool(resolve_dashboard_token_value(data, "nuke_api_key")) and not bool(
                    str(data.get("nuke_api_key") or "").strip()
                )

            for _label, key_name in self._token_fields:
                token = resolve_dashboard_token_value(data, key_name)
                if token:
                    self._token_inputs[key_name].setText(_MASK)
                else:
                    self._token_inputs[key_name].setText("")

            if self._include_project_paths:
                temp_dir = ""
                if self._temp_dir_provider is not None:
                    try:
                        temp_dir = str(self._temp_dir_provider() or "")
                    except Exception:
                        temp_dir = ""
                self._temp_folder.setText(temp_dir)

                use_project = bool(data.get("use_bria_project_path") or False)
                self._use_project.blockSignals(True)
                self._use_project.setChecked(use_project)
                self._use_project.blockSignals(False)
                self._sync_project_controls()

                project_path = str(data.get("nuke_output_dir") or "").strip()
                if not project_path:
                    project_path = str(os.getenv("BRIA_PROJECT_PATH") or "").strip()
                self._project_path.setText(project_path)
                self._notify_project_path_changed(use_project=use_project, project_path=project_path)

            if has_any_dashboard_token(data, self._token_fields):
                self._update_status("connected", "Configured")
            elif using_houdini_fallback:
                self._update_status("connected", "Configured (using houdini_api_key fallback)")
            else:
                self._update_status("disconnected", "Not configured")
        except Exception as exc:
            self._update_status("error", f"Error: {exc}")

    def _save_all(self) -> None:
        try:
            to_save: dict[str, str] = {}
            for _label, key_name in self._token_fields:
                token_input = self._token_inputs.get(key_name)
                if token_input is None:
                    continue
                token = (token_input.text() or "").strip()
                if not token or token == _MASK:
                    continue
                to_save[key_name] = token

            if to_save:
                save_dashboard_tokens(to_save, self._config_path, dcc=self._dcc)
                for key_name in to_save:
                    token_input = self._token_inputs.get(key_name)
                    if token_input is not None:
                        token_input.setText(_MASK)

            if self._include_project_paths:
                data = read_dashboard_config(self._config_path)
                use_project = bool(self._use_project.isChecked())
                data["use_bria_project_path"] = use_project

                raw_project_path = (self._project_path.text() or "").strip()
                if raw_project_path:
                    normalized = self._validate_project_path(raw_project_path)
                    data["nuke_output_dir"] = normalized
                    self._project_path.setText(normalized)
                else:
                    data.pop("nuke_output_dir", None)

                write_dashboard_config(data, self._config_path)
                self._apply_project_path_env(use_project=use_project, project_path=(self._project_path.text() or "").strip())

            self._save_btn.setEnabled(False)
            self._load_current_state()
            self._show_message("Saved Bria dashboard settings.", error=False)
        except Exception as exc:
            self._show_message(f"Save failed: {exc}", error=True)

    def _set_project_path_now(self) -> None:
        if not self._include_project_paths:
            return
        try:
            data = read_dashboard_config(self._config_path)

            use_project = bool(self._use_project.isChecked())
            data["use_bria_project_path"] = use_project

            raw_project_path = (self._project_path.text() or "").strip()
            if raw_project_path:
                normalized = self._validate_project_path(raw_project_path)
                data["nuke_output_dir"] = normalized
                self._project_path.setText(normalized)
            else:
                data.pop("nuke_output_dir", None)

            write_dashboard_config(data, self._config_path)
            self._apply_project_path_env(use_project=use_project, project_path=(self._project_path.text() or "").strip())
            self._load_current_state()
            self._show_message("Project output settings applied.", error=False)
        except Exception as exc:
            self._show_message(f"Failed to apply project output settings: {exc}", error=True)

    def _clear_all(self) -> None:
        reply = QtWidgets.QMessageBox.question(
            self,
            "Clear Credentials",
            "Are you sure you want to remove stored Bria settings for this DCC?",
            _MSGBOX_YES | _MSGBOX_NO,
            _MSGBOX_NO,
        )
        if reply != _MSGBOX_YES:
            return

        try:
            clear_dashboard_tokens((key_name for _label, key_name in self._token_fields), self._config_path, dcc=self._dcc)

            data = read_dashboard_config(self._config_path)
            if self._include_project_paths:
                data.pop("nuke_output_dir", None)
                data.pop("use_bria_project_path", None)
                for key in _CONNECTION_KEYS:
                    data.pop(key, None)
            write_dashboard_config(data, self._config_path)

            for token_input in self._token_inputs.values():
                token_input.clear()

            if self._include_project_paths:
                self._use_project.blockSignals(True)
                self._use_project.setChecked(False)
                self._use_project.blockSignals(False)
                self._project_path.clear()
                self._apply_project_path_env(use_project=False, project_path="")

            self._load_current_state()
            self._show_message("Cleared stored Bria settings.", error=False)
        except Exception as exc:
            self._show_message(str(exc), error=True)

    def _apply_project_path_env(self, use_project: bool, project_path: str) -> None:
        os.environ["USE_BRIA_PROJECT_PATH"] = "1" if bool(use_project) else "0"
        normalized = (project_path or "").strip()
        if normalized:
            os.environ["BRIA_PROJECT_PATH"] = normalized
        else:
            os.environ.pop("BRIA_PROJECT_PATH", None)

        self._notify_project_path_changed(use_project=use_project, project_path=normalized)

    def _notify_project_path_changed(self, *, use_project: bool, project_path: str) -> None:
        if self._project_path_callback is None:
            return
        try:
            self._project_path_callback(bool(use_project), str(project_path or "").strip())
        except Exception:
            pass

    def _validate_project_path(self, raw_path: str) -> str:
        expanded = os.path.expandvars(os.path.expanduser(str(raw_path).strip()))
        if not expanded:
            raise ValueError("Project path is empty.")

        path = Path(expanded)
        if not path.is_absolute():
            raise ValueError("Project path must be an absolute path.")

        path.mkdir(parents=True, exist_ok=True)

        probe = path / ".bria_write_test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except Exception as exc:
            raise ValueError(f"Project path is not writable: {path} ({exc})") from exc

        return str(path)

    def _run_status_check(self, check_network: bool) -> None:
        try:
            status = get_status(dcc=self._dcc, check_network=check_network, timeout_s=3)
            endpoint = str(status.get("resolved_endpoint") or "")
            self._status_url.setText(endpoint)
            if status.get("ok"):
                mode_label = "Network OK" if check_network else "Config OK"
                detail = str(status.get("message") or "OK")
                self._update_status("connected", mode_label)
                if endpoint:
                    self._show_message(f"{mode_label} | {detail} | {endpoint}", error=False)
                else:
                    self._show_message(f"{mode_label} | {detail}", error=False)
            else:
                mode_label = "Network Warning" if check_network else "Config Warning"
                self._update_status("disconnected", mode_label)
                self._show_message(f"{mode_label} | {status.get('message')} ({status.get('reason')})", error=True)
        except Exception as exc:
            self._status_url.setText("")
            self._update_status("error", "Error")
            self._show_message(str(exc), error=True)

    def _open_url(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except Exception:
            self._show_message(f"Please visit {url}", error=False)

    def _show_message(self, message: str, *, error: bool = False) -> None:
        color = "#f44336" if error else "#4caf50"
        self._message_label.setStyleSheet(f"color: {color};")
        self._message_label.setText(message)
        self._message_label.show()

        if self._message_fn is not None:
            try:
                self._message_fn(message)
            except Exception:
                pass

        QtCore.QTimer.singleShot(5000, self._message_label.hide)

    def _update_status(self, status: str, message: str) -> None:
        colors = {
            "connected": "#4caf50",
            "disconnected": "#ff9800",
            "error": "#f44336",
        }
        names = {
            "connected": "status_ok",
            "disconnected": "status_warn",
            "error": "status_err",
        }
        color = colors.get(status, "#888")
        self._status_indicator.setStyleSheet(f"background-color: {color}; border-radius: 6px; border: none;")
        self._status_label.setObjectName(names.get(status, "status_warn"))
        self._status_label.setText(message)
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)

    def _toggle_token_visibility(self, key_name: str) -> None:
        token_input = self._token_inputs.get(key_name)
        show_btn = self._token_show_buttons.get(key_name)
        if token_input is None or show_btn is None:
            return

        showing = token_input.echoMode() == QtWidgets.QLineEdit.Normal
        if showing:
            token_input.setEchoMode(QtWidgets.QLineEdit.Password)
            show_btn.setText("👁")
        else:
            token_input.setEchoMode(QtWidgets.QLineEdit.Normal)
            show_btn.setText("🙈")

    def _browse_project_path(self) -> None:
        if not self._include_project_paths:
            return

        start = (self._project_path.text() or "").strip()
        if not start and self._temp_dir_provider is not None:
            try:
                start = str(self._temp_dir_provider() or "")
            except Exception:
                start = ""

        selected = None
        if self._folder_picker is not None:
            try:
                selected = self._folder_picker(start)
            except Exception:
                selected = None

        if not selected:
            selected = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Bria Project Folder", start or os.path.expanduser("~"))

        if selected:
            self._project_path.setText(str(selected))

    def _on_token_changed(self, _text: str) -> None:
        for token_input in self._token_inputs.values():
            val = (token_input.text() or "").strip()
            if val and val != _MASK:
                self._save_btn.setEnabled(True)
                return
        self._save_btn.setEnabled(False)

    def _on_project_changed(self, _value: Any = None) -> None:
        self._sync_project_controls()
        if self._save_btn is not None:
            self._save_btn.setEnabled(True)

    def _on_use_project_changed(self, _value: Any = None) -> None:
        self._sync_project_controls()

        if not self._include_project_paths:
            return

        try:
            data = read_dashboard_config(self._config_path)
            use_project = bool(self._use_project.isChecked())
            data["use_bria_project_path"] = use_project
            write_dashboard_config(data, self._config_path)
            self._apply_project_path_env(
                use_project=use_project,
                project_path=(self._project_path.text() or "").strip(),
            )
            self._show_message(
                "Global project path toggle applied.",
                error=False,
            )
        except Exception as exc:
            self._show_message(f"Failed to apply global project path toggle: {exc}", error=True)

    def _sync_project_controls(self) -> None:
        if not self._include_project_paths:
            return
        enabled = bool(self._use_project.isChecked())
        self._temp_folder.setEnabled(not enabled)
        if hasattr(self, "_temp_label"):
            self._temp_label.setEnabled(not enabled)
        self._project_path.setEnabled(enabled)
        if hasattr(self, "_browse_btn"):
            self._browse_btn.setEnabled(enabled)
        if hasattr(self, "_set_btn"):
            self._set_btn.setEnabled(enabled)
