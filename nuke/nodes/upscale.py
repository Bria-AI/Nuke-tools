"""Nuke Bria Upscale node logic."""

from __future__ import annotations

import os
import time
from typing import Any

import nuke  # type: ignore

from adapter import upscale_from_files
from bria_core.config import load_config, resolve_api_endpoint
from bria_core.errors import BriaConfigError, BriaRequestError
from bria_core.utils import download_url, extract_image_url, resolve_proxies, validate_bria_image_file
from common import (
    is_batch_mode as _is_batch_mode,
    append_generation_history as _append_generation_history,
    create_progress_task as _create_progress_task,
    ensure_generation_history_ui as _ensure_generation_history_ui,
    export_input_png as _export_input_png,
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

_debug_log = debug_logger("Bria Nuke Upscale")


def upscale_bria(node: Any) -> None:
    t0 = time.perf_counter()
    progress = _create_progress_task("Bria Upscale")
    try:
        _ensure_generation_metadata_ui(node)
        _ensure_generation_history_ui(node)
        _on_main_thread(lambda: _set_status(node, "processing"))
        _update_progress(progress, "Preparing request...", 5)

        tmp = _resolve_input_temp_dir("upscale")
        run_id = str(int(time.time() * 1000))
        in_path = os.path.join(tmp, f"bria_upscale_input_{run_id}.png")

        _export_input_png(node, 0, in_path)
        validate_bria_image_file(in_path, "Input image")
        _update_progress(progress, "Resolving config...", 15)

        desired_increase = int((_opt_parm_menu_str(node, "desired_increase") or "2").strip() or "2")

        cfg = load_config()
        out_path = _resolve_output_path(node, cfg, run_id, "upscale")
        use_bearer = bool(cfg.nuke_use_bearer_auth)
        use_env_proxy = cfg.nuke_use_env_proxy
        if use_env_proxy is None:
            use_env_proxy = True
        proxies = resolve_proxies(
            http_proxy=cfg.nuke_http_proxy,
            https_proxy=cfg.nuke_https_proxy,
            use_env_proxy=use_env_proxy,
        )

        data = upscale_from_files(
            image_path=in_path,
            mode="increase_resolution",
            desired_increase=desired_increase,
            resolution=None,
            preserve_alpha=bool(_opt_parm_bool(node, "preserve_alpha")),
            use_bearer=use_bearer,
            proxies=proxies,
            session=None,
        )
        _update_progress(progress, "Downloading result image...", 70)

        dl_url = extract_image_url(data)
        if not dl_url:
            raise BriaRequestError(f"Unexpected API response (no image_url): {data}")

        img_bytes, _content_type = download_url(dl_url, timeout_s=300, proxies=proxies)
        with open(out_path, "wb") as f:
            f.write(img_bytes)

        def _apply_success_ui() -> None:
            payload = {
                "operation": "upscale",
                "endpoint": resolve_api_endpoint(cfg),
                "result_path": out_path,
                "response": data,
                "settings": {
                    "mode": "increase_resolution",
                    "desired_increase": desired_increase,
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
        nuke.tprint(f"Bria Nuke Upscale Complete ({dt:.2f}s) | Saved: {out_path}")
    except (BriaConfigError, BriaRequestError) as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        msg = f"Bria Nuke Upscale Error: {exc}"
        _debug_log(msg)
        if _is_batch_mode():
            raise
        nuke.message(msg)
    except Exception as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        msg = f"Bria Nuke Upscale Exception: {exc}"
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

    if knob_name in {"output_view", "result_path"}:
        _sync_preview_common(
            node=node,
            result_path=_opt_parm_str(node, "result_path") or "",
            output_view=_opt_parm_menu_str(node, "output_view") or "Before",
        )


def on_upscale(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")
    _ensure_generation_metadata_ui(node)
    _ensure_generation_history_ui(node)
    upscale_bria(node)
