"""Generation preset save/load/catalog for Bria Nuke nodes.

Extracted from common.py for maintainability. All symbols are re-exported
from common.py for backward compatibility, so existing
``from common import on_save_generation_preset`` imports still work.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nuke  # type: ignore

from node_utils import opt_parm_bool, opt_parm_str, resolve_temp_dir

# Imports from common.py. Safe against circular load because this module is
# only imported at the *end* of common.py (after all common defs complete).
from common import (
    _default_temp_bria_dir,
    _expand_env_tokens,
    _refresh_prompt_and_model_ui,
    _resolved_saved_path,
    _same_path,
    _sanitize_generation_metadata,
    _set_enum_values,
    _tokenize_examples_path,
    _write_json_knob,
    as_nuke_path,
    ensure_parent_dir,
    ensure_parent_dir as _ensure_parent_dir,
    on_main_thread,
    set_result_path,
    set_status,
)


# ---------------------------------------------------------------------------
# Preset field metadata (drives save/load — which knobs are captured per profile)
# ---------------------------------------------------------------------------

_PRESET_FIELDS: dict[str, list[str]] = {
    "fibo_generate": [
        "preserve_alpha",
        "aspect_ratio",
        "resolution",
        "model",
        "seed",
        "guidance_scale",
        "steps_num",
        "prompt",
        "use_negative_prompt",
        "negative_prompt",
        "use_structured_prompt",
        "structured_prompt",
        "tailored_model_id",
        "tailored_model_influence",
    ],
    "fibo_edit": [
        "seed",
        "guidance_scale",
        "steps_num",
        "prompt",
        "use_negative_prompt",
        "negative_prompt",
        "use_structured_prompt",
        "structured_prompt",
        "mask_type",
        "compare_source",
    ],
    "structured_prompt": [
        "pipeline",
        "prompt",
        "use_input_image",
        "use_structured_prompt_input",
        "structured_prompt_input",
        "use_custom_label",
        "output_label",
    ],
}
_PRESET_EXTENSION = ".preset"


def _ensure_generation_preset_ui(node: Any) -> None:
    if node is None:
        return

    try:
        required = (
            "presets_tab",
            "preset_name",
            "save_generation_preset",
            "preset_selected",
            "load_generation_preset",
            "refresh_preset_list",
            "load_presets_from_examples",
            "preset_path",
            "preset_summary",
            "preset_details",
            "generation_preset_data",
        )
        if all(node.knob(name) is not None for name in required):
            return
    except Exception:
        pass

    try:
        if node.knob("presets_tab") is None:
            node.addKnob(nuke.Tab_Knob("presets_tab", "Presets"))
    except Exception:
        pass

    try:
        name_knob = node.knob("preset_name")
        if name_knob is None:
            name_knob = nuke.String_Knob("preset_name", "Preset Name", "")
            node.addKnob(name_knob)
    except Exception:
        pass

    try:
        save_btn = node.knob("save_generation_preset")
        if save_btn is None:
            save_btn = nuke.PyScript_Knob(
                "save_generation_preset",
                "Save Preset",
                "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_save_generation_preset()",
            )
            node.addKnob(save_btn)
        try:
            save_btn.clearFlag(nuke.STARTLINE)
        except Exception:
            pass
    except Exception:
        pass

    try:
        if node.knob("preset_name_sep") is None:
            node.addKnob(nuke.Text_Knob("preset_name_sep", "", ""))
    except Exception:
        pass

    try:
        selected_knob = node.knob("preset_selected")
        if selected_knob is None:
            selected_knob = nuke.Enumeration_Knob("preset_selected", "Preset", ["Select"])
            node.addKnob(selected_knob)
        try:
            selected_knob.setLabel("Preset")
        except Exception:
            pass
    except Exception:
        pass

    try:
        load_btn = node.knob("load_generation_preset")
        if load_btn is None:
            load_btn = nuke.PyScript_Knob(
                "load_generation_preset",
                "Load Preset",
                "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_load_generation_preset()",
            )
            node.addKnob(load_btn)
        load_btn.setFlag(nuke.STARTLINE)
    except Exception:
        pass

    try:
        refresh_btn = node.knob("refresh_preset_list")
        if refresh_btn is None:
            refresh_btn = nuke.PyScript_Knob(
                "refresh_preset_list",
                "Refresh Presets",
                "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_refresh_generation_presets()",
            )
            node.addKnob(refresh_btn)
        try:
            refresh_btn.clearFlag(nuke.STARTLINE)
        except Exception:
            pass
    except Exception:
        pass

    try:
        example_toggle = node.knob("load_presets_from_examples")
        if example_toggle is None:
            example_toggle = nuke.Boolean_Knob("load_presets_from_examples", "Load from Bria Examples")
            node.addKnob(example_toggle)
        example_toggle.setFlag(nuke.STARTLINE)
        example_toggle.setTooltip(
            "Include shipped presets from $BRIA_EXAMPLES/presets in the preset list. Saving still uses the normal preset path."
        )
    except Exception:
        pass

    try:
        legacy_help = node.knob("preset_help")
        if legacy_help is not None:
            legacy_help.setVisible(False)
            legacy_help.setEnabled(False)
    except Exception:
        pass

    try:
        if node.knob("preset_actions_sep") is None:
            node.addKnob(nuke.Text_Knob("preset_actions_sep", "", ""))
    except Exception:
        pass

    try:
        if node.knob("preset_path") is None:
            node.addKnob(nuke.File_Knob("preset_path", "Preset Path"))
    except Exception:
        pass

    try:
        auto_knob = node.knob("preset_path_auto_value")
        if auto_knob is None:
            auto_knob = nuke.String_Knob("preset_path_auto_value", "Preset Path Auto", "")
            node.addKnob(auto_knob)
        auto_knob.setVisible(False)
        auto_knob.setEnabled(False)
    except Exception:
        pass

    try:
        summary_knob = node.knob("preset_summary")
        if summary_knob is None:
            summary_knob = nuke.String_Knob("preset_summary", "Preset Summary", "No preset loaded")
            node.addKnob(summary_knob)
        summary_knob.setEnabled(False)
    except Exception:
        pass

    try:
        details_knob = node.knob("preset_details")
        if details_knob is None:
            details_knob = nuke.Multiline_Eval_String_Knob("preset_details", "Preset Details")
            node.addKnob(details_knob)
        details_knob.setEnabled(False)
    except Exception:
        pass

    try:
        if node.knob("preset_help_info") is None:
            node.addKnob(
                nuke.Text_Knob(
                    "preset_help_info",
                    "Presets",
                    "Reusable node settings saved as JSON presets. Runtime result paths and generation metadata are not included. Optionally include shipped Bria example presets with the toggle above.",
                )
            )
    except Exception:
        pass

    _apply_default_preset_path(node)

    try:
        data_knob = node.knob("generation_preset_data")
        if data_knob is None:
            data_knob = nuke.Multiline_Eval_String_Knob("generation_preset_data", "Preset Data")
            node.addKnob(data_knob)
        data_knob.setVisible(False)
        data_knob.setEnabled(False)
    except Exception:
        pass
def ensure_generation_preset_ui(node: Any) -> None:
    _ensure_generation_preset_ui(node)


# ---------------------------------------------------------------------------
# Path management
# ---------------------------------------------------------------------------
def _resolved_default_preset_dir_for_mode(use_global: bool) -> str:
    if use_global:
        project_root = _expand_env_tokens(os.getenv("BRIA_PROJECT_PATH") or "")
        if project_root:
            preset_dir = Path(project_root) / "presets"
            preset_dir.mkdir(parents=True, exist_ok=True)
            return str(preset_dir)

    return _default_temp_bria_dir("presets")
def _resolved_default_preset_dir(node: Any) -> str:
    use_global = False
    try:
        knob = node.knob("use_global_project_path") if node is not None else None
        use_global = bool(knob.value()) if knob is not None else False
    except Exception:
        use_global = False

    return _resolved_default_preset_dir_for_mode(use_global)
def _get_preset_path_auto_value(node: Any) -> str:
    if node is None:
        return ""
    try:
        knob = node.knob("preset_path_auto_value")
        if knob is not None:
            return str(knob.value() or "")
    except Exception:
        pass
    return ""
def _set_preset_path_auto_value(node: Any, value: str) -> None:
    if node is None:
        return
    try:
        knob = node.knob("preset_path_auto_value")
        if knob is not None:
            knob.setValue(as_nuke_path(value or ""))
    except Exception:
        pass
def _apply_default_preset_path(node: Any, force: bool = False) -> None:
    if node is None:
        return

    try:
        knob = node.knob("preset_path")
    except Exception:
        knob = None
    if knob is None:
        return

    current = ""
    try:
        current = _expand_env_tokens(str(knob.value() or ""))
    except Exception:
        current = ""

    target = as_nuke_path(_resolved_default_preset_dir(node))
    tracked_auto = _get_preset_path_auto_value(node)

    follows_auto = not current
    if not follows_auto and tracked_auto:
        follows_auto = _same_path(current, tracked_auto)
    if not follows_auto:
        follows_auto = _same_path(current, _resolved_default_preset_dir_for_mode(False)) or _same_path(
            current, _resolved_default_preset_dir_for_mode(True)
        )

    if current and not force and not follows_auto:
        return

    try:
        knob.setValue(target)
        _set_preset_path_auto_value(node, target)
    except Exception:
        pass
def _detect_preset_profile(node: Any) -> str | None:
    if node is None:
        return None
    try:
        if node.knob("run_fibo_generate") is not None:
            return "fibo_generate"
    except Exception:
        pass
    try:
        if node.knob("run_fibo_edit") is not None:
            return "fibo_edit"
    except Exception:
        pass
    try:
        if node.knob("run_structured_prompt") is not None:
            return "structured_prompt"
    except Exception:
        pass
    return None
def _knob_value_for_preset(node: Any, knob_name: str) -> Any:
    knob = node.knob(knob_name) if node is not None else None
    if knob is None:
        return None

    try:
        values = list(knob.values())
    except Exception:
        values = None

    try:
        raw = knob.value()
    except Exception:
        return None

    if values:
        try:
            if isinstance(raw, (int, float)):
                idx = int(raw)
                if 0 <= idx < len(values):
                    return str(values[idx])
            txt = str(raw).strip()
            if txt.isdigit():
                idx = int(txt)
                if 0 <= idx < len(values):
                    return str(values[idx])
        except Exception:
            pass

    if isinstance(raw, (int, float, bool)) or raw is None:
        return raw
    return str(raw)
def _set_knob_from_preset(node: Any, knob_name: str, value: Any) -> None:
    if node is None:
        return
    knob = node.knob(knob_name)
    if knob is None:
        return
    try:
        knob.setValue(value)
        return
    except Exception:
        pass
    try:
        knob.setValue(str(value))
    except Exception:
        pass
def _default_preset_dir(node: Any) -> str:
    preset_path = ""
    try:
        knob = node.knob("preset_path") if node is not None else None
        if knob is not None:
            preset_path = _expand_env_tokens(str(knob.value() or ""))
    except Exception:
        preset_path = ""

    if preset_path:
        preset = Path(preset_path).expanduser()
        if preset.suffix:
            return str(preset.parent)
        return str(preset)

    return _resolved_default_preset_dir(node)
def _preset_default_filename(node: Any) -> str:
    profile = _detect_preset_profile(node) or "bria_node"
    return f"{profile}_preset_v001{_PRESET_EXTENSION}"
def _sanitize_preset_name(name: str) -> str:
    txt = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(name or "").strip())
    txt = txt.strip(" ._")
    return txt[:120]
def _preset_slug(name: str, profile: str | None = None) -> str:
    txt = str(name or "").strip()
    if txt.lower().endswith(_PRESET_EXTENSION):
        txt = txt[: -len(_PRESET_EXTENSION)]
    txt = txt.replace(" ", "_").lower()
    txt = re.sub(r"[^a-z0-9._-]+", "_", txt)
    txt = re.sub(r"_+", "_", txt).strip("._-")

    if profile:
        prefix = f"{profile}_"
        if txt.startswith(prefix):
            txt = txt[len(prefix) :]

    txt = re.sub(r"_v\d{3}$", "", txt)
    txt = txt.strip("._-")
    return txt or "preset"
def _preset_filename(profile: str, slug: str, version: int) -> str:
    return f"{profile}_{slug}_v{max(1, int(version)):03d}{_PRESET_EXTENSION}"
def _preset_name_parts(path_or_name: str, profile: str | None = None) -> tuple[str | None, str, int | None]:
    txt = Path(str(path_or_name or "")).stem.lower().strip()
    candidate_profiles: list[str] = []
    if profile:
        candidate_profiles.append(str(profile))
    candidate_profiles.extend([key for key in _PRESET_FIELDS.keys() if key not in candidate_profiles])

    for candidate in candidate_profiles:
        prefix = f"{candidate}_"
        if not txt.startswith(prefix):
            continue
        remainder = txt[len(prefix) :]
        match = re.match(r"^(?P<slug>.+?)_v(?P<version>\d{3})$", remainder)
        if match:
            return (
                candidate,
                match.group("slug").strip("._-") or "preset",
                int(match.group("version")),
            )

    return (profile, _preset_slug(txt, profile=profile), None)
def _next_preset_version(preset_dir: Path, profile: str, slug: str) -> int:
    highest = 0
    patterns = [f"{profile}_{slug}_v*{_PRESET_EXTENSION}", f"{profile}_{slug}_v*.json"]
    for pattern in patterns:
        for path in preset_dir.glob(pattern):
            matched_profile, matched_slug, matched_version = _preset_name_parts(path.name, profile)
            if matched_profile == profile and matched_slug == slug and matched_version:
                highest = max(highest, int(matched_version))
    return highest + 1 if highest > 0 else 1
def _infer_preset_profile_from_path(path: Path) -> str | None:
    stem = path.stem.strip().lower()
    if stem.startswith("fibo_generate_") or "fibo_generate" in stem:
        return "fibo_generate"
    if stem.startswith("fibo_edit_") or "fibo_edit" in stem:
        return "fibo_edit"
    return None
def _read_preset_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return None
def _read_preset_profile(path: Path) -> str | None:
    payload = _read_preset_payload(path)
    if isinstance(payload, dict):
        profile = str(payload.get("node_profile") or "").strip()
        if profile:
            return profile
    return _infer_preset_profile_from_path(path)
def _preset_display_label(path: Path, profile: str | None) -> str:
    matched_profile, matched_slug, matched_version = _preset_name_parts(path.name, profile)
    if matched_profile and matched_version:
        label = f"{matched_slug}_v{matched_version:03d}"
    else:
        stem = path.stem
        prefix = f"{profile}_" if profile else ""
        if prefix and stem.lower().startswith(prefix.lower()):
            stem = stem[len(prefix):]
        elif stem.lower().endswith("_preset"):
            stem = stem[:-7]
        label = stem.strip(" _.-") or path.stem

    if len(label) > 32:
        label = f"{label[:29]}..."
    return label
def _load_presets_from_examples_enabled(node: Any) -> bool:
    if node is None:
        return False
    try:
        knob = node.knob("load_presets_from_examples")
        return bool(knob.value()) if knob is not None else False
    except Exception:
        return False
def _examples_preset_dir() -> Path | None:
    try:
        resolved = _expand_env_tokens("$BRIA_EXAMPLES/presets")
        if not resolved:
            return None
        return Path(resolved).expanduser()
    except Exception:
        return None
def _preset_catalog(node: Any) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []

    preset_dirs: list[Path] = [Path(_default_preset_dir(node))]
    examples_dir = _examples_preset_dir() if _load_presets_from_examples_enabled(node) else None
    if examples_dir is not None:
        try:
            if examples_dir not in preset_dirs:
                preset_dirs.append(examples_dir)
        except Exception:
            preset_dirs.append(examples_dir)

    node_profile = _detect_preset_profile(node)
    label_counts: dict[str, int] = {}
    preset_paths: list[Path] = []
    for preset_dir in preset_dirs:
        if not preset_dir.exists():
            continue
        preset_paths.extend(list(preset_dir.glob(f"*{_PRESET_EXTENSION}")))
        preset_paths.extend(list(preset_dir.glob("*.json")))

    if not preset_paths:
        return entries

    seen_paths: set[str] = set()
    for path in sorted(preset_paths, key=lambda p: (p.name.lower(), str(p).lower())):
        normalized_path = str(path.resolve()) if path.exists() else str(path)
        if normalized_path in seen_paths:
            continue
        seen_paths.add(normalized_path)

        preset_profile = _read_preset_profile(path)
        matched_profile, _matched_slug, _matched_version = _preset_name_parts(path.name, preset_profile or node_profile)

        if path.suffix.lower() == _PRESET_EXTENSION and node_profile and matched_profile and matched_profile != node_profile:
            continue
        if node_profile and preset_profile and preset_profile != node_profile:
            continue
        if node_profile and not preset_profile:
            continue

        label = _preset_display_label(path, preset_profile or node_profile)
        seen = label_counts.get(label, 0)
        label_counts[label] = seen + 1
        if seen:
            label = f"{label} ({seen + 1})"

        entries.append(
            {
                "label": label,
                "stem": path.stem,
                "path": str(path),
                "profile": str(preset_profile or ""),
            }
        )
    return entries
def _preset_file_map(node: Any) -> dict[str, str]:
    return {entry["label"]: entry["path"] for entry in _preset_catalog(node)}
def _format_preset_details(payload: dict[str, Any] | None, preset_path: str | None = None) -> str:
    if not isinstance(payload, dict):
        return "No preset loaded"

    info = {
        "node_profile": payload.get("node_profile"),
        "saved_at_utc": payload.get("saved_at_utc"),
        "path": preset_path or payload.get("path"),
        "values": payload.get("values") if isinstance(payload.get("values"), dict) else {},
    }
    return json.dumps(info, indent=2, ensure_ascii=False)
def _set_preset_details(node: Any, text: str) -> None:
    try:
        knob = node.knob("preset_details")
        if knob is not None:
            knob.setValue(str(text or ""))
    except Exception:
        pass
def refresh_generation_preset_ui(
    node: Any,
    preferred_name: str | None = None,
    *,
    apply_default_path: bool = True,
) -> None:
    if node is None:
        return

    _ensure_generation_preset_ui(node)
    if apply_default_path:
        _apply_default_preset_path(node)
    catalog = _preset_catalog(node)
    file_map = {entry["label"]: entry["path"] for entry in catalog}
    stem_to_label = {entry["stem"]: entry["label"] for entry in catalog}
    options = ["Select"] + [entry["label"] for entry in catalog]

    try:
        preset_selected = node.knob("preset_selected")
    except Exception:
        preset_selected = None

    selected_name = stem_to_label.get(preferred_name or "", preferred_name or "Select")
    try:
        if preset_selected is not None:
            current = str(preset_selected.value() or "").strip()
            if not preferred_name and current:
                selected_name = current
    except Exception:
        pass

    if selected_name not in options:
        selected_name = "Select"

    _set_enum_values(preset_selected, options)
    try:
        if preset_selected is not None:
            preset_selected.setValue(selected_name)
    except Exception:
        try:
            if preset_selected is not None:
                preset_selected.setValue(options.index(selected_name))
        except Exception:
            pass

    try:
        summary_knob = node.knob("preset_summary")
        if summary_knob is not None:
            suffix = " (including Bria Examples)" if _load_presets_from_examples_enabled(node) else ""
            summary_knob.setValue(f"{len(file_map)} preset(s) available{suffix}")
    except Exception:
        pass

    details_text = "No preset loaded"
    selected_path = file_map.get(selected_name) if selected_name and selected_name != "Select" else None
    if selected_path:
        try:
            payload = json.loads(Path(selected_path).read_text(encoding="utf-8-sig"))
            details_text = _format_preset_details(payload, selected_path)
            try:
                summary_knob = node.knob("preset_summary")
                if summary_knob is not None:
                    summary_knob.setValue(f"Selected {Path(selected_path).name}")
            except Exception:
                pass
        except Exception as exc:
            details_text = f"Failed to read preset:\n{exc}"
    _set_preset_details(node, details_text)
def on_refresh_generation_presets(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")
    refresh_generation_preset_ui(node)
def _resolve_preset_save_path(node: Any) -> str | None:
    explicit_path = ""
    try:
        preset_path_knob = node.knob("preset_path") if node is not None else None
        if preset_path_knob is not None:
            explicit_path = _expand_env_tokens(str(preset_path_knob.value() or ""))
    except Exception:
        explicit_path = ""

    profile = _detect_preset_profile(node) or "bria_node"
    preset_dir = Path(_default_preset_dir(node))

    try:
        preset_name = _sanitize_preset_name(str(node.knob("preset_name").value() or "")) if node is not None and node.knob("preset_name") is not None else ""
    except Exception:
        preset_name = ""

    if preset_name:
        slug = _preset_slug(preset_name, profile=profile)
        version = _next_preset_version(preset_dir, profile, slug)
        return str(preset_dir / _preset_filename(profile, slug, version))

    if not preset_name:
        try:
            selected_knob = node.knob("preset_selected") if node is not None else None
            selected_name = str(selected_knob.value() or "").strip() if selected_knob is not None else ""
            if selected_name and selected_name != "Select":
                for entry in _preset_catalog(node):
                    if entry.get("label") == selected_name:
                        selected_path = str(entry.get("path") or "").strip()
                        if selected_path:
                            return selected_path
                        break
        except Exception:
            preset_name = ""

    if explicit_path:
        path = Path(explicit_path).expanduser()
        if path.suffix.lower() in {_PRESET_EXTENSION, ".json"}:
            return str(path)

    return str(preset_dir / _preset_default_filename(node))
def _resolve_preset_load_path(node: Any) -> str | None:
    try:
        selected_knob = node.knob("preset_selected") if node is not None else None
        selected_name = str(selected_knob.value() or "").strip() if selected_knob is not None else ""
    except Exception:
        selected_name = ""

    if selected_name and selected_name != "Select":
        selected_path = _preset_file_map(node).get(selected_name)
        if selected_path:
            return selected_path

    start_dir = _default_preset_dir(node)
    if _load_presets_from_examples_enabled(node):
        examples_dir = _examples_preset_dir()
        if examples_dir is not None and examples_dir.exists():
            start_dir = str(examples_dir)
    expected_profile = _detect_preset_profile(node)
    file_pattern = f"{expected_profile}_*{_PRESET_EXTENSION} *.json" if expected_profile else f"*{_PRESET_EXTENSION} *.json"
    try:
        selected = nuke.getFilename("Load Bria Preset", file_pattern, as_nuke_path(start_dir))
    except TypeError:
        try:
            selected = nuke.getFilename("Load Bria Preset", file_pattern)
        except Exception:
            selected = None
    except Exception:
        selected = None

    if not selected:
        return None
    selected_path = Path(str(selected)).expanduser()
    selected_profile = _read_preset_profile(selected_path)
    selected_name_profile, _selected_slug, _selected_version = _preset_name_parts(selected_path.name, selected_profile or expected_profile)
    if expected_profile and selected_path.suffix.lower() == _PRESET_EXTENSION and selected_name_profile and selected_name_profile != expected_profile:
        raise RuntimeError(f"Preset filename mismatch: expected {expected_profile}, got {selected_name_profile}.")
    if expected_profile and selected_profile and selected_profile != expected_profile:
        raise RuntimeError(f"Preset profile mismatch: expected {expected_profile}, got {selected_profile}.")
    if expected_profile and not selected_profile:
        raise RuntimeError(f"Preset file does not declare a compatible node profile for {expected_profile}.")
    return str(selected_path)
def _collect_preset_payload(node: Any) -> dict[str, Any]:
    profile = _detect_preset_profile(node)
    if not profile:
        raise RuntimeError("This node does not support Bria presets.")

    values: dict[str, Any] = {}
    for knob_name in _PRESET_FIELDS.get(profile, []):
        values[knob_name] = _knob_value_for_preset(node, knob_name)

    return {
        "preset_type": "bria_node_preset",
        "preset_version": 1,
        "node_profile": profile,
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "values": _sanitize_generation_metadata(values),
    }
def _set_preset_summary(node: Any, summary: str, payload: dict[str, Any] | None = None) -> None:
    try:
        knob = node.knob("preset_summary")
        if knob is not None:
            knob.setValue(summary)
    except Exception:
        pass
    if payload is not None:
        _write_json_knob(node, "generation_preset_data", payload)
        _set_preset_details(node, _format_preset_details(payload))
def on_save_generation_preset(node: Any = None) -> None:
    try:
        if node is None:
            try:
                node = nuke.thisNode()
            except Exception:
                node = None
        if node is None:
            raise ValueError("Missing node")

        _ensure_generation_preset_ui(node)
        payload = _collect_preset_payload(node)
        save_path = _resolve_preset_save_path(node)
        if not save_path:
            return

        _ensure_parent_dir(save_path)
        Path(save_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        preset_stem = Path(save_path).stem

        try:
            preset_knob = node.knob("preset_path")
            if preset_knob is not None:
                preset_knob.setValue(as_nuke_path(save_path))
        except Exception:
            pass

        try:
            name_knob = node.knob("preset_name")
            if name_knob is not None:
                _saved_profile, saved_slug, _saved_version = _preset_name_parts(preset_stem, _detect_preset_profile(node))
                name_knob.setValue(saved_slug)
        except Exception:
            pass

        refresh_generation_preset_ui(node, preferred_name=preset_stem)
        _set_preset_summary(node, f"Saved {Path(save_path).name}", payload)
        nuke.tprint(f"[Bria] Saved preset: {save_path}")
    except Exception as exc:
        nuke.message(f"Failed to save preset: {exc}")
def on_load_generation_preset(node: Any = None) -> None:
    try:
        if node is None:
            try:
                node = nuke.thisNode()
            except Exception:
                node = None
        if node is None:
            raise ValueError("Missing node")

        _ensure_generation_preset_ui(node)
        load_path = _resolve_preset_load_path(node)
        if not load_path:
            return

        payload = json.loads(Path(load_path).read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise RuntimeError("Preset file must contain a JSON object.")
        if str(payload.get("preset_type") or "") != "bria_node_preset":
            raise RuntimeError("Unsupported preset type.")

        node_profile = _detect_preset_profile(node)
        preset_profile = str(payload.get("node_profile") or "")
        if node_profile and preset_profile and node_profile != preset_profile:
            raise RuntimeError(f"Preset profile mismatch: expected {node_profile}, got {preset_profile}.")

        values = payload.get("values")
        if not isinstance(values, dict):
            raise RuntimeError("Preset JSON is missing a valid values block.")

        for knob_name in _PRESET_FIELDS.get(node_profile or preset_profile, []):
            if knob_name in values:
                _set_knob_from_preset(node, knob_name, values.get(knob_name))

        _refresh_prompt_and_model_ui(node)

        try:
            preset_knob = node.knob("preset_path")
            if preset_knob is not None:
                preset_knob.setValue(as_nuke_path(load_path))
        except Exception:
            pass

        try:
            name_knob = node.knob("preset_name")
            if name_knob is not None:
                _loaded_profile, loaded_slug, _loaded_version = _preset_name_parts(Path(load_path).stem, _detect_preset_profile(node))
                name_knob.setValue(loaded_slug)
        except Exception:
            pass

        refresh_generation_preset_ui(node, preferred_name=Path(load_path).stem)
        _set_preset_summary(node, f"Loaded {Path(load_path).name}", payload)
        nuke.tprint(f"[Bria] Loaded preset: {load_path}")
    except Exception as exc:
        nuke.message(f"Failed to load preset: {exc}")


# ---------------------------------------------------------------------------
# Progress / threading
# ---------------------------------------------------------------------------
