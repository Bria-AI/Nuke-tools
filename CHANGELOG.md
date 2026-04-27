# Bria Nuke Integration - Changelog

## v0.1.0 (2026-04-25)

Initial release. 11 production Group nodes for Nuke 15+.

### Production Nodes

- Bria Erase v2 -- remove content under a mask (2-input: image + mask)
- Bria GenFill v2 -- generate new content under a mask with a prompt (2-input)
- Bria Expand v2 -- outpaint via aspect ratio, directional, or canvas size modes
- Bria FIBO Edit v2 -- prompt-based edit with basic or structured prompts
- Bria FIBO Edit Recipes -- FIBO Edit with 99 curated preset prompts across 11 categories (style, weather, seasons, time of day, camera, lighting, compositing, cleanup, AI corrections, object edits)
- Bria FIBO Generate -- text-to-image generation (basic or structured prompts)
- Bria RMBG v2 -- remove background from a single image
- Bria Upscale v2 -- increase resolution (2x/4x)
- Bria Enhance -- enhance image quality (full / nc_keymix variants), mask support
- Bria Generate VGL -- generate structured-prompt JSON (formerly "Structured Prompt"); chains into FIBO Edit / FIBO Generate / FIBO Edit Recipes
- Bria Sequence Output -- batch render a chain of upstream Bria nodes across a frame range with topo-sort, progress dialog, and per-frame error collection

### Features

- **Group-first Architecture** -- nodes are created as Nuke Group nodes programmatically, with optional gizmo export
- **Result History** -- browse and reload previous results on all nodes
- **Generation Presets** -- save and load preset configurations
- **Metadata Sidecars** -- JSON metadata saved alongside every API result (timing, request params, full API response)
- **Viewer Refresh** -- automatic Nuke viewer update after API results via `sync_preview()`
- **Bria Dashboard** -- Qt panel for API key setup, project path configuration (`~/.bria/bria.json`)
- **Dashboard Propagation** -- toggling global project path in dashboard updates all Bria nodes in the scene
- **Generate VGL Linking** -- chain Generate VGL nodes into FIBO Edit/Generate via input connections (passes structured prompt JSON)
- **Content Moderation** -- granular input/output toggles on supported nodes

### Cross-DCC Compatibility

- Shared config file: `~/.bria/bria.json` (compatible with Bria Houdini integration)
- Cross-DCC API key fallback: Nuke resolves `houdini_api_key` when `nuke_api_key` absent (and vice versa)
- Unified `bria_core` package with DCC-agnostic config, auth, HTTP client, and dashboard helpers
- Token alias system for backward compatibility with legacy key naming

### Platform Support

- macOS verified
- SSL: auto-detection of CA bundles via `certs.py` (macOS, Linux)
- Config: `~/.bria/bria.json` via `Path.expanduser()` (cross-platform)
- Nuke TCL expression support: `[getenv VAR]` syntax in path fields via `_expand_env_tokens()`
- Exponential backoff polling for async API results
- Network resilience: TimeoutError, socket.timeout, OSError handling

### API

- All endpoints use Bria API v2
- Multipart encoding with compliant CRLF framing
- Retry with data-URI fallback for GenFill/Erase
- Proxy support (`http_proxy`, `https_proxy`, `use_env_proxy`)
- Bearer token auth option
- User-Agent header: `BriaNuke/{version}`

## Pre-release polish round 2 (2026-04-21 – 2026-04-25)

Second round of pre-release work, focused on the new VGL-driven node chain (Generate VGL → FIBO Generate / FIBO Edit) and the new batch renderer.

### New Nodes

- **FIBO Edit Recipes** — FIBO Edit variant with 99 curated preset prompts across 11 categories. Object-edit and compositing presets support 5 fixed target slots each, with constraint-injection ("STRICT LOCKS") templates ported from the Houdini equivalent. Delegates execution to the existing FIBO Edit pipeline; only the preset assembly and dynamic UI are new.
- **Sequence Output** — batch frame-range renderer. Walks upstream from the node, identifies Bria nodes by knob presence, topologically sorts them with cycle detection, then per frame: cooks each Bria node in order and renders the final composite via a temporary Write node. Honors `Lock VGL` on Generate VGL nodes; pre-flight validation surfaces empty-prompt / missing-mask issues before frame zero. Per-frame errors collected; one bad frame doesn't abort the batch.

### Renames

- **Structured Prompt → Generate VGL** in user-facing labels (menu path `Bria → Create → Generate VGL`, node header, Run button, Lock VGL toggle). Internal Python module remains `nuke/nodes/structured_prompt.py` and knob names (`run_structured_prompt`, `structured_prompt_output`, etc.) are preserved for backward compatibility with already-saved `.nk` files. Sequence Output dispatch key uses `generate_vgl` to match Houdini.

### VGL / Structured Prompt Bug Fixes

- **TCL escape on write** — `Multiline_Eval_String_Knob.value()` evaluates TCL on read, so JSON content with `[` / `]` / `$` was getting truncated mid-stream. New `escape_for_eval_knob()` helper in `common.py` is applied wherever JSON content is written into one of these knobs (`structured_prompt.py:_set_output_text` and `_notify_downstream_nodes`; `fibo_edit.py` and `fibo_generate.py:_sync_structured_prompt_link`). `.value()` un-escapes via TCL eval, so reads return the original JSON unchanged.
- **Auto-inject `objects: []`** — the VLM that produces VGL JSON occasionally omits the required `objects` array, causing a 422 "Field required" semantic error from the image-generation API. `_ensure_vgl_objects()` in `nuke/adapter.py` now parses the structured prompt and adds an empty `objects: []` if missing, before the call. Matches Houdini's existing fix.
- **Force resync on Run** — FIBO Edit / FIBO Generate handlers now call `_sync_structured_prompt_link()` at the start of `on_<op>()` so the latest upstream VGL is always pulled from the connected Generate VGL node, even if knobChanged hasn't fired since the last SP regen.
- **VGL persistence across .nk save/reload** — Nuke's save/load round-trip does not preserve the escape-on-write encoding for `Multiline_Eval_String_Knob`. After reopening a saved scene, the SP `structured_prompt_output` knob would show the corrupted truncated form. Fixed via a new `_restore_structured_prompt_outputs()` callback registered in `nuke/init.py` via `addOnScriptLoad`. On script load, walks all Generate VGL nodes, reads each one's `result_path` JSON file from disk (the SP run already writes this), and re-populates the knob with the proper escaping. A second pass then refreshes downstream FIBO nodes' `structured_prompt` knob from the freshly-restored upstream SP.

### Sequence Output reliability

- **Batch-mode synchronous main-thread execution** — during a Sequence Output render, `on_main_thread()` in `common.py` now calls `fn()` directly instead of via `nuke.executeInMainThread()`, which is asynchronous. Previously, the SP node's success-UI update (which writes the new VGL into `structured_prompt_output`) was queued and the next chain node would cook before it ran, reading stale data. Sequence Output already runs on the main thread, so direct execution is safe and necessary for correct chain cooking.
- **Pre-flight chain validation** — `_validate_chain()` checks every Bria node in the chain for missing prompts (FIBO Edit / Recipes / Generate / GenFill) and missing mask inputs (Erase / GenFill) before frame zero. One clear dialog up front instead of one popup per failed frame.
- **Suppressed modal popups in batch mode** — `set_batch_mode()` flag toggled by Sequence Output; all 9 Bria node error handlers check `is_batch_mode()` and re-raise instead of showing `nuke.message()` so per-frame errors are collected by the batch loop and reported in the final summary.

### Code quality

- **`common.py` split** — extracted `nuke/nodes/history.py` (779 LOC, generation history UI + actions) and `nuke/nodes/presets.py` (906 LOC, preset save/load/catalog) from the previously-monolithic `common.py` (2936 LOC → 1368 LOC). Backward compat preserved via `from history import *` / `from presets import *` re-export trailer in `common.py`. All 9 Bria node files' existing `from common import X` statements continue to work unchanged.
- **PySide6/PySide2 import order** — confirmed PySide6 is tried first across all Qt-touching files (Nuke 17 ships PySide6).

### Cross-platform

- **Linux support** in `bria_core/certs.py` — added `LINUX_CA_BUNDLE_CANDIDATES` covering Debian/Ubuntu (`/etc/ssl/certs/ca-certificates.crt`), RHEL/CentOS/Fedora (`/etc/pki/tls/certs/ca-bundle.crt`), openSUSE (`/etc/ssl/ca-bundle.pem`), and Alpine (`/etc/ssl/cert.pem`).
- **Linux launcher** in `tools/dev/start_nuke.sh` — `uname`-based platform dispatch; macOS uses `open -a`, Linux searches `/usr/local/`, `/opt/`, and `$PATH` for the Nuke binary.
- **Linux audit confirmed** — file browser uses `xdg-open`, clipboard supports `wl-copy` (Wayland) and `xclip`/`xsel` (X11), all paths use `Path.home()`/`expanduser()`, temp dirs use `tempfile.gettempdir()`.

### Repo & release scaffolding

- `.github/` — added `PULL_REQUEST_TEMPLATE.md`, `ISSUE_TEMPLATE/bug_report.yml`, `ISSUE_TEMPLATE/feature_request.yml`, `workflows/pr-title.yml` (conventional-commits enforcement), `workflows/release-please.yml` (auto-versioning + auto-changelog).
- `.pre-commit-config.yaml` — ruff format + ruff check + prettier + commitizen hooks.
- `pyproject.toml` URLs filled (`Repository`, `Documentation`, `Issues`, `Changelog`).
- `DEVELOPER_GUIDE.md` and `REPO_MAP.md` added under `docs/contributing/` for engineer onboarding.
- Internal delivery / feedback / roadmap docs stripped from `docs/`. Public docs reduced from 45 markdown files to 4: `CONFIG.md`, `BRIA_DASHBOARD.md`, `bria_nuke/README.md`, `bria_nuke/TROUBLESHOOTING.md`.
- Repo License: MIT (matches Houdini integration).

## Pre-release polish (2026-04-02 – 2026-04-21)

Changes made during the open source release preparation.

### Bug Fixes (verified in Nuke)

- **Prompt field height collapse on .nk reload** — Multiline prompt fields (FIBO Edit, FIBO Generate, Structured Prompt) collapsed from their intended height to a single line after saving and reloading a .nk file. Nuke does not persist `Multiline_Eval_String_Knob` display height. Fixed with PySide6 + QTimer.singleShot deferred resize in `common.py`, triggered on `showPanel`. Trailing-newline padding is restored on script load via `nuke.addOnScriptLoad` in `init.py`.
- **PySide2/PySide6 import order** — Nuke 17 ships PySide6 (Qt 6), but three files tried to import PySide2 first, which would fail silently or fall through incorrectly. Fixed import order to try PySide6 first in `dashboard_qt.py`, `bria_dashboard.py`, and `common.py`.
- **importlib.reload() on every knobChanged** — FIBO Edit, FIBO Generate, and Enhance had `importlib.reload()` calls in their `knobChanged` handlers, causing full module reloads on every knob interaction (typing, clicking, dropdown changes). Removed from `menu.py`.
- **Structured Prompt seed inconsistency** — Prompt field seeds in `menu.py` used 2 newlines but height hints expected 3. Aligned to 3 newlines so prompt fields display at their intended height on node creation.

### Cross-Platform Fixes

- **Windows TLS certificates** — Added certifi-based certificate resolution for Windows in `certs.py`. When `SSL_CERT_FILE` is not set on Windows, the tool now checks for a certifi bundle before falling back to the Windows system certificate store. Prevents `CERTIFICATE_VERIFY_FAILED` errors on Windows.
- **Hardcoded macOS path in dashboard docs** — Replaced a hardcoded absolute home path in `bria_panel.py` with dynamic `Path("~/.bria/bria.json").expanduser()` for portability.

### Audits Completed

- **Code quality audit** — Reviewed showPanel handler coverage, PySide import order, importlib.reload usage, and height hint consistency across all node types. All issues found were fixed (see bug fixes above).
- **Cross-platform audit** — Reviewed Windows and Linux compatibility across the codebase. Two issues found and fixed (see cross-platform fixes above). The rest of the codebase uses `os.pathsep`, `Path`, and platform-agnostic APIs throughout.
- **API endpoint verification** — All 6 API endpoints (enhance, increase_resolution, remove_background, gen_fill, erase, expand) verified current against docs.bria.ai v2 documentation. No changes needed — all defaults already point to v2.

### Installation Simplification

- **README rewritten** — New community-standard Quick Start: single `nuke.pluginAddPath()` one-liner in `~/.nuke/init.py`, matching the convention used by NukeSurvivalToolkit, Cryptomatte, and other popular Nuke plugins.
- **Simplified install path** — `user_init.py` template moved from `nuke/startup/` to `tools/advanced/`. The one-liner `pluginAddPath` approach replaces it as the primary install method. The existing `init.py` + `_startup_loader.py` auto-discovers `bria_core/` from its own file location.
- **Setup helper** — `tools/bria_setup.py` stub replaced with a helper that prints the exact `pluginAddPath` line with the correct path.
- **Vanilla install tested** — Confirmed working from a clean state with no environment variables set. Single `pluginAddPath` line loads the Bria menu, Dashboard panel, and all node types.

### Houdini Compatibility (confirmed)

- Shared config file `~/.bria/bria.json` works with both Nuke and Houdini toolsets simultaneously.
- Cross-DCC API key fallback verified — `houdini_api_key` is used when `nuke_api_key` is absent, and vice versa.
- No changes to `bria_core/` were needed — the shared module already handles both DCCs.
