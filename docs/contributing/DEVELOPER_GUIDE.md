# Developer Guide

Onboarding doc for engineers reviewing or contributing to the Bria Nuke integration.
Target audience: someone who already knows Python + Nuke and wants to get productive in ~30 minutes.

- [What this is](#what-this-is)
- [Entrypoint & boot order](#entrypoint--boot-order)
- [How a node executes](#how-a-node-executes)
- [Shared config](#shared-config)
- [Running locally](#running-locally)
- [Client files vs dev-only files](#client-files-vs-dev-only-files)
- [Conventional commits & Release Please](#conventional-commits--release-please)
- [Pre-commit hooks](#pre-commit-hooks)
- [Tests](#tests)
- [User-Agent contract](#user-agent-contract)
- [Post-refactor watch-outs](#post-refactor-watch-outs)

## What this is

Nuke integration for the Bria AI image API. Ships 11 production nodes:

| Node | Op |
|------|----|
| Bria RMBG | remove background |
| Bria Upscale | increase resolution |
| Bria Enhance | quality enhancement |
| Bria Erase | inpaint under a mask |
| Bria GenFill | prompt-driven inpaint |
| Bria Expand | outpaint / canvas extension |
| Bria FIBO Generate | text-to-image generation |
| Bria FIBO Edit | prompt-driven edit |
| Bria FIBO Edit Recipes | FIBO Edit with 99 curated preset prompts |
| Bria Generate VGL | Structured-prompt (VGL) generator |
| Bria Sequence Output | batch renders a chain of upstream Bria nodes across a frame range |

Split into two Python packages:

- `bria_core/` — DCC-agnostic core (HTTP client, auth, config, TLS, logging, shared Qt dashboard widget). **Shared 1:1 with the Houdini integration.** Same `~/.bria/bria.json` config file works for both DCCs.
- `nuke/` — Nuke-specific adapter, menu/panel registration, and per-node modules.

Full node reference: [`docs/bria_nuke/README.md`](../bria_nuke/README.md).
Directory-level map: [`REPO_MAP.md`](REPO_MAP.md).

## Entrypoint & boot order

There is no single traditional entrypoint — Nuke's plugin system does the loading.

The user adds one line to `~/.nuke/init.py`:

```python
nuke.pluginAddPath("/path/to/bria-nuke/nuke")
```

From there, boot order is:

1. Nuke finds `nuke/init.py` via the registered plugin path. This file is the orchestrator. See `nuke/init.py` function `_init()` (lines 238-255).
2. `_init()` calls `_startup_loader.resolve_repo_root()`, which walks up from its own file location to find `bria_core/` alongside `nuke/`.
3. `_startup_loader.ensure_paths()` adds `repo_root`, `nuke/`, `nuke/panels/`, `nuke/nodes/`, `nuke/gizmos/` to `sys.path`. This is what makes `from bria_core.xxx import yyy` and `from common import zzz` work.
4. `nuke/init.py` registers the Bria gizmo/node/panel folders via `nuke.pluginAddPath()` (lines 25-38).
5. `_register_bria_node_defaults()` installs `nuke.addOnCreate()` callbacks for each Bria node class (lines 93-102) and an `addOnScriptLoad()` callback to re-pad prompt field heights on .nk reload (line 164).
6. Nuke auto-loads `nuke/menu.py` via the same plugin path, which builds the "Bria" top-level menu plus the "Bria AI" entries under Nodes > Bria AI.
7. `run_bootstrap_init()` initialises `bria_core` logging and config-file resolution.

Key files for the boot path:
- `nuke/init.py`
- `nuke/_startup_loader.py`
- `nuke/menu.py`
- `nuke/bootstrap.py`

## How a node executes

1. User picks a node from Bria menu. `nuke/menu.py` invokes the per-node builder (`_create_<op>_node()`) which creates a Nuke `Group` node, adds all the knobs, and wires up a `knobChanged` handler that imports the matching module from `nuke/nodes/`.
2. User clicks the Run button. The button's PyScript knob calls `on_<op>(node)` in the matching `nuke/nodes/<op>.py` module.
3. The `on_<op>` handler reads knobs, exports the input image(s) to a temp PNG (`nuke/nodes/export_utils.py`), and calls the typed adapter function in `nuke/adapter.py`.
4. The adapter builds a payload dict and calls `bria_core.client.BriaClient.post_image_edit()`. The client handles HTTP, retries (with base64 + data-URI + multipart fallback for erase/genfill), and async polling.
5. On success, the handler downloads the result via `bria_core.utils.download_url()`, writes a `.json` metadata sidecar, pushes the result path into the node's `result_path` knob, and syncs the viewer via `sync_preview()` (which switches an internal Switch node between "before" and "after" Reads).
6. Result metadata is appended to the node's History tab via `history.append_generation_history()`. Presets can be saved/loaded via the Presets tab (`presets.on_save_generation_preset()` / `on_load_generation_preset()`).

Simplest end-to-end reference node: `nuke/nodes/rmbg.py` (~8 KB, single endpoint, no mask, no prompt).

## Shared config

Config file: `~/.bria/bria.json`. Shared cross-DCC with Houdini.

```json
{
  "nuke_api_key": "...",
  "houdini_api_key": "...",
  "use_bria_project_path": false,
  "nuke_output_dir": "~/Desktop/bria_nuke_output"
}
```

Resolution order (see `bria_core/config.py:resolve_api_key()`):
1. explicit `api_key=` argument to the adapter (if passed)
2. `{dcc}_api_key` in `~/.bria/bria.json`
3. Cross-DCC fallback: if Nuke, fall back to `houdini_api_key`, and vice versa. This is intentional so an artist with a single Bria key configured for one DCC can immediately use the other.
4. Environment variable `BRIA_API_KEY_{DCC}` (e.g. `BRIA_API_KEY_NUKE`)
5. Cross-DCC env fallback
6. Error

## Running locally

Without installing to `~/.nuke/`, use the dev launchers:

- `tools/dev/start_nuke.sh` (macOS/Linux)
- `tools/dev/start_nuke.bat` (Windows)

They set:

- `NUKE_PATH=<repo>/nuke` (prepended)
- `BRIA_EXTENSION_ROOT=<repo>`
- `BRIA_NUKE_ROOT=<repo>/nuke`
- `NUKE_BRIA=<repo>` (repo-root alias for `.nk` scene path portability)
- TLS cert bundle (macOS: auto-detects `/etc/ssl/cert.pem` / Homebrew CA; Linux: Debian/Ubuntu/RHEL/SUSE paths)
- `BRIA_API_KEY_NUKE` fallback from `BRIA_API_KEY_HOUDINI` if only the Houdini env var is set

Both scripts detect the platform via `uname` and launch Nuke accordingly. Edit the `NUKE_BIN` path near the bottom to match your local Nuke install.

## Client files vs dev-only files

See [`REPO_MAP.md`](REPO_MAP.md) for the full per-file breakdown. Summary:

**Ships to users:** `bria_core/`, `nuke/`, `tools/bria_setup.py`, `tools/advanced/user_init.py`, `examples/`, `docs/`, root files (`README.md`, `LICENSE`, `changelog.md`, `pyproject.toml`).

**Dev-only:** `tools/dev/start_nuke.sh`, `tools/dev/start_nuke.bat`, `nuke/export_gizmos.py` (gizmo regeneration — only runs when `BRIA_EXPORT_GIZMOS_ON_STARTUP=1`), `.github/`, `.pre-commit-config.yaml`, `docs/contributing/`, `.gitignore`.

## Conventional commits & Release Please

All commits on `main` must follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add sequence output node
fix: restore structured prompt sync on run
docs: document lock_vgl toggle
refactor: split common.py into history and presets
chore: bump changelog
ci: add pr-title workflow
```

The PR title check (`.github/workflows/pr-title.yml`) blocks non-conventional titles.

Release Please (`.github/workflows/release-please.yml`) runs on push to `main`. It opens a standing "Release Please" PR that aggregates commits since the last tag and bumps the version per:

- `feat:` → minor bump (0.1.0 → 0.2.0)
- `fix:` → patch bump (0.1.0 → 0.1.1)
- `feat!:` or `BREAKING CHANGE:` footer → major bump
- `chore:` / `docs:` / `refactor:` / `test:` / `ci:` → appear in changelog, no version bump

**Rules:**

- One commit per feature. Release Please segments on commit type, not PR, so bundling unrelated changes pollutes the generated changelog.
- Do **not** hand-edit the release-please-managed `CHANGELOG.md` after the v0.1.0 baseline.
- To override the auto-computed version (e.g. cut v0.2.0 directly from a `fix:`-only window), add a footer on any commit:
  ```
  Release-As: 0.2.0
  ```
- The workflow needs a `TEMP_RELEASE_TOKEN` secret (a PAT with `contents: write` + `pull-requests: write`) so release-please PRs can push to a protected `main`.

## Pre-commit hooks

```
pip install pre-commit
pre-commit install --install-hooks
```

Hooks (`/.pre-commit-config.yaml`):

- `prettier` on yaml/json5
- `commitizen` on commit-msg (enforces Conventional Commits for local commits too)
- `ruff format` + `ruff check --fix`

The ruff hooks shell out to `uv run --dev ruff`. If you don't use [uv](https://docs.astral.sh/uv/), swap `entry: uv run --dev ruff` for `entry: ruff` in `.pre-commit-config.yaml`.

Ruff config lives in `pyproject.toml`:

```toml
[tool.ruff]
line-length = 160
target-version = "py310"

[tool.ruff.lint]
select = ["C4", "E", "F", "I", "PERF", "UP"]
ignore = ["PERF203", "E402", "F821"]
```

## Tests

There is no automated test suite yet. Verification is manual in Nuke — see the regression checklist in `REPO_MAP.md` and the smoke-test walkthrough in [`docs/bria_nuke/README.md`](../bria_nuke/README.md). Adding pytest coverage for `bria_core/` is a reasonable post-release task since the core is DCC-agnostic and doesn't need Nuke to run.

## User-Agent contract

Every HTTP request to the Bria API sends a `User-Agent: BriaNuke/<version>` header. This is how Bria's backend distinguishes Nuke traffic from other integrations and is load-bearing for telemetry / rate-limit tuning.

Where it's emitted: `bria_core/auth.py` line 19:

```python
headers["User-Agent"] = f"Bria{dcc.capitalize()}/{__version__}"
```

`dcc="nuke"` is passed by `nuke/adapter.py` when it instantiates `BriaClient`. `__version__` is read from `bria_core/version.py`. Do not break this contract when refactoring — the Bria ops team watches this header.

Version sources (all must stay in sync):
- `pyproject.toml` → `[project] version = "0.1.0"`
- `bria_core/version.py` → `__version__ = "0.1.0"`
- User-Agent header at runtime → `BriaNuke/0.1.0`

## Post-refactor watch-outs

When refactoring `bria_core/` or `nuke/` layouts, these are the most common breakage surfaces (learned from the Houdini refactor):

1. **`from bria_core.*` stale imports.** If `bria_core/` moves under a package parent (e.g. `bria_nuke/bria_core/`), every `from bria_core.xxx import yyy` breaks. Affected files: `nuke/__init__.py`, `nuke/adapter.py`, `nuke/bootstrap.py`, `nuke/node_utils.py`, `nuke/panels/bria_dashboard.py`, and all 11 `nuke/nodes/*.py` modules. Update in one commit with the move.
2. **`_startup_loader.py`**: runs before `sys.path` is final. After any layout change, re-verify `resolve_nuke_root()` and `resolve_repo_root()` still locate the right folder.
3. **Panel plugin path**: `nuke/panels/bria_dashboard.py` imports `from bria_core.dashboard_qt import BriaDashboardWidget`. Moving the panel breaks this AND requires updating `_register_plugin_paths()` in `nuke/init.py` (lines 25-38).
4. **`.gizmo` nodeClass strings**: the 9 `.gizmo` files under `nuke/gizmos/` reference Python nodeClasses by string. If the class names move, gizmo nodes still create but their knobs are empty — a silent failure only visible by opening a scene with pre-placed Bria nodes and inspecting knobs.
5. **`addOnCreate` callbacks**: `nuke/init.py` lines 50-103 register callbacks keyed by nodeClass. Class names MUST stay identical — renaming will silently break already-placed nodes in saved `.nk` scenes.
6. **User-Agent**: verify `BriaNuke/{version}` still reaches HTTP headers after any `bria_core/` move. Quickest check: `BRIA_DEBUG=1` launch, run any node, grep the log for "User-Agent".
7. **Cross-DCC config fallback**: regression-test by placing only `houdini_api_key` in `~/.bria/bria.json` and confirming Nuke resolves it.
8. **`addOnScriptLoad` prompt-height fix**: `nuke/init.py` registers `_restore_prompt_heights()` to re-pad multiline prompt knobs after .nk reload. Confirm it still fires — easiest test is to create a FIBO Edit or FIBO Generate node, save the .nk, reopen, check that prompt textareas stay tall.

For a per-node smoke checklist, see `REPO_MAP.md` → "Per-node regression test".
