"""Nuke Bria Enhance node logic."""

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
    apply_mask_export_colorspace as _apply_mask_export_colorspace,
    apply_png_export_colorspace as _apply_png_export_colorspace,
    restore_knobs as _restore_knobs,
    as_nuke_path as _as_nuke_path,
    append_generation_history as _append_generation_history,
    create_progress_task as _create_progress_task,
    ensure_generation_history_ui as _ensure_generation_history_ui,
    export_input_png as _export_input_png,
    export_mask_input_png as _export_mask_input_png,
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
    opt_parm_int as _opt_parm_int,
    opt_parm_menu_str as _opt_parm_menu_str,
    opt_parm_str as _opt_parm_str,
)

_debug_log = debug_logger("Bria Nuke Enhance")

_ENHANCE_REQUIRED_INTERNAL_NODES = (
    "input_image",
    "input_mask",
    "mask_to_alpha_expr",
    "loader_result",
    "before_to_result",
    "mask_to_result",
    "keymix_region",
    "switch_before_after",
    "output1",
)

_ENHANCE_NC_KEYMIX_NODES = (
    "input_image",
    "input_mask",
    "loader_result",
    "before_to_result",
    "mask_to_result",
    "keymix_region",
    "switch_before_after",
    "output1",
)


def _looks_like_enhance_group(node: Any) -> bool:
    if node is None:
        return False
    try:
        return node.knob("run_enhance") is not None and node.knob("result_path") is not None
    except Exception:
        return False


def _resolve_enhance_group_node(node: Any = None) -> Any:
    if _looks_like_enhance_group(node):
        return node

    try:
        current = nuke.thisNode()
    except Exception:
        current = None
    if _looks_like_enhance_group(current):
        return current

    try:
        selected = nuke.selectedNode()
    except Exception:
        selected = None
    if _looks_like_enhance_group(selected):
        return selected

    return None


def _enhance_variant(node: Any) -> str:
    value = (_opt_parm_str(node, "enhance_variant") or "").strip().lower()
    return value or "full"


def _sync_mask_expression_from_knob(node: Any) -> None:
    if node is None:
        return
    if _enhance_variant(node) != "full":
        return
    try:
        node.begin()
        mask_expr = nuke.toNode("mask_to_alpha_expr")
        if mask_expr is not None:
            expr = "max(max(r,g),max(b,a))" if bool(_opt_parm_bool(node, "use_mask")) else "1"
            for knob_name in ("expr0", "expr1", "expr2", "expr3"):
                expr_knob = mask_expr.knob(knob_name)
                if expr_knob is not None:
                    expr_knob.setValue(expr)
            label_knob = mask_expr.knob("label")
            if label_knob is not None:
                label_knob.setValue("Mask max(r,g,b,a)" if bool(_opt_parm_bool(node, "use_mask")) else "Mask disabled -> full result")
    finally:
        try:
            node.end()
        except Exception:
            pass


def _sync_nc_keymix_from_knob(node: Any) -> None:
    """Toggle use_mask on nc_keymix by rewiring switch_before_after:
    use_mask=False → switch → loader_result (full enhanced, bypass keymix);
    use_mask=True  → switch → keymix_region (mask-controlled blend)."""
    if node is None:
        return
    if _enhance_variant(node) != "nc_keymix":
        return
    try:
        node.begin()
        keymix_region = nuke.toNode("keymix_region")
        switch_before_after = nuke.toNode("switch_before_after")
        loader_result = nuke.toNode("loader_result")
        use_mask = bool(_opt_parm_bool(node, "use_mask"))
        if keymix_region is not None:
            mix_knob = keymix_region.knob("mix")
            if mix_knob is not None:
                mix_knob.setValue(1.0)
        if switch_before_after is not None:
            if use_mask and keymix_region is not None:
                switch_before_after.setInput(1, keymix_region)
            elif not use_mask and loader_result is not None:
                switch_before_after.setInput(1, loader_result)
    finally:
        try:
            node.end()
        except Exception:
            pass


def _repair_enhance_graph(node: Any) -> bool:
    if node is None:
        return False
    try:
        import _bria_nuke_menu as _bria_menu  # type: ignore

        repair_fn = getattr(_bria_menu, "_repair_enhance_internal_graph", None)
        if callable(repair_fn):
            return bool(repair_fn(node))
    except Exception:
        pass
    return False


def _ensure_enhance_internal_graph(node: Any) -> bool:
    if node is None:
        return False

    variant = _enhance_variant(node)
    required = _ENHANCE_REQUIRED_INTERNAL_NODES if variant == "full" else _ENHANCE_NC_KEYMIX_NODES

    missing_names: list[str] = []
    try:
        node.begin()
        for node_name in required:
            if nuke.toNode(node_name) is None:
                missing_names.append(node_name)
    finally:
        try:
            node.end()
        except Exception:
            pass

    if not missing_names:
        return True

    return _repair_enhance_graph(node)


def _force_validate_read(node: Any) -> None:
    if node is None:
        return
    try:
        force_validate = getattr(node, "forceValidate", None)
        if callable(force_validate):
            force_validate()
    except Exception:
        pass


def _export_internal_image_input_png(
    node: Any,
    internal_node_name: str,
    dst_path: str,
    missing_input_message: str,
) -> str:
    writer = None
    source_node = None
    old_color_values: dict[str, str | None] = {}
    try:
        node.begin()
        source_node = nuke.toNode(internal_node_name)
        if source_node is None:
            raise RuntimeError(missing_input_message)
        writer = nuke.nodes.Write(inputs=[source_node], file=_as_nuke_path(dst_path), file_type="png")
        old_color_values = _apply_png_export_colorspace(writer)
        frame = int(nuke.frame())
        nuke.execute(writer.name(), frame, frame)
    finally:
        try:
            if writer is not None:
                _restore_knobs(writer, old_color_values)
        except Exception:
            pass
        try:
            if writer is not None:
                nuke.delete(writer)
        except Exception:
            pass
        try:
            node.end()
        except Exception:
            pass

    if not os.path.exists(dst_path) or os.path.getsize(dst_path) <= 0:
        raise RuntimeError(f"Failed to export internal image input: {dst_path}")
    return dst_path


def _export_internal_mask_input_png(
    node: Any,
    internal_node_name: str,
    dst_path: str,
    missing_input_message: str,
) -> str:
    normalize = None
    writer = None
    source_node = None
    old_color_values: dict[str, str | None] = {}
    try:
        node.begin()
        source_node = nuke.toNode(internal_node_name)
        if source_node is None:
            raise RuntimeError(missing_input_message)
        normalize = nuke.nodes.Expression(inputs=[source_node])
        expr = "max(max(r,g),max(b,a))"
        for knob_name in ("expr0", "expr1", "expr2", "expr3"):
            expr_knob = normalize.knob(knob_name)
            if expr_knob is not None:
                expr_knob.setValue(expr)
        writer = nuke.nodes.Write(inputs=[normalize], file=_as_nuke_path(dst_path), file_type="png")
        old_color_values = _apply_mask_export_colorspace(writer)
        frame = int(nuke.frame())
        nuke.execute(writer.name(), frame, frame)
    finally:
        try:
            if writer is not None:
                _restore_knobs(writer, old_color_values)
        except Exception:
            pass
        try:
            if writer is not None:
                nuke.delete(writer)
        except Exception:
            pass
        try:
            if normalize is not None:
                nuke.delete(normalize)
        except Exception:
            pass
        try:
            node.end()
        except Exception:
            pass

    if not os.path.exists(dst_path) or os.path.getsize(dst_path) <= 0:
        raise RuntimeError(f"Failed to export internal mask input: {dst_path}")
    return dst_path


def _bake_masked_result_with_temp_nodes(before_path: str, result_path: str, mask_path: str) -> None:
    temp_output = f"{result_path}.masked_tmp.png"
    temp_nodes: list[Any] = []
    writer = None
    old_color_values: dict[str, str | None] = {}
    try:
        before_read = nuke.nodes.Read(file=_as_nuke_path(before_path))
        temp_nodes.append(before_read)
        result_read = nuke.nodes.Read(file=_as_nuke_path(result_path))
        temp_nodes.append(result_read)
        mask_read = nuke.nodes.Read(file=_as_nuke_path(mask_path))
        temp_nodes.append(mask_read)

        before_to_result = nuke.nodes.Reformat(inputs=[before_read])
        temp_nodes.append(before_to_result)
        mask_to_result = nuke.nodes.Reformat(inputs=[mask_read])
        temp_nodes.append(mask_to_result)

        _force_validate_read(result_read)
        width = int(result_read.width() or 0)
        height = int(result_read.height() or 0)
        if width > 0 and height > 0:
            _configure_reformat(before_to_result, width, height)
            _configure_reformat(mask_to_result, width, height, filter_name="Impulse")

        keymix = nuke.nodes.Keymix(inputs=[before_to_result, result_read, mask_to_result])
        temp_nodes.append(keymix)
        try:
            if keymix.knob("maskChannel") is not None:
                keymix.knob("maskChannel").setValue("rgba.red")
        except Exception:
            pass

        writer = nuke.nodes.Write(inputs=[keymix], file=_as_nuke_path(temp_output), file_type="png")
        old_color_values = _apply_png_export_colorspace(writer)
        frame = int(nuke.frame())
        nuke.execute(writer.name(), frame, frame)
        if not os.path.exists(temp_output) or os.path.getsize(temp_output) <= 0:
            raise RuntimeError(f"Failed to bake masked enhance result: {temp_output}")
        os.replace(temp_output, result_path)
    finally:
        if writer is not None:
            try:
                _restore_knobs(writer, old_color_values)
            except Exception:
                pass
        for temp_node in reversed(temp_nodes + ([writer] if writer is not None else [])):
            try:
                if temp_node is not None:
                    nuke.delete(temp_node)
            except Exception:
                pass
        try:
            if os.path.exists(temp_output):
                os.remove(temp_output)
        except Exception:
            pass


def _configure_reformat(node: Any, width: int, height: int, filter_name: str | None = None) -> None:
    if node is None:
        return
    try:
        if node.knob("type") is not None:
            node.knob("type").setValue("to box")
    except Exception:
        pass
    try:
        if node.knob("box_width") is not None:
            node.knob("box_width").setValue(int(width))
        if node.knob("box_height") is not None:
            node.knob("box_height").setValue(int(height))
    except Exception:
        pass
    try:
        if node.knob("box_fixed") is not None:
            node.knob("box_fixed").setValue(True)
    except Exception:
        pass
    try:
        if node.knob("resize") is not None:
            node.knob("resize").setValue("distort")
    except Exception:
        pass
    try:
        if node.knob("center") is not None:
            node.knob("center").setValue(False)
    except Exception:
        pass
    if filter_name:
        try:
            if node.knob("filter") is not None:
                node.knob("filter").setValue(filter_name)
        except Exception:
            pass


def _sync_reformat_to_result(node: Any) -> None:
    """Resize before_to_result and mask_to_result Reformats to match the current loader_result
    dimensions.  Called after a new result is successfully loaded — never on knob toggles."""
    if node is None:
        return
    variant = _enhance_variant(node)
    if variant not in {"full", "nc_keymix"}:
        return
    try:
        node.begin()
        loader_result = nuke.toNode("loader_result")
        before_to_result = nuke.toNode("before_to_result")
        mask_to_result = nuke.toNode("mask_to_result")
        if loader_result is not None and before_to_result is not None and mask_to_result is not None:
            _force_validate_read(loader_result)
            width = int(loader_result.width() or 0)
            height = int(loader_result.height() or 0)
            if width > 0 and height > 0:
                _configure_reformat(before_to_result, width, height)
                _configure_reformat(mask_to_result, width, height, filter_name="Impulse")
    finally:
        try:
            node.end()
        except Exception:
            pass


def _ensure_mask_preview_graph(node: Any) -> None:
    """Update expression values and keymix mix on use_mask / enhance_variant changes.
    Does NOT rewire node inputs and does NOT resize Reformat nodes — those are stable
    after creation and are only updated by _sync_reformat_to_result after a new result
    is loaded."""
    if node is None:
        return
    variant = _enhance_variant(node)
    if variant not in {"full", "nc_keymix"}:
        return
    try:
        node.begin()

        if variant == "full":
            input_mask = nuke.toNode("input_mask")
            mask_to_alpha_expr = nuke.toNode("mask_to_alpha_expr")
            mask_to_result = nuke.toNode("mask_to_result")
            if input_mask is not None and mask_to_alpha_expr is not None:
                mask_to_alpha_expr.setInput(0, input_mask)
                try:
                    expr = "max(max(r,g),max(b,a))" if bool(_opt_parm_bool(node, "use_mask")) else "1"
                    for knob_name in ("expr0", "expr1", "expr2", "expr3"):
                        if mask_to_alpha_expr.knob(knob_name) is not None:
                            mask_to_alpha_expr.knob(knob_name).setValue(expr)
                    if mask_to_alpha_expr.knob("label") is not None:
                        mask_to_alpha_expr.knob("label").setValue(
                            "Mask max(r,g,b,a)" if bool(_opt_parm_bool(node, "use_mask")) else "Mask disabled -> full result"
                        )
                except Exception:
                    pass
            if mask_to_result is not None and mask_to_alpha_expr is not None:
                mask_to_result.setInput(0, mask_to_alpha_expr)

        elif variant == "nc_keymix":
            keymix_region = nuke.toNode("keymix_region")
            switch_before_after = nuke.toNode("switch_before_after")
            loader_result = nuke.toNode("loader_result")
            use_mask = bool(_opt_parm_bool(node, "use_mask"))
            # Keep keymix mix=1.0 always; control the After path via switch wiring.
            if keymix_region is not None:
                mix_knob = keymix_region.knob("mix")
                if mix_knob is not None:
                    mix_knob.setValue(1.0)
            if switch_before_after is not None:
                if use_mask and keymix_region is not None:
                    # Mask on: After → keymix (A=original blended with B=enhanced via mask)
                    switch_before_after.setInput(1, keymix_region)
                elif not use_mask and loader_result is not None:
                    # Mask off: After → loader_result directly (full enhanced, no masking)
                    switch_before_after.setInput(1, loader_result)

    finally:
        try:
            node.end()
        except Exception:
            pass


def enhance_bria(node: Any) -> None:
    t0 = time.perf_counter()
    progress = _create_progress_task("Bria Enhance")
    try:
        _ensure_generation_metadata_ui(node)
        _ensure_generation_history_ui(node)
        _on_main_thread(lambda: _set_status(node, "processing"))
        _update_progress(progress, "Preparing request...", 5)

        tmp = _resolve_input_temp_dir("enhance")
        run_id = str(int(time.time() * 1000))
        in_path = os.path.join(tmp, f"bria_enhance_input_{run_id}.png")

        try:
            _export_internal_image_input_png(
                node,
                "input_image",
                in_path,
                "Connect an image to input 1.",
            )
        except RuntimeError as exc:
            try:
                _export_input_png(node, 0, in_path)
            except RuntimeError:
                input0 = None
                input_count = None
                variant = ""
                try:
                    input0 = node.input(0)
                except Exception:
                    input0 = None
                try:
                    inputs_knob = node.knob("inputs")
                    if inputs_knob is not None:
                        input_count = int(inputs_knob.value())
                except Exception:
                    input_count = None
                try:
                    variant = str(node.knob("enhance_variant").value() or "") if node.knob("enhance_variant") is not None else ""
                except Exception:
                    variant = ""
                source_name = "None"
                try:
                    if input0 is not None:
                        source_name = str(input0.name())
                except Exception:
                    source_name = "<unavailable>"
                raise RuntimeError(
                    f"{exc} Detail: variant={variant or '<unset>'}, group_inputs={input_count}, external_input_1={source_name}"
                )
        validate_bria_image_file(in_path, "Input image")

        _update_progress(progress, "Resolving config...", 15)

        resolution = (_opt_parm_menu_str(node, "resolution") or "2MP").strip()
        seed = _opt_parm_int(node, "seed")
        sync = True
        if _opt_parm_bool(node, "sync") is False:
            _debug_log(
                "Enhance sync disabled on node; forcing sync=True because async polling is not implemented."
            )
            _on_main_thread(lambda: node.knob("sync").setValue(True) if node.knob("sync") is not None else None)

        cfg = load_config()
        out_path = _resolve_output_path(node, cfg, run_id, "enhance")
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
            mode="enhance",
            desired_increase=None,
            resolution=resolution,
            seed=int(seed) if seed is not None and int(seed) > 0 else None,
            sync=sync,
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

        if _enhance_variant(node) == "full" and bool(_opt_parm_bool(node, "use_mask")):
            mask_path = os.path.join(tmp, f"bria_enhance_mask_{run_id}.png")
            try:
                _export_internal_mask_input_png(
                    node,
                    "input_mask",
                    mask_path,
                    "Connect a mask to input 2 or disable Use Mask.",
                )
            except RuntimeError:
                _export_mask_input_png(
                    node,
                    1,
                    mask_path,
                    missing_input_message="Connect a mask to input 2 or disable Use Mask.",
                )
            _update_progress(progress, "Applying mask composite...", 82)
            _bake_masked_result_with_temp_nodes(in_path, out_path, mask_path)

        def _apply_success_ui() -> None:
            payload = {
                "operation": "enhance",
                "endpoint": resolve_api_endpoint(cfg),
                "result_path": out_path,
                "response": data,
                "settings": {
                    "resolution": resolution,
                    "seed": (int(seed) if seed is not None else None),
                    "sync": bool(sync),
                    "preserve_alpha": bool(_opt_parm_bool(node, "preserve_alpha")),
                    "use_mask": bool(_opt_parm_bool(node, "use_mask")),
                    "variant": _enhance_variant(node),
                },
            }
            _set_generation_metadata(node, payload)
            _append_generation_history(node, payload)
            _set_result_path(node, out_path)
            _ensure_mask_preview_graph(node)
            _sync_mask_expression_from_knob(node)
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
            _sync_reformat_to_result(node)
            _set_status(node, "done")

        _on_main_thread(_apply_success_ui)
        _update_progress(progress, "Finalizing preview...", 95)

        dt = time.perf_counter() - t0
        _update_progress(progress, percent=100)
        nuke.tprint(f"Bria Nuke Enhance Complete ({dt:.2f}s) | Saved: {out_path}")
    except (BriaConfigError, BriaRequestError) as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        msg = f"Bria Nuke Enhance Error: {exc}"
        _debug_log(msg)
        if _is_batch_mode():
            raise
        nuke.message(msg)
    except Exception as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        msg = f"Bria Nuke Enhance Exception: {exc}"
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

    if knob_name in {"use_mask", "enhance_variant"}:
        _ensure_mask_preview_graph(node)
        if knob_name == "use_mask":
            _sync_mask_expression_from_knob(node)
            _sync_nc_keymix_from_knob(node)
        return

    if knob_name in {"output_view", "result_path"}:
        _sync_preview_common(
            node=node,
            result_path=_opt_parm_str(node, "result_path") or "",
            output_view=_opt_parm_menu_str(node, "output_view") or "Before",
        )
        if knob_name == "result_path":
            _sync_reformat_to_result(node)


def on_enhance(node: Any = None) -> None:
    node = _resolve_enhance_group_node(node)
    if node is None:
        raise ValueError("Select a Bria Enhance group node before running Enhance.")
    _ensure_generation_metadata_ui(node)
    _ensure_generation_history_ui(node)
    enhance_bria(node)
