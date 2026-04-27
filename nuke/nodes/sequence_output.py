"""Nuke Bria Sequence Output node logic.

Batch-renders a chain of upstream Bria nodes across a frame range. Walks
upstream from this node, identifies Bria nodes by knob presence (matching
init.py's _classify_bria_node), topologically sorts them, then for each frame:

    1. Sets the Nuke frame to the current iteration
    2. Calls each Bria node's on_<op>() entry point in topological order
       (each node exports its own input, calls the API, downloads the result)
    3. Renders the output via a temporary Write node pointed at this node's
       input(0), substituting $F / #### tokens in the output path

Per-frame errors are collected and reported at the end; a single bad frame
does not abort the batch. A progress dialog with a Cancel button is shown
throughout.

Follows the Houdini Bria Sequence Output design for cross-DCC consistency.
"""

from __future__ import annotations

import os
import re
import traceback
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import nuke  # type: ignore

from node_utils import (
    debug_logger,
    opt_parm_bool as _opt_parm_bool,
    opt_parm_int as _opt_parm_int,
    opt_parm_str as _opt_parm_str,
)

_debug_log = debug_logger("Bria Sequence Output")


# ---------------------------------------------------------------------------
# Bria node classification (mirrors nuke/init.py:_classify_bria_node)
# ---------------------------------------------------------------------------

# Dispatch key -> entry point module.function lazy-loaded at call time.
# Using lazy imports so sequence_output can load before the node modules do
# (avoids import cycles in Nuke's startup path).

_DISPATCH_SPECS: Dict[str, Tuple[str, str]] = {
    "erase":              ("erase",              "on_erase"),
    "genfill":            ("genfill",            "on_genfill"),
    "expand":             ("expand",             "on_expand"),
    "fibo_edit":          ("fibo_edit",          "on_fibo_edit"),
    "fibo_edit_recipes":  ("fibo_edit_recipes",  "on_fibo_edit_recipes"),
    "fibo_generate":      ("fibo_generate",      "on_fibo_generate"),
    # Matches Houdini's `generate_vgl` key. Nuke's underlying module is still
    # structured_prompt.py for backward compatibility with saved .nk files
    # whose downstream FIBO nodes read `structured_prompt_output`.
    "generate_vgl":       ("structured_prompt",  "on_structured_prompt"),
    "rmbg":               ("rmbg",               "on_rmbg"),
    "upscale":            ("upscale",            "on_upscale"),
    "enhance":            ("enhance",            "on_enhance"),
}


def _classify_bria_node(node: Any) -> Optional[str]:
    """Return dispatch key for a Bria node, or None if not a Bria node.

    Uses knob presence rather than Class()/name so renamed nodes still work.
    Order matters: more specific checks first.
    """
    if node is None:
        return None

    # Sequence Output itself is skipped (avoid self-recursion)
    if node.knob("render_sequence") is not None and node.knob("frame_start") is not None:
        return None

    # Generate VGL (Structured Prompt) has run_structured_prompt button
    if node.knob("run_structured_prompt") is not None:
        return "generate_vgl"

    # FIBO Edit Recipes has category knob on top of FIBO Edit fields
    if node.knob("category") is not None and node.knob("structured_prompt") is not None:
        return "fibo_edit_recipes"

    # FIBO Edit has structured_prompt + mask_type, no aspect_ratio
    if node.knob("structured_prompt") is not None and node.knob("aspect_ratio") is None and node.knob("mask_type") is not None:
        return "fibo_edit"

    # FIBO Generate has aspect_ratio + prompt
    if node.knob("aspect_ratio") is not None and node.knob("prompt") is not None:
        return "fibo_generate"

    # Enhance has run_enhance
    if node.knob("run_enhance") is not None:
        return "enhance"

    # Upscale has run_upscale (and desired_resolution)
    if node.knob("run_upscale") is not None or node.knob("desired_resolution") is not None:
        return "upscale"

    # RMBG has run_rmbg
    if node.knob("run_rmbg") is not None or node.knob("force_bg_detection") is not None:
        return "rmbg"

    # Erase has run_erase (image + mask node with no prompt)
    if node.knob("run_erase") is not None:
        return "erase"

    # GenFill has run_genfill
    if node.knob("run_genfill") is not None:
        return "genfill"

    # Expand has run_expand
    if node.knob("run_expand") is not None or node.knob("expansion_mode") is not None:
        return "expand"

    return None


# ---------------------------------------------------------------------------
# Graph traversal
# ---------------------------------------------------------------------------

def _iter_inputs(node: Any):
    """Iterate over all connected input nodes."""
    try:
        n_inputs = node.inputs()
    except Exception:
        return
    for i in range(n_inputs):
        try:
            upstream = node.input(i)
        except Exception:
            upstream = None
        if upstream is not None:
            yield upstream


def _collect_upstream_bria(start_node: Any) -> Dict[Any, str]:
    """DFS upstream from start_node, collecting all Bria nodes found.

    Returns a dict mapping bria_node -> dispatch_key. Does NOT include start_node
    itself even if it's a Bria node. Handles cycles via a visited set.
    """
    result: Dict[Any, str] = {}
    visited = set()
    stack = list(_iter_inputs(start_node))
    while stack:
        node = stack.pop()
        try:
            node_id = node.fullName()
        except Exception:
            node_id = id(node)
        if node_id in visited:
            continue
        visited.add(node_id)

        bria_type = _classify_bria_node(node)
        if bria_type is not None:
            result[node] = bria_type

        # Keep walking upstream regardless — Bria nodes can have non-Bria
        # nodes between them, and those non-Bria nodes may themselves have
        # Bria nodes upstream.
        for upstream in _iter_inputs(node):
            stack.append(upstream)

    return result


def _find_bria_deps(bria_node: Any, all_bria: Dict[Any, str]) -> List[Any]:
    """Find direct Bria dependencies of bria_node (other Bria nodes upstream,
    traversing through any number of non-Bria nodes in between but stopping
    at the first Bria node found on each branch).
    """
    deps: List[Any] = []
    visited = set()
    stack = list(_iter_inputs(bria_node))
    while stack:
        node = stack.pop()
        try:
            node_id = node.fullName()
        except Exception:
            node_id = id(node)
        if node_id in visited:
            continue
        visited.add(node_id)

        if node in all_bria:
            deps.append(node)
            # Stop this branch — downstream Bria depends on this one, not
            # its further-upstream Bria ancestors (those are handled via
            # this node's own deps).
            continue

        for upstream in _iter_inputs(node):
            stack.append(upstream)

    return deps


def _topo_sort(all_bria: Dict[Any, str], deps_map: Dict[Any, List[Any]]) -> List[Any]:
    """Kahn's algorithm. Returns Bria nodes in cook order. Raises RuntimeError
    on cycle detection.
    """
    in_degree: Dict[Any, int] = {node: 0 for node in all_bria}
    dependents: Dict[Any, List[Any]] = defaultdict(list)
    for node, deps in deps_map.items():
        in_degree[node] = len(deps)
        for dep in deps:
            dependents[dep].append(node)

    queue = deque(n for n in all_bria if in_degree[n] == 0)
    result: List[Any] = []
    while queue:
        node = queue.popleft()
        result.append(node)
        for downstream in dependents.get(node, []):
            in_degree[downstream] -= 1
            if in_degree[downstream] == 0:
                queue.append(downstream)

    if len(result) != len(all_bria):
        raise RuntimeError(
            f"Cycle detected in upstream Bria graph "
            f"({len(result)} of {len(all_bria)} nodes sortable)"
        )
    return result


def discover_upstream_bria_dag(output_node: Any) -> List[Tuple[str, Any]]:
    """Return upstream Bria chain as list of (dispatch_key, node) in cook order."""
    all_bria = _collect_upstream_bria(output_node)
    if not all_bria:
        return []

    deps_map: Dict[Any, List[Any]] = {}
    for bria_node in all_bria:
        deps_map[bria_node] = _find_bria_deps(bria_node, all_bria)

    sorted_nodes = _topo_sort(all_bria, deps_map)
    return [(all_bria[n], n) for n in sorted_nodes]


# ---------------------------------------------------------------------------
# Per-type dispatch
# ---------------------------------------------------------------------------

def _lookup_entry(dispatch_key: str) -> Optional[Callable[[Any], None]]:
    """Lazily import the node module and return its on_<op> function."""
    spec = _DISPATCH_SPECS.get(dispatch_key)
    if spec is None:
        return None
    module_name, fn_name = spec
    try:
        mod = __import__(module_name)
    except Exception as exc:
        _debug_log(f"Failed to import {module_name}: {exc}")
        return None
    fn = getattr(mod, fn_name, None)
    if not callable(fn):
        _debug_log(f"Module {module_name} has no callable {fn_name}")
        return None
    return fn


def _cook_node(dispatch_key: str, bria_node: Any) -> None:
    """Dispatch to the node's synchronous run function. Respects Lock
    Structured Prompt (the SP node itself short-circuits when locked)."""
    fn = _lookup_entry(dispatch_key)
    if fn is None:
        raise RuntimeError(f"No dispatch entry for Bria type '{dispatch_key}'")
    fn(bria_node)


# ---------------------------------------------------------------------------
# Pre-flight validation
# ---------------------------------------------------------------------------

def _validate_chain(chain: List[Tuple[str, Any]]) -> List[str]:
    """Check each Bria node for obvious misconfiguration before starting a
    batch. Returns list of problem strings (empty if valid).

    Currently checks:
    - FIBO Edit / Recipes / Generate: prompt or structured_prompt must be set
      (respecting the use_structured_prompt toggle).
    - Erase / GenFill: mask input must be connected.
    - GenFill: prompt must be set.
    """
    problems: List[str] = []
    for dispatch_key, bria_node in chain:
        name = bria_node.name()

        if dispatch_key in ("fibo_edit", "fibo_edit_recipes", "fibo_generate"):
            use_structured = bool(_opt_parm_bool(bria_node, "use_structured_prompt"))
            if use_structured:
                sp = (_opt_parm_str(bria_node, "structured_prompt") or "").strip()
                if not sp:
                    problems.append(
                        f"{name} ({dispatch_key}): Structured Prompt mode is on "
                        f"but the structured_prompt field is empty."
                    )
            else:
                pr = (_opt_parm_str(bria_node, "prompt") or "").strip()
                if not pr:
                    problems.append(
                        f"{name} ({dispatch_key}): prompt field is empty. "
                        f"Either enter a prompt or enable Structured Prompt with JSON content."
                    )

        if dispatch_key == "genfill":
            pr = (_opt_parm_str(bria_node, "prompt") or "").strip()
            if not pr:
                problems.append(f"{name} (genfill): prompt field is required.")

        if dispatch_key in ("erase", "genfill"):
            # Mask is input 1 on these nodes
            try:
                mask = bria_node.input(1)
            except Exception:
                mask = None
            if mask is None:
                problems.append(
                    f"{name} ({dispatch_key}): mask input (input 2) is not connected."
                )

    return problems


# ---------------------------------------------------------------------------
# Output rendering via temporary Write node
# ---------------------------------------------------------------------------

def _expand_frame_token(template: str, frame: int) -> str:
    """Substitute $F, $F4, $F2, #, ####, %04d etc. in a path template for frame.

    Nuke's File_Knob stores paths with a mix of conventions; we handle the
    common ones so users can write paths like 'out/render.####.png' or
    'out/render.$F4.png' interchangeably.
    """
    if not template:
        return template

    result = template

    # $F4, $F3, $F2, $F1
    def _dollar_f_sub(m):
        width = int(m.group(1)) if m.group(1) else 1
        return f"{frame:0{width}d}"
    result = re.sub(r"\$F(\d+)?", _dollar_f_sub, result)

    # %04d, %d etc.
    def _printf_sub(m):
        width = int(m.group(1)) if m.group(1) else 1
        return f"{frame:0{width}d}"
    result = re.sub(r"%(\d+)?d", _printf_sub, result)

    # #### shorthand (Nuke style). Count the #s to determine width.
    def _hash_sub(m):
        width = len(m.group(0))
        return f"{frame:0{width}d}"
    result = re.sub(r"#+", _hash_sub, result)

    return result


def _export_frame_output(output_node: Any, frame: int, output_path_template: str) -> str:
    """Render frame via a temporary Write node pointed at output_node.input(0).

    Returns the resolved per-frame output path. Raises on failure.
    """
    resolved = _expand_frame_token(output_path_template, frame)
    resolved = os.path.expandvars(os.path.expanduser(resolved))
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)

    upstream = None
    try:
        upstream = output_node.input(0)
    except Exception:
        upstream = None
    if upstream is None:
        raise RuntimeError("Sequence Output has no input connected")

    file_type = _guess_file_type(resolved)

    # Try to import the colorspace helpers used by other Bria exports, so
    # downloaded LDR sequences look right regardless of project OCIO.
    _apply_cs = None
    _restore = None
    try:
        import export_utils as _export_utils  # type: ignore
        _apply_cs = getattr(_export_utils, "apply_png_export_colorspace", None)
        _restore = getattr(_export_utils, "restore_knobs", None)
    except Exception:
        pass

    write_node = None
    restore_values: Dict[str, Any] = {}
    try:
        write_node = nuke.nodes.Write(
            inputs=[upstream],
            file=resolved.replace("\\", "/"),
            file_type=file_type,
            create_directories=True,
        )
        if _apply_cs is not None and file_type in ("png", "jpg", "jpeg"):
            try:
                restore_values = _apply_cs(write_node) or {}
            except Exception:
                restore_values = {}
        nuke.execute(write_node, frame, frame)
    finally:
        if write_node is not None:
            if restore_values and _restore is not None:
                try:
                    _restore(write_node, restore_values)
                except Exception:
                    pass
            try:
                nuke.delete(write_node)
            except Exception:
                pass

    return resolved


def _guess_file_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    # Nuke accepts a wide range of file_type values matching the extension.
    return ext or "png"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _set_status(node: Any, msg: str) -> None:
    try:
        knob = node.knob("status")
        if knob is not None:
            knob.setValue(msg)
    except Exception:
        pass


def render_sequence(output_node: Any = None) -> None:
    """Entry point for the Render button."""
    if output_node is None:
        try:
            output_node = nuke.thisNode()
        except Exception:
            output_node = None
    if output_node is None:
        raise ValueError("Missing node")

    frame_start = int(_opt_parm_int(output_node, "frame_start") or 1)
    frame_end = int(_opt_parm_int(output_node, "frame_end") or 1)
    frame_step = max(1, int(_opt_parm_int(output_node, "frame_step") or 1))
    output_path_template = (_opt_parm_str(output_node, "output_path") or "").strip()

    if not output_path_template:
        _set_status(output_node, "error: no output path")
        nuke.message("Bria Sequence: set an Output Path before rendering.")
        return

    if frame_end < frame_start:
        _set_status(output_node, "error: frame_end < frame_start")
        nuke.message(
            f"Bria Sequence: Frame End ({frame_end}) must be >= Frame Start "
            f"({frame_start})."
        )
        return

    # Discover upstream Bria chain
    try:
        chain = discover_upstream_bria_dag(output_node)
    except RuntimeError as exc:
        _set_status(output_node, f"error: {exc}")
        nuke.message(f"Bria Sequence: {exc}")
        return

    if not chain:
        _set_status(output_node, "error: no Bria nodes found upstream")
        nuke.message(
            "Bria Sequence: no Bria nodes found upstream. Connect this node "
            "after one or more Bria nodes."
        )
        return

    # Pre-flight validation: catch misconfigured nodes before firing off
    # dozens of frames that will all fail the same way.
    problems = _validate_chain(chain)
    if problems:
        _set_status(output_node, f"error: {len(problems)} invalid node(s)")
        shown = "\n  - " + "\n  - ".join(problems[:10])
        extra = f"\n  ... and {len(problems) - 10} more" if len(problems) > 10 else ""
        nuke.message(
            "Bria Sequence: cannot start render. The following upstream Bria "
            f"nodes are misconfigured:\n{shown}{extra}\n\n"
            "Fix these, then try again."
        )
        return

    frames = list(range(frame_start, frame_end + 1, frame_step))
    total = len(frames)
    errors: List[str] = []
    original_frame = nuke.frame()

    chain_summary = ", ".join(key for (key, _n) in chain)
    _debug_log(
        f"Rendering {total} frames through chain: {chain_summary}; "
        f"output template: {output_path_template}"
    )

    task = None
    try:
        task = nuke.ProgressTask(f"Bria Sequence ({len(chain)} node(s))")
    except Exception:
        task = None

    # Turn on batch mode so Bria node handlers suppress modal popups and
    # re-raise exceptions we can collect per-frame.
    try:
        from common import set_batch_mode
        set_batch_mode(True)
    except Exception:
        pass

    try:
        for i, frame in enumerate(frames):
            if task is not None:
                try:
                    if task.isCancelled():
                        _set_status(output_node, f"cancelled at frame {frame}")
                        _debug_log(f"Cancelled by user at frame {frame}")
                        break
                    task.setProgress(int(100 * i / total))
                    task.setMessage(f"Frame {frame} ({i+1}/{total})")
                except Exception:
                    pass
            _set_status(output_node, f"frame {frame} ({i+1}/{total})")

            try:
                _cook_frame(output_node, chain, frame, output_path_template)
            except Exception as exc:
                tb = traceback.format_exc()
                msg = f"Frame {frame}: {exc}"
                errors.append(msg)
                _debug_log(f"Frame {frame} failed: {exc}\n{tb}")

        if task is not None:
            try:
                task.setProgress(100)
            except Exception:
                pass

    finally:
        # Always disable batch mode so subsequent single-shot runs work normally.
        try:
            from common import set_batch_mode
            set_batch_mode(False)
        except Exception:
            pass
        # Restore original frame so the artist's session isn't disrupted.
        try:
            nuke.frame(original_frame)
        except Exception:
            try:
                nuke.setFrame(original_frame)
            except Exception:
                pass
        if task is not None:
            try:
                del task
            except Exception:
                pass

    # Summary
    if errors:
        _set_status(output_node, f"done with {len(errors)} error(s)")
        shown = "\n".join(errors[:20])
        extra = f"\n...and {len(errors) - 20} more" if len(errors) > 20 else ""
        nuke.message(
            f"Bria Sequence: completed {total - len(errors)}/{total} frames with "
            f"{len(errors)} error(s).\n\n{shown}{extra}"
        )
    else:
        _set_status(output_node, f"done ({total} frames)")
        nuke.message(f"Bria Sequence: rendered {total} frames successfully.")


def _cook_frame(
    output_node: Any,
    chain: List[Tuple[str, Any]],
    frame: int,
    output_path_template: str,
) -> None:
    """Process a single frame: set frame, cook each Bria node, render output."""
    try:
        nuke.frame(frame)
    except Exception:
        nuke.setFrame(frame)

    for dispatch_key, bria_node in chain:
        node_name = bria_node.name()
        _debug_log(f"Frame {frame}: cooking {dispatch_key} ({node_name})")
        try:
            _cook_node(dispatch_key, bria_node)
        except Exception as exc:
            raise RuntimeError(
                f"{node_name} ({dispatch_key}): {exc}"
            ) from exc

    _export_frame_output(output_node, frame, output_path_template)


# ---------------------------------------------------------------------------
# Knob changed dispatcher
# ---------------------------------------------------------------------------

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
        # Sync frame range defaults from project if blank
        try:
            if int(_opt_parm_int(node, "frame_end") or 0) <= 0:
                root = nuke.root()
                last = int(root.knob("last_frame").value())
                first = int(root.knob("first_frame").value())
                node.knob("frame_start").setValue(first)
                node.knob("frame_end").setValue(last)
        except Exception:
            pass
        return


def on_render_sequence(node: Any = None) -> None:
    """Entry point mirroring other Bria nodes' on_<op>() convention."""
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")
    render_sequence(node)
