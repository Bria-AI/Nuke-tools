"""Generation history UI and actions for Bria Nuke nodes.

Extracted from common.py for maintainability. All symbols are re-exported
from common.py for backward compatibility, so existing
``from common import append_generation_history`` imports still work.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nuke  # type: ignore

from node_utils import opt_parm_bool, opt_parm_str, resolve_temp_dir

# Pull shared helpers from common (safe: these are defined earlier in
# common.py before its trailing `from history import *` re-export).
from common import (
    _copy_text_to_clipboard,
    _read_json_knob_list,
    _reveal_path,
    _same_path,
    _set_enum_values,
    _selected_enum_index,
    _write_json_knob,
    as_nuke_path,
    ensure_parent_dir,
    get_internal_group_read_file,
    on_main_thread,
    set_internal_group_read_file,
    set_result_path,
    set_status,
    sync_preview,
)

# Imports added post-refactor (2026-04-21)
from presets import _PRESET_EXTENSION, _PRESET_FIELDS, _detect_preset_profile, _set_knob_from_preset  # noqa: E402
from common import (  # noqa: E402
    _build_generation_summary_text,
    _ensure_generation_metadata_ui,
    _prepare_generation_metadata_payload,
    _refresh_prompt_and_model_ui,
    _resolved_saved_path,
    _set_internal_group_read_file,
)


def set_history_debug(node: Any, message: str) -> None:
    if node is None:
        return

    text = str(message or "")[:500]
    try:
        knob = node.knob("history_debug")
        if knob is not None:
            knob.setValue(text)
    except Exception:
        pass

    try:
        debug_enabled = str(os.getenv("BRIA_NUKE_HISTORY_DEBUG") or "").strip().lower() in {"1", "true", "yes", "on"}
        if debug_enabled:
            name = node.name() if hasattr(node, "name") else "BriaNode"
            nuke.tprint(f"[Bria][History][{name}] {text}")
    except Exception:
        pass
def _history_supports_before_load(node: Any) -> bool:
    if node is None:
        return False

    try:
        node.begin()
        return nuke.toNode("loader_before") is not None
    except Exception:
        return False
    finally:
        try:
            node.end()
        except Exception:
            pass
def _sync_generation_history_target_ui(node: Any) -> None:
    if node is None:
        return

    try:
        knob = node.knob("history_load_target")
    except Exception:
        knob = None
    if knob is None:
        return

    options = ["Before", "After"] if _history_supports_before_load(node) else ["After"]

    try:
        current_value = str(knob.value() or "").strip()
    except Exception:
        current_value = ""

    _set_enum_values(knob, options)

    target_value = current_value if current_value in options else ("After" if "After" in options else options[0])
    try:
        knob.setValue(target_value)
    except Exception:
        try:
            knob.setValue(options.index(target_value))
        except Exception:
            pass
def _ensure_generation_history_ui(node: Any) -> None:
    if node is None:
        return

    try:
        required = (
            "history_tab",
            "history_help",
            "history_selected_entry",
            "history_count",
            "history_selected_summary",
            "history_selected_path",
            "history_load_selected_result",
            "history_reveal_selected_result",
            "history_view_selected_metadata",
            "history_copy_selected_metadata",
            "history_clear_entries",
            "generation_history_data",
        )
        if all(node.knob(name) is not None for name in required):
            return
    except Exception:
        pass

    try:
        if node.knob("history_tab") is None:
            node.addKnob(nuke.Tab_Knob("history_tab", "History"))
    except Exception:
        pass

    try:
        if node.knob("history_help") is None:
            node.addKnob(
                nuke.Text_Knob(
                    "history_help",
                    "",
                    "Recent results history saved in this node. Use the selector to load the desired available run.",
                )
            )
    except Exception:
        pass

    try:
        if node.knob("history_help_sep") is None:
            node.addKnob(nuke.Text_Knob("history_help_sep", "", ""))
    except Exception:
        pass

    try:
        if node.knob("history_selected_entry") is None:
            node.addKnob(nuke.Enumeration_Knob("history_selected_entry", "Selected Run", ["No history yet"]))
    except Exception:
        pass

    try:
        load_target_knob = node.knob("history_load_target")
        if load_target_knob is None:
            load_target_knob = nuke.Enumeration_Knob("history_load_target", "Load Selected Run As", ["Before", "After"])
            node.addKnob(load_target_knob)
            try:
                load_target_knob.setValue("After")
            except Exception:
                pass
        else:
            try:
                current_target = str(load_target_knob.value() or "").strip()
            except Exception:
                current_target = ""
            if current_target not in {"Before", "After"}:
                try:
                    load_target_knob.setValue("After")
                except Exception:
                    pass
    except Exception:
        pass

    try:
        count_knob = node.knob("history_count")
        if count_knob is None:
            count_knob = nuke.String_Knob("history_count", "History Count", "0")
            node.addKnob(count_knob)
        count_knob.setEnabled(False)
        count_knob.setVisible(False)
    except Exception:
        pass

    try:
        debug_knob = node.knob("history_debug")
        if debug_knob is None:
            debug_knob = nuke.String_Knob("history_debug", "History Debug", "ui ready")
            node.addKnob(debug_knob)
        debug_knob.setEnabled(False)
        debug_knob.setVisible(False)
    except Exception:
        pass

    try:
        summary_knob = node.knob("history_selected_summary")
        if summary_knob is None:
            summary_knob = nuke.String_Knob("history_selected_summary", "Selection", "No history yet")
            node.addKnob(summary_knob)
        summary_knob.setEnabled(False)
    except Exception:
        pass

    try:
        path_knob = node.knob("history_selected_path")
        if path_knob is None:
            path_knob = nuke.String_Knob("history_selected_path", "File Path", "")
            node.addKnob(path_knob)
        path_knob.setEnabled(False)
    except Exception:
        pass

    for name, label, script in (
        (
            "history_load_selected_result",
            "Load",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_load_history_result()",
        ),
        (
            "history_reveal_selected_result",
            "Preview File",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_reveal_history_result()",
        ),
        (
            "history_view_selected_metadata",
            "View Selected Metadata",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_view_history_metadata()",
        ),
        (
            "history_copy_selected_metadata",
            "Copy Selected Metadata",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_copy_history_metadata()",
        ),
        (
            "history_load_selected_metadata",
            "Load Selected Settings",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_load_history_metadata()",
        ),
        (
            "history_clear_entries",
            "Clear History",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_clear_generation_history()",
        ),
    ):
        try:
            btn = node.knob(name)
            if btn is None:
                btn = nuke.PyScript_Knob(name, label, script)
                node.addKnob(btn)
            btn.setFlag(nuke.STARTLINE)
        except Exception:
            pass

    try:
        legacy_compare_btn = node.knob("history_compare_selected_result")
        if legacy_compare_btn is not None:
            legacy_compare_btn.setVisible(False)
            legacy_compare_btn.setEnabled(False)
    except Exception:
        pass

    try:
        if node.knob("history_actions_sep") is None:
            node.addKnob(nuke.Text_Knob("history_actions_sep", "", ""))
    except Exception:
        pass

    try:
        load_target_knob = node.knob("history_load_target")
        if load_target_knob is not None:
            load_target_knob.setFlag(nuke.STARTLINE)
    except Exception:
        pass

    _sync_generation_history_target_ui(node)

    try:
        load_btn = node.knob("history_load_selected_result")
        if load_btn is not None:
            load_btn.clearFlag(nuke.STARTLINE)
    except Exception:
        pass

    for name in (
        "history_reveal_selected_result",
        "history_view_selected_metadata",
        "history_copy_selected_metadata",
        "history_load_selected_metadata",
        "history_clear_entries",
        "history_actions_sep",
    ):
        try:
            knob = node.knob(name)
            if knob is not None:
                knob.setFlag(nuke.STARTLINE)
        except Exception:
            pass

    try:
        data_knob = node.knob("generation_history_data")
        if data_knob is None:
            data_knob = nuke.Multiline_Eval_String_Knob("generation_history_data", "Generation History")
            node.addKnob(data_knob)
        data_knob.setVisible(False)
        data_knob.setEnabled(False)
    except Exception:
        pass

    _ensure_generation_metadata_ui(node)

    # SP nodes: rewire the "Load" button to load the prompt JSON into structured_prompt_output.
    # Runs every call so it survives .nk reloads.
    try:
        if node.knob("run_structured_prompt") is not None:
            load_btn = node.knob("history_load_selected_result")
            if load_btn is not None:
                load_btn.setCommand(
                    "import structured_prompt as _bria_sp;"
                    " _bria_sp.on_load_history_structured_prompt()"
                )
            meta_btn = node.knob("history_load_selected_metadata")
            if meta_btn is not None:
                meta_btn.setVisible(False)
                meta_btn.setEnabled(False)
    except Exception:
        pass
def ensure_generation_history_ui(node: Any) -> None:
    _ensure_generation_history_ui(node)


# ---------------------------------------------------------------------------
# Generation presets
# ---------------------------------------------------------------------------
def _history_prompt_excerpt(entry: dict[str, Any]) -> str:
    settings = entry.get("settings") if isinstance(entry.get("settings"), dict) else {}
    prompt = str(settings.get("prompt") or "").strip()
    if prompt:
        return re.sub(r"\s+", " ", prompt)[:60]
    if settings.get("structured_prompt"):
        result_path = str(entry.get("result_path") or "").strip()
        if result_path:
            return Path(result_path).name
        return "structured prompt"
    response_summary = str(entry.get("response_summary") or "").strip()
    if response_summary:
        return re.sub(r"\s+", " ", response_summary)[:60]
    result_path = str(entry.get("result_path") or "").strip()
    if result_path:
        return Path(result_path).name
    return "result"
def _history_entry_label(entry: dict[str, Any]) -> str:
    ts = str(entry.get("timestamp_utc") or "").strip()
    if ts:
        ts = ts.replace("T", " ").replace("+00:00", " UTC")[:19]
    op = str(entry.get("operation") or "run").strip()
    excerpt = _history_prompt_excerpt(entry)
    bits = [bit for bit in (ts, op, excerpt) if bit]
    label = " | ".join(bits)
    return label[:140] if label else "History entry"
def _history_timestamp_label(entry: dict[str, Any]) -> str:
    ts = str(entry.get("timestamp_utc") or "").strip()
    if not ts:
        return "Unknown time"

    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return parsed.strftime("%m/%d %H:%M")
    except Exception:
        pass

    cleaned = ts.replace("T", " ").replace("+00:00", "")
    return cleaned[:16] if cleaned else "Unknown time"
def _history_option_labels(entries: list[dict[str, Any]]) -> list[str]:
    if not entries:
        return ["No history yet"]

    labels: list[str] = []
    for idx, entry in enumerate(entries):
        stamp = _history_timestamp_label(entry)
        excerpt = _history_prompt_excerpt(entry)
        if idx == 0:
            label = f"Latest ({stamp}) - {excerpt}"
        else:
            label = f"{stamp} - {excerpt}"
        labels.append(label[:96])

    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1

    if all(total == 1 for total in counts.values()):
        return labels

    seen: dict[str, int] = {}
    unique_labels: list[str] = []
    for label in labels:
        total = counts[label]
        if total <= 1:
            unique_labels.append(label)
            continue

        occurrence = seen.get(label, 0) + 1
        seen[label] = occurrence
        suffix = f" [{occurrence}/{total}]"
        trimmed = label[: max(1, 96 - len(suffix))]
        unique_labels.append(f"{trimmed}{suffix}")

    return unique_labels
def _history_entries(node: Any) -> list[dict[str, Any]]:
    return _read_json_knob_list(node, "generation_history_data")
def _history_selected_entry(node: Any) -> dict[str, Any] | None:
    entries = _history_entries(node)
    if not entries:
        return None

    labels = _history_option_labels(entries)
    knob = None
    try:
        knob = node.knob("history_selected_entry")
    except Exception:
        knob = None

    idx = _selected_enum_index(knob, labels)
    if idx < 0 or idx >= len(entries):
        idx = 0
    return entries[idx]
def get_history_selected_entry(node: Any) -> dict[str, Any] | None:
    """Public accessor for the currently-selected history entry."""
    return _history_selected_entry(node)
def _apply_history_selection_ui(node: Any, entries: list[dict[str, Any]]) -> None:
    summary_text = "No history yet"
    selected: dict[str, Any] | None = None

    if entries:
        selected = _history_selected_entry(node) or entries[0]
        label_text = _history_entry_label(selected)
        metadata_text = _build_generation_summary_text(selected)
        summary_text = label_text
        if metadata_text and metadata_text != label_text:
            summary_text = f"{label_text} || {metadata_text}"
        result_path = _resolved_saved_path(str(selected.get("result_path") or "").strip())
        if result_path and not Path(result_path).exists():
            summary_text += " | missing on disk"

    try:
        summary_knob = node.knob("history_selected_summary")
        if summary_knob is not None:
            summary_knob.setValue(summary_text)
    except Exception:
        pass

    try:
        path_knob = node.knob("history_selected_path")
        if path_knob is not None:
            selected_path = str(selected.get("result_path") or "").strip() if selected else ""
            path_knob.setValue(selected_path)
    except Exception:
        pass

    try:
        count_knob = node.knob("history_count")
        if count_knob is not None:
            count_knob.setValue(str(len(entries)))
    except Exception:
        pass
def refresh_generation_history_ui(node: Any, preferred_index: int | None = None) -> None:
    if node is None:
        return

    _ensure_generation_history_ui(node)
    _sync_generation_history_target_ui(node)
    entries = _history_entries(node)
    labels = _history_option_labels(entries)
    set_history_debug(node, f"refresh history entries={len(entries)} labels={len(labels)}")

    entry_knob = None
    try:
        entry_knob = node.knob("history_selected_entry")
    except Exception:
        entry_knob = None

    if entry_knob is not None:
        try:
            previous_idx = _selected_enum_index(entry_knob, labels if entries else ["No history yet"])
        except Exception:
            previous_idx = 0
        _set_enum_values(entry_knob, labels)
        target_idx = preferred_index if preferred_index is not None else previous_idx
        if target_idx is None or target_idx < 0 or target_idx >= len(labels):
            target_idx = 0
        try:
            entry_knob.setValue(int(target_idx))
        except Exception:
            try:
                entry_knob.setValue(labels[int(target_idx)])
            except Exception:
                pass
    _apply_history_selection_ui(node, entries)
def update_generation_history_selection_ui(node: Any) -> None:
    if node is None:
        return

    _ensure_generation_history_ui(node)
    _sync_generation_history_target_ui(node)
    _apply_history_selection_ui(node, _history_entries(node))
def append_generation_history(node: Any, metadata: dict[str, Any], max_entries: int = 20) -> None:
    if node is None:
        return

    _ensure_generation_history_ui(node)
    entry = _prepare_generation_metadata_payload(metadata)
    entry["history_label"] = _history_entry_label(entry)
    set_history_debug(node, f"append start op={entry.get('operation')} path={Path(str(entry.get('result_path') or '')).name}")

    entries = _history_entries(node)
    set_history_debug(node, f"append read existing entries={len(entries)}")
    entries.insert(0, entry)
    entries = entries[: max(1, int(max_entries or 20))]
    _write_json_knob(node, "generation_history_data", entries)
    written_entries = _history_entries(node)
    set_history_debug(node, f"append readback entries={len(written_entries)}")
    refresh_generation_history_ui(node, preferred_index=0)
def on_view_history_metadata(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    entry = _history_selected_entry(node)
    if not entry:
        nuke.message("No history data available yet. Run the node first.")
        return

    nuke.message(json.dumps(entry, indent=2, ensure_ascii=False))
def on_copy_history_metadata(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    entry = _history_selected_entry(node)
    if not entry:
        nuke.message("No history data available yet. Run the node first.")
        return

    txt = json.dumps(entry, indent=2, ensure_ascii=False)
    if _copy_text_to_clipboard(txt):
        nuke.tprint("[Bria] Copied selected history metadata to clipboard")
    else:
        nuke.message("Could not access clipboard in this environment.")
def on_load_history_metadata(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    entry = _history_selected_entry(node)
    if not entry:
        nuke.message("No history data available yet. Run the node first.")
        return

    settings = entry.get("settings")
    if not isinstance(settings, dict) or not settings:
        nuke.message("Selected history entry has no settings to restore.")
        return

    profile = _detect_preset_profile(node)
    fields = _PRESET_FIELDS.get(profile or "", [])
    if not fields:
        nuke.message("Cannot restore settings: node type not recognised.")
        return

    # History stores some keys under different names than knobs.
    translated = dict(settings)

    # fibo nodes: "pipeline" was stored but the knob is named "model".
    if profile in ("fibo_generate", "fibo_edit"):
        if "pipeline" in translated and "model" not in translated:
            translated["model"] = translated.pop("pipeline")

    # structured_prompt node: "structured_prompt" was stored but the knob is "structured_prompt_input".
    if profile == "structured_prompt":
        if "structured_prompt" in translated and "structured_prompt_input" not in translated:
            translated["structured_prompt_input"] = translated.pop("structured_prompt")

    # Infer boolean toggles from presence of values when not explicitly stored.
    if "use_negative_prompt" not in translated:
        translated["use_negative_prompt"] = bool(translated.get("negative_prompt"))
    if "use_structured_prompt" not in translated:
        translated["use_structured_prompt"] = bool(translated.get("structured_prompt"))

    # Apply all fields, including nulls (reset to defaults for int/float knobs).
    applied: list[str] = []
    for knob_name in fields:
        if knob_name not in translated:
            continue
        value = translated[knob_name]
        # For nullable numeric fields, map None → 0 (Nuke default / "unset").
        if value is None and knob_name in ("seed", "steps_num", "guidance_scale", "tailored_model_influence"):
            value = 0
        if value is None:
            continue
        _set_knob_from_preset(node, knob_name, value)
        applied.append(knob_name)

    _refresh_prompt_and_model_ui(node)
    nuke.tprint(f"[Bria] Restored settings from history: {', '.join(applied)}")
def on_reveal_history_result(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    entry = _history_selected_entry(node)
    if not entry:
        nuke.message("No history result available yet. Run the node first.")
        return

    result_path = _resolved_saved_path(str(entry.get("result_path") or "").strip())
    if not result_path:
        nuke.message("Selected history entry has no saved result path.")
        return

    if not _reveal_path(result_path):
        nuke.message(f"Could not reveal path: {result_path}")
def on_load_history_result(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    entry = _history_selected_entry(node)
    if not entry:
        nuke.message("No history result available yet. Run the node first.")
        return

    result_path = _resolved_saved_path(str(entry.get("result_path") or "").strip())
    if not result_path:
        nuke.message("Selected history entry has no saved result path.")
        return

    load_target = "After"
    try:
        target_knob = node.knob("history_load_target")
        if target_knob is not None:
            load_target = str(target_knob.value() or "After").strip() or "After"
    except Exception:
        load_target = "After"

    if load_target == "Before" and _history_supports_before_load(node):
        _set_internal_group_read_file(node, "loader_before", result_path)
        try:
            compare_knob = node.knob("compare_source")
            if compare_knob is not None:
                compare_knob.setValue("Previous Edit")
        except Exception:
            pass
        try:
            node.begin()
            switch_compare = nuke.toNode("switch_compare_source")
            if switch_compare is not None and switch_compare.knob("which") is not None:
                switch_compare.knob("which").setValue(1)
        except Exception:
            pass
        finally:
            try:
                node.end()
            except Exception:
                pass

        current_path = ""
        try:
            result_knob = node.knob("result_path")
            if result_knob is not None:
                current_path = str(result_knob.value() or "")
        except Exception:
            current_path = ""

        current_output_view = "After"
        try:
            output_knob = node.knob("output_view")
            if output_knob is not None:
                current_output_view = str(output_knob.value() or "After")
        except Exception:
            current_output_view = "After"

        sync_preview(
            node=node,
            result_path=current_path,
            output_view=current_output_view,
            after_node_name="loader_result",
        )
    else:
        sync_preview(
            node=node,
            result_path=result_path,
            output_view="After",
            after_node_name="loader_result",
        )
        try:
            output_knob = node.knob("output_view")
            if output_knob is not None:
                output_knob.setValue("After")
        except Exception:
            pass

    refresh_generation_history_ui(node)


# TODO(DC1): This function is currently unreachable dead code. The only button
# that called it (history_compare_selected_result) is always hidden and disabled
# in _ensure_generation_history_ui. Options:
#   - Remove entirely, OR
#   - Scope to FIBO Edit / FIBO Generate only, where the internal loader_before
#     node exists and a Before-slot load actually makes sense.
def on_compare_history_result(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    _ensure_generation_history_ui(node)
    try:
        target_knob = node.knob("history_load_target")
        if target_knob is not None:
            target_knob.setValue("Before")
    except Exception:
        pass
    on_load_history_result(node)
def on_clear_generation_history(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    _write_json_knob(node, "generation_history_data", [])
    refresh_generation_history_ui(node, preferred_index=0)


