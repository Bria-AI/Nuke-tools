"""Export Bria Nuke Group-shell nodes to gizmo files.

Run inside Nuke Script Editor:

    import export_gizmos
    export_gizmos.export_all_gizmos()

By default this writes into <repo>/nuke/gizmos.
"""

from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path
from typing import Callable, Iterable, Tuple


_GIZMO_INPUT_COUNTS = {
    "bria_expand": 1,
    "bria_erase": 2,
    "bria_genfill": 2,
    "bria_fibo_edit": 2,
    "bria_enhance": 2,
    "bria_structured_prompt": 1,
}


def _gizmo_base_name(path: Path) -> str:
    name = path.name
    if name.endswith(".gizmo.nknc"):
        return name[: -len(".gizmo.nknc")]
    if name.endswith(".gizmo"):
        return name[: -len(".gizmo")]
    return path.stem


def _ensure_gizmo_inputs_line(lines: list[str], count: int) -> None:
    gizmo_idx = -1
    for idx, line in enumerate(lines):
        if line.strip() == "Gizmo {":
            gizmo_idx = idx
            break
    if gizmo_idx < 0:
        return

    body_idx = gizmo_idx + 1
    while body_idx < len(lines):
        stripped = lines[body_idx].strip()
        if not stripped:
            body_idx += 1
            continue
        if stripped.startswith("inputs "):
            indent = lines[body_idx][: len(lines[body_idx]) - len(lines[body_idx].lstrip())]
            lines[body_idx] = f"{indent}inputs {int(count)}"
            return
        if stripped.startswith("name ") or stripped.startswith("knobChanged "):
            indent = lines[body_idx][: len(lines[body_idx]) - len(lines[body_idx].lstrip())]
            lines.insert(body_idx, f"{indent}inputs {int(count)}")
            return
        body_idx += 1


def _ensure_input_number(lines: list[str], input_ordinal: int, number_value: int) -> None:
    input_indices: list[int] = [idx for idx, line in enumerate(lines) if line.strip() == "Input {"]
    if len(input_indices) <= input_ordinal:
        return

    start_idx = input_indices[input_ordinal]
    end_idx = start_idx + 1
    while end_idx < len(lines):
        if lines[end_idx].strip() == "}":
            break
        end_idx += 1
    if end_idx >= len(lines):
        return

    number_idx = -1
    for idx in range(start_idx + 1, end_idx):
        if lines[idx].strip().startswith("number "):
            number_idx = idx
            break

    indent = lines[start_idx][: len(lines[start_idx]) - len(lines[start_idx].lstrip())] + " "
    if number_idx >= 0:
        lines[number_idx] = f"{indent}number {int(number_value)}"
    else:
        lines.insert(end_idx, f"{indent}number {int(number_value)}")


def _resolve_repo_root() -> Path:
    for key in ("BRIA_EXTENSION_ROOT", "NUKE_BRIA"):
        value = os.getenv(key, "").strip()
        if value:
            return Path(value).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _resolve_nuke_root() -> Path:
    value = os.getenv("BRIA_NUKE_ROOT", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    return Path(__file__).resolve().parent


def _nc_suffix(nuke_module) -> str:
    try:
        if bool(nuke_module.env.get("nc")):
            return ".gizmo.nknc"
    except Exception:
        pass
    return ".gizmo"


def _load_menu_module(nuke_root: Path):
    module = sys.modules.get("_bria_nuke_menu")
    if module is not None:
        return module

    runpy.run_path(str(nuke_root / "menu.py"), run_name="__bria_nuke_menu_export__")
    module = sys.modules.get("_bria_nuke_menu")
    if module is None:
        raise RuntimeError("Failed to load Bria Nuke menu module alias '_bria_nuke_menu'")
    return module


def _clear_selection(nuke_module) -> None:
    for node in nuke_module.allNodes(recurseGroups=False):
        try:
            node["selected"].setValue(False)
        except Exception:
            pass


def _find_new_group_node(nuke_module, before_names: set[str]):
    after = nuke_module.allNodes(recurseGroups=False)
    created = [node for node in after if node.name() not in before_names and node.Class() == "Group"]
    if not created:
        return None
    return created[-1]


def _export_single(
    nuke_module,
    create_fn: Callable[[], None],
    output_path: Path,
) -> None:
    before_names = {node.name() for node in nuke_module.allNodes(recurseGroups=False)}
    create_fn()
    node = _find_new_group_node(nuke_module, before_names)
    if node is None:
        raise RuntimeError(f"Could not detect created Group node for {output_path.name}")

    _clear_selection(nuke_module)
    node["selected"].setValue(True)
    nuke_module.nodeCopy(str(output_path))
    _normalize_gizmo_file(output_path)
    nuke_module.delete(node)


def _normalize_gizmo_file(path: Path) -> None:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")

    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() == "Group {":
            indent = line[: len(line) - len(line.lstrip())]
            lines[idx] = f"{indent}Gizmo {{"
            break

    base_name = _gizmo_base_name(path)
    expected_inputs = _GIZMO_INPUT_COUNTS.get(base_name)
    if expected_inputs is not None:
        _ensure_gizmo_inputs_line(lines, expected_inputs)
        if expected_inputs >= 1:
            _ensure_input_number(lines, 0, 0)
        if expected_inputs >= 2:
            _ensure_input_number(lines, 1, 1)

    normalized = "\n".join(lines) + "\n"
    path.write_bytes(normalized.encode("utf-8"))


def export_all_gizmos(output_dir: str | None = None) -> Path:
    import nuke  # type: ignore

    repo_root = _resolve_repo_root()
    nuke_root = _resolve_nuke_root()

    if output_dir:
        target_dir = Path(output_dir).expanduser().resolve()
    else:
        target_dir = repo_root / "nuke" / "gizmos"
    target_dir.mkdir(parents=True, exist_ok=True)

    menu_mod = _load_menu_module(nuke_root)
    suffix = _nc_suffix(nuke)

    exports: Iterable[Tuple[str, str]] = (
        ("_create_erase_node", "bria_erase"),
        ("_create_genfill_node", "bria_genfill"),
        ("_create_expand_node", "bria_expand"),
        ("_create_fibo_edit_node", "bria_fibo_edit"),
        ("_create_fibo_generate_node", "bria_fibo_generate"),
        ("_create_structured_prompt_node", "bria_structured_prompt"),
        ("_create_rmbg_node", "bria_rmbg"),
        ("_create_upscale_node", "bria_upscale"),
        ("_create_enhance_node", "bria_enhance"),
    )

    for create_name, base_name in exports:
        create_fn = getattr(menu_mod, create_name, None)
        if not callable(create_fn):
            raise RuntimeError(f"Missing menu creator: {create_name}")
        out_path = target_dir / f"{base_name}{suffix}"
        _export_single(nuke, create_fn, out_path)
        nuke.tprint(f"[Bria] Exported {out_path}")

    nuke.tprint(f"[Bria] Export complete: {target_dir}")
    return target_dir


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Bria Nuke nodes as gizmos")
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        default=None,
        help="Optional output folder (defaults to <repo>/nuke/gizmos)",
    )
    return parser


def _main(argv: list[str]) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    try:
        import nuke  # type: ignore # noqa: F401
    except Exception:
        print("[Bria] export_gizmos.py must run inside Nuke's Python environment.")
        print("[Bria] Use one of these:")
        print("  1) Nuke Script Editor:")
        print("     import export_gizmos; export_gizmos.export_all_gizmos()")
        print("  2) Nuke terminal mode:")
        print('     <Nuke executable> -t -c "import export_gizmos; export_gizmos.export_all_gizmos()"')
        return 2

    try:
        output = export_all_gizmos(args.output_dir)
        print(f"[Bria] Exported gizmos to: {output}")
        return 0
    except Exception as exc:
        print(f"[Bria] Gizmo export failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
