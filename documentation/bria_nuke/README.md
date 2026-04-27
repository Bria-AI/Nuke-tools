# Bria Nuke Setup

## Installation

Add one line to `~/.nuke/init.py` (create the file if it doesn't exist):

```python
nuke.pluginAddPath("/path/to/bria-nuke/nuke")
```

Replace the path with the `nuke` subfolder inside your Bria tools folder. Restart Nuke — the **Bria** menu will appear. Open **Bria → Dashboard** to enter your API key.

See the [top-level README](../../README.md) for full quick-start instructions including where to find `~/.nuke/` on each platform.

## Alternative setup (studios and pipelines)

### Environment variable

Set `NUKE_PATH` before launching Nuke:

```bash
export NUKE_PATH="/path/to/bria-nuke/nuke:$NUKE_PATH"
```

### Dev launcher scripts

- `tools/dev/start_nuke.sh` (macOS/Linux)
- `tools/dev/start_nuke.bat` (Windows)

These set `NUKE_PATH`, `BRIA_EXTENSION_ROOT`, `BRIA_NUKE_ROOT`, and `NUKE_BRIA` automatically. They also handle API-key fallback (if `BRIA_API_KEY_NUKE` is unset and `BRIA_API_KEY_HOUDINI` exists, the Houdini key is used). On macOS, `start_nuke.sh` auto-detects a CA bundle to prevent TLS errors.

These scripts are intended for local developer machines and may include hardcoded Nuke paths.

### Environment variables reference

- `NUKE_PATH` — must include the `nuke/` subfolder (prepended)
- `BRIA_EXTENSION_ROOT` — repo root (optional, auto-detected from file location)
- `BRIA_NUKE_ROOT` — the `nuke/` subfolder (optional, auto-detected)
- `NUKE_BRIA` — repo root alias for portable `.nk` file paths (recommended for examples)
- `BRIA_EXPORT_GIZMOS_ON_STARTUP=1` — force gizmo refresh on launch
- `BRIA_DISABLE_GIZMO_AUTOEXPORT=1` — guarantee no startup gizmo export

## Bria menu in Nuke

After startup, Nuke includes a **Bria** menu:

- `Dashboard`
- `Create/Erase`
- `Create/GenFill`
- `Create/Expand`
- `Create/FIBO Edit`
- `Create/FIBO Edit Recipes`
- `Create/FIBO Generate`
- `Create/Generate VGL`
- `Create/RMBG`
- `Create/Upscale`
- `Create/Enhance`
- `Create/Sequence Output`
- `Status/Check Config`
- `Status/Check Network`

Dashboard is also available in `Pane -> Bria Dashboard`.

Nodes toolbar / Tab-search integration:

- Nuke `Nodes` menu includes a `Bria AI` submenu that routes through the same Bria creator functions and creates Group nodes by default.
- Commands are labeled `Bria ...` to make `Tab + type "bria"` discoverable.

Gizmo maintenance command:

- `Nuke -> Bria -> Tools/Refresh Gizmos` regenerates exported gizmo artifacts from the current creator scripts.
- These exported gizmos are retained for compatibility, distribution, and discovery workflows; the main runtime path is Group-first.

## Node behavior overview

All Bria nodes:

- write result file path into `result_path`
- support internal preview switching via `Read + Switch`
- are created from `nuke/menu.py` and execute via `nuke/nodes/*.py`
- default to menu-created Group nodes for better scene reliability and steadier UX

Current compare behavior:

- most image-input nodes use standard `Before` / `After` preview (`original input` vs `latest result`)
- FIBO Generate uses `Before` = previous generated result, `After` = latest generated result
- FIBO Edit uses `output_view` plus `compare_source`:
	- `compare_source = Original` → compare original input vs latest edit
	- `compare_source = Previous Edit` → compare previous edit vs latest edit

Current node set (11):

- Erase (`Create/Erase`) — image + mask input
- GenFill (`Create/GenFill`) — image + mask + prompt
- Expand (`Create/Expand`) — single image + prompt
- FIBO Edit (`Create/FIBO Edit`) — image + optional mask + prompt/structured prompt (3 inputs: Image, Mask, Structured Prompt)
- FIBO Edit Recipes (`Create/FIBO Edit Recipes`) — FIBO Edit with 99 curated preset prompts across 11 categories; same input layout as FIBO Edit
- FIBO Generate (`Create/FIBO Generate`) — generation from prompt/structured prompt (1 input for connected Generate VGL node, no image input required)
- Generate VGL (`Create/Generate VGL`) — generate structured prompt JSON from text and/or images (formerly "Structured Prompt" node)
- RMBG (`Create/RMBG`) — single image background removal
- Upscale (`Create/Upscale`) — single image increase-resolution upscale
- Enhance (`Create/Enhance`) — single image enhance by target resolution (optional region mask)
- Sequence Output (`Create/Sequence Output`) — batch render a chain of upstream Bria nodes across a frame range (NoOp; output ports through to a Write-equivalent inside the node)

Enhance defaults and controls:

- `preserve_alpha` defaults to **on**
- `sync` is hidden on current new nodes and forced to `true` at runtime because async polling is not implemented for Enhance yet
- `seed` is optional
- `Use Mask` toggles region-limited internal composite (`Keymix`) between original and enhanced result
- mask inputs without alpha are normalized internally (mask red -> alpha) before compositing

Enhance Non-Commercial note:

- Enhance has a dedicated Non-Commercial variant (`nc_keymix` mode) that builds a simplified 8-node internal graph to stay within Nuke NC's internal node/object limits.
- The NC variant uses a `Keymix`-based composite rather than the full commercial graph.
- `.gizmo.nknc` export artifacts can be generated in Nuke Non-Commercial.
- Mask toggling works in both commercial and NC variants without rewiring.

Result path UX:

- all Bria creators and gizmos expose `result_path` as a file-browser knob (Read-node style path picker)
- shipped example-scene history/metadata paths are tokenized under `$BRIA_EXAMPLES` where possible for better portability across machines
- older saved absolute history entries remain unchanged until those runs are regenerated or the node history is cleared

Sidecar metadata:

- a `.json` sidecar file is automatically written beside every result image on each successful run
- filename: `bria_{prefix}_result_{run_id}.json` (same `run_id` as the image file)
- content includes: operation, endpoint, result path, key settings, and full API response
- no user action needed; export is always-on

Generation Summary:

- `View Generation Data`, `Copy Generation Data`, and the generation summary field are in the **Settings** tab on all nodes

FIBO Generate UX highlights:

- `seed` default is `0`
- `guidance_scale` default is `4`
- `steps_num` default is `0` (treated as unset)
- `sync` is hidden in UI
- `prompt`, `negative_prompt`, and `structured_prompt` are multiline fields (negative/structured are toggle-gated)
- when `Structured Prompt (JSON)` is enabled:
	- plain `prompt` is hidden/disabled
	- `Load JSON/VGL` is enabled
- when structured mode is disabled:
	- plain `prompt` is shown/enabled
	- `Load JSON/VGL` is disabled
- `Load JSON/VGL` reads `.json`, `.vgl`, or `.txt` from disk and inserts file contents into `structured_prompt` as text
- presets refresh on node creation
- `Load from Bria Examples` can include shipped presets from `$BRIA_EXAMPLES/presets` without changing the save destination
- includes `status` field (`idle` / `processing` / `done` / `error`)
- includes `Open Bria Dashboard` button
- `Before` / `After` compares previous generated result against latest generated result

FIBO Edit UX highlights:

- supports image input, optional mask input (`mask_type = manual|automatic`), and optional Generate VGL node connected to input 2
	- input 0: Image
	- input 1: Mask (optional)
	- input 2: Generate VGL node (optional — connects output of a `Bria Generate VGL` node directly)
- `seed` default is `0`
- `guidance_scale` default is `4`
- `steps_num` default is `0` (treated as unset)
- structured prompt uses the same toggle-gated behavior as Generate:
	- enabling structured mode hides/disables plain `prompt`
	- `Load JSON/VGL` stays disabled until structured mode is enabled
- `Load JSON/VGL` reads `.json`, `.vgl`, or `.txt` into `structured_prompt` as text
- presets refresh on node creation
- `Load from Bria Examples` can include shipped presets from `$BRIA_EXAMPLES/presets` without changing the save destination
- `compare_source` lets artists switch `Before` between:
	- `Original`
	- `Previous Edit`
- `After` always shows the latest edit result
- history selection updates no longer force the UI back to `Latest`

Structured prompt notes:

- in the shipped Nuke flow, JSON/VGL is sent as structured prompt text, not as multipart file upload
- the dedicated `Bria Generate VGL` node follows the documented endpoint combinations: `prompt`, `images`, `images + prompt`, or `structured_prompt + prompt`
- Edit requests must send either plain prompt or structured prompt, not both
- current runtime hides/ignores the plain prompt whenever structured mode is active

Generate VGL node history:

- the `Bria Generate VGL` node tracks generation history in its History tab (same pattern as other nodes)
- each history entry is labelled with the result filename (e.g. `bria_structured_prompt_test_v002.json`)
- `File Path` shows the path to the saved prompt JSON for the selected run
- `Load` (beside File Path) loads the generated structured prompt JSON content into the `Structured Prompt` output field — 1-1 match between the selected history entry and the file on disk
- `Load Selected Settings` is hidden for Generate VGL nodes (preset system deferred to next phase)
- output filenames:
	- with custom label: `bria_structured_prompt_{slug}_v001.json`, auto-versioning to `_v002.json` etc. on each run with the same name
	- without custom label: `bria_structured_prompt_result_{run_id}.json` (timestamp-based)
- the custom label toggle and Name field appear inline beside the Generate button
- **Lock VGL toggle** — when on, the node skips the API call on subsequent Run clicks (and during Sequence Output batches) as long as a cached output exists. Useful for keeping a structured prompt constant across all frames of a sequence.

FIBO Edit Recipes UX highlights:

- categorized preset launcher built on FIBO Edit's execution path; ships with 99 prompts across 11 categories (Style, Weather, Seasons, Time of Day, Camera, Lighting, Compositing, Cleanup, AI Corrections, Object Edits, plus Custom for user prompts)
- pick a category → the matching Preset dropdown appears with that category's options; selecting a preset auto-fills the prompt field
- **Object-targeted presets** (5 of them: delete, replace, isolate, change color, change material) reveal 5 fixed Object slots — fill in the names of objects in your image and the prompt is rebuilt with constraint-injection ("STRICT LOCKS") wording so the model only edits what you specify
- **Compositing-targeted presets** (7 of them: harmonize lighting, match shadows, blend edges, color harmonize, fix reflections, integrate elements, match ambient) reveal 5 fixed Element slots — describe which composited elements need attention
- auto-vs-manual prompt tracking: if you edit the prompt manually after a preset is loaded, switching presets won't clobber your edits
- delegates the actual API call to the existing FIBO Edit pipeline — same history, presets, metadata sidecar, and viewer behavior

Sequence Output node:

- batch render a chain of upstream Bria nodes across a frame range
- input(0) connects to the last node of your Bria chain (typically a FIBO Generate / FIBO Edit / Enhance whose result you want as the per-frame output)
- knobs: Frame Start / Frame End / Frame Step (default to project frame range), Output Path (use `####` or `%04d` for per-frame numbering — Houdini-style `$F` is also accepted), Render button, Status field
- on Render, walks upstream from the node, identifies all Bria nodes by knob presence, topologically sorts them with cycle detection, then per frame: sets `nuke.frame()`, cooks each Bria node in dependency order via that node's `on_<op>()` entry point, and renders the final composite via a temporary Write node
- progress dialog with a Cancel button (`nuke.ProgressTask`)
- pre-flight validation: chain is checked before frame zero for missing prompts (FIBO Edit / Recipes / Generate / GenFill) and missing mask inputs (Erase / GenFill); one clear dialog up front instead of one popup per failed frame
- per-frame errors are collected and reported in a final summary; one bad frame does not abort the rest of the batch
- modal popups in error handlers are suppressed during Sequence Output; failures land in the final summary instead
- respects `Lock VGL` on Generate VGL nodes — when locked and a cached output exists, the SP step is skipped per frame and the cached VGL is reused
- final frame output uses the same colorspace handling as other Bria exports so PNG/JPG sequences look right under any project OCIO config

## Exporting gizmos (secondary artifacts)

You can export all 8 Bria nodes as gizmo files from Nuke in one shot.

This is a secondary compatibility/distribution path. The primary runtime workflow is the menu-created Group node set.

Important: run this in Nuke's Python environment (Script Editor or Nuke `-t`), not system `python3`.

In Nuke Script Editor:

```python
import export_gizmos
export_gizmos.export_all_gizmos()
```

Details:

- Script: `nuke/export_gizmos.py`
- Default output: `nuke/gizmos`
- In Non-Commercial builds, output suffix is `.gizmo.nknc`
- In Commercial builds, output suffix is `.gizmo`

Important NC caveat:

- `.gizmo.nknc` export support does **not** mean every node is fully usable in Nuke Non-Commercial
- Enhance uses a simplified NC-safe variant (`nc_keymix` mode) when running in Non-Commercial builds — full commercial graph features are not available in NC

This is the recommended path for generating distributable gizmos on Windows Commercial.
It is not the preferred authoring/runtime path for day-to-day node creation.

Tip:

- If you need a clean refresh after changing node creators, set `BRIA_EXPORT_GIZMOS_ON_STARTUP=1` for one launch, then unset it.

Optional terminal-mode example:

```bash
<Nuke executable> -t -c "import export_gizmos; export_gizmos.export_all_gizmos()"
```

## Python callbacks: current model vs embedding

Current model (used now):

- Gizmo knobs call external callback modules in `nuke/nodes/*.py`.
- Example knob script pattern: `import fibo_generate as _bria_fg; _bria_fg.on_fibo_generate()`
- This keeps Python logic versioned in normal `.py` files and avoids editing encrypted `.nknc` blobs.

What this means for distribution:

- Ship both the exported gizmos and the `nuke/nodes` Python modules.
- Ensure startup sets `NUKE_PATH` / `sys.path` so those modules can be imported.

Alternative (full embedding):

- You can embed callback code directly into gizmo knobs/hidden script knobs.
- It reduces external file dependencies, but is harder to maintain/debug and less diff-friendly.
- External Python modules are the default approach and what the release uses.

## Dashboard

Nuke dashboard includes:

- Open Login / Open API Keys
- API key entry (`nuke_api_key`) + Save All / Clear All
- Connection settings (`nuke_use_bearer_auth`, proxy options)
- Settings tab for output-path behavior:
	- `Temp Bria Save Folder` display
	- `Set Bria Project Folder` toggle
	- `Project Path` + browse + `SET`
- Check Config / Check Network

When enabled, dashboard stores:

- `nuke_output_dir`
- `use_bria_project_path`

and rehydrates session env at startup:

- `BRIA_PROJECT_PATH`
- `USE_BRIA_PROJECT_PATH`

Default image output behavior:

- Image-producing Bria Nuke nodes support `Use Global Project Path`.
- With node toggle ON, output resolution priority is:
	1. global project path (if dashboard global path is enabled)
	2. node `result_path` (if provided)
	3. temp fallback
- With node toggle OFF, output resolution remains `result_path` -> temp fallback.

When the default path is used, image results are now organized under per-node folders:

- `images/erase/`
- `images/genfill/`
- `images/expand/`
- `images/fibo_edit/`
- `images/fibo_generate/`
- `images/rmbg/`
- `images/upscale/`
- `images/enhance/`

The dedicated Generate VGL node remains separate and writes JSON output under `prompt/` by default.

Implementation note:

- This image-path behavior is centralized for Nuke in `nuke/nodes/common.py`.
- It applies across the Nuke nodes that call the shared `resolve_output_path()` helper.
- Houdini does not automatically inherit this foldering behavior, because its node output paths are currently resolved in separate Houdini node modules.

If `nuke_api_key` is empty but `houdini_api_key` exists in `~/.bria/bria.json`, the panel shows configured fallback status and masks the key field.

See: [../BRIA_DASHBOARD.md](../BRIA_DASHBOARD.md)

## Notes

- `BRIA_NUKE_ROOT` should point to the `nuke` folder.
- `NUKE_BRIA` is a repo-root alias for Nuke script paths (for example, `$NUKE_BRIA/examples/images/...`).
- If `BRIA_EXTENSION_ROOT` is missing, startup attempts to infer repo root from `BRIA_NUKE_ROOT`.
- Bria API key can be configured via dashboard (`~/.bria/bria.json`) or env vars.
- Nuke key lookup supports fallback to Houdini key when `nuke_api_key` is not present.
- If you need a custom trust store, set `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` before launch.

## Troubleshooting

- See: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

### Gizmo looks stale vs Create/* node

Symptoms:

- `Nuke -> Bria -> Create/...` node has latest UI/behavior.
- `Nodes -> Bria AI -> ...` gizmo still shows older UI/behavior.

Quick fix (recommended):

1. Run `Nuke -> Bria -> Tools/Refresh Gizmos`.
2. Create a new gizmo node (existing placed nodes keep their saved knob state).

Important for recent FIBO Edit / Generate updates:

- existing placed nodes may not pick up newer internal compare wiring or structured-prompt UI defaults
- if a node looks disconnected or lacks a new knob such as `Before Source`, recreate it after refresh

If still stale:

- Restart Nuke once after running a manual refresh.
- Ensure `BRIA_DISABLE_GIZMO_AUTOEXPORT` is not set to `1`.
- Optional forced refresh on startup: set `BRIA_EXPORT_GIZMOS_ON_STARTUP=1` for one launch.

Manual refresh alternative (Script Editor):

```python
import export_gizmos
export_gizmos.export_all_gizmos()
```
