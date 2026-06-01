"""Shared helpers for Bria Nuke node modules.

Sections in this file:
    - Batch mode flag (set_batch_mode, is_batch_mode, report_run_error)
    - Prompt field height padding (ensure_prompt_field_heights)
    - Core helpers (as_nuke_path, set_result_path, set_status)
    - Generation metadata UI
    - Path management (_expand_env_tokens, resolve_output_path)
    - Clipboard + JSON knob helpers
    - Progress / threading
    - Prompt mode UI
    - Preview / sync
    - Export utilities (re-exported from export_utils)
    - Structured prompt helpers

Sections moved to sibling modules (2026-04-21 refactor):
    - Generation history UI and actions  → history.py
    - Generation presets save/load/catalog → presets.py

Both modules are re-exported at the bottom of this file via
``from history import *`` / ``from presets import *``, so existing imports like
``from common import append_generation_history`` continue to work.
"""

from __future__ import annotations

import base64
import os
import re
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nuke  # type: ignore

from node_utils import opt_parm_bool, resolve_temp_dir


# ---------------------------------------------------------------------------
# Batch mode flag
# ---------------------------------------------------------------------------
# When enabled (by the Sequence Output node during a batch render), Bria node
# run handlers should suppress modal nuke.message() popups and re-raise
# exceptions so the sequence output can collect and report errors in a final
# summary. Single-shot interactive runs leave this off and show dialogs as
# usual.

_BATCH_MODE = False


def set_batch_mode(enabled: bool) -> None:
    global _BATCH_MODE
    _BATCH_MODE = bool(enabled)


def is_batch_mode() -> bool:
    return _BATCH_MODE


def report_run_error(message: str) -> None:
    """Show a modal error to the user, or suppress it during batch mode.

    Bria node run handlers catch exceptions and typically display a
    nuke.message() dialog. During a Sequence Output render that would pop
    up once per failed frame, which is terrible UX. This helper replaces
    the direct nuke.message() call: in interactive mode it shows the
    dialog; in batch mode it does nothing (the original exception is
    re-raised by the caller so sequence_output can report it in the final
    summary).
    """
    if _BATCH_MODE:
        return
    try:
        nuke.message(message)
    except Exception:
        pass

# Re-export image export utilities from dedicated module.
from export_utils import (  # noqa: F401
    apply_mask_export_colorspace,
    apply_png_export_colorspace,
    export_input_png,
    export_mask_input_png,
    export_via_internal_save,
    restore_knobs,
    write_result_sidecar,
)


# ---------------------------------------------------------------------------
# Prompt field height padding
# ---------------------------------------------------------------------------
# Nuke does not persist Multiline_Eval_String_Knob display height in .nk
# files.  We re-apply trailing-newline padding on showPanel so prompt areas
# keep their intended height after script reload.
# Values are stripped by opt_parm_str() (node_utils.py) before API calls.

_PROMPT_HEIGHT_HINTS: dict[str, list[tuple[str, int]]] = {
    "fibo_edit": [("prompt", 2), ("negative_prompt", 2), ("structured_prompt", 2)],
    "fibo_edit_recipes": [("prompt", 2), ("negative_prompt", 2), ("structured_prompt", 2)],
    "fibo_generate": [("prompt", 5), ("negative_prompt", 5)],
    "sp": [("prompt", 3), ("structured_prompt_input", 3), ("structured_prompt_output", 5)],
}


def ensure_prompt_field_heights(node: Any, node_type: str) -> None:
    """Resize multiline prompt knob widgets on showPanel.

    Nuke does not persist Multiline_Eval_String_Knob display height in .nk
    files.  On showPanel we (1) re-pad values with trailing newlines and
    (2) use a deferred QTimer to resize the QTextEdit widgets via PySide
    after the panel layout completes.
    """
    hints = _PROMPT_HEIGHT_HINTS.get(node_type)
    if not hints:
        return

    # Ensure trailing newlines are present (for content sizing)
    for knob_name, min_newlines in hints:
        try:
            knob = node.knob(knob_name)
            if knob is None:
                continue
            val = str(knob.value() or "")
            trailing = len(val) - len(val.rstrip("\n"))
            if trailing < min_newlines:
                knob.setValue(val.rstrip("\n") + "\n" * min_newlines)
        except Exception:
            pass

    # Use PySide6/PySide2 + QTimer to resize text edits AFTER panel layout
    # completes.  showPanel fires before the panel is fully rendered, so we
    # defer the resize with a timer.
    try:
        try:
            from PySide6 import QtWidgets, QtCore  # type: ignore  # Nuke 17+
        except ImportError:
            from PySide2 import QtWidgets, QtCore  # type: ignore  # Nuke <17

        LINE_HEIGHT_PX = 20
        target_h = max(max(nl for _, nl in hints), 3) * LINE_HEIGHT_PX

        def _deferred_resize():
            try:
                app = QtWidgets.QApplication.instance()
                for w in app.allWidgets():
                    if not isinstance(w, (QtWidgets.QPlainTextEdit, QtWidgets.QTextEdit)):
                        continue
                    if not w.isVisible() or w.height() >= target_h:
                        continue
                    w.setMinimumHeight(target_h)
                    w.setFixedHeight(target_h)
                    sp = w.sizePolicy()
                    sp.setVerticalPolicy(QtWidgets.QSizePolicy.Policy.MinimumExpanding)
                    w.setSizePolicy(sp)
                    w.updateGeometry()
                    parent = w.parentWidget()
                    for _ in range(10):
                        if parent is None:
                            break
                        lay = parent.layout()
                        if lay is not None:
                            lay.invalidate()
                            lay.activate()
                        parent.updateGeometry()
                        parent.update()
                        parent = parent.parentWidget()
            except Exception:
                pass

        for delay in (50, 200, 500):
            QtCore.QTimer.singleShot(delay, _deferred_resize)
    except ImportError:
        pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def escape_for_eval_knob(text: Any) -> str:
    """Escape TCL-special characters so a `Multiline_Eval_String_Knob` stores
    them literally.

    Every multiline prompt knob in this codebase is a
    `Multiline_Eval_String_Knob`, which TCL-evaluates its content on `.value()`.
    Storing JSON (which contains `[`, `]`, `$`) directly causes TCL to
    interpret `[...]` as command substitution, truncating the value at the
    first `[` and emitting a "Syntax error" pseudo-message inside the field.

    Round trip: after ``knob.setValue(escape_for_eval_knob(text))``,
    ``knob.value()`` returns ``text`` unchanged because TCL eval un-escapes
    the backslash sequences. Order matters — backslash MUST be escaped first
    to avoid double-escaping the backslashes added for the other characters.
    """
    if text is None:
        return ""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("$", "\\$")
    )


def as_nuke_path(path: str) -> str:
    return str(path or "").replace("\\", "/")


def set_result_path(node: Any, path: str) -> None:
    try:
        knob = node.knob("result_path")
        if knob is not None:
            knob.setValue(as_nuke_path(path))
    except Exception:
        pass


def set_status(node: Any, status: str) -> None:
    try:
        knob = node.knob("status")
        if knob is not None:
            knob.setValue(str(status or ""))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Generation metadata UI
# ---------------------------------------------------------------------------

def _ensure_generation_metadata_ui(node: Any) -> None:
    if node is None:
        return

    try:
        required = (
            "generation_summary",
            "view_generation_data",
            "copy_generation_data",
            "generation_data",
        )
        if all(node.knob(name) is not None for name in required):
            return
    except Exception:
        pass

    try:
        if node.knob("generation_meta_sep") is None:
            node.addKnob(nuke.Text_Knob("generation_meta_sep", "", ""))
    except Exception:
        pass

    try:
        summary_knob = node.knob("generation_summary")
        if summary_knob is None:
            summary_knob = nuke.String_Knob("generation_summary", "Generation Summary", "")
            node.addKnob(summary_knob)
        summary_knob.setEnabled(False)
    except Exception:
        pass

    try:
        view_btn = node.knob("view_generation_data")
        if view_btn is None:
            view_btn = nuke.PyScript_Knob(
                "view_generation_data",
                "View Generation Data",
                "import common as _bria_common; _bria_common.on_view_generation_data()",
            )
            node.addKnob(view_btn)
        view_btn.setFlag(nuke.STARTLINE)
    except Exception:
        pass

    try:
        copy_btn = node.knob("copy_generation_data")
        if copy_btn is None:
            copy_btn = nuke.PyScript_Knob(
                "copy_generation_data",
                "Copy Generation Data",
                "import common as _bria_common; _bria_common.on_copy_generation_data()",
            )
            node.addKnob(copy_btn)
        copy_btn.setFlag(nuke.STARTLINE)
    except Exception:
        pass

    try:
        data_knob = node.knob("generation_data")
        if data_knob is None:
            data_knob = nuke.Multiline_Eval_String_Knob("generation_data", "Generation Data")
            node.addKnob(data_knob)
        data_knob.setVisible(False)
        data_knob.setEnabled(False)
    except Exception:
        pass


def _sanitize_generation_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, val in value.items():
            key_txt = str(key)
            lower = key_txt.lower()
            if any(token in lower for token in ("api_key", "token", "secret", "password", "authorization", "bearer")):
                out[key_txt] = "***"
            else:
                out[key_txt] = _sanitize_generation_metadata(val)
        return out
    if isinstance(value, (list, tuple)):
        return [_sanitize_generation_metadata(v) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    txt = str(value)
    if len(txt) > 8000:
        return txt[:8000] + "...<truncated>"
    return txt


def ensure_generation_metadata_ui(node: Any) -> None:
    _ensure_generation_metadata_ui(node)


# ---------------------------------------------------------------------------
# Generation history UI
# ---------------------------------------------------------------------------













def _default_temp_bria_dir(subdir: str) -> str:
    root = Path(resolve_temp_dir(nuke)) / "bria"
    path = root / str(subdir or "").strip()
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _default_output_dir(subdir: str) -> str:
    """Return the default output directory when no global project path or
    explicit ``result_path`` is set. Mirrors the priority chain in the Bria
    Houdini integration (``bria_houdini/node_utils.py:resolve_output_dir``):

    1. Current Nuke script directory + ``/bria_output/<subdir>/`` (analog of
       Houdini's ``$HIP/bria_output/``).
    2. ``~/Desktop/bria_nuke_tool_output/<subdir>/``.
    3. ``~/.bria/cache/<script_name>/<subdir>/`` (last resort if Desktop is
       absent — still persistent, never the OS temp directory).

    Used so saved Read-node references survive across days. Input staging
    still uses :func:`_default_temp_bria_dir` because inputs are ephemeral.
    """
    subdir_clean = str(subdir or "").strip()

    # 1. Script directory (Nuke equivalent of $HIP). Only when the .nk file
    #    has been saved at least once.
    try:
        script_path = str(nuke.root().knob("name").value() or "").strip()
    except Exception:
        script_path = ""
    if script_path:
        try:
            script_dir = Path(os.path.expandvars(os.path.expanduser(script_path))).parent
            if script_dir.is_dir():
                path = script_dir / "bria_output" / subdir_clean
                path.mkdir(parents=True, exist_ok=True)
                return str(path)
        except Exception:
            pass

    # 2. Desktop. ``XDG_DESKTOP_DIR`` on Linux distros that follow XDG,
    #    otherwise ``~/Desktop`` (present on macOS and Windows by default).
    desktop = os.environ.get("XDG_DESKTOP_DIR", "").strip()
    if not desktop or not os.path.isdir(desktop):
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        try:
            path = Path(desktop) / "bria_nuke_tool_output" / subdir_clean
            path.mkdir(parents=True, exist_ok=True)
            return str(path)
        except Exception:
            pass

    # 3. ``~/.bria/cache/<script>/`` — last resort if Desktop doesn't exist
    #    (unusual). Still persistent, never the OS temp dir.
    script_name = "untitled"
    if script_path:
        try:
            stem = Path(script_path).stem.strip()
            if stem:
                script_name = stem
        except Exception:
            pass
    path = Path.home() / ".bria" / "cache" / script_name / subdir_clean
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def resolve_input_temp_dir(prefix: str) -> str:
    """Return (and create) a Bria-namespaced temp subdirectory for ephemeral node input files."""
    return _default_temp_bria_dir(os.path.join("inputs", str(prefix or "node")))


def _normalize_compare_path(path_value: str) -> str:
    txt = _expand_env_tokens(str(path_value or "").strip())
    if not txt:
        return ""
    try:
        return os.path.normcase(os.path.normpath(str(Path(txt).expanduser())))
    except Exception:
        return os.path.normcase(os.path.normpath(txt))


def _same_path(left: str, right: str) -> bool:
    left_norm = _normalize_compare_path(left)
    right_norm = _normalize_compare_path(right)
    return bool(left_norm and right_norm and left_norm == right_norm)




def _tokenize_examples_path(path_value: str) -> str:
    txt = str(path_value or "").strip()
    if not txt:
        return ""

    try:
        examples_root = _expand_env_tokens("$BRIA_EXAMPLES")
    except Exception:
        examples_root = ""
    if not examples_root:
        return txt

    try:
        normalized_path = Path(txt).expanduser().resolve()
    except Exception:
        normalized_path = Path(txt).expanduser()

    try:
        normalized_examples = Path(examples_root).expanduser().resolve()
    except Exception:
        normalized_examples = Path(examples_root).expanduser()

    try:
        relative = normalized_path.relative_to(normalized_examples)
    except Exception:
        return txt

    return as_nuke_path(f"$BRIA_EXAMPLES/{relative.as_posix()}")


def _resolved_saved_path(path_value: str) -> str:
    return as_nuke_path(_expand_env_tokens(str(path_value or "").strip()))










def _extract_request_and_job_ids(response_data: Any) -> tuple[str | None, str | None, str | None]:
    request_id = None
    job_id = None
    summary = None

    def _walk(obj: Any) -> None:
        nonlocal request_id, job_id, summary
        if isinstance(obj, dict):
            for key, val in obj.items():
                k = str(key).lower()
                if request_id is None and k in {"request_id", "requestid", "x_request_id", "x-request-id"} and val:
                    request_id = str(val)
                if job_id is None and k in {"job_id", "jobid", "task_id", "status_id", "statusid"} and val:
                    job_id = str(val)
                if summary is None and k in {"message", "status", "detail", "result", "summary"} and val:
                    text = str(val)
                    if text and len(text) <= 300:
                        summary = text
                if isinstance(val, (dict, list)):
                    _walk(val)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(response_data)
    return request_id, job_id, summary


def _prepare_generation_metadata_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    payload = dict(metadata or {})
    payload.setdefault("timestamp_utc", datetime.now(timezone.utc).isoformat())

    try:
        result_path = str(payload.get("result_path") or "").strip()
        if result_path:
            payload["result_path"] = _tokenize_examples_path(result_path)
    except Exception:
        pass

    response_data = payload.pop("response", None)
    if response_data is not None:
        req_id, job_id, resp_summary = _extract_request_and_job_ids(response_data)
        if req_id and not payload.get("request_id"):
            payload["request_id"] = req_id
        if job_id and not payload.get("job_id"):
            payload["job_id"] = job_id
        if resp_summary and not payload.get("response_summary"):
            payload["response_summary"] = resp_summary

    if "response_summary" in payload:
        txt = str(payload.get("response_summary") or "")
        if len(txt) > 300:
            payload["response_summary"] = txt[:300] + "..."

    return _sanitize_generation_metadata(payload)


def _build_generation_summary_text(safe_payload: dict[str, Any]) -> str:
    op = str(safe_payload.get("operation") or "")
    path = str(safe_payload.get("result_path") or "")
    req_id = str(safe_payload.get("request_id") or "")
    job_id = str(safe_payload.get("job_id") or "")
    summary_bits = []
    if op:
        summary_bits.append(op)
    if path:
        summary_bits.append(Path(path).name)
    if req_id:
        summary_bits.append(f"req:{req_id[:8]}")
    if job_id:
        summary_bits.append(f"job:{job_id[:8]}")
    return " | ".join(summary_bits) if summary_bits else "metadata available"


def set_generation_metadata(node: Any, metadata: dict[str, Any]) -> None:
    if node is None:
        return

    _ensure_generation_metadata_ui(node)

    safe_payload = _prepare_generation_metadata_payload(metadata)
    text = json.dumps(safe_payload, indent=2, ensure_ascii=False)

    try:
        knob = node.knob("generation_data")
        if knob is not None:
            knob.setValue(text)
    except Exception:
        pass

    try:
        summary_text = _build_generation_summary_text(safe_payload)
        summary_knob = node.knob("generation_summary")
        if summary_knob is not None:
            summary_knob.setValue(summary_text)
    except Exception:
        pass

    # Write sidecar .json alongside the result image.
    try:
        result_path = safe_payload.get("result_path") or (metadata or {}).get("result_path")
        if result_path:
            write_result_sidecar(result_path, safe_payload)
    except Exception:
        pass


def on_view_generation_data(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    txt = ""
    try:
        knob = node.knob("generation_data")
        if knob is not None:
            txt = str(knob.value() or "")
    except Exception:
        txt = ""

    if not txt.strip():
        nuke.message("No generation data available yet. Run the node first.")
        return
    nuke.message(txt)


def _copy_text_to_clipboard(txt: str) -> bool:
    if not txt:
        return False

    try:
        try:
            from PySide6 import QtWidgets  # type: ignore  # Nuke 17+
        except Exception:
            from PySide2 import QtWidgets  # type: ignore

        app = QtWidgets.QApplication.instance()
        if app is not None and app.clipboard() is not None:
            app.clipboard().setText(txt)
            return True
    except Exception:
        pass

    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(txt)
        root.update()
        root.destroy()
        return True
    except Exception:
        pass

    try:
        if os.name == "nt":
            subprocess.run(["clip"], input=txt, text=True, encoding="utf-8", check=True, timeout=5)
            return True
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=txt, text=True, encoding="utf-8", check=True, timeout=5)
            return True
        if shutil.which("wl-copy"):
            subprocess.run(["wl-copy"], input=txt, text=True, encoding="utf-8", check=True, timeout=5)
            return True
        if shutil.which("xclip"):
            subprocess.run(["xclip", "-selection", "clipboard"], input=txt, text=True, encoding="utf-8", check=True, timeout=5)
            return True
        if shutil.which("xsel"):
            subprocess.run(["xsel", "--clipboard", "--input"], input=txt, text=True, encoding="utf-8", check=True, timeout=5)
            return True
    except Exception:
        pass

    return False


def on_copy_generation_data(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    txt = ""
    try:
        knob = node.knob("generation_data")
        if knob is not None:
            txt = str(knob.value() or "")
    except Exception:
        txt = ""

    if not txt.strip():
        nuke.message("No generation data available yet. Run the node first.")
        return

    if _copy_text_to_clipboard(txt):
        nuke.tprint("[Bria] Copied generation data to clipboard")
    else:
        nuke.message("Could not access clipboard in this environment.")


def _read_json_knob_list(node: Any, knob_name: str) -> list[dict[str, Any]]:
    if node is None or not knob_name:
        return []

    raw = ""
    try:
        knob = node.knob(knob_name)
        if knob is not None:
            raw = str(knob.value() or "")
    except Exception:
        raw = ""

    if not raw.strip():
        return []

    if knob_name == "generation_history_data":
        decoded_text = raw
        try:
            decoded_text = base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")
        except Exception:
            decoded_text = raw
        raw = decoded_text

    try:
        data = json.loads(raw)
    except Exception:
        set_history_debug(node, f"read_json parse failed for {knob_name}: {raw[:120]}")
        return []

    if not isinstance(data, list):
        set_history_debug(node, f"read_json expected list in {knob_name}, got {type(data).__name__}")
        return []

    return [entry for entry in data if isinstance(entry, dict)]


def _write_json_knob(node: Any, knob_name: str, value: Any) -> None:
    if node is None or not knob_name:
        return
    try:
        knob = node.knob(knob_name)
        if knob is not None:
            if knob_name == "generation_history_data":
                payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
                text = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
            else:
                text = json.dumps(value, indent=2, ensure_ascii=False)
            knob.setValue(text)
            if knob_name == "generation_history_data":
                set_history_debug(node, f"history json write ok chars={len(text)}")
        elif knob_name == "generation_history_data":
            set_history_debug(node, "history json write skipped: knob missing")
    except Exception:
        if knob_name == "generation_history_data":
            set_history_debug(node, f"history json write failed: {sys.exc_info()[1]}")










def _set_enum_values(knob: Any, values: list[str]) -> None:
    if knob is None:
        return

    def _quote_enum_value(value: str) -> str:
        txt = str(value or "")
        txt = txt.replace("\\", "\\\\")
        txt = txt.replace("{", "\\{").replace("}", "\\}")
        txt = txt.replace(" ", "\\ ")
        return txt

    try:
        knob.setValues(values)
        return
    except Exception:
        pass
    try:
        knob.fromScript(" ".join(_quote_enum_value(value) for value in values))
    except Exception:
        pass


def _selected_enum_index(knob: Any, labels: list[str]) -> int:
    if knob is None or not labels:
        return 0

    try:
        raw = knob.value()
    except Exception:
        raw = None

    try:
        if isinstance(raw, (int, float)):
            idx = int(raw)
            if 0 <= idx < len(labels):
                return idx
    except Exception:
        pass

    txt = str(raw or "").strip()
    if txt.isdigit():
        idx = int(txt)
        if 0 <= idx < len(labels):
            return idx

    try:
        values = list(knob.values())
    except Exception:
        values = labels

    if txt:
        for idx, value in enumerate(values):
            if str(value) == txt:
                return idx
        for idx, label in enumerate(labels):
            if label == txt:
                return idx

    return 0
















def get_internal_group_read_file(node: Any, read_name: str) -> str:
    if node is None or not read_name:
        return ""
    try:
        node.begin()
        rd = nuke.toNode(read_name)
        if rd is None or rd.knob("file") is None:
            return ""
        return str(rd.knob("file").value() or "")
    except Exception:
        return ""
    finally:
        try:
            node.end()
        except Exception:
            pass


def set_internal_group_read_file(node: Any, read_name: str, path_value: str) -> None:
    if node is None or not read_name:
        return
    try:
        node.begin()
        rd = nuke.toNode(read_name)
        if rd is not None and rd.knob("file") is not None:
            rd.knob("file").setValue(as_nuke_path(path_value or ""))
            try:
                force_validate = getattr(rd, "forceValidate", None)
                if callable(force_validate):
                    force_validate()
            except Exception:
                pass
    except Exception:
        pass
    finally:
        try:
            node.end()
        except Exception:
            pass


def _set_internal_group_read_file(node: Any, read_name: str, path_value: str) -> None:
    set_internal_group_read_file(node, read_name, _resolved_saved_path(path_value))


def _reveal_path(path_value: str) -> bool:
    txt = _resolved_saved_path(path_value)
    if not txt:
        return False

    try:
        path = Path(txt).expanduser().resolve()
    except Exception:
        path = Path(txt).expanduser()

    target = path if path.exists() else path.parent
    if not target:
        return False

    try:
        if os.name == "nt":
            if path.exists() and path.is_file() and hasattr(os, "startfile"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.run(["explorer", str(target)], check=False, timeout=10)
            return True
        if sys.platform == "darwin":
            subprocess.run(["open", str(path if path.exists() and path.is_file() else target)], check=False, timeout=10)
            return True
        opener = shutil.which("xdg-open")
        if opener:
            subprocess.run([opener, str(path if path.exists() and path.is_file() else target)], check=False, timeout=10)
            return True
    except Exception:
        return False

    return False




















def _refresh_prompt_and_model_ui(node: Any) -> None:
    if node is None:
        return

    try:
        neg_knob = node.knob("negative_prompt")
        neg_enabled = bool(node.knob("use_negative_prompt").value()) if node.knob("use_negative_prompt") is not None else False
        if neg_knob is not None:
            neg_knob.setVisible(neg_enabled)
    except Exception:
        pass

    try:
        use_structured = bool(node.knob("use_structured_prompt").value()) if node.knob("use_structured_prompt") is not None else False
    except Exception:
        use_structured = False

    for knob_name, visible, enabled in (
        ("prompt", not use_structured, not use_structured),
        ("structured_prompt", use_structured, use_structured),
    ):
        try:
            knob = node.knob(knob_name)
            if knob is not None:
                knob.setVisible(visible)
                knob.setEnabled(enabled)
        except Exception:
            pass

    try:
        load_knob = node.knob("load_structured_prompt")
        if load_knob is not None:
            load_knob.setEnabled(use_structured)
    except Exception:
        pass

    try:
        model_knob = node.knob("model")
        model_value = str(model_knob.value() or "").strip().lower() if model_knob is not None else ""
        tailored = model_value == "tailored"
        for knob_name in ("tailored_model_id", "tailored_model_influence"):
            knob = node.knob(knob_name)
            if knob is not None:
                knob.setVisible(tailored)
    except Exception:
        pass



















































def on_main_thread(fn: Any) -> None:
    # During a Sequence Output batch render we are *already* on the main
    # thread (the Render button's PyScript handler runs there) and we need
    # success-UI updates (knob writes, history append, downstream notify)
    # to complete *synchronously* before the next chain node cooks. The
    # default `executeInMainThread` path is async — it queues the callable
    # and returns immediately, so the next node's read of e.g. an SP node's
    # `structured_prompt_output` knob would see stale data. Bypass the
    # queue when batch mode is active and let exceptions propagate so
    # sequence_output's per-frame error catcher reports them.
    if _BATCH_MODE:
        fn()
        return
    try:
        exec_main = getattr(nuke, "executeInMainThread", None)
        if callable(exec_main):
            exec_main(fn)
            return
    except Exception:
        pass
    fn()


def create_progress_task(title: str) -> Any:
    try:
        return nuke.ProgressTask(str(title or "Bria"))
    except Exception:
        return None


def update_progress(task: Any, message: str | None = None, percent: int | None = None) -> None:
    if task is None:
        return
    is_cancelled = getattr(task, "isCancelled", None)
    if callable(is_cancelled) and is_cancelled():
        raise RuntimeError("Cancelled by user.")
    if message is not None:
        try:
            task.setMessage(str(message))
        except Exception:
            pass
    if percent is not None:
        try:
            task.setProgress(int(percent))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Prompt mode UI
# ---------------------------------------------------------------------------

def sync_prompt_mode_ui(node: Any) -> None:
    use_structured = bool(opt_parm_bool(node, "use_structured_prompt"))

    prompt_knob = node.knob("prompt")
    if prompt_knob is not None:
        try:
            prompt_knob.setVisible(not use_structured)
        except Exception:
            pass
        try:
            prompt_knob.setEnabled(not use_structured)
        except Exception:
            pass

    structured_knob = node.knob("structured_prompt")
    if structured_knob is not None:
        try:
            structured_knob.setVisible(use_structured)
        except Exception:
            pass
        try:
            structured_knob.setEnabled(use_structured)
        except Exception:
            pass

    load_knob = node.knob("load_structured_prompt")
    if load_knob is not None:
        try:
            load_knob.setEnabled(use_structured)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Preview / sync
# ---------------------------------------------------------------------------

def sync_preview(
    node: Any,
    result_path: str,
    output_view: str,
    after_node_name: str = "loader_result",
    switch_node_names: tuple[str, ...] = ("switch_before_after", "switch_result"),
) -> None:
    if node is None:
        return

    path_value = as_nuke_path(_expand_env_tokens(result_path or ""))
    out_view = (output_view or "Before").strip().lower()

    def _refresh_group_thumbnail(group_node: Any) -> None:
        if group_node is None:
            return

        try:
            postage = group_node.knob("postage_stamp")
            if postage is not None:
                try:
                    current = bool(postage.value())
                except Exception:
                    current = True
                try:
                    postage.setValue(not current)
                    postage.setValue(current)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            force_validate = getattr(group_node, "forceValidate", None)
            if callable(force_validate):
                force_validate()
        except Exception:
            pass

        try:
            update_ui = getattr(nuke, "updateUI", None)
            if callable(update_ui):
                update_ui()
        except Exception:
            pass

    try:
        node.begin()
        after = nuke.toNode(after_node_name)

        def _remove_cached_file(file_path: str) -> None:
            if not file_path:
                return
            try:
                remove_cached = getattr(nuke, "removeCachedFile", None)
                if callable(remove_cached):
                    remove_cached(file_path)
            except Exception:
                pass

        def _refresh_read(read_node: Any) -> None:
            if read_node is None:
                return
            try:
                force_validate = getattr(read_node, "forceValidate", None)
                if callable(force_validate):
                    force_validate()
            except Exception:
                pass

        switch = None
        for switch_name in switch_node_names:
            switch = nuke.toNode(switch_name)
            if switch is not None:
                break

        if after is not None and after.knob("file") is not None:
            file_knob = after.knob("file")
            try:
                current_path = as_nuke_path(file_knob.value())
            except Exception:
                current_path = ""

            _remove_cached_file(current_path)
            if current_path == path_value:
                try:
                    file_knob.setValue("")
                except Exception:
                    pass
                _refresh_read(after)

            file_knob.setValue(path_value)
            _remove_cached_file(path_value)
            _refresh_read(after)

        if switch is not None and switch.knob("which") is not None:
            target = 1 if out_view in {"after", "1"} else 0
            try:
                current = int(switch.knob("which").value())
            except Exception:
                current = target
            if current == target:
                try:
                    switch.knob("which").setValue(1 - target)
                except Exception:
                    pass
            switch.knob("which").setValue(target)
    finally:
        try:
            node.end()
        except Exception:
            pass

    _refresh_group_thumbnail(node)






# ---------------------------------------------------------------------------
# Structured prompt helpers
# ---------------------------------------------------------------------------

def load_text_file_into_knob(
    node: Any,
    target_knob_name: str,
    enable_knob_name: str | None = None,
    dialog_title: str = "Load JSON / VGL",
    file_pattern: str = "*.json *.vgl *.txt",
    start_dir: str | None = None,
) -> bool:
    if node is None or not target_knob_name:
        return False

    browse_start = str(start_dir or "").strip() or _default_structured_prompt_browse_dir()

    try:
        if browse_start:
            file_path = nuke.getFilename(dialog_title, file_pattern, as_nuke_path(browse_start))
        else:
            file_path = nuke.getFilename(dialog_title, file_pattern)
    except TypeError:
        try:
            file_path = nuke.getFilename(dialog_title, file_pattern)
        except Exception:
            file_path = None
    except Exception:
        file_path = None

    if not file_path:
        return False

    path = Path(str(file_path)).expanduser()
    content = None
    last_exc: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            content = path.read_text(encoding=encoding)
            break
        except Exception as exc:
            last_exc = exc

    if content is None:
        raise RuntimeError(f"Failed to read file: {path} ({last_exc})")

    target_knob = node.knob(target_knob_name)
    if target_knob is None:
        raise RuntimeError(f"Missing knob: {target_knob_name}")

    target_knob.setValue(content)

    if enable_knob_name:
        enable_knob = node.knob(enable_knob_name)
        if enable_knob is not None:
            try:
                enable_knob.setValue(True)
            except Exception:
                pass

    try:
        target_knob.setVisible(True)
    except Exception:
        pass

    return True


def on_load_structured_prompt(node: Any = None, title: str = "Load Structured Prompt JSON / VGL") -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    try:
        loaded = load_text_file_into_knob(
            node=node,
            target_knob_name="structured_prompt",
            enable_knob_name="use_structured_prompt",
            dialog_title=title,
            start_dir="",
        )
        if loaded:
            sync_prompt_mode_ui(node)
            nuke.tprint(f"[Bria] Loaded structured prompt from file into {node.name()}")
    except Exception as exc:
        nuke.message(f"Failed to load structured prompt file: {exc}")


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _default_structured_prompt_browse_dir() -> str:
    try:
        ex = _expand_env_tokens("$BRIA_EXAMPLES")
        if ex:
            prompts = Path(ex).expanduser() / "prompts"
            if prompts.exists():
                return str(prompts)
            return str(Path(ex).expanduser())
    except Exception:
        pass

    return ""


def _expand_env_tokens(path_value: str) -> str:
    txt = str(path_value or "").strip()
    if not txt:
        return ""

    def _env_value(key: str) -> str | None:
        val = os.getenv(key)
        if val:
            return val
        if key == "BRIA_EXAMPLES":
            root = os.getenv("BRIA_EXTENSION_ROOT") or os.getenv("NUKE_BRIA")
            candidates: list[Path] = []

            if root:
                root_path = Path(root).expanduser()
                candidates.append(root_path / "examples")
                candidates.append(root_path.parent / "examples")

            try:
                # Fallback to workspace-relative layout from this module:
                # <repo>/nuke/nodes/common.py -> parents[2] == <repo>
                repo_root = Path(__file__).resolve().parents[2]
                candidates.append(repo_root / "examples")
            except Exception:
                pass

            for candidate in candidates:
                try:
                    resolved = candidate.resolve()
                    if resolved.exists():
                        return str(resolved)
                except Exception:
                    if candidate.exists():
                        return str(candidate)

            # Last resort: return the first plausible candidate even if not present yet.
            if candidates:
                return str(candidates[0])
        return None

    txt = os.path.expanduser(txt)
    txt = os.path.expandvars(txt)

    # Support Nuke/Tcl style tokens such as: [getenv BRIA_EXAMPLES]/images/foo.png
    tcl_env_pattern = re.compile(r"\[getenv\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\]")

    def _replace_tcl(match: re.Match[str]) -> str:
        key = match.group("name")
        if not key:
            return match.group(0)
        resolved = _env_value(key)
        return resolved if resolved else match.group(0)

    txt = tcl_env_pattern.sub(_replace_tcl, txt)

    pattern = re.compile(r"\$(\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))")

    def _replace(match: re.Match[str]) -> str:
        key = match.group("braced") or match.group("plain")
        if not key:
            return match.group(0)
        resolved = _env_value(key)
        return resolved if resolved else match.group(0)

    return pattern.sub(_replace, txt)


def _ensure_parent_dir(path_value: str) -> None:
    parent = Path(path_value).expanduser().parent
    parent.mkdir(parents=True, exist_ok=True)


def ensure_parent_dir(path_value: str) -> None:
    _ensure_parent_dir(path_value)


def resolve_output_path(node: Any, cfg: Any, run_id: str, result_prefix: str) -> str:
    file_name = f"bria_{result_prefix}_result_{run_id}.png"
    image_subdir = str(result_prefix or "image").strip() or "image"

    use_global_knob = False
    try:
        use_global_knob = bool(node.knob("use_global_project_path").value())
    except Exception:
        use_global_knob = False

    # Per-node knob is the source of truth. The global default already
    # consulted env/cfg at knob-init time (see project_path_defaults.py);
    # re-checking here produced a silent fallback to OS-cleaned temp when
    # env/cfg were unset, which broke non-destructive Read-node persistence.
    if use_global_knob:
        global_dir = _expand_env_tokens(
            os.getenv("BRIA_PROJECT_PATH") or getattr(cfg, "nuke_output_dir", "") or ""
        )
        if global_dir:
            p = Path(global_dir) / "images" / image_subdir
            p.mkdir(parents=True, exist_ok=True)
            return str(p / file_name)
        try:
            node_name = node.name()
        except Exception:
            node_name = "<unknown>"
        try:
            nuke.tprint(
                f"[Bria] '{node_name}' has use_global_project_path=ON but no "
                f"BRIA_PROJECT_PATH env or cfg.nuke_output_dir is set — falling "
                f"back to script-dir / Desktop default"
            )
        except Exception:
            pass

    explicit_path = ""
    try:
        k = node.knob("result_path")
        explicit_path = _expand_env_tokens(k.value() if k is not None else "")
    except Exception:
        explicit_path = ""

    if explicit_path:
        ext = Path(explicit_path).suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            # Use the same directory but with a fresh run_id filename so each
            # run produces a distinct file rather than overwriting the previous one.
            explicit_dir = Path(explicit_path).parent
            _ensure_parent_dir(str(explicit_dir / file_name))
            return str(explicit_dir / file_name)

        explicit_dir = Path(explicit_path)
        explicit_dir.mkdir(parents=True, exist_ok=True)
        return str(explicit_dir / file_name)

    cache_dir = _default_output_dir(os.path.join("images", image_subdir))
    out_path = os.path.join(cache_dir, file_name)
    _ensure_parent_dir(out_path)
    return out_path


# ---------------------------------------------------------------------------
# Backward-compatibility re-exports
# ---------------------------------------------------------------------------
# History UI and actions moved to history.py (2026-04-21).
# Preset save/load/catalog moved to presets.py (2026-04-21).
# Existing ``from common import <symbol>`` imports continue to work via these
# star re-exports. The imports happen AFTER all common.py definitions so
# history.py and presets.py can safely ``from common import <helper>``.

from history import *  # noqa: E402, F401, F403
from presets import *  # noqa: E402, F401, F403
