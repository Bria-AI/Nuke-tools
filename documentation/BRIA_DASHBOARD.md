# Bria Dashboard (Cross-DCC)

The Bria Dashboard is intended to provide a consistent UX across DCCs (Houdini, Nuke, Toon Boom) for status checks and API-key setup.

## Quick usage flow

1. Open the dashboard panel in your DCC.
2. Open Bria Login / API Keys page.
3. Copy API key(s) from Bria Console.
4. Save key(s) to local config (where supported by current DCC panel).
5. Run status/config/network check from the panel.

This mirrors the same end-user goal across DCCs: configure once, validate quickly, then run nodes/tools.

## How to open the dashboard

- Nuke:
	- Use Nuke menu → Bria → Dashboard.

## Shared config location

All DCC integrations read from the same local config file:

- `~/.bria/bria.json`

Primary per-DCC API-key fields used by runtime resolution:

- Nuke → `nuke_api_key`

## Environment variable equivalents

- Nuke → `BRIA_API_KEY_NUKE`

Other shared env/config values:

- Endpoint override: `BRIA_API_ENDPOINT`
- Config path override: `BRIA_CONFIG_PATH`

## Current panel behavior by DCC

- Nuke:
	- Dashboard panel includes login links, key entry, save/clear, and status/config/network checks
	- Dashboard includes a Connection tab for Nuke runtime defaults (Bearer auth and proxy settings)
	- Dashboard includes a Settings tab for project output control (`nuke_output_dir`, `use_bria_project_path`)
	- Uses `nuke_api_key` in shared config/env resolution

## Save/Clear behavior

- Nuke:
	- Save All writes entered keys plus Nuke connection defaults and output settings to `~/.bria/bria.json`.
	- Clear All removes saved Nuke dashboard keys, Nuke connection defaults, and output settings from `~/.bria/bria.json`.

## Login links

- Login: https://platform.bria.ai/login
- API keys: https://platform.bria.ai/console/account/api-keys

## Notes

- Keys are local-only and are not embedded in scene/project files.
- Nuke currently has a convenience fallback to Houdini key when `nuke_api_key` is not set.
- Dashboard backend logic is centralized in `bria_core/dashboard.py`.
- UI layers remain DCC-specific (`nuke/panels/bria_dashboard.py`).
- Nuke connection keys stored in config are:
	- `nuke_use_bearer_auth`
	- `nuke_use_env_proxy`
	- `nuke_http_proxy`
	- `nuke_https_proxy`
	- `nuke_output_dir`
	- `use_bria_project_path`
- For implementation details, see:
	- [nuke/panels/bria_dashboard.py](../nuke/panels/bria_dashboard.py)
