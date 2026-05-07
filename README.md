# Bria AI Tools for Nuke

AI-powered image editing tools for Nuke — enhance, upscale, remove backgrounds,
generative fill, erase, expand, and more.

## Quick Start

### 1. Download

Download the [latest release](https://github.com/Bria-AI/bria-nuke/releases) and unzip it anywhere on your computer.

### 2. Add one line to your Nuke startup

Open (or create) your Nuke startup file at `~/.nuke/init.py` and add this line:

```python
nuke.pluginAddPath("/path/to/bria-nuke/nuke")
```

Replace the path with wherever you unzipped the folder. Point it at the `nuke` subfolder inside.

**Where is `~/.nuke/`?**
- **macOS:** Finder → Go menu → Go to Folder → type `~/.nuke`
- **Windows:** File Explorer address bar → type `%USERPROFILE%\.nuke`
- **Linux:** `~/.nuke/`

If `init.py` doesn't exist yet, create a new text file named `init.py` with just that one line.
If it already exists, add the line at the end.

### 3. Restart Nuke

Look for **Bria** in the top menu bar. You should also see **Bria AI** under the Nodes menu.

### 4. Enter your API key

Go to **Bria → Dashboard** and paste your API key.

Get your key at [platform.bria.ai](https://platform.bria.ai/console/account/api-keys)

That's it — you're ready to use the tools.

## What's included

| Tool | Description |
|------|-------------|
| Enhance | Improve image quality at target resolution |
| Upscale | Increase image resolution |
| Remove Background | Clean background removal |
| Generative Fill | AI-powered fill with prompt control |
| Erase | Remove objects with inpainting |
| Expand | Extend image canvas with AI generation |
| FIBO Edit | Prompt-driven image editing |
| FIBO Edit Recipes | FIBO Edit with 100+ curated preset prompts across 11 categories — including Create Mask, Create Soft Mask, and Remove Greenscreen Spill compositing presets |
| FIBO Generate | Text-to-image generation with multi-variant batch (1–4 in parallel) |
| Generate VGL | Structured-prompt JSON generator (chains into FIBO nodes) |
| Sequence Output | Batch render a chain of upstream Bria nodes across a frame range |

All tools are accessible from the **Bria** menu or by pressing Tab and typing "bria".

Every Bria node also includes **View Generation Data** and **Copy Generation Data** buttons (Settings tab) for inspecting or copying the most recent run's prompt, seed, response, and full sidecar JSON.

## Shared configuration

Bria tools store configuration in `~/.bria/bria.json`. This file is shared across
all Bria DCC integrations (Nuke, Houdini, etc.). If you've already set up Bria tools
in Houdini, your API key will automatically be available in Nuke — and vice versa.

## Alternative setup (for studios and pipelines)

If your studio uses centralized launchers or environment management, you can set
`NUKE_PATH` to include the `nuke/` subfolder instead of editing `init.py`:

```bash
export NUKE_PATH="/path/to/bria-nuke/nuke:$NUKE_PATH"
```

Dev launcher scripts are also provided in `tools/dev/`.

## Documentation

- Detailed setup and node reference: [documentation/bria_nuke/README.md](documentation/bria_nuke/README.md)
- Configuration reference: [documentation/CONFIG.md](documentation/CONFIG.md)
- Repo structure: [STRUCTURE.md](STRUCTURE.md)
- Troubleshooting: [documentation/bria_nuke/TROUBLESHOOTING.md](documentation/bria_nuke/TROUBLESHOOTING.md)

## License

[MIT](LICENSE)
