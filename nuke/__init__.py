"""Nuke adapter package for Bria integrations."""

from bria_core.version import __version__  # noqa: F401

from .adapter import (
	erase_from_files,
	genfill_from_files,
	fibo_edit_from_files,
	remove_background_from_files,
	upscale_from_files,
	expand_from_files,
	fibo_generate_from_payload,
	structured_prompt_generate,
)

from .node_utils import (
	NukeNodeUtils,
	as_nuke_path,
	resolve_temp_dir,
	opt_parm_bool,
	opt_parm_float,
	opt_parm_int,
	opt_parm_menu_str,
	opt_parm_str,
)

from .nodes import (
	fibo_generate_bria,
	on_fibo_generate,
	on_erase,
	on_expand,
	on_genfill,
	on_fibo_edit,
	on_rmbg,
	on_upscale,
	on_enhance,
	on_structured_prompt,
)

__all__ = [
	"NukeNodeUtils",
	"opt_parm_str",
	"opt_parm_menu_str",
	"opt_parm_bool",
	"opt_parm_int",
	"opt_parm_float",
	"as_nuke_path",
	"resolve_temp_dir",
	"erase_from_files",
	"genfill_from_files",
	"fibo_edit_from_files",
	"remove_background_from_files",
	"upscale_from_files",
	"expand_from_files",
	"fibo_generate_from_payload",
	"structured_prompt_generate",
	"fibo_generate_bria",
	"on_fibo_generate",
	"on_erase",
	"on_expand",
	"on_genfill",
	"on_fibo_edit",
	"on_rmbg",
	"on_upscale",
	"on_enhance",
	"on_structured_prompt",
]
