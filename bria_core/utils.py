"""Core helpers (DCC-agnostic)."""

from __future__ import annotations

import base64
import os
import socket
import tempfile
from pathlib import Path
from typing import Dict, Literal, Optional, Tuple
from urllib import error as url_error
from urllib import request as url_request


def file_to_base64(path: str | Path) -> str:
    p = Path(path)
    with p.open("rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def compute_image_size_location(
    width: int,
    height: int,
    anchor: Literal["top-left", "center"] = "top-left",
    canvas_width: int | None = None,
    canvas_height: int | None = None,
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Compute original_image_size and original_image_location for expand/outpaint.

    Args:
        width: Source image width in pixels.
        height: Source image height in pixels.
        anchor: Placement mode ("top-left" or "center").
        canvas_width: Optional target canvas width (required for center).
        canvas_height: Optional target canvas height (required for center).
    """

    size = {"width": int(width), "height": int(height)}

    if anchor == "center":
        if canvas_width is None or canvas_height is None:
            raise ValueError("canvas_width and canvas_height are required for center anchor")
        x = int((canvas_width - width) // 2)
        y = int((canvas_height - height) // 2)
    else:
        x = 0
        y = 0

    return size, {"x": x, "y": y}


def download_url(
    url: str,
    timeout_s: int = 300,
    proxies: Dict[str, str] | None = None,
) -> Tuple[bytes, str | None]:
    opener = _build_opener(proxies)
    req = url_request.Request(url, method="GET")
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower() or None
            return resp.read(), content_type
    except url_error.HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        raise RuntimeError(f"Download failed: HTTP {exc.code} ({body[:200]!r})") from exc
    except url_error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
            raise RuntimeError(f"Download timed out after {timeout_s}s: {url}") from exc
        raise RuntimeError(f"Download failed: network error ({reason})") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"Download timed out after {timeout_s}s: {url}") from exc
    except OSError as exc:
        if "timed out" in str(exc).lower():
            raise RuntimeError(f"Download timed out after {timeout_s}s: {url}") from exc
        raise


def _build_opener(proxies: Dict[str, str] | None = None) -> url_request.OpenerDirector:
    handlers = []
    if proxies is not None:
        handlers.append(url_request.ProxyHandler(proxies))
    return url_request.build_opener(*handlers)


def resolve_proxies(
    http_proxy: str | None = None,
    https_proxy: str | None = None,
    use_env_proxy: bool | None = True,
) -> Dict[str, str] | None:
    proxies: Dict[str, str] = {}

    if bool(use_env_proxy):
        try:
            env_proxies = url_request.getproxies() or {}
        except Exception:
            env_proxies = {}

        for key in ("http", "https"):
            value = env_proxies.get(key) or env_proxies.get(key.upper())
            if isinstance(value, str) and value.strip():
                proxies[key] = value.strip()

    if isinstance(http_proxy, str) and http_proxy.strip():
        proxies["http"] = http_proxy.strip()
    if isinstance(https_proxy, str) and https_proxy.strip():
        proxies["https"] = https_proxy.strip()

    if proxies:
        return proxies

    if use_env_proxy is False:
        return {}

    return None


def extract_image_url(data: dict) -> str | None:
    urls = extract_image_urls(data)
    return urls[0] if urls else None


def extract_image_urls(data: dict) -> list[str]:
    """Return every image URL the API response contains, in order.

    Handles a few shapes Bria's endpoints have used:
      - {"result": {"image_url": "..."}}                       (single, FIBO Generate sync)
      - {"result": {"images": ["url1", "url2"]}}              (multi as list of strings)
      - {"result": {"images": [{"image_url": "..."}, ...]}}   (multi as list of dicts)
      - {"result": [{"image_url": "..."}, ...]}               (result as list of dicts)
      - {"image_url": "..."}                                    (top-level)
      - {"images": [...]}                                       (top-level array)
    """
    if not isinstance(data, dict):
        return []

    seen: set[str] = set()
    out: list[str] = []

    def _add(value: object) -> None:
        if isinstance(value, str):
            v = value.strip()
            if v and v not in seen:
                seen.add(v)
                out.append(v)

    def _walk_image_dict(obj: object) -> None:
        if isinstance(obj, dict):
            for key in ("image_url", "url", "imageUrl"):
                _add(obj.get(key))

    def _walk_image_list(items: object) -> None:
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str):
                    _add(item)
                elif isinstance(item, dict):
                    _walk_image_dict(item)

    result = data.get("result")
    if isinstance(result, dict):
        _walk_image_dict(result)
        _walk_image_list(result.get("images"))
        _walk_image_list(result.get("results"))
    elif isinstance(result, list):
        _walk_image_list(result)

    # Top-level fallbacks
    _walk_image_dict(data)
    _walk_image_list(data.get("images"))
    _walk_image_list(data.get("results"))

    return out


def png_ihdr_info(path: str) -> dict | None:
    try:
        with open(path, "rb") as f:
            sig = f.read(8)
            if sig != b"\x89PNG\r\n\x1a\n":
                return None
            length = int.from_bytes(f.read(4), "big")
            ctype = f.read(4)
            if ctype != b"IHDR":
                return None
            ihdr = f.read(length)
            if len(ihdr) < 13:
                return None
            width = int.from_bytes(ihdr[0:4], "big")
            height = int.from_bytes(ihdr[4:8], "big")
            return {"width": width, "height": height, "bit_depth": int(ihdr[8]), "color_type": int(ihdr[9])}
    except Exception:
        return None


def validate_bria_image_file(path: str, label: str) -> None:
    if not path or not os.path.exists(path):
        raise RuntimeError(f"Missing {label} file: {path}")

    ext = os.path.splitext(path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise RuntimeError(
            f"Unsupported {label} file extension '{ext}'. Bria supports JPEG/JPG/PNG/WEBP."
        )

    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    if isinstance(size, int) and size > 12 * 1024 * 1024:
        raise RuntimeError(f"{label} is too large for Bria (>12MB): {path} ({size} bytes)")

    if ext == ".png":
        info = png_ihdr_info(path)
        if info:
            ct = info.get("color_type")
            bd = info.get("bit_depth")
            if ct in (0, 4):
                raise RuntimeError(
                    f"{label} PNG is grayscale (color_type={ct}). Bria requires RGB/RGBA/CMYK. "
                    "Convert to RGB/RGBA before export."
                )
            if ct == 3:
                raise RuntimeError(
                    f"{label} PNG is indexed/paletted (color_type=3). Convert to RGB/RGBA before export."
                )
            if isinstance(bd, int) and bd not in (8,):
                raise RuntimeError(
                    f"{label} PNG bit depth is {bd}. Bria commonly expects 8-bit RGB/RGBA. Export as 8-bit if possible."
                )


def _non_empty(value: object) -> str | None:
    if value is None:
        return None
    txt = str(value).strip()
    return txt or None


def resolve_temp_dir(dcc_module: object | None = None, fallback: str = ".") -> str:
    """Resolve a writable temp directory across DCC and OS environments.

    Resolution order:
    1) DCC env vars via `dcc_module.getenv`: TEMP, TMP, TMPDIR
    2) DCC expanded vars via `dcc_module.expandString`: $TEMP, $TMP, $TMPDIR
    3) Process env vars: TEMP, TMP, TMPDIR
    4) Python tempfile.gettempdir()
    5) fallback (default: current directory)
    """

    candidates: list[str] = []

    if dcc_module is not None and hasattr(dcc_module, "getenv"):
        for key in ("TEMP", "TMP", "TMPDIR"):
            try:
                value = _non_empty(getattr(dcc_module, "getenv")(key))
            except Exception:
                value = None
            if value:
                candidates.append(value)

    if dcc_module is not None and hasattr(dcc_module, "expandString"):
        for var in ("$TEMP", "$TMP", "$TMPDIR"):
            try:
                value = _non_empty(getattr(dcc_module, "expandString")(var))
            except Exception:
                value = None
            if value:
                candidates.append(value)

    for key in ("TEMP", "TMP", "TMPDIR"):
        value = _non_empty(os.getenv(key))
        if value:
            candidates.append(value)

    py_temp = _non_empty(tempfile.gettempdir())
    if py_temp:
        candidates.append(py_temp)

    fallback_path = _non_empty(fallback)
    if fallback_path:
        candidates.append(fallback_path)

    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            return path
        except Exception:
            continue

    return "."


_ASPECT_RATIO_ALLOWED = frozenset(["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9"])
_ASPECT_RATIO_LIST = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9"]


def normalize_aspect_ratio(value: Optional[str]) -> Optional[str]:
    """Validate and normalise an aspect-ratio string.

    Accepts:
    - Canonical form: "16:9", "4:3", etc.
    - Slash form: "16/9" is rewritten to "16:9".
    - Integer index (as string): "0"–"8" maps to the canonical ordered list.

    Returns the canonical string, or ``None`` if *value* is empty.
    Raises ``RuntimeError`` for unrecognised values.
    """
    if not value:
        return None
    txt = value.strip().lower().replace(" ", "")
    if not txt:
        return None
    if txt.isdigit():
        idx = int(txt)
        if 0 <= idx < len(_ASPECT_RATIO_LIST):
            return _ASPECT_RATIO_LIST[idx]
    if "/" in txt:
        parts = txt.split("/", 1)
        txt = f"{parts[0]}:{parts[1]}"
    if txt not in _ASPECT_RATIO_ALLOWED:
        raise RuntimeError(
            "aspect_ratio must be one of: 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9."
        )
    return txt
