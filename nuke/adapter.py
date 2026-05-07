"""Nuke adapter (DCC-specific).

This module intentionally stays thin and bridges Nuke workflows to `bria_core`.
All functions are file/path based so they are reusable outside Nuke node callbacks.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from bria_core import BriaClient, load_config, resolve_api_endpoint, resolve_api_key, resolve_rmbg_endpoint
from bria_core.utils import file_to_base64
import json

try:
    from .node_utils import debug_logger
except Exception:  # Loaded as top-level module (no package context)
    from node_utils import debug_logger


_debug_log = debug_logger("Bria Nuke")


def _is_true_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _fibo_payload_debug_enabled() -> bool:
    return _is_true_env(os.getenv("BRIA_DEBUG_FIBO_PAYLOAD")) or _is_true_env(os.getenv("BRIA_DEBUG"))


def _mime_from_path(path: str) -> str:
    ext = os.path.splitext((path or "").lower())[1]
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    return "image/png"


def _edit_base(api_endpoint: str) -> str:
    base = (api_endpoint or "").strip().rstrip("/")

    # Legacy host compatibility: api.bria.ai is often stale/unresolvable.
    if "://api.bria.ai" in base:
        base = base.replace("://api.bria.ai", "://engine.prod.bria-api.com")

    # Normalize to API host root before forcing v2 image/edit.
    # This avoids broken combinations like: .../v1/v2/image/edit
    root = re.split(r"/v[12](?:/.*)?$", base, maxsplit=1)[0].rstrip("/")
    if not root:
        root = base

    return root + "/v2/image/edit"


def _erase_target(api_endpoint: str) -> tuple[str, str]:
    base = (api_endpoint or "").strip().rstrip("/")

    if base.endswith("/v2/image/edit/erase"):
        return base[: -len("/erase")], "erase"

    if base.endswith("/v2/image/edit") or base.endswith("/v2"):
        return _edit_base(base), "erase"

    if base.endswith("/v1/eraser"):
        return base[: -len("/eraser")], "eraser"

    if base.endswith("/v1"):
        return base, "eraser"

    if base.endswith("/eraser"):
        return base[: -len("/eraser")], "eraser"

    return _edit_base(base), "erase"


def _rmbg_target(api_endpoint: str) -> tuple[str, str]:
    base = (api_endpoint or "").strip().rstrip("/")

    if base.endswith("/v2/image/edit/remove_background"):
        return base[: -len("/remove_background")], "remove_background"

    if base.endswith("/v2/image/edit"):
        return base, "remove_background"

    if base.endswith("/v2"):
        return base + "/image/edit", "remove_background"

    if base.endswith("/v1/background/remove"):
        return base[: -len("/background/remove")], "background/remove"

    if base.endswith("/v1"):
        return base, "background/remove"

    if base.endswith("/background/remove"):
        return base[: -len("/background/remove")], "background/remove"

    if base.endswith("/remove_background"):
        return base[: -len("/remove_background")], "remove_background"

    return base + "/v2/image/edit", "remove_background"


def _upscale_target(api_endpoint: str, mode: str) -> tuple[str, str]:
    base = (api_endpoint or "").strip().rstrip("/")
    mode_key = (mode or "").strip().lower()
    if mode_key in ("increase", "increase_resolution", "upscale"):
        endpoint_name = "increase_resolution"
    else:
        endpoint_name = "enhance"

    if base.endswith(f"/v2/image/edit/{endpoint_name}"):
        return base[: -len(f"/{endpoint_name}")], endpoint_name

    if base.endswith("/v2/image/edit"):
        return base, endpoint_name

    if base.endswith("/v2"):
        return base + "/image/edit", endpoint_name

    if base.endswith(f"/v1/{endpoint_name}"):
        return base[: -len(f"/{endpoint_name}")], endpoint_name

    if base.endswith("/v1"):
        return base, endpoint_name

    if base.endswith(f"/{endpoint_name}"):
        return base[: -len(f"/{endpoint_name}")], endpoint_name

    return base + "/v2/image/edit", endpoint_name


def _generate_base(api_endpoint: str) -> str:
    base = (api_endpoint or "").strip().rstrip("/")
    if base.endswith("/v2"):
        return base
    if base.endswith("/v2/image/edit"):
        return base[: -len("/v2/image/edit")] + "/v2"
    return base + "/v2"


def _expand_target(api_endpoint: str) -> tuple[str, str]:
    base = (api_endpoint or "").strip().rstrip("/")

    if base.endswith("/v2/image/edit/expand"):
        return base[: -len("/expand")], "expand"

    if base.endswith("/v2/image/edit"):
        return base, "expand"

    if base.endswith("/v2"):
        return base + "/image/edit", "expand"

    if base.endswith("/v1/image_expansion"):
        return base[: -len("/image_expansion")], "image_expansion"

    if base.endswith("/v1"):
        return base, "image_expansion"

    if base.endswith("/image_expansion"):
        return base[: -len("/image_expansion")], "image_expansion"

    return base + "/v2/image/edit", "expand"


def _genfill_target(api_endpoint: str) -> tuple[str, str]:
    base = (api_endpoint or "").strip().rstrip("/")

    # Legacy host compatibility: api.bria.ai is often stale/unresolvable.
    if "://api.bria.ai" in base:
        base = base.replace("://api.bria.ai", "://engine.prod.bria-api.com")

    if base.endswith("/v2/image/edit/gen_fill"):
        return base[: -len("/gen_fill")], "gen_fill"

    if base.endswith("/v2/image/edit"):
        return base, "gen_fill"

    if base.endswith("/v2"):
        return base + "/image/edit", "gen_fill"

    if base.endswith("/v1/gen_fill"):
        return base[: -len("/gen_fill")], "gen_fill"

    if base.endswith("/gen_fill"):
        return base[: -len("/gen_fill")], "gen_fill"

    if base.endswith("/v1"):
        return base, "gen_fill"

    return base + "/v2/image/edit", "gen_fill"


def _sanitize_text_field(value: str) -> str:
    # Bria v1/gen_fill can reject multiline/control-character text payloads.
    txt = (value or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    txt = txt.replace("\x00", " ")
    return " ".join(txt.split()).strip()


def erase_from_files(
    image_path: str,
    mask_path: str,
    preserve_alpha: bool = True,
    api_key: Optional[str] = None,
    api_endpoint: Optional[str] = None,
    timeout_s: Optional[int] = None,
    use_bearer: bool = False,
    allow_multipart_fallback: bool = False,
    proxies: Optional[Dict[str, str]] = None,
    session: Optional[object] = None,
) -> Dict:
    """Call Bria Erase from file paths (DCC-agnostic usage)."""
    cfg = load_config()
    endpoint = api_endpoint or resolve_api_endpoint(cfg)
    key = api_key or resolve_api_key("nuke", cfg)

    edit_base, erase_endpoint = _erase_target(endpoint)

    client = BriaClient(
        api_endpoint=edit_base,
        api_key=key,
        timeout_s=timeout_s or (cfg.default_timeout or 1200),
        use_bearer=use_bearer,
        session=session,
    )

    payload = {
        "image": file_to_base64(image_path),
        "mask": file_to_base64(mask_path),
        "sync": True,
        "preserve_alpha": bool(preserve_alpha),
        "version": 2,
    }

    return client.post_image_edit(
        endpoint=erase_endpoint,
        payload_json=payload,
        proxies=proxies,
        allow_multipart=allow_multipart_fallback,
        image_path=image_path,
        mask_path=mask_path,
    )


def genfill_from_files(
    image_path: str,
    mask_path: str,
    prompt: str,
    negative_prompt: Optional[str] = None,
    preserve_alpha: bool = True,
    api_key: Optional[str] = None,
    api_endpoint: Optional[str] = None,
    timeout_s: Optional[int] = None,
    use_bearer: bool = False,
    allow_multipart_fallback: bool = False,
    proxies: Optional[Dict[str, str]] = None,
    session: Optional[object] = None,
) -> Dict:
    """Call Bria GenFill from file paths."""
    cfg = load_config()
    endpoint = api_endpoint or resolve_api_endpoint(cfg)
    key = api_key or resolve_api_key("nuke", cfg)

    genfill_base, genfill_endpoint = _genfill_target(endpoint)

    client = BriaClient(
        api_endpoint=genfill_base,
        api_key=key,
        timeout_s=timeout_s or (cfg.default_timeout or 1200),
        use_bearer=use_bearer,
        session=session,
    )

    clean_prompt = _sanitize_text_field(prompt)
    clean_negative_prompt = _sanitize_text_field(negative_prompt) if negative_prompt else None

    payload = {
        # NOTE: v1/gen_fill accepts plain base64 strings (or public URLs).
        # Sending data-URIs here can trigger 460 "Failed to download image"
        # on some Bria deployments.
        "image": file_to_base64(image_path),
        "mask": file_to_base64(mask_path),
        "prompt": clean_prompt,
        "sync": True,
        "preserve_alpha": bool(preserve_alpha),
        "version": 2,
    }
    if clean_negative_prompt:
        payload["negative_prompt"] = clean_negative_prompt

    return client.post_image_edit(
        endpoint=genfill_endpoint,
        payload_json=payload,
        proxies=proxies,
        allow_multipart=allow_multipart_fallback,
        image_path=image_path,
        mask_path=mask_path,
    )


def fibo_edit_from_files(
    image_path: str,
    prompt: Optional[str] = None,
    structured_prompt: Optional[str] = None,
    guidance_scale: Optional[int] = None,
    negative_prompt: Optional[str] = None,
    seed: Optional[int] = None,
    steps_num: Optional[int] = None,
    mask_path: Optional[str] = None,
    mask_type: Optional[str] = None,
    use_cache: Optional[bool] = None,
    # Back-compat:
    instruction: Optional[str] = None,
    structured_instruction: Optional[str] = None,
    api_key: Optional[str] = None,
    api_endpoint: Optional[str] = None,
    timeout_s: Optional[int] = None,
    use_bearer: bool = False,
    proxies: Optional[Dict[str, str]] = None,
    session: Optional[object] = None,
) -> Dict:
    """Call the general Bria FIBO Edit from an image file path."""
    if not prompt and not structured_prompt and not instruction and not structured_instruction:
        raise ValueError("Provide either prompt or structured_prompt")

    # Prefer prompt names.
    if not prompt and instruction:
        prompt = instruction
    if not structured_prompt and structured_instruction:
        structured_prompt = structured_instruction

    cfg = load_config()
    endpoint = api_endpoint or resolve_api_endpoint(cfg)
    key = api_key or resolve_api_key("nuke", cfg)

    edit_base = _edit_base(endpoint)

    client = BriaClient(
        api_endpoint=edit_base,
        api_key=key,
        timeout_s=timeout_s or (cfg.default_timeout or 1200),
        use_bearer=use_bearer,
        session=session,
    )

    image_b64 = file_to_base64(image_path)

    payload: Dict = {
        # /v2/image/edit requires `images`.
        # Keep plain base64 (no data URI) for consistency.
        "images": [image_b64],
        # Compatibility for integrations/tools still consuming singular `image`.
        "image": image_b64,
        "sync": True,
        "version": 2,
    }
    if prompt:
        payload["instruction"] = str(prompt)
    if structured_prompt:
        payload["structured_instruction"] = str(structured_prompt)
    if guidance_scale is not None:
        payload["guidance_scale"] = int(guidance_scale)
    if negative_prompt:
        payload["negative_prompt"] = str(negative_prompt)
    if seed is not None:
        payload["seed"] = int(seed)
    if steps_num is not None:
        payload["steps_num"] = int(steps_num)
    if mask_path:
        payload["mask"] = file_to_base64(mask_path)
    if mask_type:
        payload["mask_type"] = str(mask_type)
    if use_cache is not None:
        payload["use_cache"] = bool(use_cache)

    # Endpoint is the base itself (/v2/image/edit). Join with empty suffix.
    return client.post_image_edit(
        endpoint="",
        payload_json=payload,
        proxies=proxies,
        allow_multipart=False,
    )


def remove_background_from_files(
    image_path: str,
    preserve_alpha: bool = True,
    api_key: Optional[str] = None,
    api_endpoint: Optional[str] = None,
    timeout_s: Optional[int] = None,
    use_bearer: bool = False,
    proxies: Optional[Dict[str, str]] = None,
    session: Optional[object] = None,
) -> Dict:
    """Call Bria RMBG (remove_background) from file paths."""
    cfg = load_config()
    endpoint = api_endpoint or resolve_rmbg_endpoint(cfg)
    key = api_key or resolve_api_key("nuke", cfg)

    rmbg_base, rmbg_endpoint = _rmbg_target(endpoint)

    client = BriaClient(
        api_endpoint=rmbg_base,
        api_key=key,
        timeout_s=timeout_s or (cfg.default_timeout or 1200),
        use_bearer=use_bearer,
        session=session,
    )

    payload = {
        "image": file_to_base64(image_path),
        "sync": True,
        "preserve_alpha": bool(preserve_alpha),
    }

    return client.post_image_edit(
        endpoint=rmbg_endpoint,
        payload_json=payload,
        proxies=proxies,
        allow_multipart=False,
        image_path=image_path,
        mask_path=None,
    )


def resolve_rmbg_request_url(api_endpoint: Optional[str] = None) -> str:
    """Resolve the effective RMBG request URL for diagnostics/logging."""
    cfg = load_config()
    endpoint = api_endpoint or resolve_rmbg_endpoint(cfg)
    rmbg_base, rmbg_endpoint = _rmbg_target(endpoint)
    return f"{rmbg_base.rstrip('/')}/{str(rmbg_endpoint or '').lstrip('/')}"


def upscale_from_files(
    image_path: str,
    mode: str = "increase_resolution",
    desired_increase: Optional[int] = None,
    resolution: Optional[str] = None,
    seed: Optional[int] = None,
    sync: Optional[bool] = None,
    preserve_alpha: Optional[bool] = None,
    content_moderation: Optional[bool] = None,
    use_cache: Optional[bool] = None,
    output_color_space: Optional[str] = None,
    api_key: Optional[str] = None,
    api_endpoint: Optional[str] = None,
    timeout_s: Optional[int] = None,
    use_bearer: bool = False,
    proxies: Optional[Dict[str, str]] = None,
    session: Optional[object] = None,
) -> Dict:
    """Call Bria Upscale (increase_resolution or enhance) from file paths."""
    cfg = load_config()
    endpoint = api_endpoint or resolve_api_endpoint(cfg)
    key = api_key or resolve_api_key("nuke", cfg)

    base, endpoint_name = _upscale_target(endpoint, mode)

    client = BriaClient(
        api_endpoint=base,
        api_key=key,
        timeout_s=timeout_s or (cfg.default_timeout or 1200),
        use_bearer=use_bearer,
        session=session,
    )

    # As of 2026-05, /v2/image/edit/enhance returns 422 for sync=true ("sync request is not
    # supported with this endpoint"). BriaClient._poll_status handles async responses, so
    # default Enhance to sync=False. Other modes (increase_resolution) retain the prior default.
    default_sync = False if endpoint_name == "enhance" else True
    payload: Dict[str, object] = {
        "image": file_to_base64(image_path),
        "sync": default_sync if sync is None else bool(sync),
    }

    if seed is not None:
        if int(seed) > 0:
            payload["seed"] = int(seed)
    if preserve_alpha is not None:
        payload["preserve_alpha"] = bool(preserve_alpha)
    if content_moderation is not None:
        payload["content_moderation"] = bool(content_moderation)
    if use_cache is not None:
        payload["use_cache"] = bool(use_cache)
    if output_color_space:
        payload["output_color_space"] = str(output_color_space)

    if endpoint_name == "increase_resolution":
        if desired_increase is not None:
            payload["desired_increase"] = int(desired_increase)
    else:
        if resolution:
            payload["resolution"] = str(resolution)

    return client.post_image_edit(
        endpoint=endpoint_name,
        payload_json=payload,
        proxies=proxies,
        allow_multipart=False,
        image_path=image_path,
        mask_path=None,
    )


def expand_from_files(
    image_path: str,
    prompt: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    seed: Optional[int] = None,
    canvas_size: Optional[dict] = None,
    original_image_size: Optional[dict] = None,
    original_image_location: Optional[dict] = None,
    api_key: Optional[str] = None,
    api_endpoint: Optional[str] = None,
    timeout_s: Optional[int] = None,
    use_bearer: bool = False,
    proxies: Optional[Dict[str, str]] = None,
    session: Optional[object] = None,
) -> Dict:
    """Call Bria Expand (outpainting) from file paths."""
    cfg = load_config()
    endpoint = api_endpoint or resolve_api_endpoint(cfg)
    key = api_key or resolve_api_key("nuke", cfg)

    base, endpoint_name = _expand_target(endpoint)

    client = BriaClient(
        api_endpoint=base,
        api_key=key,
        timeout_s=timeout_s or (cfg.default_timeout or 1200),
        use_bearer=use_bearer,
        session=session,
    )

    payload: Dict[str, object] = {
        "image": file_to_base64(image_path),
        "sync": True,
    }

    def _normalize_pair(value: object, keys: tuple[str, str]) -> object | None:
        if value is None:
            return None
        if isinstance(value, dict):
            a = value.get(keys[0])
            b = value.get(keys[1])
            if a is None or b is None:
                return None
            try:
                return [int(a), int(b)]
            except Exception:
                return None
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                return [int(value[0]), int(value[1])]
            except Exception:
                return None
        return None

    if prompt:
        payload["prompt"] = str(prompt)
    if aspect_ratio:
        payload["aspect_ratio"] = str(aspect_ratio)
    if seed is not None:
        payload["seed"] = int(seed)
    normalized_canvas = _normalize_pair(canvas_size, ("width", "height"))
    normalized_size = _normalize_pair(original_image_size, ("width", "height"))
    normalized_loc = _normalize_pair(original_image_location, ("x", "y"))
    if normalized_canvas is not None:
        payload["canvas_size"] = normalized_canvas
    if normalized_size is not None:
        payload["original_image_size"] = normalized_size
    if normalized_loc is not None:
        payload["original_image_location"] = normalized_loc

    return client.post_image_edit(
        endpoint=endpoint_name,
        payload_json=payload,
        proxies=proxies,
        allow_multipart=False,
        image_path=image_path,
        mask_path=None,
    )


def _ensure_vgl_objects(payload: Dict[str, object]) -> None:
    """Ensure the structured-prompt VGL contains the required 'objects' field.

    The Bria image-generation API requires an `objects` array in the VGL JSON.
    The VLM that produces VGLs sometimes omits it, which causes a 422
    "Field required" semantic error. We default it to an empty list when
    missing so that the API call succeeds. Houdini does the same in its
    adapter (`_ensure_vgl_objects`).
    """
    sp = payload.get("structured_prompt")
    if not isinstance(sp, str):
        return
    try:
        vgl = json.loads(sp)
    except (json.JSONDecodeError, TypeError):
        return
    if isinstance(vgl, dict) and "objects" not in vgl:
        vgl["objects"] = []
        payload["structured_prompt"] = json.dumps(vgl)


def fibo_generate_from_payload(
    payload: Dict[str, object],
    pipeline: str = "standard",
    use_structured_prompt: bool = False,
    api_key: Optional[str] = None,
    api_endpoint: Optional[str] = None,
    timeout_s: Optional[int] = None,
    use_bearer: bool = False,
    proxies: Optional[Dict[str, str]] = None,
    session: Optional[object] = None,
) -> Dict:
    """Call Bria FIBO generation endpoints with prepared payload."""
    _ensure_vgl_objects(payload)
    cfg = load_config()
    endpoint = api_endpoint or resolve_api_endpoint(cfg)
    key = api_key or resolve_api_key("nuke", cfg)

    base = _generate_base(endpoint)

    pipe = (pipeline or "standard").strip().lower()
    if pipe not in ("standard", "lite", "tailored"):
        pipe = "standard"

    if pipe == "tailored":
        endpoint_name = "image/generate/tailored"
    else:
        # Structured prompt data is sent as part of the normal image-generation
        # payload. Using structured_prompt/generate returns a prompt-generation
        # result object rather than an image payload.
        endpoint_name = "image/generate"
        if pipe == "lite":
            endpoint_name += "/lite"

    client = BriaClient(
        api_endpoint=base,
        api_key=key,
        timeout_s=timeout_s or (cfg.default_timeout or 1200),
        use_bearer=use_bearer,
        session=session,
    )

    clean_payload: Dict[str, object] = dict(payload or {})
    if "num_results" not in clean_payload:
        clean_payload["num_results"] = 1

    if _fibo_payload_debug_enabled():
        try:
            masked_key = None
            if key:
                k = str(key)
                masked_key = (k[:4] + "..." + k[-4:]) if len(k) > 8 else "****"
            payload_text = json.dumps(clean_payload, default=str)
        except Exception:
            payload_text = str(clean_payload)
        try:
            _debug_log(f"[FIBO-DUMP] base={base} endpoint={endpoint_name} api_key={masked_key} session={'yes' if session else 'no'} proxies={proxies}")
            _debug_log(f"[FIBO-PAYLOAD] {payload_text}")
        except Exception:
            print("BRIA_DEBUG: fibo payload dump", base, endpoint_name, masked_key, payload_text)

    return client.post_image_edit(
        endpoint=endpoint_name,
        payload_json=clean_payload,
        proxies=proxies,
        allow_multipart=False,
        image_path=None,
        mask_path=None,
    )


def structured_prompt_generate(
    prompt: Optional[str] = None,
    structured_prompt: Optional[str] = None,
    pipeline: str = "standard",
    image_path: Optional[str] = None,
    api_key: Optional[str] = None,
    api_endpoint: Optional[str] = None,
    timeout_s: Optional[int] = None,
    use_bearer: bool = False,
    proxies: Optional[Dict[str, str]] = None,
    session: Optional[object] = None,
) -> Dict[str, Any]:
    """Generate a structured prompt via Bria v2 endpoints (standard/lite)."""
    txt = str(prompt or "").strip()
    structured_txt = str(structured_prompt or "").strip()
    has_image = bool(image_path)
    if not txt and not has_image and not structured_txt:
        raise ValueError("Provide valid inputs according to endpoint rules.")

    cfg = load_config()
    endpoint = api_endpoint or resolve_api_endpoint(cfg)
    key = api_key or resolve_api_key("nuke", cfg)

    base = _generate_base(endpoint)
    pipe = (pipeline or "standard").strip().lower()
    endpoint_name = "structured_prompt/generate/lite" if pipe == "lite" else "structured_prompt/generate"

    client = BriaClient(
        api_endpoint=base,
        api_key=key,
        timeout_s=timeout_s or (cfg.default_timeout or 1200),
        use_bearer=use_bearer,
        session=session,
    )

    payload: Dict[str, Any] = {}
    if txt:
        payload["prompt"] = txt
    if structured_txt:
        payload["structured_prompt"] = structured_txt
    if image_path:
        payload["images"] = [file_to_base64(image_path)]

    return client.post_image_edit(
        endpoint=endpoint_name,
        payload_json=payload,
        proxies=proxies,
        allow_multipart=False,
        image_path=None,
        mask_path=None,
    )


# Backward-compat stub alias.
def erase_stub(node=None) -> Dict:
    """Backward-compatible stub API.

    Kept only to avoid breaking existing call sites while migrating to
    file-based adapter calls.
    """
    _debug_log("erase_stub is deprecated; use erase_from_files")
    return {
        "status": "not_implemented",
        "api_endpoint": None,
        "hint": "Use erase_from_files(image_path, mask_path, ...) instead.",
    }
