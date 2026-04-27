"""Nuke Bria GenFill node logic."""

from __future__ import annotations

import os
import time
from typing import Any

import nuke  # type: ignore

from adapter import genfill_from_files
from bria_core.config import load_config, resolve_api_endpoint
from bria_core.errors import BriaConfigError, BriaRequestError
from bria_core.utils import download_url, extract_image_url, resolve_proxies, validate_bria_image_file
from common import (
    is_batch_mode as _is_batch_mode,
    append_generation_history as _append_generation_history,
    create_progress_task as _create_progress_task,
    ensure_generation_history_ui as _ensure_generation_history_ui,
    ensure_parent_dir as _ensure_parent_dir,
    export_input_png as _export_input_png,
    export_mask_input_png as _export_mask_input_png,
    export_via_internal_save as _export_via_internal_save,
    on_main_thread as _on_main_thread,
    refresh_generation_history_ui as _refresh_generation_history_ui,
    update_generation_history_selection_ui as _update_generation_history_selection_ui,
    resolve_input_temp_dir as _resolve_input_temp_dir,
    resolve_output_path as _resolve_output_path,
    set_status as _set_status,
    set_generation_metadata as _set_generation_metadata,
    ensure_generation_metadata_ui as _ensure_generation_metadata_ui,
    set_result_path as _set_result_path,
    sync_preview as _sync_preview_common,
    update_progress as _update_progress,
)
from node_utils import (
    debug_logger,
    opt_parm_bool as _opt_parm_bool,
    opt_parm_menu_str as _opt_parm_menu_str,
    opt_parm_str as _opt_parm_str,
)

_debug_log = debug_logger("Bria Nuke GenFill")


def genfill_bria(node: Any) -> None:
    t0 = time.perf_counter()
    progress = _create_progress_task("Bria GenFill")
    try:
        _ensure_generation_metadata_ui(node)
        _ensure_generation_history_ui(node)
        _on_main_thread(lambda: _set_status(node, "processing"))
        _update_progress(progress, "Preparing request...", 5)
        prompt = (_opt_parm_str(node, "prompt") or "").strip()
        if not prompt:
            raise RuntimeError("Prompt is required.")

        use_negative = bool(_opt_parm_bool(node, "use_negative_prompt"))
        negative_prompt = (_opt_parm_str(node, "negative_prompt") or "").strip() if use_negative else None

        tmp = _resolve_input_temp_dir("genfill")
        run_id = str(int(time.time() * 1000))
        image_path = os.path.join(tmp, f"bria_genfill_input_{run_id}.png")
        mask_path = os.path.join(tmp, f"bria_genfill_mask_{run_id}.png")

        _export_via_internal_save(node, "save_image", image_path) or _export_input_png(
            node,
            0,
            image_path,
            missing_input_message="Connect image to input 1 and mask to input 2.",
        )
        _export_mask_input_png(
            node,
            1,
            mask_path,
            missing_input_message="Connect image to input 1 and mask to input 2.",
        )

        validate_bria_image_file(image_path, "Input image")
        validate_bria_image_file(mask_path, "Mask")
        _update_progress(progress, "Resolving config...", 15)

        cfg = load_config()
        out_path = _resolve_output_path(node, cfg, run_id, "genfill")
        use_bearer = bool(cfg.nuke_use_bearer_auth)
        use_env_proxy = cfg.nuke_use_env_proxy
        if use_env_proxy is None:
            use_env_proxy = True
        proxies = resolve_proxies(
            http_proxy=cfg.nuke_http_proxy,
            https_proxy=cfg.nuke_https_proxy,
            use_env_proxy=use_env_proxy,
        )

        data = genfill_from_files(
            image_path=image_path,
            mask_path=mask_path,
            prompt=prompt,
            negative_prompt=negative_prompt,
            preserve_alpha=bool(_opt_parm_bool(node, "preserve_alpha")),
            allow_multipart_fallback=True,
            use_bearer=use_bearer,
            proxies=proxies,
            session=None,
        )
        _update_progress(progress, "Downloading result image...", 70)

        dl_url = extract_image_url(data)
        if not dl_url:
            raise BriaRequestError(f"Unexpected API response (no image_url): {data}")

        img_bytes, _ct = download_url(dl_url, timeout_s=300, proxies=proxies)
        _ensure_parent_dir(out_path)
        with open(out_path, "wb") as f:
            f.write(img_bytes)

        def _apply_success_ui() -> None:
            payload = {
                "operation": "genfill",
                "endpoint": resolve_api_endpoint(cfg),
                "result_path": out_path,
                "mask_mode": "manual",
                "response": data,
                "settings": {
                    "prompt": prompt,
                    "negative_prompt": negative_prompt,
                    "preserve_alpha": bool(_opt_parm_bool(node, "preserve_alpha")),
                },
            }
            _set_generation_metadata(node, payload)
            _append_generation_history(node, payload)
            _set_result_path(node, out_path)
            try:
                ov = node.knob("output_view")
                if ov is not None:
                    ov.setValue("After")
            except Exception:
                pass
            _sync_preview_common(
                node=node,
                result_path=out_path,
                output_view="After",
            )
            _set_status(node, "done")

        _on_main_thread(_apply_success_ui)
        _update_progress(progress, "Finalizing preview...", 95)

        dt = time.perf_counter() - t0
        _update_progress(progress, percent=100)
        nuke.tprint(f"Bria Nuke GenFill Complete ({dt:.2f}s) | Saved: {out_path}")
    except (BriaConfigError, BriaRequestError) as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        msg = f"Bria Nuke GenFill Error: {exc}"
        _debug_log(msg)
        if _is_batch_mode():
            raise
        nuke.message(msg)
    except Exception as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        msg = f"Bria Nuke GenFill Exception: {exc}"
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
    if knob_name == "inputChange":
        return

    if knob_name == "history_selected_entry":
        _ensure_generation_history_ui(node)
        _update_generation_history_selection_ui(node)
        return

    _ensure_generation_metadata_ui(node)

    if knob_name == "use_negative_prompt":
        neg = node.knob("negative_prompt")
        if neg is not None:
            try:
                neg.setVisible(bool(_opt_parm_bool(node, "use_negative_prompt")))
            except Exception:
                pass
        return

    if knob_name in {"output_view", "result_path"}:
        _sync_preview_common(
            node=node,
            result_path=_opt_parm_str(node, "result_path") or "",
            output_view=_opt_parm_menu_str(node, "output_view") or "Before",
        )


def on_genfill(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")
    _ensure_generation_metadata_ui(node)
    _ensure_generation_history_ui(node)
    genfill_bria(node)
