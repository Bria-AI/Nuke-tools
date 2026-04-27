"""Print Bria Nuke installation instructions.

Run with system Python (no Nuke required):
    python tools/bria_setup.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    nuke_dir = repo_root / "nuke"

    # Detect ~/.nuke location
    if sys.platform == "win32":
        dot_nuke = Path(os.environ.get("USERPROFILE", "~")) / ".nuke"
        dot_nuke_display = r"%USERPROFILE%\.nuke"
    else:
        dot_nuke = Path.home() / ".nuke"
        dot_nuke_display = "~/.nuke"

    print()
    print("=" * 60)
    print("  Bria AI Tools for Nuke — Installation")
    print("=" * 60)
    print()
    print("Add this line to your Nuke startup file")
    print(f"({dot_nuke_display}/init.py):")
    print()
    print(f'  nuke.pluginAddPath("{nuke_dir}")')
    print()

    init_py = dot_nuke / "init.py"
    if init_py.exists():
        print(f"  Your init.py is at: {init_py}")
        print("  Add the line above to the end of that file.")
    else:
        print(f"  {dot_nuke_display}/init.py does not exist yet.")
        print(f"  Create it at: {dot_nuke / 'init.py'}")
        print("  with just that one line.")

    print()
    print("Then restart Nuke and go to Bria > Dashboard to")
    print("enter your API key.")
    print()
    print("Get your key at:")
    print("  https://platform.bria.ai/console/account/api-keys")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
