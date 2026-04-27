"""Image export and colorspace utilities for Bria Nuke nodes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import nuke  # type: ignore


def as_nuke_path(path: str) -> str:
    return str(path or "").replace("\\", "/")


def _preferred_png_colorspace() -> str:
    try:
        root = nuke.root()
        if root is not None:
            k = root.knob("int8Lut")
            if k is not None:
                v = str(k.value() or "").strip()
                if v:
                    return v
    except Exception:
        pass
    return "sRGB"


def _set_knob_if_present(node: Any, knob_name: str, value: str) -> str | None:
    if node is None:
        return None
    knob = node.knob(knob_name)
    if knob is None:
        return None
    old_val = None
    try:
        old_val = str(knob.value())
    except Exception:
        old_val = None
    try:
        knob.setValue(value)
    except Exception:
        pass
    return old_val


def _set_knob_from_candidates(node: Any, knob_name: str, candidates: tuple[str, ...]) -> str | None:
    if node is None:
        return None
    knob = node.knob(knob_name)
    if knob is None:
        return None

    old_val = None
    try:
        old_val = str(knob.value())
    except Exception:
        old_val = None

    allowed: set[str] | None = None
    try:
        vals = knob.values()
        if vals is not None:
            allowed = {str(v) for v in vals}
    except Exception:
        allowed = None

    for candidate in candidates:
        if not candidate:
            continue
        if allowed is not None and candidate not in allowed:
            continue
        try:
            knob.setValue(candidate)
            return old_val
        except Exception:
            continue

    return old_val


def apply_png_export_colorspace(node: Any) -> dict[str, str | None]:
    target = _preferred_png_colorspace()
    return {
        "out_colorspace": _set_knob_if_present(node, "out_colorspace", target),
        "ocioColorspace": _set_knob_if_present(node, "ocioColorspace", target),
        "colorspace": _set_knob_if_present(node, "colorspace", target),
    }


def apply_mask_export_colorspace(node: Any) -> dict[str, str | None]:
    return {
        "out_colorspace": _set_knob_from_candidates(node, "out_colorspace", ("scene_linear", "linear", "sRGB")),
        "ocioColorspace": _set_knob_from_candidates(node, "ocioColorspace", ("scene_linear", "linear", "raw", "sRGB")),
        "colorspace": _set_knob_from_candidates(node, "colorspace", ("scene_linear", "linear", "raw", "sRGB")),
    }


def restore_knobs(node: Any, values: dict[str, str | None]) -> None:
    if node is None:
        return
    for knob_name, old_val in (values or {}).items():
        if old_val is None:
            continue
        knob = node.knob(knob_name)
        if knob is None:
            continue
        try:
            knob.setValue(old_val)
        except Exception:
            pass


def write_result_sidecar(image_path: str, metadata: dict[str, Any]) -> str | None:
    """Write a .json sidecar file alongside *image_path* containing *metadata*.

    Returns the sidecar path on success, None if it could not be written.
    The sidecar has the same stem as the image with a .json extension, e.g.
    ``bria_fibo_generate_result_123.png``  ->  ``bria_fibo_generate_result_123.json``.

    Skipped when *image_path* is already a .json file (e.g. structured prompt
    output) to avoid overwriting the result with the metadata sidecar.
    """
    if not image_path:
        return None
    if Path(image_path).suffix.lower() == ".json":
        return None
    try:
        sidecar_path = str(Path(image_path).with_suffix(".json"))
        text = json.dumps(metadata, indent=2, ensure_ascii=False, default=str)
        with open(sidecar_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return sidecar_path
    except Exception:
        return None


def export_input_png(
    node: Any,
    input_index: int,
    dst_path: str,
    missing_input_message: str | None = None,
) -> str:
    src = None
    try:
        src = node.input(input_index)
    except Exception:
        src = None
    if src is None:
        raise RuntimeError(missing_input_message or f"Connect an image to input {input_index + 1}.")

    writer = None
    old_color_values: dict[str, str | None] = {}
    try:
        writer = nuke.nodes.Write(inputs=[src], file=as_nuke_path(dst_path), file_type="png")
        old_color_values = apply_png_export_colorspace(writer)
        frame = int(nuke.frame())
        nuke.execute(writer.name(), frame, frame)
    finally:
        if writer is not None:
            try:
                restore_knobs(writer, old_color_values)
            except Exception:
                pass
            try:
                nuke.delete(writer)
            except Exception:
                pass

    if not os.path.exists(dst_path) or os.path.getsize(dst_path) <= 0:
        raise RuntimeError(f"Failed to export input image: {dst_path}")
    return dst_path


def export_mask_input_png(
    node: Any,
    input_index: int,
    dst_path: str,
    missing_input_message: str | None = None,
) -> str:
    src = None
    try:
        src = node.input(input_index)
    except Exception:
        src = None
    if src is None:
        raise RuntimeError(missing_input_message or f"Connect a mask to input {input_index + 1}.")

    normalize = None
    writer = None
    old_color_values: dict[str, str | None] = {}
    try:
        normalize = nuke.nodes.Expression(inputs=[src])
        try:
            expr = "max(max(r,g),max(b,a))"
            if normalize.knob("expr0") is not None:
                normalize.knob("expr0").setValue(expr)
            if normalize.knob("expr1") is not None:
                normalize.knob("expr1").setValue(expr)
            if normalize.knob("expr2") is not None:
                normalize.knob("expr2").setValue(expr)
            if normalize.knob("expr3") is not None:
                normalize.knob("expr3").setValue(expr)
        except Exception:
            pass

        writer = nuke.nodes.Write(inputs=[normalize], file=as_nuke_path(dst_path), file_type="png")
        old_color_values = apply_mask_export_colorspace(writer)
        frame = int(nuke.frame())
        nuke.execute(writer.name(), frame, frame)
    finally:
        if writer is not None:
            try:
                restore_knobs(writer, old_color_values)
            except Exception:
                pass
            try:
                nuke.delete(writer)
            except Exception:
                pass
        if normalize is not None:
            try:
                nuke.delete(normalize)
            except Exception:
                pass

    if not os.path.exists(dst_path) or os.path.getsize(dst_path) <= 0:
        raise RuntimeError(f"Failed to export mask image: {dst_path}")
    return dst_path


def export_via_internal_save(node: Any, save_node_name: str, dst_path: str) -> str | None:
    if node is None or not save_node_name:
        return None

    save_node = None
    old_file = None
    old_color_values: dict[str, str | None] = {}
    try:
        node.begin()
        save_node = nuke.toNode(save_node_name)
        if save_node is None:
            return None

        file_knob = save_node.knob("file")
        if file_knob is None:
            return None

        try:
            old_file = file_knob.value()
        except Exception:
            old_file = None

        file_knob.setValue(as_nuke_path(dst_path))
        ft = save_node.knob("file_type")
        if ft is not None:
            try:
                ft.setValue("png")
            except Exception:
                pass
        old_color_values = apply_png_export_colorspace(save_node)
        dis = save_node.knob("disable")
        if dis is not None:
            try:
                dis.setValue(False)
            except Exception:
                pass

        frame = int(nuke.frame())
        nuke.execute(save_node.name(), frame, frame)
    except Exception:
        return None
    finally:
        try:
            if save_node is not None and old_file is not None and save_node.knob("file") is not None:
                save_node.knob("file").setValue(old_file)
            if save_node is not None:
                restore_knobs(save_node, old_color_values)
            if save_node is not None and save_node.knob("disable") is not None:
                save_node.knob("disable").setValue(True)
        except Exception:
            pass
        try:
            node.end()
        except Exception:
            pass

    if os.path.exists(dst_path) and os.path.getsize(dst_path) > 0:
        return dst_path
    return None
