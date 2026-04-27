"""Nuke Bria FIBO Edit node logic."""

from __future__ import annotations

import json
import os
import re
import socket
import time
from typing import Any

import nuke  # type: ignore

from adapter import fibo_edit_from_files
from bria_core.config import load_config, resolve_api_endpoint
from bria_core.errors import BriaConfigError, BriaRequestError
from bria_core.utils import download_url, extract_image_url, resolve_proxies, validate_bria_image_file
from common import (
    append_generation_history as _append_generation_history,
    create_progress_task as _create_progress_task,
    ensure_parent_dir as _ensure_parent_dir,
    ensure_generation_history_ui as _ensure_generation_history_ui,
    escape_for_eval_knob as _escape_for_eval_knob,
    export_input_png as _export_input_png,
    export_mask_input_png as _export_mask_input_png,
    export_via_internal_save as _export_via_internal_save,
    is_batch_mode as _is_batch_mode,
    on_load_structured_prompt as _common_on_load_structured_prompt,
    on_main_thread as _on_main_thread,
    ensure_generation_preset_ui as _ensure_generation_preset_ui,
    refresh_generation_preset_ui as _refresh_generation_preset_ui,
    refresh_generation_history_ui as _refresh_generation_history_ui,
    update_generation_history_selection_ui as _update_generation_history_selection_ui,
    resolve_input_temp_dir as _resolve_input_temp_dir,
    resolve_output_path as _resolve_output_path,
    set_status as _set_status,
    set_generation_metadata as _set_generation_metadata,
    ensure_generation_metadata_ui as _ensure_generation_metadata_ui,
    set_result_path as _set_result_path,
    set_history_debug as _set_history_debug,
    sync_preview as _sync_preview_common,
    update_progress as _update_progress,
    get_internal_group_read_file as _get_internal_read_file,
    set_internal_group_read_file as _set_internal_read_file,
    sync_prompt_mode_ui as _sync_prompt_mode_ui,
)
from node_utils import (
    debug_logger,
    opt_parm_bool as _opt_parm_bool,
    opt_parm_int as _opt_parm_int,
    opt_parm_menu_str as _opt_parm_menu_str,
    opt_parm_str as _opt_parm_str,
)

_debug_log = debug_logger("Bria Nuke FIBO Edit")


def _sync_group_preview(node: Any) -> None:
    if node is None:
        return

    result_path = _opt_parm_str(node, "result_path") or ""
    output_view = (_opt_parm_menu_str(node, "output_view") or "Before").strip().lower()
    compare_source = (_opt_parm_menu_str(node, "compare_source") or "Original").strip().lower()
    previous_path = _get_internal_read_file(node, "loader_before")

    _sync_preview_common(
        node=node,
        result_path=result_path,
        output_view=output_view,
        after_node_name="loader_result",
        switch_node_names=("switch_before_after",),
    )

    use_previous = compare_source in {"previous edit", "previous"} and bool(previous_path)

    try:
        node.begin()
        switch_compare = nuke.toNode("switch_compare_source")
        if switch_compare is not None and switch_compare.knob("which") is not None:
            switch_compare.knob("which").setValue(1 if use_previous else 0)
    finally:
        try:
            node.end()
        except Exception:
            pass


def _sync_group_preview_explicit(node: Any, result_path: str, output_view: str) -> None:
    if node is None:
        return

    compare_source = (_opt_parm_menu_str(node, "compare_source") or "Original").strip().lower()
    previous_path = _get_internal_read_file(node, "loader_before")

    _sync_preview_common(
        node=node,
        result_path=result_path,
        output_view=output_view,
        after_node_name="loader_result",
        switch_node_names=("switch_before_after",),
    )

    use_previous = compare_source in {"previous edit", "previous"} and bool(previous_path)

    try:
        node.begin()
        switch_compare = nuke.toNode("switch_compare_source")
        if switch_compare is not None and switch_compare.knob("which") is not None:
            switch_compare.knob("which").setValue(1 if use_previous else 0)
    finally:
        try:
            node.end()
        except Exception:
            pass





def _friendly_bria_request_error(node: Any, exc: Exception) -> str:
    raw = str(exc or "")

    if "Bria API Error: 422" in raw:
        request_id = None
        try:
            match = re.search(r'"request_id":"([^"]+)"', raw)
            if match:
                request_id = match.group(1)
        except Exception:
            request_id = None

        messages: list[str] = []

        if "steps_num" in raw and "greater than or equal to 20" in raw:
            steps_num = _opt_parm_int(node, "steps_num")
            messages.append(
                "Bria rejected this FIBO Edit request because `Steps` must be 20 or higher. "
                f"Current value: {int(steps_num) if steps_num is not None else 'unset'}."
            )

        if "guidance_scale" in raw and "greater than or equal to 3" in raw:
            guidance_scale = _opt_parm_int(node, "guidance_scale")
            messages.append(
                "Bria rejected this FIBO Edit request because `Guidance Scale` must be 3 or higher. "
                f"Current value: {int(guidance_scale) if guidance_scale is not None else 'unset'}."
            )

        if messages:
            msg = "\n\n".join(messages) + "\n\nThese limits are coming from the Bria API."
            if request_id:
                msg += f"\n\nRequest ID: {request_id}"
            return msg

    return f"Bria Nuke FIBO Edit Error: {raw}"


def _sync_structured_prompt_link(node: Any) -> None:
    """Called on inputChange. If a Structured Prompt node is connected to input 2,
    copies its output into the structured_prompt field and switches to structured mode.
    On disconnect, restores the stashed manual prompt."""
    if node is None:
        return

    upstream = None
    try:
        upstream = node.input(2)
    except Exception:
        upstream = None

    sp_out_knob = None
    if upstream is not None:
        try:
            sp_out_knob = upstream.knob("structured_prompt_output")
        except Exception:
            sp_out_knob = None

    if sp_out_knob is not None:
        # --- CONNECT: upstream is a Structured Prompt node ---
        try:
            stash = node.knob("prompt_manual_stash")
            prompt_knob = node.knob("prompt")
            if stash is not None and prompt_knob is not None:
                stash.setValue(str(prompt_knob.value() or ""))
        except Exception:
            pass
        try:
            # Re-escape TCL specials so JSON survives the eval-knob round-trip
            # on the downstream Edit node's structured_prompt field.
            sp_text = str(sp_out_knob.value() or "")
            sp_knob = node.knob("structured_prompt")
            if sp_knob is not None:
                sp_knob.setValue(_escape_for_eval_knob(sp_text))
        except Exception:
            pass
        try:
            use_sp = node.knob("use_structured_prompt")
            if use_sp is not None:
                use_sp.setValue(True)
                use_sp.setEnabled(False)
        except Exception:
            pass
        try:
            prompt_knob = node.knob("prompt")
            if prompt_knob is not None:
                prompt_knob.setEnabled(False)
        except Exception:
            pass
        try:
            status = node.knob("prompt_link_status")
            if status is not None:
                status.setValue(f"linked \u2192 {upstream.name()}")
        except Exception:
            pass
    else:
        # --- DISCONNECT: restore manual mode ---
        try:
            stash = node.knob("prompt_manual_stash")
            prompt_knob = node.knob("prompt")
            if stash is not None and prompt_knob is not None:
                stash_val = str(stash.value() or "")
                # Only restore from stash if it has real content — avoids
                # wiping user prompts when a non-SP input (image/mask) is
                # disconnected and the stash was never populated.
                if stash_val.strip():
                    prompt_knob.setValue(stash_val)
                prompt_knob.setEnabled(True)
        except Exception:
            pass
        try:
            use_sp = node.knob("use_structured_prompt")
            if use_sp is not None:
                use_sp.setValue(False)
                use_sp.setEnabled(True)
        except Exception:
            pass
        try:
            status = node.knob("prompt_link_status")
            if status is not None:
                status.setValue("manual")
        except Exception:
            pass

    _sync_prompt_mode_ui(node)


def on_load_structured_prompt(node: Any = None) -> None:
    _common_on_load_structured_prompt(node, "Load FIBO Edit JSON / VGL")


def fibo_edit_bria(node: Any) -> None:
    t0 = time.perf_counter()
    progress = _create_progress_task("Bria FIBO Edit")
    try:
        _ensure_generation_metadata_ui(node)
        _ensure_generation_history_ui(node)
        _ensure_generation_preset_ui(node)
        _on_main_thread(lambda: _set_status(node, "processing"))
        _update_progress(progress, "Preparing request...", 5)

        use_negative = bool(_opt_parm_bool(node, "use_negative_prompt"))
        negative_prompt = (_opt_parm_str(node, "negative_prompt") or "").strip() if use_negative else None

        use_structured = bool(_opt_parm_bool(node, "use_structured_prompt"))
        prompt = None if use_structured else (_opt_parm_str(node, "prompt") or "").strip()
        structured_prompt_raw = (_opt_parm_str(node, "structured_prompt") or "").strip() if use_structured else None
        structured_prompt = None
        if structured_prompt_raw:
            try:
                json.loads(structured_prompt_raw)
            except Exception as exc:
                raise RuntimeError(f"structured_prompt must be valid JSON: {exc}")
            structured_prompt = structured_prompt_raw

        if not prompt and not structured_prompt:
            raise RuntimeError("Provide prompt or structured prompt.")

        tmp = _resolve_input_temp_dir("fibo_edit")
        run_id = str(int(time.time() * 1000))
        image_path = os.path.join(tmp, f"bria_fibo_edit_input_{run_id}.png")
        mask_path = os.path.join(tmp, f"bria_fibo_edit_mask_{run_id}.png")

        _export_via_internal_save(node, "save_image", image_path) or _export_input_png(
            node,
            0,
            image_path,
        )
        validate_bria_image_file(image_path, "Input image")

        mask_input_connected = False
        try:
            mask_input_connected = node.input(1) is not None
        except Exception:
            mask_input_connected = False

        mask_type = (_opt_parm_menu_str(node, "mask_type") or "manual").strip().lower()

        mask_to_send = None
        if mask_input_connected and mask_type == "manual":
            _export_mask_input_png(
                node,
                1,
                mask_path,
                missing_input_message="Connect mask to input 2.",
            )
            validate_bria_image_file(mask_path, "Mask")
            mask_to_send = mask_path

        cfg = load_config()
        _update_progress(progress, "Resolving config...", 15)
        out_path = _resolve_output_path(node, cfg, run_id, "fibo_edit")
        use_bearer = bool(cfg.nuke_use_bearer_auth)
        use_env_proxy = cfg.nuke_use_env_proxy
        if use_env_proxy is None:
            use_env_proxy = True
        proxies = resolve_proxies(
            http_proxy=cfg.nuke_http_proxy,
            https_proxy=cfg.nuke_https_proxy,
            use_env_proxy=use_env_proxy,
        )

        request_timeout_s = int(getattr(cfg, "default_timeout", 0) or 0)
        if request_timeout_s < 1200:
            request_timeout_s = 1200

        seed = _opt_parm_int(node, "seed")
        guidance_scale = _opt_parm_int(node, "guidance_scale")
        steps_num = _opt_parm_int(node, "steps_num")

        if guidance_scale is not None and int(guidance_scale) > 5:
            raise RuntimeError("Guidance Scale must be between 1 and 5.")

        mask_type_to_send = "manual" if mask_to_send else None

        try:
            _update_progress(progress, "Sending request...", 40)
            data = fibo_edit_from_files(
                image_path=image_path,
                prompt=prompt or None,
                structured_prompt=structured_prompt,
                negative_prompt=negative_prompt,
                guidance_scale=(int(guidance_scale) if guidance_scale is not None and int(guidance_scale) > 0 else None),
                seed=(int(seed) if seed is not None and int(seed) > 0 else None),
                steps_num=(int(steps_num) if steps_num is not None and int(steps_num) > 0 else None),
                mask_path=mask_to_send,
                mask_type=mask_type_to_send,
                timeout_s=request_timeout_s,
                use_bearer=use_bearer,
                proxies=proxies,
                session=None,
            )
        except BriaRequestError as exc:
            msg = str(exc).lower()
            if "timed out" not in msg and "timeout" not in msg:
                raise
            retry_timeout_s = max(request_timeout_s * 2, 1800)
            _debug_log(
                f"FIBO Edit API timeout at {request_timeout_s}s, retrying once with {retry_timeout_s}s"
            )
            data = fibo_edit_from_files(
                image_path=image_path,
                prompt=prompt or None,
                structured_prompt=structured_prompt,
                negative_prompt=negative_prompt,
                guidance_scale=(int(guidance_scale) if guidance_scale is not None and int(guidance_scale) > 0 else None),
                seed=(int(seed) if seed is not None and int(seed) > 0 else None),
                steps_num=(int(steps_num) if steps_num is not None and int(steps_num) > 0 else None),
                mask_path=mask_to_send,
                mask_type=mask_type_to_send,
                timeout_s=retry_timeout_s,
                use_bearer=use_bearer,
                proxies=proxies,
                session=None,
            )

        dl_url = extract_image_url(data)
        if not dl_url:
            raise BriaRequestError(f"Unexpected API response (no image_url): {data}")

        download_timeout_s = int(getattr(cfg, "default_timeout", 0) or 0)
        if download_timeout_s < 300:
            download_timeout_s = 300

        try:
            _update_progress(progress, "Downloading result image...", 70)
            img_bytes, _ct = download_url(dl_url, timeout_s=download_timeout_s, proxies=proxies)
        except Exception as exc:
            text = str(exc).lower()
            if "timed out" not in text and not isinstance(exc, (TimeoutError, socket.timeout)):
                raise
            _debug_log(f"FIBO Edit download timeout on first attempt, retrying once: {exc}")
            img_bytes, _ct = download_url(dl_url, timeout_s=download_timeout_s, proxies=proxies)
        _ensure_parent_dir(out_path)
        with open(out_path, "wb") as f:
            f.write(img_bytes)

        def _apply_success_ui() -> None:
            try:
                _set_history_debug(node, "success ui entered")
                prev_path = (_opt_parm_str(node, "result_path") or "").strip()
                if prev_path and prev_path != out_path:
                    _set_internal_read_file(node, "loader_before", prev_path)
                _set_generation_metadata(
                    node,
                    {
                        "operation": "fibo_edit",
                        "endpoint": resolve_api_endpoint(cfg),
                        "result_path": out_path,
                        "mask_mode": mask_type_to_send or "automatic",
                        "response": data,
                        "settings": {
                            "prompt": prompt,
                            "negative_prompt": negative_prompt,
                            "structured_prompt": structured_prompt,
                            "mask_type": mask_type,
                            "mask_connected": mask_input_connected,
                            "guidance_scale": (int(guidance_scale) if guidance_scale is not None and int(guidance_scale) > 0 else None),
                            "seed": (int(seed) if seed is not None and int(seed) > 0 else None),
                            "steps_num": (int(steps_num) if steps_num is not None and int(steps_num) > 0 else None),
                            "compare_source": (_opt_parm_menu_str(node, "compare_source") or "Original"),
                        },
                    },
                )
                _set_history_debug(node, "generation metadata written")
                _append_generation_history(
                    node,
                    {
                        "operation": "fibo_edit",
                        "endpoint": resolve_api_endpoint(cfg),
                        "result_path": out_path,
                        "mask_mode": mask_type_to_send or "automatic",
                        "response": data,
                        "settings": {
                            "prompt": prompt,
                            "negative_prompt": negative_prompt,
                            "structured_prompt": structured_prompt,
                            "mask_type": mask_type,
                            "mask_connected": mask_input_connected,
                            "guidance_scale": (int(guidance_scale) if guidance_scale is not None and int(guidance_scale) > 0 else None),
                            "seed": (int(seed) if seed is not None and int(seed) > 0 else None),
                            "steps_num": (int(steps_num) if steps_num is not None and int(steps_num) > 0 else None),
                            "compare_source": (_opt_parm_menu_str(node, "compare_source") or "Original"),
                        },
                    },
                )
                _set_history_debug(node, "history append returned")
                _set_result_path(node, out_path)
                try:
                    ov = node.knob("output_view")
                    if ov is not None:
                        ov.setValue("After")
                except Exception:
                    pass
                try:
                    _sync_group_preview_explicit(node, out_path, "After")
                except Exception as exc:
                    _debug_log(f"FIBO Edit preview sync warning (non-fatal): {exc}")
                    try:
                        _sync_group_preview_explicit(node, out_path, "After")
                    except Exception:
                        pass
                _set_status(node, "done")
            except Exception as exc:
                _set_history_debug(node, f"success ui failed: {exc}")
                raise

        _on_main_thread(_apply_success_ui)
        _update_progress(progress, "Finalizing preview...", 95)

        dt = time.perf_counter() - t0
        _debug_log(f"FIBO Edit download timeout used: {download_timeout_s}s")
        _update_progress(progress, percent=100)
        nuke.tprint(f"Bria Nuke FIBO Edit Complete ({dt:.2f}s) | Saved: {out_path}")
    except (BriaConfigError, BriaRequestError) as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        raw_msg = f"Bria Nuke FIBO Edit Error: {exc}"
        msg = _friendly_bria_request_error(node, exc)
        _debug_log(raw_msg)
        if _is_batch_mode():
            raise
        nuke.message(msg)
    except Exception as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        msg = f"Bria Nuke FIBO Edit Exception: {exc}"
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
            ensure_prompt_field_heights(node, "fibo_edit")
        except Exception:
            pass
        return

    if knob_name == "inputChange":
        _sync_structured_prompt_link(node)
        return

    if knob_name == "history_selected_entry":
        _ensure_generation_history_ui(node)
        _update_generation_history_selection_ui(node)
        return

    if knob_name == "preset_selected":
        _ensure_generation_preset_ui(node)
        current = _opt_parm_menu_str(node, "preset_selected")
        _refresh_generation_preset_ui(node, preferred_name=current)
        return

    if knob_name == "use_negative_prompt":
        k = node.knob("negative_prompt")
        if k is not None:
            try:
                k.setVisible(bool(_opt_parm_bool(node, "use_negative_prompt")))
            except Exception:
                pass
        return

    if knob_name == "use_structured_prompt":
        _sync_prompt_mode_ui(node)
        return

    if knob_name in {"output_view", "compare_source"}:
        _sync_group_preview(node)
        return

    if knob_name in {"result_path", "use_global_project_path", "preset_path", "load_presets_from_examples"}:
        _sync_group_preview(node)
        _ensure_generation_preset_ui(node)
        _refresh_generation_preset_ui(node, apply_default_path=(knob_name != "preset_path"))


def on_fibo_edit(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")
    _ensure_generation_metadata_ui(node)
    # Force a fresh sync from any connected Structured Prompt (VGL) node
    # before running. Guards against stale `structured_prompt` state when
    # the upstream SP node was re-run but knobChanged didn't propagate.
    _sync_structured_prompt_link(node)
    fibo_edit_bria(node)
