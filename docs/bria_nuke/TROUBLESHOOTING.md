# Troubleshooting – Bria Nuke

## `Load JSON/VGL` button is disabled

This is expected.

Current FIBO structured-prompt behavior:

- `Load JSON/VGL` is disabled until `Structured Prompt (JSON)` is enabled
- once structured mode is enabled, the button becomes active and the plain `prompt` field is hidden/disabled

Applies to:

- FIBO Edit
- FIBO Generate

## Structured Edit returns `Provide only 'prompt' or 'structured_prompt', not both`

This means both plain prompt and structured prompt were sent in the same Edit request.

Current intended behavior:

- use plain prompt only, or
- use structured prompt only

Current node behavior now prevents this on new nodes by hiding/ignoring plain `prompt` whenever structured mode is enabled.

If you see this on an older node instance:

- recreate the node, or
- clear the plain prompt manually and re-run

## Structured Edit returns `background_setting -> Field required`

Bria structured Edit validation currently expects `background_setting` to be present in the JSON body.

If your structured prompt is intentionally minimal, add a compatibility field such as:

- `"background_setting": "Match the existing background and environment of the source image."`

This keeps the edit localized while satisfying current endpoint validation.

## Dashboard shows empty key but status is OK

Nuke can authenticate via fallback when:

- `nuke_api_key` is missing, and
- `houdini_api_key` exists in `~/.bria/bria.json`

Current behavior:

- runtime auth uses fallback automatically
- dashboard shows configured fallback state

If you want a dedicated Nuke key, save `nuke_api_key` from the Nuke Dashboard.

## RMBG returns image but background looks unchanged

Check the `Preserve Alpha` toggle on the RMBG node.

- For typical opaque inputs, leave `Preserve Alpha` **off**.
- If `Preserve Alpha` is on, original alpha can be retained, which may make the result look unchanged.

For newly created RMBG nodes, default is now `Preserve Alpha = off`.

Display interpretation note:

- RMBG result is RGBA and may be interpreted as straight (unpremultiplied) color.
- In that case, RGB can appear to retain background until premult is applied for display/compositing.
- Current RMBG node shell now premults the result branch for `After` preview.

This matches observed behavior in Houdini workflows where readers commonly include premult handling.

RMBG shell alignment note:

- New RMBG nodes include internal `save_image` export wiring (same pattern as other Bria nodes).
- Existing older RMBG nodes still run via direct input-export fallback, but recreating the node is recommended.

## macOS TLS error: CERTIFICATE_VERIFY_FAILED

If you see errors like:

- `[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate`

Current behavior (both launcher and runtime bootstrap):

- `tools/dev/start_nuke.sh` auto-detects and exports a CA bundle.
- `bria_core` runtime TLS resolver (called from `nuke/bootstrap.py`) applies the same fallback paths.

Resolver sets:

- `SSL_CERT_FILE`
- `REQUESTS_CA_BUNDLE`
- `BRIA_CA_BUNDLE`

If your machine uses a custom trust store, set `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` before launch.

## Bria folder not visible in Nuke file browser sidebar

`$NUKE_BRIA` is an environment variable for path expansion; by itself it does not create a sidebar shortcut.

Current startup behavior (`nuke/init.py`):

- registers favorite dir `Bria` pointing to `$NUKE_BRIA`

If it is missing:

- restart Nuke (favorites are added at startup)
- verify `NUKE_PATH` includes `<repo>/nuke`
- verify `NUKE_BRIA` resolves to your repo root

## Portable Reader paths for examples

Use paths like:

- `$NUKE_BRIA/examples/images/<file>.png`

This keeps `.nk` scripts portable across machines and user home directories.

For generated-history and metadata entries shipped with the example scenes, current Nuke helpers also tokenize paths under `$BRIA_EXAMPLES` where possible.

If you still see older absolute machine-local paths in a saved node history:

- those entries were saved before the portability pass,
- rerun the node or clear/rebuild that history to replace them with current tokenized paths.

## Batch gizmo export does not run

Use Nuke Script Editor (not system Python):

```python
import export_gizmos
export_gizmos.export_all_gizmos()
```

If import fails, verify Bria startup loaded first (`nuke/startup/user_init.py` or launcher setup).

If you run `python3 export_gizmos.py`, it will not export gizmos because that is not Nuke's Python runtime.
Use Script Editor or Nuke terminal mode (`-t`) instead.

## FIBO Edit gizmo looks disconnected after refresh or reopening the script

Recent FIBO Edit updates added:

- structured prompt load/gating behavior
- `Before Source` compare mode (`Original` vs `Previous Edit`)

Current checked-in gizmo wiring has been normalized to a stable input-first stack pattern, but older placed nodes may still contain stale internal graphs.

Recommended fix:

1. Run `Nuke -> Bria -> Tools/Refresh Gizmos`
2. Delete the old placed FIBO Edit node
3. Create a fresh FIBO Edit node

Do not rely on older placed nodes to inherit internal graph fixes automatically.

## Enhance Use Mask does not seem to affect output

Expected behavior:

- Enable `Use Mask` to region-limit the enhanced output.
- Internal shell compositing uses `Keymix` between original input and enhanced result.

Mask-format handling:

- If the mask input lacks alpha, the node normalizes it internally by copying mask red into alpha before `Keymix`.
- This means non-alpha matte inputs are supported, as long as matte information exists in color channels.

If output still appears global:

- verify `Use Mask` is enabled
- verify the mask branch is connected to input B
- confirm mask values are non-zero in expected regions

## Shipped example presets do not appear in the preset list

For FIBO Edit and FIBO Generate, shipped presets are no longer tied to the dashboard project path.

- Enable `Load from Bria Examples` on the node.
- This adds presets from `$BRIA_EXAMPLES/presets` into the preset list.
- Saving still writes to the normal preset destination, not back into the examples folder.

If nothing appears:

- verify `BRIA_EXAMPLES` resolves to the repo's `examples` folder,
- refresh the preset UI or recreate the node if it is an older saved instance.

## Enhance `sync` / async behavior

On current new Enhance nodes:

- the `sync` knob is hidden,
- runtime forces `sync=true`,
- async polling is not yet implemented for in-node Enhance pullback.

If an older node instance still shows `sync` and it is off:

- current runtime will force it back on before the request,
- recreate the node if you want the latest UI state.

## Enhance fails in Nuke Non-Commercial

If Enhance fails to create or reports Python/internal node-object limit errors in Nuke Non-Commercial:

- this is an expected limitation of the current Enhance shell in NC,
- the issue is the size/complexity of the internal graph, not only whether the node was created from a Group or a `.gizmo.nknc`,
- `Refresh Gizmos` will not solve this by itself.

Current support position:

- Commercial Nuke is the supported target for the current Enhance implementation.
- Nuke Non-Commercial would need a simplified NC-specific Enhance shell to be considered supported.

## GUI startup script fails to load Bria extension

If `~/.nuke/init.py` uses:

- `from nuke import bootstrap`
- `bootstrap.run()`

that will fail in this repo layout.

Use the README auto-load snippet instead (Option C), which executes `nuke/init.py` via `runpy.run_path(...)`.
Preferred source: copy `nuke/startup/user_init.py` into `~/.nuke/init.py`.

If Nuke launches but no Bria menu appears:

- ensure your copied file is the latest `nuke/startup/user_init.py`
- verify `REPO_ROOT` points to your local repo
- restart Nuke after updating `~/.nuke/init.py`
- check Script Editor output for `[Bria] Failed to load...` messages
