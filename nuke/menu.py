"""Bria Nuke menu registration.

Runs after UI loads.
"""

from __future__ import annotations

import os
import runpy
import sys
import tempfile
from pathlib import Path

from project_path_defaults import default_use_global_project_path


_MODULE_ALIAS = "_bria_nuke_menu"
sys.modules[_MODULE_ALIAS] = sys.modules[__name__]
_LAST_PANEL = None

_FIBO_EDIT_INTERNAL_NK = """set cut_paste_input [stack 0]
version 16.0 v6
Input {
 inputs 0
 name input_image
 label Original
 number 0
 xpos -40
 ypos -9
}
set _bria_fibo_edit_input_image [stack 0]
Input {
 inputs 0
 name input_mask
 label Mask
 number 1
 xpos 70
 ypos -9
}
Input {
 inputs 0
 name input_structured_prompt
 label "Structured Prompt"
 number 2
 xpos 180
 ypos -9
}
Read {
 inputs 0
 in_colorspace scene_linear
 out_colorspace scene_linear
 name loader_result
 label After
 xpos 290
 ypos -33
}
Read {
 inputs 0
 in_colorspace scene_linear
 out_colorspace scene_linear
 name loader_before
 label "Before (previous edit)"
 xpos 180
 ypos -33
}
push $_bria_fibo_edit_input_image
Switch {
 inputs 2
 name switch_compare_source
 label "0=Original / 1=Previous Edit"
 xpos 180
 ypos 63
}
Switch {
 inputs 2
 which 0
 name switch_before_after
 label "0=Before selection / 1=After"
 xpos 290
 ypos 63
}
Output {
 name output1
 xpos 290
 ypos 87
}
"""

def _reload_menu_module_from_disk():
    module_path = Path(__file__).resolve()
    run_name = "__bria_nuke_menu_export__"
    runpy.run_path(str(module_path), run_name=run_name)
    return sys.modules.get(_MODULE_ALIAS)


def _warn(message: str) -> None:
    try:
        import nuke  # type: ignore

        nuke.tprint(f"[Bria] {message}")
    except Exception:
        print(f"[Bria] {message}")


def _rebuild_group_from_internal_nk(node, internal_nk: str, required_node_names: tuple[str, ...], temp_prefix: str) -> bool:
    import nuke  # type: ignore

    if node is None:
        return False

    try:
        node.begin()

        try:
            existing_nodes = list(nuke.allNodes(recurseGroups=False))
        except Exception:
            existing_nodes = []

        for candidate in reversed(existing_nodes):
            try:
                nuke.delete(candidate)
            except Exception:
                pass

        file_descriptor, temp_path = tempfile.mkstemp(prefix=temp_prefix, suffix=".nk")
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(internal_nk)

            nuke.nodePaste(temp_path)
        finally:
            try:
                os.remove(temp_path)
            except Exception:
                pass

        for node_name in required_node_names:
            try:
                if nuke.toNode(node_name) is None:
                    return False
            except Exception:
                return False

        return True
    except Exception:
        return False
    finally:
        try:
            node.end()
        except Exception:
            pass


def _apply_project_path_default(node) -> None:
    try:
        knob = node.knob("use_global_project_path") if node is not None else None
        if knob is not None:
            knob.setValue(default_use_global_project_path())
    except Exception:
        pass


def _default_preset_path_value(node) -> str:
    try:
        use_global = bool(node.knob("use_global_project_path").value()) if node is not None and node.knob("use_global_project_path") is not None else False
    except Exception:
        use_global = False

    if use_global:
        project_root = str(os.getenv("BRIA_PROJECT_PATH") or "").strip()
        if project_root:
            preset_dir = Path(os.path.expandvars(os.path.expanduser(project_root))) / "presets"
            preset_dir.mkdir(parents=True, exist_ok=True)
            return str(preset_dir).replace("\\", "/")

    preset_dir = Path(tempfile.gettempdir()) / "bria" / "presets"
    preset_dir.mkdir(parents=True, exist_ok=True)
    return str(preset_dir).replace("\\", "/")


def _apply_preset_path_default(node) -> None:
    try:
        knob = node.knob("preset_path") if node is not None else None
        if knob is not None and not str(knob.value() or "").strip():
            knob.setValue(_default_preset_path_value(node))
    except Exception:
        pass


def _add_history_metadata_knobs(node, add_knob, allow_before: bool = False) -> None:
    import nuke  # type: ignore

    load_target_values = ["Before", "After"] if allow_before else ["After"]

    add_knob(nuke.Tab_Knob("history_tab", "History"))
    add_knob(
        nuke.Text_Knob(
            "history_help",
            "",
            "Recent results history saved in this node. Use the selector to load the desired available run.",
        )
    )
    add_knob(nuke.Text_Knob("history_help_sep", "", ""))
    add_knob(nuke.Enumeration_Knob("history_selected_entry", "Selected Run", ["No history yet"]))
    add_knob(nuke.Enumeration_Knob("history_load_target", "Load Selected Run As", load_target_values))
    add_knob(nuke.String_Knob("history_count", "History Count", "0"))
    add_knob(nuke.String_Knob("history_debug", "History Debug", "ui ready"))
    add_knob(nuke.String_Knob("history_selected_summary", "Selection", "No history yet"))
    add_knob(nuke.String_Knob("history_selected_path", "File Path", ""))
    add_knob(
        nuke.PyScript_Knob(
            "history_load_selected_result",
            "Load",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_load_history_result()",
        )
    )
    add_knob(
        nuke.PyScript_Knob(
            "history_reveal_selected_result",
            "Preview File",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_reveal_history_result()",
        )
    )
    add_knob(
        nuke.PyScript_Knob(
            "history_view_selected_metadata",
            "View Selected Metadata",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_view_history_metadata()",
        )
    )
    add_knob(
        nuke.PyScript_Knob(
            "history_copy_selected_metadata",
            "Copy Selected Metadata",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_copy_history_metadata()",
        )
    )
    add_knob(
        nuke.PyScript_Knob(
            "history_load_selected_metadata",
            "Load Selected Settings",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_load_history_metadata()",
        )
    )
    add_knob(
        nuke.PyScript_Knob(
            "history_clear_entries",
            "Clear History",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_clear_generation_history()",
        )
    )
    add_knob(nuke.Text_Knob("history_actions_sep", "", ""))
    add_knob(nuke.Multiline_Eval_String_Knob("generation_data", "Generation Data"))
    add_knob(nuke.Multiline_Eval_String_Knob("generation_history_data", "Generation History"))


def _configure_history_metadata_knobs(node, allow_before: bool = False) -> None:
    import nuke  # type: ignore

    try:
        node.knob("history_count").setEnabled(False)
        node.knob("history_count").setVisible(False)
    except Exception:
        pass
    try:
        node.knob("history_debug").setEnabled(False)
        node.knob("history_debug").setVisible(False)
    except Exception:
        pass
    try:
        node.knob("history_selected_summary").setEnabled(False)
    except Exception:
        pass
    try:
        node.knob("history_selected_path").setEnabled(False)
    except Exception:
        pass
    try:
        node.knob("history_load_target").setValue("After")
        node.knob("history_load_target").setFlag(nuke.STARTLINE)
    except Exception:
        pass
    try:
        node.knob("history_load_selected_result").clearFlag(nuke.STARTLINE)
    except Exception:
        pass
    for knob_name in (
        "history_reveal_selected_result",
        "history_view_selected_metadata",
        "history_copy_selected_metadata",
        "history_load_selected_metadata",
        "history_clear_entries",
        "history_actions_sep",
    ):
        try:
            node.knob(knob_name).setFlag(nuke.STARTLINE)
        except Exception:
            pass
    try:
        node.knob("generation_summary").setEnabled(False)
    except Exception:
        pass
    try:
        node.knob("view_generation_data").setFlag(nuke.STARTLINE)
    except Exception:
        pass
    try:
        node.knob("copy_generation_data").setFlag(nuke.STARTLINE)
    except Exception:
        pass
    for knob_name in ("generation_data", "generation_history_data"):
        try:
            node.knob(knob_name).setVisible(False)
            node.knob(knob_name).setEnabled(False)
        except Exception:
            pass
    # Hide "Load Selected Settings" for nodes that don't support presets.
    # SP node hides it separately (deferred feature).
    _has_presets = (
        node.knob("run_fibo_generate") is not None
        or node.knob("run_fibo_edit") is not None
    )
    if not _has_presets:
        try:
            btn = node.knob("history_load_selected_metadata")
            if btn is not None:
                btn.setVisible(False)
                btn.setEnabled(False)
        except Exception:
            pass

def _enable_node_thumbnail(node) -> None:
    """Turn on postage stamp so Group nodes display a thumbnail like Read nodes."""
    try:
        k = node.knob("postage_stamp")
        if k is not None:
            k.setValue(True)
    except Exception:
        pass


def _capture_selected_nodes(limit: int | None = None):
    try:
        import nuke  # type: ignore

        selected = list(nuke.selectedNodes() or [])
    except Exception:
        selected = []

    if limit is not None:
        selected = selected[: max(0, int(limit))]

    selected_names: list[str] = []
    for candidate in selected:
        try:
            selected_names.append(str(candidate.name()))
        except Exception:
            continue
    return selected_names


def _connect_created_node_inputs(node, source_nodes) -> None:
    import nuke  # type: ignore

    if node is None:
        return
    for input_index, source_name in enumerate(source_nodes or []):
        if not source_name:
            continue
        try:
            source = nuke.toNode(str(source_name))
            if source is None:
                continue
            node.setInput(int(input_index), source)
        except Exception:
            pass


def _delete_node_quietly(node) -> None:
    try:
        import nuke  # type: ignore

        if node is not None:
            nuke.delete(node)
    except Exception:
        pass


def _create_gizmo_node(node_class: str, default_name: str):
    import nuke  # type: ignore

    node = nuke.createNode(str(node_class), inpanel=False)
    if node is None:
        raise RuntimeError(f"Could not create gizmo node: {node_class}")

    try:
        node.setName(str(default_name), uncollide=True)
    except Exception:
        pass

    _enable_node_thumbnail(node)
    return node


def _repair_enhance_internal_graph(node) -> bool:
    import nuke  # type: ignore

    if node is None:
        return False

    def _set_create_error(message: str) -> None:
        try:
            knob = node.knob("debug_create_error")
            if knob is not None:
                knob.setValue(str(message))
        except Exception:
            pass

    def _create_internal_node(class_name: str, node_name: str):
        try:
            creator = getattr(nuke.nodes, class_name, None)
            if not callable(creator):
                _set_create_error(f"Missing Nuke node factory: {class_name}")
                return None
            created = creator()
            if created is None:
                _set_create_error(f"Nuke returned None while creating internal node: {node_name} ({class_name})")
                return None
            try:
                created.setName(str(node_name), uncollide=False)
            except TypeError:
                created.setName(str(node_name))
            except Exception:
                try:
                    name_knob = created.knob("name")
                    if name_knob is not None:
                        name_knob.setValue(str(node_name))
                except Exception:
                    pass
            return created
        except Exception as exc:
            _set_create_error(f"Exception while creating internal node: {node_name} ({class_name}) -> {exc}")
            return None

    repaired = False

    try:
        node.begin()

        for node_name in (
            "input_image", "input_mask", "mask_to_alpha_expr", "loader_result",
            "before_to_result", "mask_to_result", "keymix_region",
            "switch_before_after", "output1",
        ):
            try:
                candidate = nuke.toNode(node_name)
            except Exception:
                candidate = None
            if candidate is None:
                continue
            try:
                nuke.delete(candidate)
            except Exception:
                pass

        input_image = _create_internal_node("Input", "input_image")
        input_mask = _create_internal_node("Input", "input_mask")
        mask_to_alpha_expr = _create_internal_node("Expression", "mask_to_alpha_expr")
        loader_result = _create_internal_node("Read", "loader_result")
        before_to_result = _create_internal_node("Reformat", "before_to_result")
        mask_to_result = _create_internal_node("Reformat", "mask_to_result")
        keymix_region = _create_internal_node("Keymix", "keymix_region")
        switch_before_after = _create_internal_node("Switch", "switch_before_after")
        out = _create_internal_node("Output", "output1")

        required_nodes = {
            "input_image": input_image,
            "input_mask": input_mask,
            "mask_to_alpha_expr": mask_to_alpha_expr,
            "loader_result": loader_result,
            "before_to_result": before_to_result,
            "mask_to_result": mask_to_result,
            "keymix_region": keymix_region,
            "switch_before_after": switch_before_after,
            "output1": out,
        }
        if any(n is None for n in required_nodes.values()):
            missing = [k for k, v in required_nodes.items() if v is None]
            _set_create_error(f"Missing internal nodes: {', '.join(missing)}")
            return False

        if input_image is not None and input_image.knob("number") is not None:
            input_image.knob("number").setValue(0)
        if input_mask is not None and input_mask.knob("number") is not None:
            input_mask.knob("number").setValue(1)

        mask_to_alpha_expr.setInput(0, input_mask)
        before_to_result.setInput(0, input_image)
        mask_to_result.setInput(0, mask_to_alpha_expr)
        keymix_region.setInput(0, before_to_result)
        keymix_region.setInput(1, loader_result)
        keymix_region.setInput(2, mask_to_result)
        switch_before_after.setInput(0, input_image)
        switch_before_after.setInput(1, keymix_region)
        out.setInput(0, switch_before_after)

        try:
            use_mask = bool(node.knob("use_mask").value()) if node.knob("use_mask") is not None else False
        except Exception:
            use_mask = False
        expr = "max(max(r,g),max(b,a))" if use_mask else "1"

        try:
            for knob_name in ("expr0", "expr1", "expr2", "expr3"):
                k = mask_to_alpha_expr.knob(knob_name)
                if k is not None:
                    k.setValue(expr)
            mask_to_alpha_expr["label"].setValue("Mask max(r,g,b,a)" if use_mask else "Mask disabled -> full result")
            input_image["label"].setValue("Before")
            input_mask["label"].setValue("Mask")
            before_to_result["label"].setValue("Resize image to result")
            mask_to_result["label"].setValue("Resize mask to result")
            loader_result["label"].setValue("Enhanced")
            keymix_region["label"].setValue("A=Before / B=Enhanced / mask=Input2")
            switch_before_after["label"].setValue("0=Before / 1=After")
            for n, kws in (
                (before_to_result, {"type": "to box", "box_fixed": True, "resize": "distort", "center": False}),
                (mask_to_result,   {"type": "to box", "box_fixed": True, "resize": "distort", "center": False, "filter": "Impulse"}),
            ):
                for k, v in kws.items():
                    try:
                        if n.knob(k) is not None:
                            n.knob(k).setValue(v)
                    except Exception:
                        pass
            if keymix_region.knob("maskChannel") is not None:
                keymix_region.knob("maskChannel").setValue("rgba.red")
            if switch_before_after.knob("which") is not None:
                switch_before_after.knob("which").setValue(0)
        except Exception:
            pass

        repaired = True
    except Exception:
        pass
    finally:
        try:
            node.end()
        except Exception:
            pass

    return repaired


def _build_enhance_nc_keymix_graph(node) -> bool:
    """NC-mode fallback: same full graph but without mask_to_alpha_expr (8 nodes).
    mask_to_result reads directly from input_mask — keymix alpha channel handles it."""
    import nuke  # type: ignore

    if node is None:
        return False

    def _set_create_error(message: str) -> None:
        try:
            knob = node.knob("debug_create_error")
            if knob is not None:
                knob.setValue(str(message))
        except Exception:
            pass

    def _create_internal_node(class_name: str, node_name: str):
        try:
            creator = getattr(nuke.nodes, class_name, None)
            if not callable(creator):
                return None
            created = creator()
            if created is None:
                _set_create_error(f"Nuke returned None while creating NC internal node: {node_name} ({class_name})")
                return None
            try:
                created.setName(str(node_name), uncollide=False)
            except TypeError:
                created.setName(str(node_name))
            except Exception:
                pass
            return created
        except Exception as exc:
            _set_create_error(f"Exception creating NC internal node: {node_name} -> {exc}")
            return None

    try:
        node.begin()

        for node_name in (
            "input_image", "input_mask", "loader_result",
            "before_to_result", "mask_to_result", "keymix_region",
            "switch_before_after", "output1",
        ):
            try:
                candidate = nuke.toNode(node_name)
            except Exception:
                candidate = None
            if candidate is None:
                continue
            try:
                nuke.delete(candidate)
            except Exception:
                pass

        input_image = _create_internal_node("Input", "input_image")
        input_mask = _create_internal_node("Input", "input_mask")
        loader_result = _create_internal_node("Read", "loader_result")
        before_to_result = _create_internal_node("Reformat", "before_to_result")
        mask_to_result = _create_internal_node("Reformat", "mask_to_result")
        keymix_region = _create_internal_node("Keymix", "keymix_region")
        switch_before_after = _create_internal_node("Switch", "switch_before_after")
        out = _create_internal_node("Output", "output1")

        required_nodes = {
            "input_image": input_image, "input_mask": input_mask,
            "loader_result": loader_result, "before_to_result": before_to_result,
            "mask_to_result": mask_to_result, "keymix_region": keymix_region,
            "switch_before_after": switch_before_after, "output1": out,
        }
        if any(n is None for n in required_nodes.values()):
            missing = [k for k, v in required_nodes.items() if v is None]
            _set_create_error(f"Missing NC internal nodes: {', '.join(missing)}")
            return False

        try:
            if input_image.knob("number") is not None:
                input_image.knob("number").setValue(0)
            if input_mask.knob("number") is not None:
                input_mask.knob("number").setValue(1)
        except Exception:
            pass

        before_to_result.setInput(0, input_image)
        mask_to_result.setInput(0, input_mask)
        keymix_region.setInput(0, before_to_result)
        keymix_region.setInput(1, loader_result)
        keymix_region.setInput(2, mask_to_result)
        switch_before_after.setInput(0, input_image)
        switch_before_after.setInput(1, keymix_region)
        out.setInput(0, switch_before_after)

        try:
            input_image["label"].setValue("Before")
            input_mask["label"].setValue("Mask")
            loader_result["label"].setValue("Enhanced")
            before_to_result["label"].setValue("Resize image to result")
            mask_to_result["label"].setValue("Resize mask to result")
            keymix_region["label"].setValue("A=Bypass / B=Enhanced / mask=Input2")
            switch_before_after["label"].setValue("0=Before / 1=After")
            for n, kws in (
                (before_to_result, {"type": "to box", "box_fixed": True, "resize": "distort", "center": False}),
                (mask_to_result,   {"type": "to box", "box_fixed": True, "resize": "distort", "center": False, "filter": "Impulse"}),
            ):
                for k, v in kws.items():
                    try:
                        if n.knob(k) is not None:
                            n.knob(k).setValue(v)
                    except Exception:
                        pass
            if keymix_region.knob("maskChannel") is not None:
                try:
                    keymix_region.knob("maskChannel").setValue("rgba.red")
                except Exception:
                    pass
        except Exception:
            pass
        return True
    finally:
        try:
            node.end()
        except Exception:
            pass


def _repair_fibo_edit_internal_graph(node) -> bool:
    required_node_names = (
        "input_image",
        "input_mask",
        "input_structured_prompt",
        "loader_before",
        "loader_result",
        "switch_compare_source",
        "switch_before_after",
        "output1",
    )

    return _rebuild_group_from_internal_nk(
        node,
        internal_nk=_FIBO_EDIT_INTERNAL_NK,
        required_node_names=required_node_names,
        temp_prefix="bria_fibo_edit_",
    )


def _create_enhance_gizmo_fallback():
    import nuke  # type: ignore

    node = _create_gizmo_node("bria_enhance", "Bria_Enhance")
    if not _repair_enhance_internal_graph(node):
        _delete_node_quietly(node)
        raise RuntimeError("Could not build the Bria Enhance internal graph")
    nuke.tprint(f"[Bria] Created node via gizmo fallback: {node.name()}")
    return node


def _add_group_compare_internal(node, include_mask_input: bool = False) -> None:
    import nuke  # type: ignore

    try:
        node.begin()
        input_image = nuke.nodes.Input(name="input_image")
        input_mask = None
        save_image = None
        save_mask = None
        if include_mask_input:
            input_mask = nuke.nodes.Input(name="input_mask")
            save_image = nuke.nodes.Write(name="save_image")
            save_mask = nuke.nodes.Write(name="save_mask")

            try:
                save_image.setInput(0, input_image)
            except Exception:
                pass
            try:
                save_mask.setInput(0, input_mask)
            except Exception:
                pass
        after = nuke.nodes.Read(name="loader_result")
        sw = nuke.nodes.Switch(name="switch_before_after")
        out = nuke.nodes.Output(name="output1")

        sw.setInput(0, input_image)
        sw.setInput(1, after)
        out.setInput(0, sw)

        try:
            input_image["label"].setValue("Before")
            if input_mask is not None:
                input_mask["label"].setValue("Mask")
            if save_image is not None and save_image.knob("disable") is not None:
                save_image.knob("disable").setValue(True)
            if save_mask is not None and save_mask.knob("disable") is not None:
                save_mask.knob("disable").setValue(True)
            after["label"].setValue("After")
            sw["label"].setValue("0=Before / 1=After")
        except Exception:
            pass
    finally:
        try:
            node.end()
        except Exception:
            pass


def _create_rmbg_node() -> None:
    import nuke  # type: ignore

    node = nuke.createNode("Group", inpanel=False)
    node.setName("Bria_RMBG", uncollide=True)
    _enable_node_thumbnail(node)

    def _add_knob(knob):
        try:
            if node.knob(knob.name()) is None:
                node.addKnob(knob)
        except Exception:
            node.addKnob(knob)

    _add_knob(nuke.Tab_Knob("settings_tab", "Settings"))
    _add_knob(nuke.Text_Knob("bria_header", "", "Bria : Remove Background"))
    _add_knob(nuke.Boolean_Knob("preserve_alpha", "Preserve Alpha", False))
    _add_knob(nuke.Boolean_Knob("premultiply", "Premultiply", True))
    _add_knob(nuke.Text_Knob("run_sep_top", "", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "run_rmbg",
            "Remove Background",
            "import rmbg as _bria_rmbg; _bria_rmbg.on_rmbg()",
        )
    )
    _add_knob(nuke.Text_Knob("run_sep_bottom", "", ""))
    _add_knob(nuke.Boolean_Knob("use_global_project_path", "Use Global Project Path", False))
    _add_knob(nuke.File_Knob("result_path", "Result Path"))
    _add_knob(nuke.Enumeration_Knob("output_view", "Output", ["Before", "After"]))
    _add_knob(nuke.Text_Knob("dashboard_sep", "", ""))
    _add_knob(nuke.String_Knob("status", "Status", "idle"))
    _add_knob(
        nuke.PyScript_Knob(
            "open_dashboard",
            "Open Bria Dashboard",
            "import _bria_nuke_menu as _bria_menu; _bria_menu._open_bria_panel()",
        )
    )
    _add_knob(nuke.Text_Knob("gen_summary_sep", "", ""))
    _add_knob(nuke.String_Knob("generation_summary", "Generation Summary", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "view_generation_data",
            "View Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_view_generation_data()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "copy_generation_data",
            "Copy Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_copy_generation_data()",
        )
    )
    _add_history_metadata_knobs(node, _add_knob, allow_before=False)
    _add_knob(nuke.Tab_Knob("info_tab", "Info"))
    _add_knob(nuke.Text_Knob("gizmo_info", "Gizmo", "Bria RMBG v1.1.1"))
    _add_knob(nuke.Text_Knob("about_info", "About this Gizmo: ", "Removes background using Bria AI"))

    try:
        node.knob("premultiply").setValue(True)
        _apply_project_path_default(node)
        node.knob("output_view").setValue("Before")
        node.knob("run_rmbg").setFlag(nuke.STARTLINE)
        node.knob("open_dashboard").setFlag(nuke.STARTLINE)
        _configure_history_metadata_knobs(node, allow_before=False)
        if node.knob("status") is not None:
            node.knob("status").setEnabled(False)
        node.knob("knobChanged").setValue("import rmbg as _bria_rmbg; _bria_rmbg.on_knob_changed()")
    except Exception:
        pass

    try:
        node.begin()
        input_image = nuke.nodes.Input(name="input_image")
        save_image = nuke.nodes.Write(name="save_image")
        loader_result = nuke.nodes.Read(name="loader_result")
        premult_result = nuke.nodes.Premult(name="premult_result")
        switch_result = nuke.nodes.Switch(name="switch_result")
        out = nuke.nodes.Output(name="output1")

        try:
            save_image.setInput(0, input_image)
        except Exception:
            pass

        try:
            premult_result.setInput(0, loader_result)
        except Exception:
            pass

        switch_result.setInput(0, input_image)
        switch_result.setInput(1, premult_result)
        out.setInput(0, switch_result)

        try:
            input_image["label"].setValue("Before")
            loader_result["label"].setValue("After")
            premult_result["label"].setValue("Premult result")
            switch_result["label"].setValue("0=Before / 1=After")
            if save_image.knob("disable") is not None:
                save_image.knob("disable").setValue(True)
        except Exception:
            pass
    finally:
        try:
            node.end()
        except Exception:
            pass

    nuke.tprint(f"[Bria] Created node: {node.name()}")


def _create_erase_node() -> None:
    import nuke  # type: ignore

    node = nuke.createNode("Group", inpanel=False)
    node.setName("Bria_Erase", uncollide=True)
    _enable_node_thumbnail(node)
    try:
        if node.knob("inputs") is not None:
            node.knob("inputs").setValue(2)
    except Exception:
        pass

    def _add_knob(knob):
        try:
            if node.knob(knob.name()) is None:
                node.addKnob(knob)
        except Exception:
            node.addKnob(knob)

    _add_knob(nuke.Tab_Knob("settings_tab", "Settings"))
    _add_knob(nuke.Text_Knob("bria_header", "", "Bria : Erase"))
    _add_knob(nuke.Boolean_Knob("preserve_alpha", "Preserve Alpha", True))
    _add_knob(nuke.Text_Knob("run_sep_top", "", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "run_erase",
            "Run Erase",
            "import erase as _bria_er; _bria_er.on_erase()",
        )
    )
    _add_knob(nuke.Text_Knob("run_sep_bottom", "", ""))
    _add_knob(nuke.Boolean_Knob("use_global_project_path", "Use Global Project Path", False))
    _add_knob(nuke.File_Knob("result_path", "Result Path"))
    _add_knob(nuke.Enumeration_Knob("output_view", "Output", ["Before", "After"]))
    _add_knob(nuke.Text_Knob("dashboard_sep", "", ""))
    _add_knob(nuke.String_Knob("status", "Status", "idle"))
    _add_knob(
        nuke.PyScript_Knob(
            "open_dashboard",
            "Open Bria Dashboard",
            "import _bria_nuke_menu as _bria_menu; _bria_menu._open_bria_panel()",
        )
    )
    _add_knob(nuke.Text_Knob("gen_summary_sep", "", ""))
    _add_knob(nuke.String_Knob("generation_summary", "Generation Summary", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "view_generation_data",
            "View Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_view_generation_data()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "copy_generation_data",
            "Copy Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_copy_generation_data()",
        )
    )
    _add_history_metadata_knobs(node, _add_knob, allow_before=False)
    _add_knob(nuke.Tab_Knob("info_tab", "Info"))
    _add_knob(nuke.Text_Knob("gizmo_info", "Gizmo", "Bria Erase v1.1.1"))
    _add_knob(nuke.Text_Knob("about_info", "About this Gizmo: ", "Erases masked regions using Bria AI"))

    try:
        _apply_project_path_default(node)
        node.knob("output_view").setValue("Before")
        node.knob("run_erase").setFlag(nuke.STARTLINE)
        node.knob("open_dashboard").setFlag(nuke.STARTLINE)
        _configure_history_metadata_knobs(node, allow_before=False)
        if node.knob("status") is not None:
            node.knob("status").setEnabled(False)
        node.knob("knobChanged").setValue("import erase as _bria_er; _bria_er.on_knob_changed()")
    except Exception:
        pass

    # Build Erase-specific internal layout:
    # image + mask inputs, save placeholders, result loader,
    # before/after switch, output.
    try:
        node.begin()
        input_image = nuke.nodes.Input(name="input_image")
        input_mask = nuke.nodes.Input(name="input_mask")
        try:
            if input_image.knob("number") is not None:
                input_image.knob("number").setValue(0)
            if input_mask.knob("number") is not None:
                input_mask.knob("number").setValue(1)
        except Exception:
            pass

        save_image = nuke.nodes.Write(name="save_image")
        save_mask = nuke.nodes.Write(name="save_mask")
        loader_result = nuke.nodes.Read(name="loader_result")
        switch_result = nuke.nodes.Switch(name="switch_result")
        out = nuke.nodes.Output(name="output1")

        try:
            save_image.setInput(0, input_image)
        except Exception:
            pass
        try:
            save_mask.setInput(0, input_mask)
        except Exception:
            pass

        switch_result.setInput(0, input_image)
        switch_result.setInput(1, loader_result)
        out.setInput(0, switch_result)

        try:
            input_image["label"].setValue("Image")
            input_mask["label"].setValue("Mask")
            switch_result["label"].setValue("0=Before / 1=After")
            if save_image.knob("disable") is not None:
                save_image.knob("disable").setValue(True)
            if save_mask.knob("disable") is not None:
                save_mask.knob("disable").setValue(True)
        except Exception:
            pass
    finally:
        try:
            node.end()
        except Exception:
            pass

    nuke.tprint(f"[Bria] Created node: {node.name()}")


def _create_genfill_node() -> None:
    import nuke  # type: ignore

    node = nuke.createNode("Group", inpanel=False)
    node.setName("Bria_GenFill", uncollide=True)
    _enable_node_thumbnail(node)
    try:
        if node.knob("inputs") is not None:
            node.knob("inputs").setValue(2)
    except Exception:
        pass

    def _add_knob(knob):
        try:
            if node.knob(knob.name()) is None:
                node.addKnob(knob)
        except Exception:
            node.addKnob(knob)

    _add_knob(nuke.Tab_Knob("settings_tab", "Settings"))
    _add_knob(nuke.Text_Knob("bria_header", "", "Bria : GenFill"))
    preserve_alpha_knob = nuke.Boolean_Knob("preserve_alpha", "Preserve Alpha", True)
    _add_knob(preserve_alpha_knob)
    _add_knob(nuke.Multiline_Eval_String_Knob("prompt", "Prompt"))
    use_negative_knob = nuke.Boolean_Knob("use_negative_prompt", "Negative Prompt", False)
    _add_knob(use_negative_knob)
    _add_knob(nuke.Multiline_Eval_String_Knob("negative_prompt", "Negative"))
    _add_knob(nuke.Text_Knob("run_sep_top", "", ""))
    run_knob = nuke.PyScript_Knob(
        "run_genfill",
        "Run GenFill",
        "import genfill as _bria_gf; _bria_gf.on_genfill()",
    )
    _add_knob(run_knob)
    _add_knob(nuke.Text_Knob("run_sep_bottom", "", ""))
    _add_knob(nuke.Boolean_Knob("use_global_project_path", "Use Global Project Path", False))
    _add_knob(nuke.File_Knob("result_path", "Result Path"))
    _add_knob(nuke.Enumeration_Knob("output_view", "Before / After", ["Before", "After"]))
    _add_knob(nuke.Text_Knob("dashboard_sep", "", ""))
    _add_knob(nuke.String_Knob("status", "Status", "idle"))
    _add_knob(
        nuke.PyScript_Knob(
            "open_dashboard",
            "Open Bria Dashboard",
            "import _bria_nuke_menu as _bria_menu; _bria_menu._open_bria_panel()",
        )
    )
    _add_knob(nuke.Text_Knob("gen_summary_sep", "", ""))
    _add_knob(nuke.String_Knob("generation_summary", "Generation Summary", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "view_generation_data",
            "View Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_view_generation_data()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "copy_generation_data",
            "Copy Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_copy_generation_data()",
        )
    )
    _add_history_metadata_knobs(node, _add_knob, allow_before=False)
    _add_knob(nuke.Tab_Knob("info_tab", "Info"))
    _add_knob(nuke.Text_Knob("gizmo_info", "Gizmo", "Bria GenFill v1.1.1"))
    _add_knob(nuke.Text_Knob("about_info", "About this Gizmo: ", "Fills in area using Bria AI"))

    try:
        preserve_alpha_knob.setFlag(nuke.STARTLINE)
        node.knob("prompt").setTooltip("input your prompt here!")
        use_negative_knob.setFlag(nuke.STARTLINE)
        node.knob("negative_prompt").setFlag(nuke.STARTLINE)
        run_knob.setFlag(nuke.STARTLINE)
        node.knob("open_dashboard").setFlag(nuke.STARTLINE)
    except Exception:
        pass

    try:
        node.knob("negative_prompt").setVisible(False)
        _apply_project_path_default(node)
        node.knob("output_view").setValue("Before")
        _configure_history_metadata_knobs(node, allow_before=False)
        if node.knob("status") is not None:
            node.knob("status").setEnabled(False)
        node.knob("knobChanged").setValue("import genfill as _bria_gf; _bria_gf.on_knob_changed()")
    except Exception:
        pass

    # Build GenFill-specific internal layout with explicit save nodes.
    try:
        node.begin()
        input_image = nuke.nodes.Input(name="input_image")
        input_mask = nuke.nodes.Input(name="input_mask")
        try:
            if input_image.knob("number") is not None:
                input_image.knob("number").setValue(0)
            if input_mask.knob("number") is not None:
                input_mask.knob("number").setValue(1)
        except Exception:
            pass
        save_image = nuke.nodes.Write(name="save_image")
        save_mask = nuke.nodes.Write(name="save_mask")
        loader_result = nuke.nodes.Read(name="loader_result")
        switch_result = nuke.nodes.Switch(name="switch_result")
        out = nuke.nodes.Output(name="output1")

        try:
            save_image.setInput(0, input_image)
        except Exception:
            pass
        try:
            save_mask.setInput(0, input_mask)
        except Exception:
            pass

        switch_result.setInput(0, input_image)
        switch_result.setInput(1, loader_result)
        out.setInput(0, switch_result)

        try:
            input_image["label"].setValue("Image")
            input_mask["label"].setValue("Mask")
            switch_result["label"].setValue("0=Before / 1=After")
            if loader_result.knob("in_colorspace") is not None:
                loader_result.knob("in_colorspace").setValue("scene_linear")
            if loader_result.knob("out_colorspace") is not None:
                loader_result.knob("out_colorspace").setValue("scene_linear")
            if save_image.knob("disable") is not None:
                save_image.knob("disable").setValue(True)
            if save_mask.knob("disable") is not None:
                save_mask.knob("disable").setValue(True)
        except Exception:
            pass
    finally:
        try:
            node.end()
        except Exception:
            pass

    nuke.tprint(f"[Bria] Created node: {node.name()}")


def _create_expand_node() -> None:
    import nuke  # type: ignore

    node = nuke.createNode("Group", inpanel=False)
    node.setName("Bria_Expand", uncollide=True)
    _enable_node_thumbnail(node)
    try:
        if node.knob("inputs") is not None:
            node.knob("inputs").setValue(1)
    except Exception:
        pass

    def _add_knob(knob):
        try:
            if node.knob(knob.name()) is None:
                node.addKnob(knob)
        except Exception:
            node.addKnob(knob)

    _add_knob(nuke.Tab_Knob("settings_tab", "Settings"))
    _add_knob(nuke.Text_Knob("bria_header", "", "Bria : Expand"))
    _add_knob(nuke.Int_Knob("seed", "Seed"))
    _add_knob(nuke.Enumeration_Knob("expand_mode", "Expand Mode", ["aspect_ratio", "directional"]))
    _add_knob(nuke.Enumeration_Knob("aspect_ratio", "Aspect Ratio", ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9"]))
    _add_knob(
        nuke.Enumeration_Knob(
            "expand_direction",
            "Direction",
            ["right", "left", "top", "bottom", "top_right", "top_left", "bottom_right", "bottom_left"],
        )
    )
    _add_knob(nuke.Int_Knob("expand_amount_pct", "Amount (%)"))
    _add_knob(nuke.Multiline_Eval_String_Knob("prompt", "Prompt"))
    _add_knob(nuke.Text_Knob("run_sep_top", "", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "run_expand",
            "Run Expand",
            "import expand as _bria_ex; _bria_ex.on_expand()",
        )
    )
    _add_knob(nuke.Text_Knob("run_sep_bottom", "", ""))
    _add_knob(nuke.Boolean_Knob("use_global_project_path", "Use Global Project Path", False))
    _add_knob(nuke.File_Knob("result_path", "Result Path"))
    _add_knob(nuke.Enumeration_Knob("output_view", "Output", ["Before", "After"]))
    _add_knob(nuke.Text_Knob("dashboard_sep", "", ""))
    _add_knob(nuke.String_Knob("status", "Status", "idle"))
    _add_knob(
        nuke.PyScript_Knob(
            "open_dashboard",
            "Open Bria Dashboard",
            "import _bria_nuke_menu as _bria_menu; _bria_menu._open_bria_panel()",
        )
    )
    _add_knob(nuke.Text_Knob("gen_summary_sep", "", ""))
    _add_knob(nuke.String_Knob("generation_summary", "Generation Summary", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "view_generation_data",
            "View Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_view_generation_data()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "copy_generation_data",
            "Copy Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_copy_generation_data()",
        )
    )
    _add_history_metadata_knobs(node, _add_knob, allow_before=False)
    _add_knob(nuke.Tab_Knob("info_tab", "Info"))
    _add_knob(nuke.Text_Knob("gizmo_info", "Gizmo", "Bria Expand v1.1.1"))
    _add_knob(nuke.Text_Knob("about_info", "About this Gizmo: ", "Expands image canvas using Bria AI"))

    try:
        node.knob("seed").setTooltip("Optional. Positive integer.")
        node.knob("expand_mode").setTooltip("aspect_ratio: use ratio target. directional: expand canvas in a chosen direction.")
        node.knob("expand_direction").setTooltip("Directional expand preset used when Expand Mode = directional.")
        node.knob("expand_amount_pct").setTooltip("Directional canvas increase in percent of input size.")
        node.knob("seed").setValue(0)
        node.knob("expand_mode").setValue("aspect_ratio")
        _apply_project_path_default(node)
        node.knob("aspect_ratio").setValue("1:1")
        node.knob("expand_direction").setValue("right")
        node.knob("expand_amount_pct").setValue(25)
        node.knob("expand_amount_pct").setRange(1, 400)
        node.knob("expand_direction").setVisible(False)
        node.knob("expand_amount_pct").setVisible(False)
        node.knob("output_view").setValue("Before")
        node.knob("run_expand").setFlag(nuke.STARTLINE)
        node.knob("open_dashboard").setFlag(nuke.STARTLINE)
        _configure_history_metadata_knobs(node, allow_before=False)
        if node.knob("status") is not None:
            node.knob("status").setEnabled(False)
        node.knob("knobChanged").setValue("import expand as _bria_ex; _bria_ex.on_knob_changed()")
    except Exception:
        pass

    _add_group_compare_internal(node)
    nuke.tprint(f"[Bria] Created node: {node.name()}")


def _create_fibo_edit_node() -> None:
    import nuke  # type: ignore

    selected_inputs = _capture_selected_nodes(limit=2)

    node = nuke.createNode("Group", inpanel=False)
    node.setName("Bria_FIBO_Edit", uncollide=True)
    _enable_node_thumbnail(node)
    try:
        if node.knob("inputs") is not None:
            node.knob("inputs").setValue(3)
    except Exception:
        pass

    def _add_knob(knob):
        try:
            if node.knob(knob.name()) is None:
                node.addKnob(knob)
        except Exception:
            node.addKnob(knob)

    _add_knob(nuke.Tab_Knob("Settings", "Settings"))
    _add_knob(nuke.Text_Knob("bria_header", "", "Bria : Fibo Edit"))
    preserve_alpha_knob = nuke.Boolean_Knob("preserve_alpha", "Preserve Alpha", True)
    _add_knob(preserve_alpha_knob)
    _add_knob(nuke.Int_Knob("seed", "Seed"))
    _add_knob(nuke.Int_Knob("guidance_scale", "Guidance Scale"))
    _add_knob(nuke.Int_Knob("steps_num", "Steps"))
    _add_knob(nuke.Multiline_Eval_String_Knob("prompt", "Prompt"))
    _add_knob(nuke.Boolean_Knob("use_negative_prompt", "Negative Prompt", False))
    _add_knob(nuke.Multiline_Eval_String_Knob("negative_prompt", "Prompt (N)"))
    _add_knob(nuke.Boolean_Knob("use_structured_prompt", "Structured Prompt (JSON)", False))
    _add_knob(
        nuke.PyScript_Knob(
            "load_structured_prompt",
            "Load JSON/VGL",
            "import importlib, common as _bria_common, fibo_edit as _bria_fe; importlib.reload(_bria_common); importlib.reload(_bria_fe); _bria_fe.on_load_structured_prompt()",
        )
    )
    _add_knob(nuke.Multiline_Eval_String_Knob("structured_prompt", "Prompt (S)"))
    link_status_knob = nuke.String_Knob("prompt_link_status", "Prompt Source", "manual")
    _add_knob(link_status_knob)
    try:
        link_status_knob.setEnabled(False)
    except Exception:
        pass
    stash_knob = nuke.String_Knob("prompt_manual_stash", "_prompt_stash", "")
    _add_knob(stash_knob)
    try:
        stash_knob.setVisible(False)
    except Exception:
        pass
    _add_knob(nuke.Enumeration_Knob("mask_type", "Mask Type", ["manual", "automatic"]))
    _add_knob(nuke.Text_Knob("run_sep", "", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "run_fibo_edit",
            "Run FIBO Edit",
            "import importlib, common as _bria_common, fibo_edit as _bria_fe; importlib.reload(_bria_common); importlib.reload(_bria_fe); _bria_fe.on_fibo_edit()",
        )
    )
    _add_knob(nuke.Text_Knob("global_sep", "", ""))
    _add_knob(nuke.Boolean_Knob("use_global_project_path", "Use Global Project Path", False))
    _add_knob(nuke.File_Knob("result_path", "Result Path"))
    _add_knob(nuke.Enumeration_Knob("compare_source", "Before Source", ["Original", "Previous Edit"]))
    _add_knob(nuke.Enumeration_Knob("output_view", "Compare A|B", ["Before", "After"]))
    _add_knob(nuke.Text_Knob("dashboard_sep", "", ""))
    _add_knob(nuke.String_Knob("status", "Status", "idle"))
    _add_knob(
        nuke.PyScript_Knob(
            "open_dashboard",
            "Open Bria Dashboard",
            "import _bria_nuke_menu as _bria_menu; _bria_menu._open_bria_panel()",
        )
    )
    _add_knob(nuke.Text_Knob("gen_summary_sep", "", ""))
    _add_knob(nuke.String_Knob("generation_summary", "Generation Summary", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "view_generation_data",
            "View Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_view_generation_data()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "copy_generation_data",
            "Copy Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_copy_generation_data()",
        )
    )
    _add_knob(nuke.Tab_Knob("history_tab", "History"))
    _add_knob(
        nuke.Text_Knob(
            "history_help",
            "",
            "Recent results history saved in this node. Use the selector to load the desired available run.",
        )
    )
    _add_knob(nuke.Text_Knob("history_help_sep", "", ""))
    _add_knob(nuke.Enumeration_Knob("history_selected_entry", "Selected Run", ["No history yet"]))
    _add_knob(nuke.Enumeration_Knob("history_load_target", "Load Selected Run As", ["Before", "After"]))
    _add_knob(nuke.String_Knob("history_count", "History Count", "0"))
    _add_knob(nuke.String_Knob("history_debug", "History Debug", "ui ready"))
    _add_knob(nuke.String_Knob("history_selected_summary", "Selection", "No history yet"))
    _add_knob(nuke.String_Knob("history_selected_path", "File Path", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "history_load_selected_result",
            "Load",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_load_history_result()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_reveal_selected_result",
            "Preview File",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_reveal_history_result()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_view_selected_metadata",
            "View Selected Metadata",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_view_history_metadata()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_copy_selected_metadata",
            "Copy Selected Metadata",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_copy_history_metadata()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_load_selected_metadata",
            "Load Selected Settings",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_load_history_metadata()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_clear_entries",
            "Clear History",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_clear_generation_history()",
        )
    )
    _add_knob(nuke.Text_Knob("history_actions_sep", "", ""))
    _add_knob(nuke.Multiline_Eval_String_Knob("generation_data", "Generation Data"))
    _add_knob(nuke.Multiline_Eval_String_Knob("generation_history_data", "Generation History"))
    _add_knob(nuke.Tab_Knob("presets_tab", "Presets"))
    _add_knob(nuke.String_Knob("preset_name", "Preset Name", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "save_generation_preset",
            "Save Preset",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_save_generation_preset()",
        )
    )
    _add_knob(nuke.Text_Knob("preset_name_sep", "", ""))
    _add_knob(nuke.Enumeration_Knob("preset_selected", "Preset", ["Select"]))
    _add_knob(
        nuke.PyScript_Knob(
            "load_generation_preset",
            "Load Preset",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_load_generation_preset()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "refresh_preset_list",
            "Refresh Presets",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_refresh_generation_presets()",
        )
    )
    _add_knob(nuke.Boolean_Knob("load_presets_from_examples", "Load from Bria Examples"))
    _add_knob(nuke.Text_Knob("preset_actions_sep", "", ""))
    _add_knob(nuke.File_Knob("preset_path", "Preset Path"))
    _add_knob(nuke.String_Knob("preset_summary", "Preset Summary", "No preset loaded"))
    _add_knob(nuke.Multiline_Eval_String_Knob("preset_details", "Preset Details"))
    _add_knob(nuke.Multiline_Eval_String_Knob("generation_preset_data", "Preset Data"))
    _add_knob(nuke.Tab_Knob("info_tab", "Info"))
    _add_knob(nuke.Text_Knob("gizmo_info", "Gizmo", "Bria Edit v1.1.1"))
    _add_knob(nuke.Text_Knob("about_info", "About this Gizmo: ", "Edits an image using Bria AI"))
    _add_knob(nuke.Text_Knob("preset_help_info", "Presets", "Reusable node settings saved as JSON presets. Runtime result paths and generation metadata are not included. Optionally include shipped Bria example presets with the toggle above."))

    try:
        node.knob("seed").setTooltip("Optional. Positive integer.")
        node.knob("guidance_scale").setTooltip("Optional. Integer in range 1-5.")
        node.knob("steps_num").setTooltip("Optional. 0 means unset. Positive integer when set.")
        node.knob("prompt").setTooltip("Enter your prompt here!")
        node.knob("negative_prompt").setTooltip("Enter your negative prompt here!")
        node.knob("structured_prompt").setTooltip("Enter your structured prompt here!")
        node.knob("load_structured_prompt").setTooltip("Load a JSON/VGL file into Structured Prompt as text.")
        node.knob("compare_source").setTooltip("Before = compare latest edit against either the original input or the previous edit.")

        node.knob("guidance_scale").setRange(1, 5)
        node.knob("seed").setValue(0)
        node.knob("guidance_scale").setValue(4)
        node.knob("steps_num").setValue(0)

        node.knob("negative_prompt").setVisible(False)
        node.knob("structured_prompt").setVisible(False)
        node.knob("structured_prompt").setEnabled(False)
        node.knob("load_structured_prompt").setEnabled(False)
        node.knob("mask_type").setValue("automatic")
        _apply_project_path_default(node)
        _apply_preset_path_default(node)
        try:
            import common as _bria_common  # type: ignore

            _bria_common.refresh_generation_preset_ui(node)
        except Exception:
            pass
        node.knob("compare_source").setValue("Original")
        node.knob("output_view").setValue("Before")
        preserve_alpha_knob.setFlag(nuke.STARTLINE)
        preserve_alpha_knob.setVisible(False)
        preserve_alpha_knob.setEnabled(False)
        node.knob("use_negative_prompt").setFlag(nuke.STARTLINE)
        node.knob("negative_prompt").setFlag(nuke.STARTLINE)
        node.knob("use_structured_prompt").setFlag(nuke.STARTLINE)
        node.knob("structured_prompt").setFlag(nuke.STARTLINE)
        node.knob("run_fibo_edit").setFlag(nuke.STARTLINE)
        node.knob("open_dashboard").setFlag(nuke.STARTLINE)
        node.knob("history_count").setEnabled(False)
        node.knob("history_count").setVisible(False)
        node.knob("history_debug").setEnabled(False)
        node.knob("history_debug").setVisible(False)
        node.knob("history_selected_summary").setEnabled(False)
        node.knob("history_selected_path").setEnabled(False)
        node.knob("history_load_target").setValue("After")
        node.knob("history_load_target").setFlag(nuke.STARTLINE)
        node.knob("history_load_selected_result").clearFlag(nuke.STARTLINE)
        node.knob("history_reveal_selected_result").setFlag(nuke.STARTLINE)
        node.knob("history_view_selected_metadata").setFlag(nuke.STARTLINE)
        node.knob("history_copy_selected_metadata").setFlag(nuke.STARTLINE)
        node.knob("history_load_selected_metadata").setFlag(nuke.STARTLINE)
        node.knob("history_clear_entries").setFlag(nuke.STARTLINE)
        node.knob("history_actions_sep").setFlag(nuke.STARTLINE)
        node.knob("generation_summary").setEnabled(False)
        node.knob("view_generation_data").setFlag(nuke.STARTLINE)
        node.knob("copy_generation_data").setFlag(nuke.STARTLINE)
        node.knob("generation_data").setVisible(False)
        node.knob("generation_data").setEnabled(False)
        node.knob("generation_history_data").setVisible(False)
        node.knob("generation_history_data").setEnabled(False)
        node.knob("preset_summary").setEnabled(False)
        node.knob("preset_details").setEnabled(False)
        node.knob("save_generation_preset").clearFlag(nuke.STARTLINE)
        node.knob("load_generation_preset").setFlag(nuke.STARTLINE)
        node.knob("refresh_preset_list").clearFlag(nuke.STARTLINE)
        node.knob("load_presets_from_examples").setFlag(nuke.STARTLINE)
        node.knob("load_presets_from_examples").setTooltip("Include shipped presets from $BRIA_EXAMPLES/presets in the preset list. Saving still uses the normal preset path.")
        node.knob("preset_actions_sep").setFlag(nuke.STARTLINE)
        node.knob("generation_preset_data").setVisible(False)
        node.knob("generation_preset_data").setEnabled(False)
        if node.knob("status") is not None:
            node.knob("status").setEnabled(False)
        node.knob("knobChanged").setValue("import fibo_edit as _bria_fe; _bria_fe.on_knob_changed()")
        node.knob("prompt").setVisible(True)
        node.knob("prompt").setEnabled(True)

        # Height hint: seed multiline fields with blank lines so FIBO Edit
        # prompt areas render with comparable textarea height in Nuke UI.
        if node.knob("prompt") is not None and not str(node.knob("prompt").value() or "").strip():
            node.knob("prompt").setValue("\n\n")
        if node.knob("negative_prompt") is not None and not str(node.knob("negative_prompt").value() or "").strip():
            node.knob("negative_prompt").setValue("\n\n")
        if node.knob("structured_prompt") is not None and not str(node.knob("structured_prompt").value() or "").strip():
            node.knob("structured_prompt").setValue("\n\n")
    except Exception:
        pass

    _repair_fibo_edit_internal_graph(node)
    _connect_created_node_inputs(node, selected_inputs)

    nuke.tprint(f"[Bria] Created node: {node.name()}")


def _create_fibo_edit_recipes_node() -> None:
    """FIBO Edit + categorized preset prompts.

    Structurally identical to _create_fibo_edit_node() with recipes knobs
    (category, per-category preset dropdowns, object/comp target slots)
    inserted before the prompt knob. Delegates execution to fibo_edit.
    """
    import nuke  # type: ignore

    # Import preset data to build dropdown labels
    category_order = [("custom", "Custom")]
    category_labels = ["Custom"]
    preset_labels_by_category = {}
    max_obj_slots = 5
    max_comp_slots = 5
    try:
        import fibo_edit_recipes as _fer  # type: ignore
        category_order = list(_fer.CATEGORY_ORDER)
        category_labels = [label for (_tok, label) in category_order]
        preset_labels_by_category = {
            cat_token: [label for (_tok, label, _p) in _fer.PRESET_CATEGORIES.get(cat_token, [])]
            for cat_token, _cat_label in category_order
            if cat_token != "custom"
        }
        max_obj_slots = _fer.MAX_OBJECT_SLOTS
        max_comp_slots = _fer.MAX_COMP_SLOTS
    except Exception:
        pass

    selected_inputs = _capture_selected_nodes(limit=2)

    node = nuke.createNode("Group", inpanel=False)
    node.setName("Bria_FIBO_Edit_Recipes", uncollide=True)
    _enable_node_thumbnail(node)
    try:
        if node.knob("inputs") is not None:
            node.knob("inputs").setValue(3)
    except Exception:
        pass

    def _add_knob(knob):
        try:
            if node.knob(knob.name()) is None:
                node.addKnob(knob)
        except Exception:
            node.addKnob(knob)

    _add_knob(nuke.Tab_Knob("Settings", "Settings"))
    _add_knob(nuke.Text_Knob("bria_header", "", "Bria : FIBO Edit Recipes"))

    # --- Recipes-specific knobs (inserted before standard FIBO Edit knobs) ---
    _add_knob(nuke.Enumeration_Knob("category", "Category", category_labels or ["Custom"]))
    for cat_token, _cat_label in category_order:
        if cat_token == "custom":
            continue
        labels = preset_labels_by_category.get(cat_token, [])
        if labels:
            _add_knob(nuke.Enumeration_Knob(f"preset_{cat_token}", "Preset", labels))

    # Object target section
    _add_knob(nuke.Text_Knob("object_targets_header", "", "Object Targets (name + replacement/color/material)"))
    for i in range(1, max_obj_slots + 1):
        _add_knob(nuke.String_Knob(f"obj_target_name_{i}", f"Object {i} Name", ""))
        _add_knob(nuke.String_Knob(f"obj_target_mod_{i}", f"  Modifier {i}", ""))

    # Compositing element section
    _add_knob(nuke.Text_Knob("comp_elements_header", "", "Compositing Elements"))
    for i in range(1, max_comp_slots + 1):
        _add_knob(nuke.String_Knob(f"comp_element_desc_{i}", f"Element {i}", ""))

    # "Premultiply with Mask" — only visible when the Create Mask preset is active.
    # When ON, an internal Copy+Premult chain produces a cutout (input image with
    # alpha sourced from the API-returned mask's red channel). Auto-clears when the
    # user switches preset/category away from create_mask.
    premult_with_mask_knob = nuke.Boolean_Knob(
        "premultiply_with_mask", "Premultiply with Mask", False
    )
    try:
        premult_with_mask_knob.setTooltip(
            "When 'Create Mask' is active, premultiply the API-returned mask against "
            "the original input image to output a clean cutout (RGB from input, alpha "
            "from mask's red channel). Disabled by default; auto-clears on preset change."
        )
        premult_with_mask_knob.setFlag(nuke.STARTLINE)
        premult_with_mask_knob.setVisible(False)
    except Exception:
        pass
    _add_knob(premult_with_mask_knob)
    # Mask Edge Softness slider — Bria's mask is sometimes returned at lower-than-source
    # resolution and upscaled with hard binary edges, leaving a stair-step pattern. A
    # small Gaussian blur smooths the boundary; a paired FilterErode at half the blur
    # size compensates for silhouette expansion so the alpha stays tight to the named
    # element. Higher values = softer edges (use for hair, motion blur, atmospherics).
    mask_softness_knob = nuke.Double_Knob("mask_edge_softness", "Mask Edge Softness")
    try:
        mask_softness_knob.setRange(0.0, 5.0)
        mask_softness_knob.setValue(1.5)
        mask_softness_knob.setTooltip(
            "Smooths staircase aliasing on the mask boundary before premultiplying. "
            "0 = raw model output (may be aliased). 1-2 = clean anti-aliased edges. "
            "3-5 = softer feather (good for hair, fur, motion blur)."
        )
        mask_softness_knob.setVisible(False)
    except Exception:
        pass
    _add_knob(mask_softness_knob)

    # Hidden auto-prompt tracker
    auto_prompt_knob = nuke.String_Knob("_auto_prompt", "_auto_prompt", "")
    _add_knob(auto_prompt_knob)
    try:
        auto_prompt_knob.setVisible(False)
    except Exception:
        pass

    # --- Standard FIBO Edit knobs (same as _create_fibo_edit_node) ---
    preserve_alpha_knob = nuke.Boolean_Knob("preserve_alpha", "Preserve Alpha", True)
    _add_knob(preserve_alpha_knob)
    _add_knob(nuke.Int_Knob("seed", "Seed"))
    _add_knob(nuke.Int_Knob("guidance_scale", "Guidance Scale"))
    _add_knob(nuke.Int_Knob("steps_num", "Steps"))
    _add_knob(nuke.Multiline_Eval_String_Knob("prompt", "Prompt"))
    _add_knob(nuke.Boolean_Knob("use_negative_prompt", "Negative Prompt", False))
    _add_knob(nuke.Multiline_Eval_String_Knob("negative_prompt", "Prompt (N)"))
    _add_knob(nuke.Boolean_Knob("use_structured_prompt", "Structured Prompt (JSON)", False))
    _add_knob(
        nuke.PyScript_Knob(
            "load_structured_prompt",
            "Load JSON/VGL",
            "import fibo_edit_recipes as _bria_fer; _bria_fer.on_load_structured_prompt()",
        )
    )
    _add_knob(nuke.Multiline_Eval_String_Knob("structured_prompt", "Prompt (S)"))
    link_status_knob = nuke.String_Knob("prompt_link_status", "Prompt Source", "manual")
    _add_knob(link_status_knob)
    try:
        link_status_knob.setEnabled(False)
    except Exception:
        pass
    stash_knob = nuke.String_Knob("prompt_manual_stash", "_prompt_stash", "")
    _add_knob(stash_knob)
    try:
        stash_knob.setVisible(False)
    except Exception:
        pass
    _add_knob(nuke.Enumeration_Knob("mask_type", "Mask Type", ["manual", "automatic"]))
    _add_knob(nuke.Text_Knob("run_sep", "", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "run_fibo_edit_recipes",
            "Run FIBO Edit Recipe",
            "import fibo_edit_recipes as _bria_fer; _bria_fer.on_fibo_edit_recipes()",
        )
    )
    _add_knob(nuke.Text_Knob("global_sep", "", ""))
    _add_knob(nuke.Boolean_Knob("use_global_project_path", "Use Global Project Path", False))
    _add_knob(nuke.File_Knob("result_path", "Result Path"))
    _add_knob(nuke.Enumeration_Knob("compare_source", "Before Source", ["Original", "Previous Edit"]))
    _add_knob(nuke.Enumeration_Knob("output_view", "Compare A|B", ["Before", "After"]))
    _add_knob(nuke.Text_Knob("dashboard_sep", "", ""))
    _add_knob(nuke.String_Knob("status", "Status", "idle"))
    _add_knob(
        nuke.PyScript_Knob(
            "open_dashboard",
            "Open Bria Dashboard",
            "import _bria_nuke_menu as _bria_menu; _bria_menu._open_bria_panel()",
        )
    )
    _add_knob(nuke.Text_Knob("gen_summary_sep", "", ""))
    _add_knob(nuke.String_Knob("generation_summary", "Generation Summary", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "view_generation_data",
            "View Generation Data",
            "import common as _bria_common; _bria_common.on_view_generation_data()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "copy_generation_data",
            "Copy Generation Data",
            "import common as _bria_common; _bria_common.on_copy_generation_data()",
        )
    )
    _add_knob(nuke.Tab_Knob("history_tab", "History"))
    _add_knob(
        nuke.Text_Knob(
            "history_help",
            "",
            "Recent results history saved in this node. Use the selector to load the desired available run.",
        )
    )
    _add_knob(nuke.Text_Knob("history_help_sep", "", ""))
    _add_knob(nuke.Enumeration_Knob("history_selected_entry", "Selected Run", ["No history yet"]))
    _add_knob(nuke.Enumeration_Knob("history_load_target", "Load Selected Run As", ["Before", "After"]))
    _add_knob(nuke.String_Knob("history_count", "History Count", "0"))
    _add_knob(nuke.String_Knob("history_debug", "History Debug", "ui ready"))
    _add_knob(nuke.String_Knob("history_selected_summary", "Selection", "No history yet"))
    _add_knob(nuke.String_Knob("history_selected_path", "File Path", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "history_load_selected_result",
            "Load",
            "import common as _bria_common; _bria_common.on_load_history_result()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_reveal_selected_result",
            "Preview File",
            "import common as _bria_common; _bria_common.on_reveal_history_result()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_view_selected_metadata",
            "View Selected Metadata",
            "import common as _bria_common; _bria_common.on_view_history_metadata()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_copy_selected_metadata",
            "Copy Selected Metadata",
            "import common as _bria_common; _bria_common.on_copy_history_metadata()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_load_selected_metadata",
            "Load Selected Settings",
            "import common as _bria_common; _bria_common.on_load_history_metadata()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_clear_entries",
            "Clear History",
            "import common as _bria_common; _bria_common.on_clear_generation_history()",
        )
    )
    _add_knob(nuke.Text_Knob("history_actions_sep", "", ""))
    _add_knob(nuke.Multiline_Eval_String_Knob("generation_data", "Generation Data"))
    _add_knob(nuke.Multiline_Eval_String_Knob("generation_history_data", "Generation History"))
    _add_knob(nuke.Tab_Knob("presets_tab", "Presets"))
    _add_knob(nuke.String_Knob("preset_name", "Preset Name", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "save_generation_preset",
            "Save Preset",
            "import common as _bria_common; _bria_common.on_save_generation_preset()",
        )
    )
    _add_knob(nuke.Text_Knob("preset_name_sep", "", ""))
    _add_knob(nuke.Enumeration_Knob("preset_selected", "Preset", ["Select"]))
    _add_knob(
        nuke.PyScript_Knob(
            "load_generation_preset",
            "Load Preset",
            "import common as _bria_common; _bria_common.on_load_generation_preset()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "refresh_preset_list",
            "Refresh Presets",
            "import common as _bria_common; _bria_common.on_refresh_generation_presets()",
        )
    )
    _add_knob(nuke.Boolean_Knob("load_presets_from_examples", "Load from Bria Examples"))
    _add_knob(nuke.Text_Knob("preset_actions_sep", "", ""))
    _add_knob(nuke.File_Knob("preset_path", "Preset Path"))
    _add_knob(nuke.String_Knob("preset_summary", "Preset Summary", "No preset loaded"))
    _add_knob(nuke.Multiline_Eval_String_Knob("preset_details", "Preset Details"))
    _add_knob(nuke.Multiline_Eval_String_Knob("generation_preset_data", "Preset Data"))
    _add_knob(nuke.Tab_Knob("info_tab", "Info"))
    _add_knob(nuke.Text_Knob("gizmo_info", "Gizmo", "Bria FIBO Edit Recipes v1.0.0"))
    _add_knob(nuke.Text_Knob("about_info", "About this Gizmo: ", "Edits an image using curated Bria AI prompt recipes"))
    _add_knob(nuke.Text_Knob("preset_help_info", "Presets", "Reusable node settings saved as JSON presets. Runtime result paths and generation metadata are not included. Optionally include shipped Bria example presets with the toggle above."))

    try:
        node.knob("seed").setTooltip("Optional. Positive integer.")
        node.knob("guidance_scale").setTooltip("Optional. Integer in range 1-5.")
        node.knob("steps_num").setTooltip("Optional. 0 means unset. Positive integer when set.")
        node.knob("prompt").setTooltip("Auto-filled from the selected category and preset. You can edit freely.")
        node.knob("negative_prompt").setTooltip("Enter your negative prompt here!")
        node.knob("structured_prompt").setTooltip("Enter your structured prompt here!")
        node.knob("load_structured_prompt").setTooltip("Load a JSON/VGL file into Structured Prompt as text.")
        node.knob("compare_source").setTooltip("Before = compare latest edit against either the original input or the previous edit.")
        node.knob("category").setTooltip("Pick a recipe category. 'Custom' lets you write a prompt from scratch.")

        node.knob("guidance_scale").setRange(1, 5)
        node.knob("seed").setValue(0)
        node.knob("guidance_scale").setValue(4)
        node.knob("steps_num").setValue(0)

        node.knob("negative_prompt").setVisible(False)
        node.knob("structured_prompt").setVisible(False)
        node.knob("structured_prompt").setEnabled(False)
        node.knob("load_structured_prompt").setEnabled(False)
        node.knob("mask_type").setValue("automatic")
        _apply_project_path_default(node)
        _apply_preset_path_default(node)
        try:
            import common as _bria_common  # type: ignore
            _bria_common.refresh_generation_preset_ui(node)
        except Exception:
            pass
        node.knob("compare_source").setValue("Original")
        node.knob("output_view").setValue("Before")

        # Hide target slots by default (Custom category on creation)
        try:
            node.knob("object_targets_header").setVisible(False)
            node.knob("comp_elements_header").setVisible(False)
            for i in range(1, max_obj_slots + 1):
                node.knob(f"obj_target_name_{i}").setVisible(False)
                node.knob(f"obj_target_mod_{i}").setVisible(False)
            for i in range(1, max_comp_slots + 1):
                node.knob(f"comp_element_desc_{i}").setVisible(False)
            # Hide per-category preset dropdowns (all hidden when category=Custom)
            for cat_token, _lab in category_order:
                if cat_token == "custom":
                    continue
                pk = node.knob(f"preset_{cat_token}")
                if pk is not None:
                    pk.setVisible(False)
        except Exception:
            pass

        preserve_alpha_knob.setFlag(nuke.STARTLINE)
        preserve_alpha_knob.setVisible(False)
        preserve_alpha_knob.setEnabled(False)
        node.knob("use_negative_prompt").setFlag(nuke.STARTLINE)
        node.knob("negative_prompt").setFlag(nuke.STARTLINE)
        node.knob("use_structured_prompt").setFlag(nuke.STARTLINE)
        node.knob("structured_prompt").setFlag(nuke.STARTLINE)
        node.knob("run_fibo_edit_recipes").setFlag(nuke.STARTLINE)
        node.knob("open_dashboard").setFlag(nuke.STARTLINE)
        node.knob("history_count").setEnabled(False)
        node.knob("history_count").setVisible(False)
        node.knob("history_debug").setEnabled(False)
        node.knob("history_debug").setVisible(False)
        node.knob("history_selected_summary").setEnabled(False)
        node.knob("history_selected_path").setEnabled(False)
        node.knob("history_load_target").setValue("After")
        node.knob("history_load_target").setFlag(nuke.STARTLINE)
        node.knob("history_load_selected_result").clearFlag(nuke.STARTLINE)
        node.knob("history_reveal_selected_result").setFlag(nuke.STARTLINE)
        node.knob("history_view_selected_metadata").setFlag(nuke.STARTLINE)
        node.knob("history_copy_selected_metadata").setFlag(nuke.STARTLINE)
        node.knob("history_load_selected_metadata").setFlag(nuke.STARTLINE)
        node.knob("history_clear_entries").setFlag(nuke.STARTLINE)
        node.knob("history_actions_sep").setFlag(nuke.STARTLINE)
        node.knob("generation_summary").setEnabled(False)
        node.knob("view_generation_data").setFlag(nuke.STARTLINE)
        node.knob("copy_generation_data").setFlag(nuke.STARTLINE)
        node.knob("generation_data").setVisible(False)
        node.knob("generation_data").setEnabled(False)
        node.knob("generation_history_data").setVisible(False)
        node.knob("generation_history_data").setEnabled(False)
        node.knob("preset_summary").setEnabled(False)
        node.knob("preset_details").setEnabled(False)
        node.knob("save_generation_preset").clearFlag(nuke.STARTLINE)
        node.knob("load_generation_preset").setFlag(nuke.STARTLINE)
        node.knob("refresh_preset_list").clearFlag(nuke.STARTLINE)
        node.knob("load_presets_from_examples").setFlag(nuke.STARTLINE)
        node.knob("load_presets_from_examples").setTooltip("Include shipped presets from $BRIA_EXAMPLES/presets in the preset list. Saving still uses the normal preset path.")
        node.knob("preset_actions_sep").setFlag(nuke.STARTLINE)
        node.knob("generation_preset_data").setVisible(False)
        node.knob("generation_preset_data").setEnabled(False)
        if node.knob("status") is not None:
            node.knob("status").setEnabled(False)
        node.knob("knobChanged").setValue("import fibo_edit_recipes as _bria_fer; _bria_fer.on_knob_changed()")
        node.knob("prompt").setVisible(True)
        node.knob("prompt").setEnabled(True)

        # Height hints to match FIBO Edit prompt area sizing
        if node.knob("prompt") is not None and not str(node.knob("prompt").value() or "").strip():
            node.knob("prompt").setValue("\n\n")
        if node.knob("negative_prompt") is not None and not str(node.knob("negative_prompt").value() or "").strip():
            node.knob("negative_prompt").setValue("\n\n")
        if node.knob("structured_prompt") is not None and not str(node.knob("structured_prompt").value() or "").strip():
            node.knob("structured_prompt").setValue("\n\n")
    except Exception:
        pass

    _repair_fibo_edit_internal_graph(node)
    _add_premultiply_with_mask_path(node)
    _connect_created_node_inputs(node, selected_inputs)

    nuke.tprint(f"[Bria] Created node: {node.name()}")


def _add_premultiply_with_mask_path(node) -> None:
    """Extend the recipes node's internal graph with a Copy + Premult path so the
    'Premultiply with Mask' toggle can produce a clean cutout when Create Mask
    preset is active.

    Graph shape after this:
        input_image ─┬───────────────────────────────────────────┐
                     │                                            │
                     ├─ Copy(rgb=in1, a=in2.red) ─ Premult ──────┐│
        loader_result┘                                            ││
                                                                  │├ switch_premult ─ output1
        loader_before ─┐                                          ││
                       ├ switch_before_after ────────────────────┘│
        loader_result ─┘                                           │
                                                                   │
    switch_premult.which is bound to `parent.premultiply_with_mask`.
    """
    import nuke  # type: ignore

    try:
        node.begin()
        input_image = nuke.toNode("input_image")
        loader_result = nuke.toNode("loader_result")
        existing_switch = nuke.toNode("switch_before_after")
        output1 = nuke.toNode("output1")
        if not all((input_image, loader_result, existing_switch, output1)):
            return

        # Don't double-build if node was repaired and already has the chain.
        if nuke.toNode("switch_premult") is not None:
            return

        # Mask edge softening: Blur smooths the staircase from the model's coarse
        # binary output; FilterErode at half size pulls the silhouette back in to
        # compensate for blur expansion, keeping the alpha tight to the named element.
        # Both controlled by `parent.mask_edge_softness` so size=0 leaves mask as-is.
        mask_blur = nuke.nodes.Blur(name="mask_blur")
        mask_blur.setInput(0, loader_result)
        try:
            mask_blur.knob("size").setExpression("parent.mask_edge_softness")
            mask_blur["label"].setValue("AA staircase")
        except Exception:
            pass

        mask_erode = nuke.nodes.FilterErode(name="mask_erode")
        mask_erode.setInput(0, mask_blur)
        try:
            mask_erode.knob("size").setExpression("parent.mask_edge_softness * 0.5")
            mask_erode["label"].setValue("compensate blur expansion")
        except Exception:
            pass

        copy_node = nuke.nodes.Copy(name="copy_mask_to_alpha")
        try:
            copy_node.knob("from0").setValue("rgba.red")
            copy_node.knob("to0").setValue("rgba.alpha")
        except Exception:
            pass
        copy_node.setInput(0, input_image)
        copy_node.setInput(1, mask_erode)
        try:
            copy_node["label"].setValue("mask.r → alpha")
        except Exception:
            pass

        premult = nuke.nodes.Premult(name="premult_mask")
        premult.setInput(0, copy_node)

        switch_premult = nuke.nodes.Switch(name="switch_premult")
        switch_premult.setInput(0, existing_switch)
        switch_premult.setInput(1, premult)
        try:
            switch_premult.knob("which").setExpression("parent.premultiply_with_mask")
            switch_premult["label"].setValue("0=raw / 1=premultiplied")
        except Exception:
            pass

        output1.setInput(0, switch_premult)
    except Exception as exc:
        try:
            nuke.tprint(f"[Bria] Premultiply path build skipped: {exc}")
        except Exception:
            pass
    finally:
        try:
            node.end()
        except Exception:
            pass


def _create_sequence_output_node() -> None:
    """Batch-renders a chain of upstream Bria nodes across a frame range."""
    import nuke  # type: ignore

    selected_inputs = _capture_selected_nodes(limit=1)

    node = nuke.createNode("NoOp", inpanel=False)
    node.setName("Bria_Sequence_Output", uncollide=True)
    try:
        if node.knob("inputs") is not None:
            node.knob("inputs").setValue(1)
    except Exception:
        pass

    def _add_knob(knob):
        try:
            if node.knob(knob.name()) is None:
                node.addKnob(knob)
        except Exception:
            node.addKnob(knob)

    _add_knob(nuke.Tab_Knob("Settings", "Settings"))
    _add_knob(nuke.Text_Knob("bria_header", "", "Bria : Sequence Output"))
    _add_knob(
        nuke.Text_Knob(
            "bria_help",
            "",
            "Batch-renders all upstream Bria nodes across a frame range.",
        )
    )
    _add_knob(nuke.Text_Knob("frame_range_sep", "", ""))

    # Default frame range to project timeline
    try:
        root = nuke.root()
        first_frame = int(root.knob("first_frame").value())
        last_frame = int(root.knob("last_frame").value())
    except Exception:
        first_frame, last_frame = 1, 1

    frame_start_knob = nuke.Int_Knob("frame_start", "Frame Start")
    _add_knob(frame_start_knob)
    frame_end_knob = nuke.Int_Knob("frame_end", "Frame End")
    _add_knob(frame_end_knob)
    frame_step_knob = nuke.Int_Knob("frame_step", "Frame Step")
    _add_knob(frame_step_knob)

    _add_knob(nuke.Text_Knob("output_sep", "", ""))
    _add_knob(nuke.File_Knob("output_path", "Output Path"))
    _add_knob(
        nuke.Text_Knob(
            "output_help",
            "",
            "Use #### or %04d for per-frame numbering (e.g. render.####.png).",
        )
    )

    _add_knob(nuke.Text_Knob("run_sep", "", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "render_sequence",
            "Render Bria Sequence",
            "import sequence_output as _bria_so; _bria_so.on_render_sequence()",
        )
    )

    _add_knob(nuke.Text_Knob("status_sep", "", ""))
    _add_knob(nuke.String_Knob("status", "Status", "idle"))
    _add_knob(
        nuke.PyScript_Knob(
            "open_dashboard",
            "Open Bria Dashboard",
            "import _bria_nuke_menu as _bria_menu; _bria_menu._open_bria_panel()",
        )
    )

    try:
        frame_start_knob.setValue(first_frame)
        frame_end_knob.setValue(last_frame)
        frame_step_knob.setValue(1)
        node.knob("frame_start").setTooltip("First frame to render (inclusive).")
        node.knob("frame_end").setTooltip("Last frame to render (inclusive).")
        node.knob("frame_step").setTooltip("Frame step. 1 = every frame, 2 = every other, etc.")
        node.knob("output_path").setTooltip(
            "Path template for per-frame output files. Use Nuke's standard "
            "#### (hash-pad, e.g. render.####.png) or %04d (printf). "
            "Houdini-style $F is also accepted for cross-DCC consistency."
        )
        node.knob("render_sequence").setFlag(nuke.STARTLINE)
        node.knob("open_dashboard").setFlag(nuke.STARTLINE)
        node.knob("status").setEnabled(False)
        node.knob("knobChanged").setValue("import sequence_output as _bria_so; _bria_so.on_knob_changed()")
    except Exception:
        pass

    _connect_created_node_inputs(node, selected_inputs)

    nuke.tprint(f"[Bria] Created node: {node.name()}")


def _create_upscale_node() -> None:
    import nuke  # type: ignore

    node = nuke.createNode("Group", inpanel=False)
    node.setName("Bria_Upscale", uncollide=True)
    _enable_node_thumbnail(node)

    def _add_knob(knob):
        try:
            if node.knob(knob.name()) is None:
                node.addKnob(knob)
        except Exception:
            node.addKnob(knob)

    _add_knob(nuke.Tab_Knob("settings_tab", "Settings"))
    _add_knob(nuke.Text_Knob("bria_header", "", "Bria : Upscale Resolution"))
    _add_knob(nuke.Boolean_Knob("preserve_alpha", "Preserve Alpha", True))
    _add_knob(nuke.Enumeration_Knob("desired_increase", "Desired Increase", ["2", "4"]))
    _add_knob(nuke.Text_Knob("run_sep_top", "", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "run_upscale",
            "Run Upscale",
            "import upscale as _bria_up; _bria_up.on_upscale()",
        )
    )
    _add_knob(nuke.Text_Knob("run_sep_bottom", "", ""))
    _add_knob(nuke.Boolean_Knob("use_global_project_path", "Use Global Project Path", False))
    _add_knob(nuke.File_Knob("result_path", "Result Path"))
    _add_knob(nuke.Enumeration_Knob("output_view", "Output", ["Before", "After"]))
    _add_knob(nuke.Text_Knob("dashboard_sep", "", ""))
    _add_knob(nuke.String_Knob("status", "Status", "idle"))
    _add_knob(
        nuke.PyScript_Knob(
            "open_dashboard",
            "Open Bria Dashboard",
            "import _bria_nuke_menu as _bria_menu; _bria_menu._open_bria_panel()",
        )
    )
    _add_knob(nuke.Text_Knob("gen_summary_sep", "", ""))
    _add_knob(nuke.String_Knob("generation_summary", "Generation Summary", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "view_generation_data",
            "View Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_view_generation_data()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "copy_generation_data",
            "Copy Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_copy_generation_data()",
        )
    )
    _add_history_metadata_knobs(node, _add_knob, allow_before=False)
    _add_knob(nuke.Tab_Knob("info_tab", "Info"))
    _add_knob(nuke.Text_Knob("gizmo_info", "Gizmo", "Bria Upscale v1.1.1"))
    _add_knob(nuke.Text_Knob("about_info", "About this Gizmo: ", "Upscales resolution using Bria AI"))
    _add_knob(
        nuke.Text_Knob(
            "enhance_note",
            "Note",
            "For quality enhancement workflows, use Bria Enhance.",
        )
    )

    try:
        node.knob("desired_increase").setValue("2")
        _apply_project_path_default(node)
        node.knob("output_view").setValue("Before")
        node.knob("run_upscale").setFlag(nuke.STARTLINE)
        node.knob("open_dashboard").setFlag(nuke.STARTLINE)
        _configure_history_metadata_knobs(node, allow_before=False)
        if node.knob("status") is not None:
            node.knob("status").setEnabled(False)
        node.knob("knobChanged").setValue("import upscale as _bria_up; _bria_up.on_knob_changed()")
    except Exception:
        pass

    _add_group_compare_internal(node)
    nuke.tprint(f"[Bria] Created node: {node.name()}")


def _create_enhance_node() -> None:
    import nuke  # type: ignore
    import gc

    is_nc = False
    try:
        is_nc = bool(nuke.env.get("nc"))
    except Exception:
        is_nc = False

    selected_inputs = [] if is_nc else _capture_selected_nodes(limit=2)
    try:
        gc.collect()
    except Exception:
        pass

    node = nuke.createNode("Group", inpanel=False)
    if node is None:
        nuke.message(
            "Bria Enhance could not be created as a Group node in this Nuke session. "
            "\n\nDetail: nuke.createNode(\"Group\", inpanel=False) returned None before any internal nodes were built."
        )
        return
    node.setName("Bria_Enhance", uncollide=True)
    _enable_node_thumbnail(node)
    try:
        if node.knob("inputs") is not None:
            node.knob("inputs").setValue(2)
    except Exception:
        pass

    def _add_knob(knob):
        try:
            if node.knob(knob.name()) is None:
                node.addKnob(knob)
        except Exception:
            node.addKnob(knob)

    _add_knob(nuke.Tab_Knob("settings_tab", "Settings"))
    _add_knob(nuke.Text_Knob("bria_header", "", "Bria : Enhance"))
    _add_knob(nuke.Boolean_Knob("preserve_alpha", "Preserve Alpha", True))
    _add_knob(nuke.Enumeration_Knob("resolution", "Resolution", ["1MP", "2MP", "4MP"]))
    _add_knob(nuke.Int_Knob("seed", "Seed"))
    sync_knob = nuke.Boolean_Knob("sync", "Sync", False)
    _add_knob(sync_knob)
    _add_knob(nuke.Boolean_Knob("use_mask", "Use Mask", False))
    _add_knob(nuke.String_Knob("enhance_variant", "Enhance Variant", ""))
    _add_knob(nuke.String_Knob("debug_create_error", "Debug Create Error", ""))
    _add_knob(nuke.Text_Knob("run_sep_top", "", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "run_enhance",
            "Run Enhance",
            "import nuke, importlib, enhance as _bria_en; importlib.reload(_bria_en); _bria_en.on_enhance(nuke.thisNode())",
        )
    )
    _add_knob(nuke.Text_Knob("run_sep_bottom", "", ""))
    _add_knob(nuke.Boolean_Knob("use_global_project_path", "Use Global Project Path", False))
    _add_knob(nuke.File_Knob("result_path", "Result Path"))
    _add_knob(nuke.Enumeration_Knob("output_view", "Output", ["Before", "After"]))
    _add_knob(nuke.Text_Knob("status_sep", "", ""))
    _add_knob(nuke.String_Knob("status", "Status", "idle"))
    _add_knob(
        nuke.PyScript_Knob(
            "open_dashboard",
            "Open Bria Dashboard",
            "import _bria_nuke_menu as _bria_menu; _bria_menu._open_bria_panel()",
        )
    )
    _add_knob(nuke.Text_Knob("gen_summary_sep", "", ""))
    _add_knob(nuke.String_Knob("generation_summary", "Generation Summary", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "view_generation_data",
            "View Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_view_generation_data()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "copy_generation_data",
            "Copy Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_copy_generation_data()",
        )
    )
    _add_history_metadata_knobs(node, _add_knob, allow_before=False)
    _add_knob(nuke.Tab_Knob("info_tab", "Info"))
    _add_knob(nuke.Text_Knob("gizmo_info", "Gizmo", "Bria Enhance v1.1.1"))
    _add_knob(nuke.Text_Knob("about_info", "About this Gizmo: ", "Enhances image quality using Bria AI"))

    try:
        node.knob("preserve_alpha").setValue(True)
        node.knob("sync").setValue(True)
        node.knob("sync").setVisible(False)
        node.knob("sync").setFlag(nuke.STARTLINE)
        node.knob("use_mask").setValue(False)
        node.knob("use_mask").setFlag(nuke.STARTLINE)
        if node.knob("enhance_variant") is not None:
            node.knob("enhance_variant").setValue("nc_pending" if is_nc else "full")
            node.knob("enhance_variant").setVisible(False)
            node.knob("enhance_variant").setEnabled(False)
        if node.knob("debug_create_error") is not None:
            node.knob("debug_create_error").setVisible(False)
            node.knob("debug_create_error").setEnabled(False)
        _apply_project_path_default(node)
        node.knob("output_view").setValue("Before")
        node.knob("run_enhance").setFlag(nuke.STARTLINE)
        node.knob("open_dashboard").setFlag(nuke.STARTLINE)
        _configure_history_metadata_knobs(node, allow_before=False)
        if node.knob("status") is not None:
            node.knob("status").setEnabled(False)
        node.knob("knobChanged").setValue("import enhance as _bria_en; _bria_en.on_knob_changed()")
    except Exception:
        pass

    if is_nc:
        if _build_enhance_nc_keymix_graph(node):
            _connect_created_node_inputs(node, selected_inputs)
            try:
                if node.knob("enhance_variant") is not None:
                    node.knob("enhance_variant").setValue("nc_keymix")
            except Exception:
                pass
            nuke.tprint(f"[Bria] Created NC keymix Enhance node: {node.name()}")
            return

        # _build_enhance_nc_keymix_graph may have partially created nodes before
        # hitting the NC limit and returning False.  Clear whatever was left inside
        # the group so _add_group_compare_internal starts from a clean slate.
        try:
            node.begin()
            for _orphan in list(nuke.allNodes(recurseGroups=False)):
                try:
                    nuke.delete(_orphan)
                except Exception:
                    pass
        finally:
            try:
                node.end()
            except Exception:
                pass

        _add_group_compare_internal(node)
        try:
            node.begin()
            loader_result = nuke.toNode("loader_result")
            if loader_result is not None and loader_result.knob("label") is not None:
                loader_result.knob("label").setValue("Enhanced")
        finally:
            try:
                node.end()
            except Exception:
                pass
        try:
            if node.knob("enhance_variant") is not None:
                node.knob("enhance_variant").setValue("nc_safe")
        except Exception:
            pass
        _connect_created_node_inputs(node, selected_inputs)
        nuke.tprint(f"[Bria] Created NC-safe Enhance node: {node.name()}")
        return

    if not _repair_enhance_internal_graph(node):
        debug_create_error = ""
        try:
            if node.knob("debug_create_error") is not None:
                debug_create_error = str(node.knob("debug_create_error").value() or "").strip()
        except Exception:
            debug_create_error = ""
        if not debug_create_error:
            debug_create_error = "repair returned False with no captured internal node failure detail"
        _delete_node_quietly(node)
        nuke.message(
            "Bria Enhance could not be created in this Nuke session. "
            + (f"\n\nDetail: {debug_create_error}" if debug_create_error else "")
        )
        return

    _connect_created_node_inputs(node, selected_inputs)
    nuke.tprint(f"[Bria] Created node: {node.name()}")


def _create_fibo_generate_node() -> None:
    import nuke  # type: ignore

    node = nuke.createNode("Group", inpanel=False)
    node.setName("Bria_FIBO_Generate", uncollide=True)
    _enable_node_thumbnail(node)
    try:
        if node.knob("inputs") is not None:
            node.knob("inputs").setValue(1)
    except Exception:
        pass

    def _add_knob(knob):
        try:
            if node.knob(knob.name()) is None:
                node.addKnob(knob)
        except Exception:
            node.addKnob(knob)

    _add_knob(nuke.Tab_Knob("settings_tab", "Settings"))
    _add_knob(nuke.Text_Knob("bria_header", "", "Bria FIBO Generate"))
    preserve_alpha_knob = nuke.Boolean_Knob("preserve_alpha", "Preserve Alpha", True)
    _add_knob(preserve_alpha_knob)
    _add_knob(nuke.Enumeration_Knob("aspect_ratio", "Aspect Ratio", ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9"]))
    _add_knob(nuke.Enumeration_Knob("resolution", "Resolution", ["1MP", "4MP"]))
    _add_knob(nuke.Enumeration_Knob("model", "Model", ["standard", "lite", "tailored"]))

    _add_knob(nuke.Int_Knob("seed", "Seed"))
    _add_knob(nuke.Int_Knob("guidance_scale", "Guidance Scale"))
    _add_knob(nuke.Int_Knob("steps_num", "Steps"))
    # "Generate Multiple Images" — toggle that gates a 4-control block. All 4
    # appear together when the toggle is ON; the slider's range and the button's
    # behavior become meaningful once a multi-variant run completes.
    #   1. use_multiple_images (this toggle, always visible)
    #   2. num_images dropdown (visible when toggle is on)
    #   3. result_variant slider (visible when toggle is on)
    #   4. use_this_image button (visible when toggle is on)
    use_multiple_knob = nuke.Boolean_Knob(
        "use_multiple_images", "Generate Multiple Images", False
    )
    try:
        use_multiple_knob.setTooltip(
            "Generate 2-4 variants per click. After Run, drag Image Index to preview them; "
            "click 'Use This Image' to lock in the one you want."
        )
        use_multiple_knob.setFlag(nuke.STARTLINE)
    except Exception:
        pass
    _add_knob(use_multiple_knob)
    # num_images cap of 4 mirrors the proven Background Replace HDA precedent
    # (the only Bria endpoint we have empirically validated num_results > 1 against).
    num_images_knob = nuke.Enumeration_Knob("num_images", "Num Images", ["2", "3", "4"])
    try:
        num_images_knob.setTooltip(
            "Number of variants to generate per call (2-4). All variants are saved to disk."
        )
        num_images_knob.setVisible(False)
    except Exception:
        pass
    _add_knob(num_images_knob)
    # Image Index — Double_Knob renders as a real slider in Nuke 17 (Int_Knob does not,
    # even with setRange + nuke.SLIDER flag). Slider range is set dynamically per-run
    # to match the ACTUAL variants saved. Value is read with int(round(...)).
    result_variant_knob = nuke.Double_Knob("result_variant", "Image Index")
    try:
        result_variant_knob.setRange(1, 4)
        result_variant_knob.setValue(1)
        result_variant_knob.setVisible(False)
        result_variant_knob.setTooltip(
            "Drag to preview each variant from the most recent multi-image run. "
            "Click 'Use This Image' to lock in the selected variant."
        )
    except Exception:
        pass
    _add_knob(result_variant_knob)
    use_this_image_knob = nuke.PyScript_Knob(
        "use_this_image",
        "Use This Image",
        "import importlib, fibo_generate as _bria_fg; importlib.reload(_bria_fg); _bria_fg.on_use_this_image()",
    )
    try:
        use_this_image_knob.setVisible(False)
        use_this_image_knob.setTooltip(
            "Promote the currently-displayed variant to the canonical Result Path."
        )
    except Exception:
        pass
    _add_knob(use_this_image_knob)
    variant_count_knob = nuke.String_Knob("result_variant_count", "_variant_count", "1")
    _add_knob(variant_count_knob)
    try:
        variant_count_knob.setVisible(False)
    except Exception:
        pass
    _add_knob(nuke.Text_Knob("prompt_sep", "", ""))
    _add_knob(nuke.Multiline_Eval_String_Knob("prompt", "Prompt"))

    use_negative_knob = nuke.Boolean_Knob("use_negative_prompt", "Negative", False)
    _add_knob(use_negative_knob)
    _add_knob(nuke.Multiline_Eval_String_Knob("negative_prompt", "Negative"))

    use_structured_knob = nuke.Boolean_Knob("use_structured_prompt", "Structured Prompt (JSON)", False)
    _add_knob(use_structured_knob)
    _add_knob(
        nuke.PyScript_Knob(
            "load_structured_prompt",
            "Load JSON/VGL",
            "import importlib, common as _bria_common, fibo_generate as _bria_fg; importlib.reload(_bria_common); importlib.reload(_bria_fg); _bria_fg.on_load_structured_prompt()",
        )
    )
    _add_knob(nuke.Multiline_Eval_String_Knob("structured_prompt", "Structured"))

    link_status_knob = nuke.String_Knob("prompt_link_status", "Prompt Source", "manual")
    _add_knob(link_status_knob)
    try:
        link_status_knob.setEnabled(False)
    except Exception:
        pass
    stash_knob = nuke.String_Knob("prompt_manual_stash", "_prompt_stash", "")
    _add_knob(stash_knob)
    try:
        stash_knob.setVisible(False)
    except Exception:
        pass

    _add_knob(nuke.String_Knob("tailored_model_id", "Tailored Model ID"))
    _add_knob(nuke.Double_Knob("tailored_model_influence", "Tailored Influence"))
    sync_knob = nuke.Boolean_Knob("sync", "Sync", True)
    _add_knob(sync_knob)

    run_knob = nuke.PyScript_Knob(
        "run_fibo_generate",
        "Run FIBO Generate",
        "import importlib, common as _bria_common, fibo_generate as _bria_fg; importlib.reload(_bria_common); importlib.reload(_bria_fg); _bria_fg.on_fibo_generate()",
    )
    _add_knob(nuke.Text_Knob("run_sep", "", ""))
    _add_knob(run_knob)
    _add_knob(nuke.Text_Knob("result_sep", "", ""))
    _add_knob(nuke.Boolean_Knob("use_global_project_path", "Use Global Project Path", False))
    _add_knob(nuke.File_Knob("result_path", "Result Path"))
    _add_knob(nuke.Enumeration_Knob("output_view", "Compare A|B", ["Before", "After"]))
    _add_knob(nuke.Text_Knob("dashboard_sep", "", ""))
    _add_knob(nuke.String_Knob("status", "Status", "idle"))
    _add_knob(
        nuke.PyScript_Knob(
            "open_dashboard",
            "Open Bria Dashboard",
            "import _bria_nuke_menu as _bria_menu; _bria_menu._open_bria_panel()",
        )
    )
    _add_knob(nuke.Text_Knob("gen_summary_sep", "", ""))
    _add_knob(nuke.String_Knob("generation_summary", "Generation Summary", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "view_generation_data",
            "View Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_view_generation_data()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "copy_generation_data",
            "Copy Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_copy_generation_data()",
        )
    )
    _add_knob(nuke.Tab_Knob("history_tab", "History"))
    _add_knob(
        nuke.Text_Knob(
            "history_help",
            "",
            "Recent results history saved in this node. Use the selector to load the desired available run.",
        )
    )
    _add_knob(nuke.Text_Knob("history_help_sep", "", ""))
    _add_knob(nuke.Enumeration_Knob("history_selected_entry", "Selected Run", ["No history yet"]))
    _add_knob(nuke.Enumeration_Knob("history_load_target", "Load Selected Run As", ["Before", "After"]))
    _add_knob(nuke.String_Knob("history_count", "History Count", "0"))
    _add_knob(nuke.String_Knob("history_debug", "History Debug", "ui ready"))
    _add_knob(nuke.String_Knob("history_selected_summary", "Selection", "No history yet"))
    _add_knob(nuke.String_Knob("history_selected_path", "File Path", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "history_load_selected_result",
            "Load",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_load_history_result()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_reveal_selected_result",
            "Preview File",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_reveal_history_result()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_view_selected_metadata",
            "View Selected Metadata",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_view_history_metadata()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_copy_selected_metadata",
            "Copy Selected Metadata",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_copy_history_metadata()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_load_selected_metadata",
            "Load Selected Settings",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_load_history_metadata()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "history_clear_entries",
            "Clear History",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_clear_generation_history()",
        )
    )
    _add_knob(nuke.Text_Knob("history_actions_sep", "", ""))
    _add_knob(nuke.Multiline_Eval_String_Knob("generation_data", "Generation Data"))
    _add_knob(nuke.Multiline_Eval_String_Knob("generation_history_data", "Generation History"))
    _add_knob(nuke.Tab_Knob("presets_tab", "Presets"))
    _add_knob(nuke.String_Knob("preset_name", "Preset Name", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "save_generation_preset",
            "Save Preset",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_save_generation_preset()",
        )
    )
    _add_knob(nuke.Text_Knob("preset_name_sep", "", ""))
    _add_knob(nuke.Enumeration_Knob("preset_selected", "Preset", ["Select"]))
    _add_knob(
        nuke.PyScript_Knob(
            "load_generation_preset",
            "Load Preset",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_load_generation_preset()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "refresh_preset_list",
            "Refresh Presets",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_refresh_generation_presets()",
        )
    )
    _add_knob(nuke.Boolean_Knob("load_presets_from_examples", "Load from Bria Examples"))
    _add_knob(nuke.Text_Knob("preset_actions_sep", "", ""))
    _add_knob(nuke.File_Knob("preset_path", "Preset Path"))
    _add_knob(nuke.String_Knob("preset_summary", "Preset Summary", "No preset loaded"))
    _add_knob(nuke.Multiline_Eval_String_Knob("preset_details", "Preset Details"))
    _add_knob(nuke.Multiline_Eval_String_Knob("generation_preset_data", "Preset Data"))
    _add_knob(nuke.Tab_Knob("info_tab", "Info"))
    _add_knob(nuke.Text_Knob("gizmo_info", "Gizmo", "Bria Generate v1.1.1"))
    _add_knob(nuke.Text_Knob("about_info", "About this Gizmo: ", "Generates an image using Bria AI"))
    _add_knob(nuke.Text_Knob("preset_help_info", "Presets", "Reusable node settings saved as JSON presets. Runtime result paths and generation metadata are not included. Optionally include shipped Bria example presets with the toggle above."))

    # Recommended starter defaults.
    try:
        node.knob("seed").setTooltip("Optional. Positive integer.")
        node.knob("guidance_scale").setTooltip("Optional. Integer in range 1-5.")
        node.knob("steps_num").setTooltip("Optional. 0 means unset. Positive integer when set.")
        node.knob("tailored_model_influence").setTooltip("Optional. Range: 0.0 to < 2.0. 0 means unset.")
        node.knob("output_view").setTooltip("Before = previous run. After = latest run.")
        node.knob("prompt").setTooltip("Enter your prompt here!")
        node.knob("negative_prompt").setTooltip("Enter your negative prompt here!")
        node.knob("structured_prompt").setTooltip("Enter your structured prompt here!")
        node.knob("load_structured_prompt").setTooltip("Load a JSON/VGL file into Structured Prompt as text.")

        node.knob("guidance_scale").setRange(1, 5)
        node.knob("tailored_model_influence").setRange(0.0, 1.99)

        node.knob("aspect_ratio").setValue("1:1")
        node.knob("model").setValue("standard")
        node.knob("seed").setValue(0)
        node.knob("guidance_scale").setValue(4)
        node.knob("steps_num").setValue(0)
        node.knob("output_view").setValue("Before")
        node.knob("tailored_model_influence").setValue(0.5)

        node.knob("negative_prompt").setVisible(False)
        node.knob("structured_prompt").setVisible(False)
        node.knob("structured_prompt").setEnabled(False)
        node.knob("load_structured_prompt").setEnabled(False)
        node.knob("tailored_model_id").setVisible(False)
        node.knob("tailored_model_influence").setVisible(False)
        node.knob("sync").setVisible(False)
        node.knob("prompt").setVisible(True)
        node.knob("prompt").setEnabled(True)

        # Height hint: seed multiline fields with blank lines so FIBO Generate
        # prompt areas render with comparable textarea height in Nuke UI.
        if node.knob("prompt") is not None and not str(node.knob("prompt").value() or "").strip():
            node.knob("prompt").setValue("\n\n\n\n\n")
        if node.knob("negative_prompt") is not None and not str(node.knob("negative_prompt").value() or "").strip():
            node.knob("negative_prompt").setValue("\n\n\n\n\n")

        preserve_alpha_knob.setFlag(nuke.STARTLINE)
        use_negative_knob.setFlag(nuke.STARTLINE)
        use_structured_knob.setFlag(nuke.STARTLINE)
        sync_knob.setFlag(nuke.STARTLINE)
        run_knob.setFlag(nuke.STARTLINE)
        node.knob("open_dashboard").setFlag(nuke.STARTLINE)
        node.knob("history_count").setEnabled(False)
        node.knob("history_count").setVisible(False)
        node.knob("history_debug").setEnabled(False)
        node.knob("history_debug").setVisible(False)
        node.knob("history_selected_summary").setEnabled(False)
        node.knob("history_selected_path").setEnabled(False)
        node.knob("history_load_target").setValue("After")
        node.knob("history_load_target").setFlag(nuke.STARTLINE)
        node.knob("history_load_selected_result").clearFlag(nuke.STARTLINE)
        node.knob("history_reveal_selected_result").setFlag(nuke.STARTLINE)
        node.knob("history_view_selected_metadata").setFlag(nuke.STARTLINE)
        node.knob("history_copy_selected_metadata").setFlag(nuke.STARTLINE)
        node.knob("history_load_selected_metadata").setFlag(nuke.STARTLINE)
        node.knob("history_clear_entries").setFlag(nuke.STARTLINE)
        node.knob("history_actions_sep").setFlag(nuke.STARTLINE)
        node.knob("generation_summary").setEnabled(False)
        node.knob("view_generation_data").setFlag(nuke.STARTLINE)
        node.knob("copy_generation_data").setFlag(nuke.STARTLINE)
        node.knob("generation_data").setVisible(False)
        node.knob("generation_data").setEnabled(False)
        node.knob("generation_history_data").setVisible(False)
        node.knob("generation_history_data").setEnabled(False)
        node.knob("preset_summary").setEnabled(False)
        node.knob("preset_details").setEnabled(False)
        node.knob("save_generation_preset").clearFlag(nuke.STARTLINE)
        node.knob("load_generation_preset").setFlag(nuke.STARTLINE)
        node.knob("refresh_preset_list").clearFlag(nuke.STARTLINE)
        node.knob("load_presets_from_examples").setFlag(nuke.STARTLINE)
        node.knob("load_presets_from_examples").setTooltip("Include shipped presets from $BRIA_EXAMPLES/presets in the preset list. Saving still uses the normal preset path.")
        node.knob("preset_actions_sep").setFlag(nuke.STARTLINE)
        node.knob("generation_preset_data").setVisible(False)
        node.knob("generation_preset_data").setEnabled(False)
        _apply_project_path_default(node)
        _apply_preset_path_default(node)
        try:
            import common as _bria_common  # type: ignore

            _bria_common.refresh_generation_preset_ui(node)
        except Exception:
            pass
        if node.knob("status") is not None:
            node.knob("status").setEnabled(False)
    except Exception:
        pass

    # Keep optional prompt/model controls tidy when knobs change.
    # importlib.reload mirrors the gizmo file's pattern so subsequent code edits
    # are picked up without requiring a Nuke restart.
    try:
        node.knob("knobChanged").setValue(
            "import importlib, fibo_generate as _bria_fg; "
            "importlib.reload(_bria_fg); _bria_fg.on_knob_changed()"
        )
    except Exception:
        pass

    # Build internal subnet network (A/B compare: previous vs latest result).
    try:
        node.begin()
        nuke.nodes.Input(name="structured_prompt_in")
        before = nuke.nodes.Read(name="loader_before")
        after = nuke.nodes.Read(name="loader_result")
        sw = nuke.nodes.Switch(name="switch_before_after")
        out = nuke.nodes.Output(name="output1")

        sw.setInput(0, before)
        sw.setInput(1, after)
        out.setInput(0, sw)

        try:
            before["label"].setValue("Before (previous)")
            after["label"].setValue("After")
            sw["label"].setValue("0=Before / 1=After")
        except Exception:
            pass
    finally:
        try:
            node.end()
        except Exception:
            pass

    _add_knob(nuke.Tab_Knob("info_tab", "Info"))
    _add_knob(nuke.Text_Knob("gizmo_info", "Gizmo", "Bria Generate v1.1.1"))
    _add_knob(nuke.Text_Knob("about_info", "About this Gizmo: ", "Generates an image using Bria AI"))

    nuke.tprint(f"[Bria] Created node: {node.name()}")


def _create_structured_prompt_node() -> None:
    import nuke  # type: ignore

    node = nuke.createNode("Group", inpanel=False)
    node.setName("Bria_Generate_VGL", uncollide=True)
    _enable_node_thumbnail(node)
    try:
        if node.knob("inputs") is not None:
            node.knob("inputs").setValue(1)
    except Exception:
        pass

    def _add_knob(knob):
        try:
            if node.knob(knob.name()) is None:
                node.addKnob(knob)
        except Exception:
            node.addKnob(knob)

    _add_knob(nuke.Tab_Knob("settings_tab", "Settings"))
    _add_knob(nuke.Text_Knob("bria_header", "", "Bria : Generate VGL"))
    _add_knob(nuke.Enumeration_Knob("pipeline", "Pipeline", ["standard", "lite"]))
    _add_knob(nuke.Boolean_Knob("use_input_image", "Use Input Image", True))
    _add_knob(nuke.Boolean_Knob("use_structured_prompt_input", "Use Structured Prompt Input", False))
    _add_knob(nuke.Text_Knob("prompt_sep", "", ""))
    _add_knob(nuke.Multiline_Eval_String_Knob("prompt", "Prompt"))
    _add_knob(
        nuke.PyScript_Knob(
            "load_structured_prompt_input",
            "Load JSON/VGL",
            "import structured_prompt as _bria_sp; _bria_sp.on_load_structured_prompt_input()",
        )
    )
    _add_knob(nuke.Multiline_Eval_String_Knob("structured_prompt_input", "Structured Prompt Input"))
    _add_knob(nuke.Boolean_Knob("lock_vgl", "Lock VGL", False))
    _add_knob(
        nuke.PyScript_Knob(
            "run_structured_prompt",
            "Generate VGL",
            "import structured_prompt as _bria_sp; _bria_sp.on_structured_prompt()",
        )
    )
    _add_knob(nuke.Boolean_Knob("use_custom_label", "Custom File Name", False))
    _add_knob(nuke.String_Knob("output_label", "Name", ""))
    _add_knob(nuke.Text_Knob("result_sep", "", ""))
    _add_knob(nuke.Boolean_Knob("use_global_project_path", "Use Global Project Path", False))
    _add_knob(nuke.File_Knob("result_path", "Result Path"))
    _add_knob(nuke.Multiline_Eval_String_Knob("structured_prompt_output", "Structured Prompt"))
    _add_knob(
        nuke.PyScript_Knob(
            "copy_structured_prompt",
            "Copy",
            "import structured_prompt as _bria_sp; _bria_sp.on_copy_structured_prompt()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "open_json_folder",
            "Open Prompt Folder",
            "import structured_prompt as _bria_sp; _bria_sp.on_open_structured_prompt_folder()",
        )
    )
    _add_knob(nuke.Text_Knob("dashboard_sep", "", ""))
    _add_knob(nuke.String_Knob("status", "Status", "idle"))
    _add_knob(
        nuke.PyScript_Knob(
            "open_dashboard",
            "Open Bria Dashboard",
            "import _bria_nuke_menu as _bria_menu; _bria_menu._open_bria_panel()",
        )
    )
    _add_knob(nuke.Text_Knob("gen_summary_sep", "", ""))
    _add_knob(nuke.String_Knob("generation_summary", "Generation Summary", ""))
    _add_knob(
        nuke.PyScript_Knob(
            "view_generation_data",
            "View Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_view_generation_data()",
        )
    )
    _add_knob(
        nuke.PyScript_Knob(
            "copy_generation_data",
            "Copy Generation Data",
            "import importlib, common as _bria_common; importlib.reload(_bria_common); _bria_common.on_copy_generation_data()",
        )
    )
    _add_history_metadata_knobs(node, _add_knob, allow_before=False)
    _add_knob(nuke.Tab_Knob("info_tab", "Info"))
    _add_knob(nuke.Text_Knob("gizmo_info", "Gizmo", "Bria Generate VGL v1.1.1"))
    _add_knob(
        nuke.Text_Knob(
            "about_info",
            "About this Gizmo: ",
            "Generates VGL (structured prompt JSON) using Bria AI.",
        )
    )

    try:
        node.knob("pipeline").setValue("standard")
        node.knob("use_input_image").setValue(True)
        node.knob("use_structured_prompt_input").setValue(False)
        node.knob("prompt").setTooltip("Describe the scene to generate structured prompt JSON.")
        node.knob("structured_prompt_input").setTooltip("Optional existing structured prompt JSON for refine mode (requires Prompt).")
        node.knob("load_structured_prompt_input").setTooltip("Load a JSON/VGL file into Structured Prompt Input.")
        node.knob("result_path").setTooltip("Optional directory or .json file path for saved structured prompt output. Default folder is prompt.")
        node.knob("structured_prompt_output").setTooltip("Generated structured prompt JSON output.")
        node.knob("copy_structured_prompt").setTooltip("Copy structured prompt output to clipboard.")
        node.knob("open_json_folder").setTooltip("Open saved prompt file location in Explorer.")
        _apply_project_path_default(node)
        if node.knob("prompt") is not None and not str(node.knob("prompt").value() or "").strip():
            node.knob("prompt").setValue("\n\n\n")
        if node.knob("prompt") is not None:
            node.knob("prompt").setVisible(True)
            node.knob("prompt").setEnabled(True)
        if node.knob("structured_prompt_input") is not None:
            node.knob("structured_prompt_input").setVisible(False)
            node.knob("structured_prompt_input").setEnabled(False)
            if not str(node.knob("structured_prompt_input").value() or "").strip():
                node.knob("structured_prompt_input").setValue("\n\n\n")
        if node.knob("load_structured_prompt_input") is not None:
            node.knob("load_structured_prompt_input").setEnabled(False)
        if node.knob("structured_prompt_output") is not None:
            node.knob("structured_prompt_output").clearFlag(nuke.DISABLED)
        if node.knob("status") is not None:
            node.knob("status").setEnabled(False)
        node.knob("run_structured_prompt").setFlag(nuke.STARTLINE)
        node.knob("use_custom_label").setTooltip("Enable to set a custom name for the saved JSON filename.")
        node.knob("output_label").setFlag(nuke.STARTLINE)
        node.knob("output_label").setTooltip("Name used in the output filename: bria_structured_prompt_{name}_{id}.json")
        node.knob("output_label").setVisible(False)
        node.knob("output_label").setEnabled(False)
        node.knob("load_structured_prompt_input").setFlag(nuke.STARTLINE)
        node.knob("use_global_project_path").setFlag(nuke.STARTLINE)
        node.knob("copy_structured_prompt").setFlag(nuke.STARTLINE)
        node.knob("open_json_folder").setFlag(nuke.STARTLINE)
        node.knob("open_dashboard").setFlag(nuke.STARTLINE)
        node.knob("generation_summary").setEnabled(False)
        node.knob("view_generation_data").setFlag(nuke.STARTLINE)
        node.knob("copy_generation_data").setFlag(nuke.STARTLINE)
        _configure_history_metadata_knobs(node, allow_before=False)
        # SP: "Load" beside File Path loads the prompt JSON; "Load Selected Settings" is hidden.
        btn = node.knob("history_load_selected_result")
        if btn is not None:
            btn.setCommand("import structured_prompt as _bria_sp; _bria_sp.on_load_history_structured_prompt()")
        meta_btn = node.knob("history_load_selected_metadata")
        if meta_btn is not None:
            meta_btn.setVisible(False)
            meta_btn.setEnabled(False)
        node.knob("knobChanged").setValue("import structured_prompt as _bria_sp; _bria_sp.on_knob_changed()")
    except Exception:
        pass

    try:
        node.begin()
        input_image = nuke.nodes.Input(name="input_image")
        try:
            if input_image is not None and input_image.knob("number") is not None:
                input_image.knob("number").setValue(0)
        except Exception:
            pass
        out = nuke.nodes.Output(name="output1")
        if input_image is not None and out is not None:
            out.setInput(0, input_image)
    finally:
        try:
            node.end()
        except Exception:
            pass

    nuke.tprint(f"[Bria] Created node: {node.name()}")


def _load_panel_class():
    # Aligned with DCC structure: nuke/panels/bria_dashboard.py
    from bria_dashboard import BriaPanel  # type: ignore

    return BriaPanel


def _show_status_message(check_network: bool = False) -> None:
    import nuke  # type: ignore

    from bria_core import get_status

    status = get_status(dcc="nuke", check_network=check_network, timeout_s=3)
    if status.get("ok"):
        msg = f"Bria Nuke OK\nEndpoint: {status.get('resolved_endpoint')}"
    else:
        msg = f"Bria Nuke Warning\n{status.get('message')} ({status.get('reason')})"
    nuke.message(msg)


def _refresh_gizmos_now() -> None:
    import nuke  # type: ignore

    try:
        from export_gizmos import export_all_gizmos

        out_dir = export_all_gizmos()
        nuke.message(f"Bria gizmos refreshed:\n{out_dir}")
    except Exception as exc:
        nuke.message(f"Bria Nuke Warning\nFailed to refresh gizmos: {exc}")


def _open_bria_panel() -> None:
    import nuke  # type: ignore
    import nukescripts  # type: ignore

    global _LAST_PANEL

    nuke.tprint("[Bria] Dashboard command invoked")

    # Try restoring registered panel first (dock-aware).
    try:
        restored = nukescripts.restorePanel("com.bria.nuke.dashboard.qt")
        if restored:
            nuke.tprint("[Bria] Dashboard restored via restorePanel")
            return
    except Exception as exc:
        nuke.tprint(f"[Bria] restorePanel failed: {exc}")

    # Fallback: instantiate directly.
    try:
        panel_cls = _load_panel_class()
        _LAST_PANEL = panel_cls()
    except Exception as exc:
        nuke.message(f"Bria Nuke Warning\nFailed to load dashboard panel class: {exc}")
        nuke.tprint(f"[Bria] Dashboard load class failed: {exc}")
        return

    try:
        _LAST_PANEL.show()
        nuke.tprint("[Bria] Dashboard opened as standalone widget")
    except Exception as exc:
        nuke.message(f"Bria Nuke Warning\nFailed to show dashboard: {exc}")


def _register_panel() -> bool:
    import __main__
    import nuke  # type: ignore
    import nukescripts  # type: ignore

    if bool(getattr(nuke, "_bria_panel_registered", False)):
        return True

    try:
        import bria_dashboard as _bria_dashboard_module  # type: ignore

        setattr(__main__, "bria_dashboard", _bria_dashboard_module)
    except Exception as exc:
        _warn(f"Dashboard module import failed: {exc}")
        return False

    try:
        nukescripts.registerWidgetAsPanel(
            "bria_dashboard.BriaPanel",
            "Bria Dashboard",
            "com.bria.nuke.dashboard.qt",
        )
    except Exception as exc:
        _warn(f"Dashboard register failed: {exc}")
        return False
    try:
        setattr(nuke, "_bria_panel_registered", True)
    except Exception:
        pass
    _warn("Dashboard panel registered")
    return True


def _install_menu() -> None:
    import nuke  # type: ignore

    if bool(getattr(nuke, "_bria_menu_installed", False)):
        return

    menu_root = nuke.menu("Nuke")
    bria = menu_root.findItem("Bria")
    if bria is None:
        bria = menu_root.addMenu("Bria")
    bria.addCommand("Dashboard", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._open_bria_panel()", "")
    bria.addCommand("Create/Erase", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_erase_node()", "")
    bria.addCommand("Create/GenFill", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_genfill_node()", "")
    bria.addCommand("Create/Expand", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_expand_node()", "")
    bria.addCommand("Create/FIBO Edit", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_fibo_edit_node()", "")
    bria.addCommand("Create/FIBO Edit Recipes", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_fibo_edit_recipes_node()", "")
    bria.addCommand("Create/FIBO Generate", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_fibo_generate_node()", "")
    bria.addCommand(
        "Create/Generate VGL",
        f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_structured_prompt_node()",
        "",
    )
    bria.addCommand("Create/RMBG", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_rmbg_node()", "")
    bria.addCommand("Create/Upscale", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_upscale_node()", "")
    bria.addCommand(
        "Create/Enhance",
        f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu = _bria_menu._reload_menu_module_from_disk() or _bria_menu; _bria_menu._create_enhance_node()",
        "",
    )
    bria.addCommand(
        "Create/Sequence Output",
        f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_sequence_output_node()",
        "",
    )
    bria.addSeparator()
    bria.addCommand(
        "Status/Check Config",
        f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._show_status_message(False)",
        "",
    )
    bria.addCommand(
        "Status/Check Network",
        f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._show_status_message(True)",
        "",
    )
    bria.addSeparator()
    bria.addCommand(
        "Tools/Refresh Gizmos",
        f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._refresh_gizmos_now()",
        "",
    )

    # Also expose it under Pane menu, which is where dockable panels are expected in Nuke.
    pane_menu = nuke.menu("Pane")
    if pane_menu.findItem("Bria Dashboard") is None:
        pane_menu.addCommand("Bria Dashboard", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._open_bria_panel()", "")

    # Register Bria create entries in the Nodes toolbar so Tab search can find them.
    try:
        nodes_toolbar = nuke.menu("Nodes")
        nodes_bria = nodes_toolbar.findItem("Bria AI")
        if nodes_bria is None:
            nodes_bria = nodes_toolbar.addMenu("Bria AI")
        nodes_bria.addCommand("Bria Erase", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_erase_node()")
        nodes_bria.addCommand("Bria GenFill", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_genfill_node()")
        nodes_bria.addCommand("Bria Expand", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_expand_node()")
        nodes_bria.addCommand("Bria FIBO Edit", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_fibo_edit_node()")
        nodes_bria.addCommand("Bria FIBO Edit Recipes", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_fibo_edit_recipes_node()")
        nodes_bria.addCommand("Bria FIBO Generate", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_fibo_generate_node()")
        nodes_bria.addCommand(
            "Bria Generate VGL",
            f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_structured_prompt_node()",
        )
        nodes_bria.addCommand("Bria RMBG", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_rmbg_node()")
        nodes_bria.addCommand("Bria Upscale", f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_upscale_node()")
        nodes_bria.addCommand(
            "Bria Enhance",
            f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu = _bria_menu._reload_menu_module_from_disk() or _bria_menu; _bria_menu._create_enhance_node()",
        )
        nodes_bria.addCommand(
            "Bria Sequence Output",
            f"import {_MODULE_ALIAS} as _bria_menu; _bria_menu._create_sequence_output_node()",
        )
    except Exception:
        pass

    try:
        setattr(nuke, "_bria_menu_installed", True)
    except Exception:
        pass

    _warn("Menu installed")


def register_ui() -> dict[str, bool]:
    panel_ok = False
    menu_ok = False

    try:
        panel_ok = bool(_register_panel())
    except Exception as exc:
        _warn(f"Dashboard registration failed non-fatally: {exc}")

    try:
        _install_menu()
        menu_ok = True
    except Exception as exc:
        _warn(f"Menu installation failed non-fatally: {exc}")

    return {"panel": panel_ok, "menu": menu_ok}


if __name__ != "__bria_nuke_menu_export__":
    register_ui()
