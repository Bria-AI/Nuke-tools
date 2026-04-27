# Repository Structure Guide

This file is a representative map of key folders/files, not a byte-for-byte exhaustive tree.

## Current Organization

```
repo-root/
├── README.md
├── STRUCTURE.md
├── LICENSE
├── changelog.md
├── pyproject.toml
├── requirements.txt
├── .gitignore
│
├── bria_core/                              ← DCC-agnostic core
│   ├── __init__.py
│   ├── auth.py
│   ├── cache.py
│   ├── certs.py
│   ├── client.py
│   ├── config.py
│   ├── dashboard.py
│   ├── dashboard_qt.py
│   ├── dcc.py
│   ├── errors.py
│   ├── logging.py
│   ├── status.py
│   ├── utils.py
│   └── version.py
│
├── nuke/                                   ← Nuke integration
│   ├── __init__.py
│   ├── _startup_loader.py
│   ├── adapter.py
│   ├── bootstrap.py
│   ├── export_gizmos.py
│   ├── init.py
│   ├── menu.py
│   ├── node_utils.py
│   ├── project_path_defaults.py
│   ├── panels/
│   │   └── bria_dashboard.py
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── common.py                       ← shared Nuke node helpers
│   │   ├── export_utils.py
│   │   ├── erase.py
│   │   ├── genfill.py
│   │   ├── expand.py
│   │   ├── fibo_edit.py
│   │   ├── fibo_edit_recipes.py            ← 99 curated prompt recipes
│   │   ├── fibo_generate.py
│   │   ├── structured_prompt.py            ← Generate VGL node
│   │   ├── sequence_output.py              ← batch frame-range renderer
│   │   ├── rmbg.py
│   │   ├── upscale.py
│   │   └── enhance.py
│   └── gizmos/
│       ├── bria_erase.gizmo
│       ├── bria_genfill.gizmo
│       ├── bria_expand.gizmo
│       ├── bria_fibo_edit.gizmo
│       ├── bria_fibo_generate.gizmo
│       ├── bria_structured_prompt.gizmo
│       ├── bria_rmbg.gizmo
│       ├── bria_upscale.gizmo
│       └── bria_enhance.gizmo
│
├── tools/
│   ├── bria_setup.py                       ← prints install instructions
│   ├── advanced/
│   │   └── user_init.py                    ← studio/env-based setup template
│   └── dev/
│       ├── start_nuke.bat
│       └── start_nuke.sh
│
├── examples/
│   ├── presets/                            ← FIBO Edit/Generate preset JSONs
│   ├── prompt/                             ← structured prompt JSONs
│   └── scenes/                             ← example .nk scenes
│
└── documentation/
    ├── CONFIG.md                           ← config file reference
    ├── BRIA_DASHBOARD.md                   ← Dashboard panel reference
    └── bria_nuke/
        ├── README.md                       ← full setup + node reference
        └── TROUBLESHOOTING.md
```

## Where to Start

- Installation: top-level [README.md](README.md)
- Full Nuke setup and node reference: [documentation/bria_nuke/README.md](documentation/bria_nuke/README.md)
- Config file reference: [documentation/CONFIG.md](documentation/CONFIG.md)
- Dashboard panel: [documentation/BRIA_DASHBOARD.md](documentation/BRIA_DASHBOARD.md)

## Notes

- The integration targets `https://engine.prod.bria-api.com` (host root); operation paths under `/v2/image/edit/...` are appended by the adapter.
- Runtime pattern is Group-first node creation. Exported `.gizmo` files are kept as secondary compatibility/distribution artifacts.
- `bria_core/` is shared with the companion Houdini integration; the same `~/.bria/bria.json` config file works for both DCCs.
- Nuke ships 11 production nodes: Erase, GenFill, Expand, FIBO Edit, FIBO Edit Recipes, FIBO Generate, Generate VGL (formerly Structured Prompt), RMBG, Upscale, Enhance, and Sequence Output.
