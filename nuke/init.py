"""Bria Nuke startup entrypoint (loaded from NUKE_PATH)."""

from __future__ import annotations

import os
from pathlib import Path

from _startup_loader import ensure_paths, resolve_nuke_root, resolve_repo_root, run_bootstrap_init
from project_path_defaults import (
    default_bria_project_path,
    default_use_global_project_path,
    rehydrate_project_path_env,
    sync_all_nodes_to_global,
)


def _is_true_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _warn(message: str) -> None:
    try:
        import nuke  # type: ignore

        nuke.tprint(f"[Bria] {message}")
    except Exception:
        print(f"[Bria] {message}")


def _register_plugin_paths() -> None:
    try:
        import nuke  # type: ignore
    except Exception:
        return

    nuke_root = resolve_nuke_root()
    for rel in ("gizmos", "nodes", "panels"):
        path = nuke_root / rel
        if path.exists():
            try:
                nuke.pluginAddPath(str(path))
            except Exception:
                pass


def _register_bria_node_defaults() -> None:
    try:
        import nuke  # type: ignore
    except Exception:
        return

    if bool(getattr(nuke, "_bria_node_defaults_registered", False)):
        return

    bria_classes = (
        "bria_erase",
        "bria_genfill",
        "bria_expand",
        "bria_fibo_edit",
        "bria_fibo_edit_recipes",
        "bria_fibo_generate",
        "bria_rmbg",
        "bria_upscale",
        "bria_enhance",
    )

    def _apply_project_path_default() -> None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
        if node is None:
            return

        try:
            knob = node.knob("use_global_project_path")
            if knob is not None:
                knob.setValue(default_use_global_project_path())
        except Exception:
            pass

    def _repair_enhance_gizmo() -> None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
        if node is None:
            return

        try:
            import _bria_nuke_menu as _bria_menu  # type: ignore

            repair_fn = getattr(_bria_menu, "_repair_enhance_internal_graph", None)
            if callable(repair_fn):
                repair_fn(node)
        except Exception:
            pass

    for class_name in bria_classes:
        try:
            nuke.addOnCreate(_apply_project_path_default, nodeClass=class_name)
        except Exception:
            pass

    try:
        nuke.addOnCreate(_repair_enhance_gizmo, nodeClass="bria_enhance")
    except Exception:
        pass

    # ── Prompt field height hints ──────────────────────────────────────
    # Nuke does not persist Multiline_Eval_String_Knob display height in
    # .nk files.  We use addOnScriptLoad (fires AFTER all knob values are
    # restored) to walk every Bria node and re-apply trailing-newline
    # padding so prompt areas keep their intended height after reload.
    # Values are stripped by opt_parm_str() before reaching any API call.

    def _pad_knobs(node, hints):
        for knob_name, min_newlines in hints:
            try:
                knob = node.knob(knob_name)
                if knob is None:
                    continue
                val = str(knob.value() or "")
                trailing = len(val) - len(val.rstrip("\n"))
                if trailing < min_newlines:
                    knob.setValue(val.rstrip("\n") + "\n" * min_newlines)
            except Exception:
                pass

    def _classify_bria_node(node):
        """Identify Bria node type by knob presence, not Class()."""
        if node.knob("run_structured_prompt") is not None:
            return "sp"
        # FIBO Edit Recipes has category knob on top of FIBO Edit's fields
        if node.knob("category") is not None and node.knob("structured_prompt") is not None:
            return "fibo_edit_recipes"
        # FIBO Edit has structured_prompt knob + mask input (3 inputs)
        if node.knob("structured_prompt") is not None and node.knob("aspect_ratio") is None:
            return "fibo_edit"
        # FIBO Generate has aspect_ratio knob
        if node.knob("aspect_ratio") is not None and node.knob("prompt") is not None:
            return "fibo_generate"
        return None

    _HINTS_BY_TYPE = {
        "fibo_edit": [("prompt", 2), ("negative_prompt", 2), ("structured_prompt", 2)],
        "fibo_edit_recipes": [("prompt", 2), ("negative_prompt", 2), ("structured_prompt", 2)],
        "fibo_generate": [("prompt", 5), ("negative_prompt", 5)],
        "sp": [("prompt", 3), ("structured_prompt_input", 3), ("structured_prompt_output", 5)],
    }

    def _restore_prompt_heights() -> None:
        """Walk all nodes after script load and re-pad multiline prompt knobs."""
        try:
            all_nodes = nuke.allNodes(recurseGroups=False)
        except Exception:
            return
        padded = 0
        for node in all_nodes:
            try:
                bria_type = _classify_bria_node(node)
                if bria_type is None:
                    continue
                hints = _HINTS_BY_TYPE.get(bria_type)
                if hints:
                    _pad_knobs(node, hints)
                    padded += 1
            except Exception:
                pass
        if padded:
            nuke.tprint(f"[Bria] Restored prompt field heights on {padded} node(s)")

    try:
        nuke.addOnScriptLoad(_restore_prompt_heights)
    except Exception:
        pass

    # ── Project-path knob re-sync ──────────────────────────────────────
    # Per-node `use_global_project_path` values persist in saved .nk
    # files. If the global default changes between sessions (e.g. the
    # user enables the Dashboard toggle after the file was last saved),
    # saved nodes load with their old knob values and silently fall back
    # to the OS temp directory for output — which is purged on a TTL by
    # every OS. This handler brings persisted per-node knobs back in
    # sync with the current global default on every script load.

    def _sync_project_path_knobs() -> None:
        try:
            changed = sync_all_nodes_to_global()
        except Exception:
            return
        if changed:
            try:
                nuke.tprint(
                    f"[Bria] Synced use_global_project_path on {changed} node(s) "
                    f"to match the current Dashboard/global setting"
                )
            except Exception:
                pass

    try:
        nuke.addOnScriptLoad(_sync_project_path_knobs)
    except Exception:
        pass

    # ── Structured prompt (VGL) restore from disk ──────────────────────
    # Nuke's save/load round-trip does not preserve the escape-on-write
    # trick used to keep TCL specials like `[` from truncating the value
    # of a Multiline_Eval_String_Knob. After reloading a saved .nk, the
    # SP node's `structured_prompt_output` knob holds the corrupted
    # truncated form. The disk .json file (path stored in `result_path`)
    # is the single source of truth and survives the cycle, so we re-read
    # it on script load and re-populate the visible knob plus any
    # downstream FIBO `structured_prompt` knobs.

    _SP_INPUT_INDEX_BY_TYPE = {
        "fibo_generate": 0,
        "fibo_edit": 2,
        "fibo_edit_recipes": 2,
    }

    def _restore_structured_prompt_outputs() -> None:
        try:
            all_nodes = nuke.allNodes(recurseGroups=False)
        except Exception:
            return

        # Late imports — these modules depend on nodes/ being on sys.path
        # which is only true after _startup_loader.ensure_paths() has run.
        try:
            from common import escape_for_eval_knob as _escape_for_eval_knob  # type: ignore
        except Exception:
            _escape_for_eval_knob = None  # type: ignore

        # First pass: re-populate every SP node's `structured_prompt_output`
        # knob from its `result_path` JSON file on disk.
        sp_nodes = []
        restored_sp = 0
        for node in all_nodes:
            try:
                bria_type = _classify_bria_node(node)
            except Exception:
                bria_type = None
            if bria_type != "sp":
                continue
            sp_nodes.append(node)
            try:
                rp_knob = node.knob("result_path")
                out_knob = node.knob("structured_prompt_output")
                if rp_knob is None or out_knob is None:
                    continue
                rp = str(rp_knob.value() or "").strip()
                if not rp:
                    continue
                rp_resolved = os.path.expandvars(os.path.expanduser(rp))
                if not os.path.isfile(rp_resolved):
                    continue
                with open(rp_resolved, "r", encoding="utf-8") as fh:
                    content = fh.read()
                if not content.strip():
                    continue
                if _escape_for_eval_knob is not None:
                    out_knob.setValue(_escape_for_eval_knob(content))
                else:
                    out_knob.setValue(content)
                restored_sp += 1
            except Exception:
                pass

        # Second pass: for each FIBO node, copy the (now-correct) VGL from its
        # upstream SP node into its `structured_prompt` knob.
        restored_fibo = 0
        for node in all_nodes:
            try:
                bria_type = _classify_bria_node(node)
            except Exception:
                bria_type = None
            input_idx = _SP_INPUT_INDEX_BY_TYPE.get(bria_type)
            if input_idx is None:
                continue
            try:
                upstream = node.input(input_idx)
            except Exception:
                upstream = None
            if upstream is None:
                continue
            try:
                if _classify_bria_node(upstream) != "sp":
                    continue
            except Exception:
                continue
            try:
                src_knob = upstream.knob("structured_prompt_output")
                dst_knob = node.knob("structured_prompt")
                if src_knob is None or dst_knob is None:
                    continue
                # `.value()` un-escapes via TCL eval and gives us the original JSON
                sp_text = str(src_knob.value() or "")
                if not sp_text.strip():
                    continue
                if _escape_for_eval_knob is not None:
                    dst_knob.setValue(_escape_for_eval_knob(sp_text))
                else:
                    dst_knob.setValue(sp_text)
                restored_fibo += 1
            except Exception:
                pass

        if restored_sp or restored_fibo:
            nuke.tprint(
                f"[Bria] Restored structured prompt: "
                f"{restored_sp} SP node(s), {restored_fibo} downstream FIBO node(s)"
            )

    try:
        nuke.addOnScriptLoad(_restore_structured_prompt_outputs)
    except Exception:
        pass

    try:
        setattr(nuke, "_bria_node_defaults_registered", True)
    except Exception:
        pass


def _maybe_export_gizmos() -> None:
    nuke_root = resolve_nuke_root()
    gizmo_dir = nuke_root / "gizmos"

    force_export = _is_true_env(os.getenv("BRIA_EXPORT_GIZMOS_ON_STARTUP"))
    disable_auto_export = _is_true_env(os.getenv("BRIA_DISABLE_GIZMO_AUTOEXPORT"))

    # Default behavior: preserve existing gizmos unless explicit startup export is requested.
    if disable_auto_export or not force_export:
        return

    try:
        from export_gizmos import export_all_gizmos

        export_all_gizmos(str(gizmo_dir))
    except Exception as exc:
        try:
            import nuke  # type: ignore

            nuke.tprint(f"[Bria] Gizmo auto-export skipped/failed: {exc}")
        except Exception:
            pass


def _register_favorite_dirs(repo_root: Path) -> None:
    try:
        import nuke  # type: ignore
    except Exception:
        return

    bria_root = Path(os.getenv("NUKE_BRIA", str(repo_root))).expanduser().resolve()
    if not bria_root.exists():
        return

    try:
        nuke.addFavoriteDir("Bria", str(bria_root))
    except Exception:
        pass

    project_root = str(os.getenv("BRIA_PROJECT_PATH") or default_bria_project_path()).strip()
    use_project = default_use_global_project_path()
    if project_root and use_project:
        project_path = Path(project_root).expanduser()
        try:
            project_path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            remove_fav = getattr(nuke, "removeFavoriteDir", None)
            if callable(remove_fav):
                remove_fav("Project")
        except Exception:
            pass
        try:
            nuke.addFavoriteDir("Project", str(project_path.resolve()))
        except Exception:
            pass


def _init() -> None:
    repo_root = resolve_repo_root()
    os.environ.setdefault("NUKE_BRIA", str(repo_root))
    os.environ.setdefault("BRIA_EXTENSION_ROOT", str(repo_root))
    os.environ.setdefault("BRIA_EXAMPLES", str((repo_root / "examples").resolve()))
    rehydrate_project_path_env()
    ensure_paths(repo_root)
    _maybe_export_gizmos()
    _register_plugin_paths()
    _register_bria_node_defaults()
    _register_favorite_dirs(repo_root)
    run_bootstrap_init()


try:
    _init()
except Exception as exc:
    _warn(f"Startup initialization failed: {exc}")
