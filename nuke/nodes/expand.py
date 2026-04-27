"""Nuke Bria Expand node logic."""

from __future__ import annotations

import os
import time
from typing import Any

import nuke  # type: ignore

from adapter import expand_from_files
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
    opt_parm_int as _opt_parm_int,
    opt_parm_menu_str as _opt_parm_menu_str,
    opt_parm_str as _opt_parm_str,
)

_debug_log = debug_logger("Bria Nuke Expand")


def _get_input_dimensions(node: Any) -> tuple[int, int]:
    try:
        src = node.input(0)
    except Exception:
        src = None
    if src is None:
        raise RuntimeError("Connect an input image to Expand node input 1.")

    width = None
    height = None
    try:
        width = int(src.width())
        height = int(src.height())
    except Exception:
        width = None
        height = None

    if not width or not height:
        try:
            fmt = src.format()
            if fmt is not None:
                width = int(fmt.width())
                height = int(fmt.height())
        except Exception:
            width = None
            height = None

    if not width or not height or width <= 0 or height <= 0:
        raise RuntimeError("Unable to determine input image dimensions for directional Expand mode.")

    return width, height


def _build_directional_payload(node: Any, src_w: int, src_h: int) -> tuple[dict, dict, dict]:
    direction = (_opt_parm_menu_str(node, "expand_direction") or "right").strip().lower()
    amount_pct = _opt_parm_int(node, "expand_amount_pct")
    pct = int(amount_pct) if amount_pct is not None else 25
    pct = max(1, min(400, pct))

    dx = max(1, int(round(src_w * (pct / 100.0))))
    dy = max(1, int(round(src_h * (pct / 100.0))))

    add_left = direction in {"left", "top_left", "bottom_left"}
    add_right = direction in {"right", "top_right", "bottom_right"}
    add_top = direction in {"top", "top_left", "top_right"}
    add_bottom = direction in {"bottom", "bottom_left", "bottom_right"}

    canvas_w = src_w + (dx if (add_left or add_right) else 0)
    canvas_h = src_h + (dy if (add_top or add_bottom) else 0)

    offset_x = dx if add_left else 0
    offset_y = dy if add_top else 0

    canvas_size = {"width": int(canvas_w), "height": int(canvas_h)}
    original_image_size = {"width": int(src_w), "height": int(src_h)}
    original_image_location = {"x": int(offset_x), "y": int(offset_y)}
    return canvas_size, original_image_size, original_image_location


def expand_bria(node: Any) -> None:
    t0 = time.perf_counter()
    progress = _create_progress_task("Bria Expand")
    try:
        _ensure_generation_metadata_ui(node)
        _ensure_generation_history_ui(node)
        _on_main_thread(lambda: _set_status(node, "processing"))
        _update_progress(progress, "Preparing request...", 5)

        tmp = _resolve_input_temp_dir("expand")
        run_id = str(int(time.time() * 1000))
        image_path = os.path.join(tmp, f"bria_expand_input_{run_id}.png")

        _export_input_png(node, 0, image_path)
        validate_bria_image_file(image_path, "Input image")
        _update_progress(progress, "Resolving config...", 15)

        cfg = load_config()
        out_path = _resolve_output_path(node, cfg, run_id, "expand")
        use_bearer = bool(cfg.nuke_use_bearer_auth)
        use_env_proxy = cfg.nuke_use_env_proxy
        if use_env_proxy is None:
            use_env_proxy = True
        proxies = resolve_proxies(
            http_proxy=cfg.nuke_http_proxy,
            https_proxy=cfg.nuke_https_proxy,
            use_env_proxy=use_env_proxy,
        )

        prompt = _opt_parm_str(node, "prompt")
        aspect_ratio = _opt_parm_menu_str(node, "aspect_ratio")
        seed = _opt_parm_int(node, "seed")
        expand_mode = (_opt_parm_menu_str(node, "expand_mode") or "aspect_ratio").strip().lower()

        canvas_size = None
        original_image_size = None
        original_image_location = None

        if expand_mode == "directional":
            src_w, src_h = _get_input_dimensions(node)
            canvas_size, original_image_size, original_image_location = _build_directional_payload(node, src_w, src_h)
            aspect_ratio = None

        data = expand_from_files(
            image_path=image_path,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            seed=(int(seed) if seed is not None and int(seed) > 0 else None),
            canvas_size=canvas_size,
            original_image_size=original_image_size,
            original_image_location=original_image_location,
            use_bearer=use_bearer,
            proxies=proxies,
            session=None,
        )
        _update_progress(progress, "Downloading result image...", 70)

        dl_url = extract_image_url(data)
        if not dl_url:
            raise BriaRequestError(f"Unexpected API response (no image_url): {data}")

        img_bytes, _ct = download_url(dl_url, timeout_s=300, proxies=proxies)
        with open(out_path, "wb") as f:
            f.write(img_bytes)

        def _apply_success_ui() -> None:
            payload = {
                "operation": "expand",
                "endpoint": resolve_api_endpoint(cfg),
                "result_path": out_path,
                "response": data,
                "settings": {
                    "expand_mode": expand_mode,
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                    "seed": (int(seed) if seed is not None else None),
                    "canvas_size": canvas_size,
                    "original_image_size": original_image_size,
                    "original_image_location": original_image_location,
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
        nuke.tprint(f"Bria Nuke Expand Complete ({dt:.2f}s) | Saved: {out_path}")
    except (BriaConfigError, BriaRequestError) as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        msg = f"Bria Nuke Expand Error: {exc}"
        _debug_log(msg)
        if _is_batch_mode():
            raise
        nuke.message(msg)
    except Exception as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        msg = f"Bria Nuke Expand Exception: {exc}"
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

    if knob_name == "expand_mode":
        mode = (_opt_parm_menu_str(node, "expand_mode") or "aspect_ratio").strip().lower()
        use_directional = mode == "directional"
        try:
            k_aspect = node.knob("aspect_ratio")
            if k_aspect is not None:
                k_aspect.setVisible(not use_directional)
        except Exception:
            pass
        try:
            k_dir = node.knob("expand_direction")
            if k_dir is not None:
                k_dir.setVisible(use_directional)
        except Exception:
            pass
        try:
            k_amt = node.knob("expand_amount_pct")
            if k_amt is not None:
                k_amt.setVisible(use_directional)
        except Exception:
            pass
        return

    if knob_name in {"output_view", "result_path"}:
        _sync_preview_common(
            node=node,
            result_path=_opt_parm_str(node, "result_path") or "",
            output_view=_opt_parm_menu_str(node, "output_view") or "Before",
        )


def on_expand(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")
    _ensure_generation_metadata_ui(node)
    _ensure_generation_history_ui(node)
    expand_bria(node)
