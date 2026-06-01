"""Nuke Bria Structured Prompt node logic."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import nuke  # type: ignore

from adapter import structured_prompt_generate
from bria_core.config import load_config, resolve_api_endpoint
from bria_core.errors import BriaConfigError, BriaRequestError
from bria_core.utils import resolve_proxies
from common import (
    is_batch_mode as _is_batch_mode,
    _resolved_saved_path as _common_resolved_saved_path,
    _default_output_dir as _default_output_dir,
    append_generation_history as _append_generation_history,
    create_progress_task as _create_progress_task,
    ensure_generation_history_ui as _ensure_generation_history_ui,
    ensure_parent_dir as _ensure_parent_dir,
    escape_for_eval_knob as _escape_for_eval_knob,
    export_input_png as _export_input_png,
    get_history_selected_entry as _get_history_selected_entry,
    load_text_file_into_knob as _load_text_file_into_knob,
    on_main_thread as _on_main_thread,
    resolve_output_path as _resolve_output_path,
    set_generation_metadata as _set_generation_metadata,
    ensure_generation_metadata_ui as _ensure_generation_metadata_ui,
    set_result_path as _set_result_path,
    set_status as _set_status,
    update_generation_history_selection_ui as _update_generation_history_selection_ui,
    update_progress as _update_progress,
    _expand_env_tokens as _expand_env_tokens,
)
from node_utils import (
    debug_logger,
    opt_parm_bool as _opt_parm_bool,
    opt_parm_menu_str as _opt_parm_menu_str,
    opt_parm_str as _opt_parm_str,
    resolve_temp_dir,
)

_debug_log = debug_logger("Bria Nuke Structured Prompt")


def _sanitize_output_label(name: str) -> str:
    """Sanitize a user-supplied label into a safe filename slug."""
    txt = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "").strip().lower())
    txt = re.sub(r"_+", "_", txt).strip("._-")
    return txt[:80]


def _sync_label_visibility(node: Any) -> None:
    if node is None:
        return
    use_label = bool(_opt_parm_bool(node, "use_custom_label"))
    try:
        k = node.knob("output_label")
        if k is not None:
            k.setVisible(use_label)
            k.setEnabled(use_label)
    except Exception:
        pass


def _sync_prompt_visibility(node: Any) -> None:
    if node is None:
        return
    try:
        prompt_knob = node.knob("prompt")
        if prompt_knob is not None:
            prompt_knob.setVisible(True)
            prompt_knob.setEnabled(True)
    except Exception:
        pass

    use_structured = bool(_opt_parm_bool(node, "use_structured_prompt_input"))
    try:
        structured_knob = node.knob("structured_prompt_input")
        if structured_knob is not None:
            structured_knob.setVisible(use_structured)
            structured_knob.setEnabled(use_structured)
    except Exception:
        pass

    try:
        load_knob = node.knob("load_structured_prompt_input")
        if load_knob is not None:
            load_knob.setEnabled(use_structured)
    except Exception:
        pass


def _set_output_text(node: Any, txt: str) -> None:
    try:
        knob = node.knob("structured_prompt_output")
        if knob is not None:
            # Escape TCL specials so JSON content with `[`, `]`, `$` round-trips
            # through Multiline_Eval_String_Knob.value() without truncation.
            knob.setValue(_escape_for_eval_knob(str(txt or "")))
    except Exception:
        pass


def _notify_downstream_nodes(node: Any) -> None:
    """Push updated structured prompt output to all connected downstream nodes."""
    if node is None:
        return
    try:
        # .value() un-escapes via TCL eval and returns the original JSON.
        sp_text = str((node.knob("structured_prompt_output").value()) or "")
    except Exception:
        return
    if not sp_text.strip():
        return
    try:
        for dep in node.dependent():
            sp_knob = dep.knob("structured_prompt")
            if sp_knob is not None:
                try:
                    # Re-escape for the downstream eval knob so the receiver's
                    # .value() also returns the un-truncated JSON.
                    sp_knob.setValue(_escape_for_eval_knob(sp_text))
                except Exception:
                    pass
    except Exception:
        pass


def _extract_structured_prompt_text(data: Any) -> str:
    candidate_keys = (
        "structured_prompt",
        "structuredPrompt",
        "prompt_json",
        "promptJson",
        "json_prompt",
        "jsonPrompt",
    )

    def _coerce(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, (dict, list)):
            try:
                return json.dumps(v, indent=2, ensure_ascii=False)
            except Exception:
                return str(v)
        return str(v)

    def _walk(obj: Any) -> str:
        if isinstance(obj, dict):
            for key in candidate_keys:
                if key in obj and obj.get(key) is not None:
                    return _coerce(obj.get(key))
            for nested_key in ("result", "data", "output", "response"):
                if nested_key in obj:
                    txt = _walk(obj.get(nested_key))
                    if txt:
                        return txt
        elif isinstance(obj, list):
            for item in obj:
                txt = _walk(item)
                if txt:
                    return txt
        return ""

    txt = _walk(data)
    if txt:
        return txt
    return _coerce(data)


def _as_json_text(txt: str) -> str:
    raw = str(txt or "").strip()
    if not raw:
        return json.dumps({}, indent=2, ensure_ascii=False)
    try:
        parsed = json.loads(raw)
        return json.dumps(parsed, indent=2, ensure_ascii=False)
    except Exception:
        return json.dumps({"structured_prompt": raw}, indent=2, ensure_ascii=False)


def _next_sp_label_version(prompt_dir: Path, slug: str) -> int:
    """Return the next available version number for a labelled SP output file."""
    highest = 0
    for path in prompt_dir.glob(f"bria_structured_prompt_{slug}_v*.json"):
        m = re.match(rf"^bria_structured_prompt_{re.escape(slug)}_v(\d{{3}})\.json$", path.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1 if highest > 0 else 1


def _resolve_structured_prompt_output_path(node: Any, cfg: Any, run_id: str) -> str:
    # Read label settings first — needed for both filename construction and path heuristics.
    use_label = bool(_opt_parm_bool(node, "use_custom_label"))
    label = (_opt_parm_str(node, "output_label") or "").strip() if use_label else ""

    # Determine the output directory.
    explicit = (_opt_parm_str(node, "result_path") or "").strip()
    if explicit:
        explicit = _expand_env_tokens(os.path.expanduser(explicit))
        p = Path(explicit)
        if p.suffix.lower() == ".json":
            # If the stored path is one we previously wrote (auto-generated filename), pull out
            # the parent so versioning works on the next run.  A user-specified path that does
            # NOT start with our prefix is honoured verbatim.
            if p.name.startswith("bria_structured_prompt_"):
                prompt_dir = p.parent
                prompt_dir.mkdir(parents=True, exist_ok=True)
            else:
                _ensure_parent_dir(str(p))
                return str(p)
        else:
            p.mkdir(parents=True, exist_ok=True)
            prompt_dir = p
    else:
        use_global_knob = bool(_opt_parm_bool(node, "use_global_project_path"))
        prompt_dir = None

        # Per-node knob is the source of truth — mirrors resolve_output_path
        # in common.py. The earlier AND-gate produced silent temp fallbacks
        # when env/cfg were unset, breaking persistence of saved .nk files.
        if use_global_knob:
            global_root = str(
                os.getenv("BRIA_PROJECT_PATH") or getattr(cfg, "nuke_output_dir", "") or ""
            ).strip()
            if global_root:
                prompt_dir = Path(os.path.expandvars(os.path.expanduser(global_root))) / "prompt"
            else:
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

        if prompt_dir is None:
            prompt_dir = Path(_default_output_dir("prompt"))

        prompt_dir.mkdir(parents=True, exist_ok=True)

    # Build filename: versioned when a custom label is set, timestamped otherwise.
    if label:
        slug = _sanitize_output_label(label)
        version = _next_sp_label_version(prompt_dir, slug)
        filename = f"bria_structured_prompt_{slug}_v{version:03d}.json"
    else:
        filename = f"bria_structured_prompt_result_{run_id}.json"

    return str(prompt_dir / filename)


def structured_prompt_bria(node: Any) -> None:
    t0 = time.perf_counter()
    progress = _create_progress_task("Bria Structured Prompt")
    try:
        _ensure_generation_metadata_ui(node)
        _ensure_generation_history_ui(node)
        _on_main_thread(lambda: _set_status(node, "processing"))
        _update_progress(progress, "Preparing request...", 5)
        run_id = str(int(time.time() * 1000))

        prompt = (_opt_parm_str(node, "prompt") or "").strip()
        structured_prompt = (_opt_parm_str(node, "structured_prompt_input") or "").strip()
        use_structured_prompt = bool(_opt_parm_bool(node, "use_structured_prompt_input"))
        if not use_structured_prompt:
            structured_prompt = ""

        pipeline = (_opt_parm_menu_str(node, "pipeline") or "standard").strip().lower()
        include_input_image = bool(_opt_parm_bool(node, "use_input_image"))

        image_path = None
        if include_input_image:
            if node.input(0) is None:
                raise RuntimeError("Connect an image to input 1 or uncheck 'Use Input Image'.")
            tmp = resolve_temp_dir(nuke)
            image_path = os.path.join(tmp, f"bria_structured_prompt_input_{run_id}.png")
            _export_input_png(
                node,
                0,
                image_path,
                missing_input_message="Connect image to input 1 or disable 'Use Input Image'.",
            )

        has_prompt = bool(prompt)
        has_image = bool(image_path)
        has_structured = bool(structured_prompt)

        valid_combo = (
            (has_prompt and not has_image and not has_structured)  # Text only
            or (has_image and not has_prompt and not has_structured)  # Image only
            or (has_image and has_prompt and not has_structured)  # Image + text
            or (has_structured and has_prompt and not has_image)  # Structured prompt + text
        )
        if not valid_combo:
            if not has_prompt and not has_image and not has_structured:
                raise RuntimeError("Provide a prompt, connect an image, or both.")
            raise RuntimeError(
                "Invalid input combination. Use exactly one: prompt | images | images+prompt | structured_prompt+prompt."
            )

        _update_progress(progress, "Resolving config...", 15)
        cfg = load_config()
        use_bearer = bool(cfg.nuke_use_bearer_auth)
        use_env_proxy = cfg.nuke_use_env_proxy
        if use_env_proxy is None:
            use_env_proxy = True
        proxies = resolve_proxies(
            http_proxy=cfg.nuke_http_proxy,
            https_proxy=cfg.nuke_https_proxy,
            use_env_proxy=use_env_proxy,
        )

        use_label = bool(_opt_parm_bool(node, "use_custom_label"))
        label = (_opt_parm_str(node, "output_label") or "").strip() if use_label else ""
        _update_progress(progress, "Sending request...", 45)
        data = structured_prompt_generate(
            prompt=prompt,
            structured_prompt=structured_prompt,
            pipeline=pipeline,
            image_path=image_path,
            use_bearer=use_bearer,
            proxies=proxies,
            session=None,
        )

        txt = _extract_structured_prompt_text(data)
        json_txt = _as_json_text(txt)
        out_path = _resolve_structured_prompt_output_path(node, cfg, run_id)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(json_txt)

        def _apply_success_ui() -> None:
            payload = {
                "operation": "structured_prompt_generate",
                "endpoint": resolve_api_endpoint(cfg),
                "result_path": out_path,
                "response": data,
                "settings": {
                    "pipeline": pipeline,
                    "use_input_image": include_input_image,
                    "use_structured_prompt_input": use_structured_prompt,
                    "prompt": prompt,
                    "structured_prompt": structured_prompt,
                    "use_custom_label": use_label,
                    "output_label": label,
                },
            }
            _set_generation_metadata(node, payload)
            _append_generation_history(node, payload)
            _set_output_text(node, json_txt)
            _notify_downstream_nodes(node)
            _set_result_path(node, out_path)
            _set_status(node, "done")

        _on_main_thread(_apply_success_ui)
        _update_progress(progress, "Finalizing...", 95)
        _update_progress(progress, percent=100)

        dt = time.perf_counter() - t0
        nuke.tprint(f"Bria Nuke Structured Prompt Complete ({dt:.2f}s) | Saved: {out_path}")
    except (BriaConfigError, BriaRequestError) as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        msg = f"Bria Nuke Structured Prompt Error: {exc}"
        _debug_log(msg)
        if _is_batch_mode():
            raise
        nuke.message(msg)
    except Exception as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        msg = f"Bria Nuke Structured Prompt Exception: {exc}"
        _debug_log(msg)
        if _is_batch_mode():
            raise
        nuke.message(msg)


def on_knob_changed() -> None:
    try:
        node = nuke.thisNode()
        knob = nuke.thisKnob()
    except Exception:
        return
    if node is None or knob is None:
        return
    knob_name = knob.name()
    if knob_name == "showPanel":
        try:
            from common import ensure_prompt_field_heights
            ensure_prompt_field_heights(node, "sp")
        except Exception:
            pass
        return
    if knob_name == "inputChange":
        _sync_prompt_visibility(node)
        return
    if knob_name == "history_selected_entry":
        _ensure_generation_history_ui(node)
        _update_generation_history_selection_ui(node)
        return
    _ensure_generation_metadata_ui(node)
    if knob_name in {"use_input_image", "use_structured_prompt_input"}:
        _sync_prompt_visibility(node)
        return
    if knob_name == "use_custom_label":
        _sync_label_visibility(node)
        return


def on_structured_prompt(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")
    _ensure_generation_metadata_ui(node)
    _ensure_generation_history_ui(node)
    _sync_prompt_visibility(node)

    # Lock toggle: if locked and a cached output exists, skip the API call.
    # Matches Houdini's `lock_vgl` flag — lets artists lock a VGL for consistency
    # across batch/sequence renders (procedural workflows leave it unlocked
    # to regenerate per frame).
    if _opt_parm_bool(node, "lock_vgl"):
        cached = (_opt_parm_str(node, "structured_prompt_output") or "").strip()
        if cached:
            _set_status(node, "locked")
            _debug_log("VGL is locked; skipping API call")
            return

    structured_prompt_bria(node)


def on_load_history_structured_prompt(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    # Read directly from the selected history entry's result_path — 1-1 correspondence
    # with the JSON file that was saved when that run completed.
    content = ""
    entry = _get_history_selected_entry(node)
    if entry:
        rp = str(entry.get("result_path") or "").strip()
        if rp:
            try:
                rp_path = Path(_common_resolved_saved_path(rp) or rp)
                if rp_path.exists() and rp_path.suffix.lower() == ".json":
                    content = rp_path.read_text(encoding="utf-8").strip()
            except Exception:
                content = ""

    if not content:
        nuke.message("No structured prompt available for the selected history entry.")
        return

    try:
        k = node.knob("structured_prompt_output")
        if k is not None:
            k.setValue(content)
            nuke.tprint(f"[Bria] Loaded structured prompt from history into {node.name()}")
    except Exception as exc:
        nuke.message(f"Failed to load structured prompt: {exc}")


def on_load_structured_prompt_input(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    try:
        loaded = _load_text_file_into_knob(
            node=node,
            target_knob_name="structured_prompt_input",
            enable_knob_name="use_structured_prompt_input",
            dialog_title="Load Structured Prompt JSON / VGL",
            start_dir="",
        )
        if loaded:
            _sync_prompt_visibility(node)
            nuke.tprint(f"[Bria] Loaded structured prompt input from file into {node.name()}")
    except Exception as exc:
        msg = f"Failed to load structured prompt input file: {exc}"
        _debug_log(msg)
        if _is_batch_mode():
            raise
        nuke.message(msg)


def on_copy_structured_prompt(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    txt = (_opt_parm_str(node, "structured_prompt_output") or "").strip()
    if not txt:
        nuke.message("Structured Prompt is empty. Run generation first.")
        return

    copied = False

    # Preferred path: Qt clipboard inside Nuke UI process.
    try:
        try:
            from PySide2 import QtWidgets  # type: ignore
        except Exception:
            from PySide6 import QtWidgets  # type: ignore

        app = QtWidgets.QApplication.instance()
        if app is not None and app.clipboard() is not None:
            app.clipboard().setText(txt)
            copied = True
    except Exception:
        copied = False

    # Secondary path: tkinter clipboard (works in many desktop sessions).
    if not copied:
        try:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(txt)
            root.update()
            root.destroy()
            copied = True
        except Exception:
            copied = False

    # Final fallback: OS clipboard commands.
    if not copied:
        try:
            if os.name == "nt":
                subprocess.run(["clip"], input=txt, text=True, encoding="utf-8", check=True, timeout=5)
                copied = True
            elif sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=txt, text=True, encoding="utf-8", check=True, timeout=5)
                copied = True
            else:
                if shutil.which("wl-copy"):
                    subprocess.run(["wl-copy"], input=txt, text=True, encoding="utf-8", check=True, timeout=5)
                    copied = True
                elif shutil.which("xclip"):
                    subprocess.run(["xclip", "-selection", "clipboard"], input=txt, text=True, encoding="utf-8", check=True, timeout=5)
                    copied = True
                elif shutil.which("xsel"):
                    subprocess.run(["xsel", "--clipboard", "--input"], input=txt, text=True, encoding="utf-8", check=True, timeout=5)
                    copied = True
        except Exception:
            copied = False

    if copied:
        nuke.tprint("[Bria Nuke Structured Prompt] Copied structured prompt to clipboard")
    else:
        nuke.message("Could not access clipboard in this environment.")


def on_open_structured_prompt_folder(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    result_path = (_opt_parm_str(node, "result_path") or "").strip()
    if not result_path:
        nuke.message("No result path set yet. Run generation first.")
        return

    resolved = Path(os.path.expandvars(os.path.expanduser(result_path)))
    target_dir = resolved.parent if resolved.suffix.lower() == ".json" else resolved
    target_file = resolved if resolved.suffix.lower() == ".json" else None

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    opened = False
    try:
        if os.name == "nt":
            if target_file is not None and target_file.exists():
                subprocess.Popen(["explorer", "/select,", str(target_file)])
            else:
                os.startfile(str(target_dir))  # type: ignore[attr-defined]
            opened = True
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target_dir)])
            opened = True
        else:
            subprocess.Popen(["xdg-open", str(target_dir)])
            opened = True
    except Exception:
        opened = False

    if opened:
        nuke.tprint(f"[Bria Nuke Structured Prompt] Opened output location: {target_dir}")
    else:
        nuke.message(f"Could not open folder: {target_dir}")
