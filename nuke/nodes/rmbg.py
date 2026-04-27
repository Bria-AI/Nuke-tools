"""Nuke Bria RMBG node logic."""

from __future__ import annotations

import os
import time
import traceback
import hashlib
from typing import Any

import nuke  # type: ignore

from adapter import remove_background_from_files, resolve_rmbg_request_url
from bria_core.config import load_config, resolve_api_endpoint
from bria_core.errors import BriaConfigError, BriaRequestError
from bria_core.utils import download_url, extract_image_url, resolve_proxies, validate_bria_image_file
from common import (
    is_batch_mode as _is_batch_mode,
    append_generation_history as _append_generation_history,
    create_progress_task as _create_progress_task,
    ensure_generation_history_ui as _ensure_generation_history_ui,
    export_input_png as _export_input_png,
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
from node_utils import debug_logger, opt_parm_bool as _opt_parm_bool, opt_parm_menu_str as _opt_parm_menu_str, opt_parm_str as _opt_parm_str

_debug_log = debug_logger("Bria Nuke RMBG")


def _file_md5(path: str) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sync_premultiply(node: Any) -> None:
    if node is None:
        return
    premultiply_on = bool(_opt_parm_bool(node, "premultiply"))
    try:
        node.begin()
        premult_node = nuke.toNode("premult_result")
        if premult_node is not None and premult_node.knob("disable") is not None:
            premult_node.knob("disable").setValue(not premultiply_on)
    finally:
        try:
            node.end()
        except Exception:
            pass


def rmbg_bria(node: Any) -> None:
    t0 = time.perf_counter()
    progress = _create_progress_task("Bria RMBG")
    try:
        _ensure_generation_metadata_ui(node)
        _ensure_generation_history_ui(node)
        _on_main_thread(lambda: _set_status(node, "processing"))
        _update_progress(progress, "Preparing request...", 5)
        _debug_log("Starting RMBG run")
        tmp = _resolve_input_temp_dir("rmbg")
        run_id = str(int(time.time() * 1000))
        in_path = os.path.join(tmp, f"bria_rmbg_input_{run_id}.png")

        _export_via_internal_save(node, "save_image", in_path) or _export_input_png(node, 0, in_path)
        validate_bria_image_file(in_path, "Input image")
        if os.getenv("BRIA_DEBUG"):
            try:
                in_size = os.path.getsize(in_path)
                in_md5 = _file_md5(in_path)
                _debug_log(f"Input exported path={in_path} | bytes={in_size} | md5={in_md5}")
            except Exception as exc:
                _debug_log(f"Input export diagnostics failed: {exc}")
        _update_progress(progress, "Resolving config...", 15)

        cfg = load_config()
        out_path = _resolve_output_path(node, cfg, run_id, "rmbg")
        use_bearer = bool(cfg.nuke_use_bearer_auth)
        preserve_alpha = bool(_opt_parm_bool(node, "preserve_alpha"))
        use_env_proxy = cfg.nuke_use_env_proxy
        if use_env_proxy is None:
            use_env_proxy = True
        proxies = resolve_proxies(
            http_proxy=cfg.nuke_http_proxy,
            https_proxy=cfg.nuke_https_proxy,
            use_env_proxy=use_env_proxy,
        )

        req_url = resolve_rmbg_request_url()
        _debug_log(
            f"Request URL={req_url} | preserve_alpha={preserve_alpha} | "
            f"use_bearer={use_bearer} | use_env_proxy={use_env_proxy}"
        )

        data = remove_background_from_files(
            image_path=in_path,
            preserve_alpha=preserve_alpha,
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
        if os.getenv("BRIA_DEBUG"):
            try:
                out_size = os.path.getsize(out_path)
                out_md5 = _file_md5(out_path)
                _debug_log(f"Output downloaded path={out_path} | bytes={out_size} | md5={out_md5}")
            except Exception as exc:
                _debug_log(f"Output diagnostics failed: {exc}")

        def _apply_success_ui() -> None:
            payload = {
                "operation": "remove_background",
                "endpoint": resolve_api_endpoint(cfg),
                "result_path": out_path,
                "response": data,
                "settings": {
                    "preserve_alpha": preserve_alpha,
                    "premultiply": bool(_opt_parm_bool(node, "premultiply")),
                },
            }
            _set_generation_metadata(node, payload)
            _append_generation_history(node, payload)
            _set_result_path(node, out_path)
            _sync_premultiply(node)
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
        _debug_log(f"Completed successfully in {dt:.2f}s | saved={out_path}")
        nuke.tprint(f"Bria Nuke RMBG Complete ({dt:.2f}s) | Saved: {out_path}")
    except (BriaConfigError, BriaRequestError) as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        msg = f"Bria Nuke RMBG Error: {exc}"
        _debug_log(msg)
        if _is_batch_mode():
            raise
        nuke.message(msg)
    except Exception as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        msg = f"Bria Nuke RMBG Exception: {exc}"
        _debug_log(msg)
        _debug_log(traceback.format_exc())
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
    if knob_name == "premultiply":
        _sync_premultiply(node)
        return
    if knob_name in {"output_view", "result_path"}:
        _sync_premultiply(node)
        _sync_preview_common(
            node=node,
            result_path=_opt_parm_str(node, "result_path") or "",
            output_view=_opt_parm_menu_str(node, "output_view") or "Before",
        )


def on_rmbg(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")
    _ensure_generation_metadata_ui(node)
    _ensure_generation_history_ui(node)
    rmbg_bria(node)
