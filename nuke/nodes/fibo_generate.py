"""Nuke Bria FIBO Generate node logic (thin DCC adapter layer)."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Optional

import nuke  # type: ignore

from adapter import fibo_generate_from_payload
from bria_core.config import load_config, resolve_api_endpoint
from bria_core.errors import BriaConfigError, BriaRequestError
from bria_core.utils import download_url, extract_image_url, extract_image_urls, normalize_aspect_ratio as _normalize_aspect_ratio, png_ihdr_info, resolve_proxies
from common import (
    is_batch_mode as _is_batch_mode,
    append_generation_history as _append_generation_history,
    create_progress_task as _create_progress_task,
    ensure_parent_dir as _ensure_parent_dir,
    ensure_generation_history_ui as _ensure_generation_history_ui,
    escape_for_eval_knob as _escape_for_eval_knob,
    on_load_structured_prompt as _common_on_load_structured_prompt,
    on_main_thread as _on_main_thread,
    ensure_generation_preset_ui as _ensure_generation_preset_ui,
    refresh_generation_preset_ui as _refresh_generation_preset_ui,
    refresh_generation_history_ui as _refresh_generation_history_ui,
    update_generation_history_selection_ui as _update_generation_history_selection_ui,
    resolve_output_path as _resolve_output_path,
    set_result_path as _set_result_path,
    set_generation_metadata as _set_generation_metadata,
    ensure_generation_metadata_ui as _ensure_generation_metadata_ui,
    set_history_debug as _set_history_debug,
    set_status as _set_status,
    sync_preview as _sync_preview_common,
    update_progress as _update_progress,
    get_internal_group_read_file as _get_internal_read_file,
    set_internal_group_read_file as _set_internal_read_file,
    sync_prompt_mode_ui as _sync_prompt_mode_ui,
)
from node_utils import (
    debug_logger,
    opt_parm_bool as _opt_parm_bool,
    opt_parm_float as _opt_parm_float,
    opt_parm_int as _opt_parm_int,
    opt_parm_menu_str as _opt_parm_menu_str,
    opt_parm_str as _opt_parm_str,
)

_debug_log = debug_logger("Bria Nuke FIBO")


def _sync_structured_prompt_link(node: Any) -> None:
    """Called on inputChange. If a Structured Prompt node is connected to input 0,
    copies its output into the structured_prompt field and switches to structured mode.
    On disconnect, restores the stashed manual prompt."""
    if node is None:
        return

    upstream = None
    try:
        upstream = node.input(0)
    except Exception:
        upstream = None

    sp_out_knob = None
    if upstream is not None:
        try:
            sp_out_knob = upstream.knob("structured_prompt_output")
        except Exception:
            sp_out_knob = None

    if sp_out_knob is not None:
        # --- CONNECT: upstream is a Structured Prompt node ---
        # Stash the current manual prompt so we can restore it on disconnect
        try:
            stash = node.knob("prompt_manual_stash")
            prompt_knob = node.knob("prompt")
            if stash is not None and prompt_knob is not None:
                stash.setValue(str(prompt_knob.value() or ""))
        except Exception:
            pass
        # Copy the structured prompt text into the structured_prompt field.
        # Re-escape TCL specials so JSON survives the eval-knob round-trip.
        try:
            sp_text = str(sp_out_knob.value() or "")
            sp_knob = node.knob("structured_prompt")
            if sp_knob is not None:
                sp_knob.setValue(_escape_for_eval_knob(sp_text))
        except Exception:
            pass
        # Activate structured prompt mode and lock the manual controls
        try:
            use_sp = node.knob("use_structured_prompt")
            if use_sp is not None:
                use_sp.setValue(True)
                use_sp.setEnabled(False)
        except Exception:
            pass
        try:
            prompt_knob = node.knob("prompt")
            if prompt_knob is not None:
                prompt_knob.setEnabled(False)
        except Exception:
            pass
        try:
            status = node.knob("prompt_link_status")
            if status is not None:
                status.setValue(f"linked \u2192 {upstream.name()}")
        except Exception:
            pass
    else:
        # --- DISCONNECT: restore manual mode ---
        try:
            stash = node.knob("prompt_manual_stash")
            prompt_knob = node.knob("prompt")
            if stash is not None and prompt_knob is not None:
                stash_val = str(stash.value() or "")
                # Only restore from stash if it has real content — avoids
                # wiping user prompts when a non-SP input is disconnected
                # and the stash was never populated.
                if stash_val.strip():
                    prompt_knob.setValue(stash_val)
                prompt_knob.setEnabled(True)
        except Exception:
            pass
        try:
            use_sp = node.knob("use_structured_prompt")
            if use_sp is not None:
                use_sp.setValue(False)
                use_sp.setEnabled(True)
        except Exception:
            pass
        try:
            status = node.knob("prompt_link_status")
            if status is not None:
                status.setValue("manual")
        except Exception:
            pass

    _sync_prompt_mode_ui(node)





def on_load_structured_prompt(node: Any = None) -> None:
    _common_on_load_structured_prompt(node, "Load FIBO Generate JSON / VGL")


def _friendly_bria_request_error(node: Any, exc: Exception) -> str:
    raw = str(exc or "")

    if "Bria API Error: 422" in raw and "greater than or equal to 20" in raw and "steps_num" in raw:
        steps_num = _opt_parm_int(node, "steps_num")
        request_id = None
        try:
            match = re.search(r'"request_id":"([^"]+)"', raw)
            if match:
                request_id = match.group(1)
        except Exception:
            request_id = None

        msg = (
            "Bria rejected this FIBO Generate request because `Steps` must be 20 or higher. "
            f"Current value: {int(steps_num) if steps_num is not None else 'unset'}. "
            "This limit is coming from the Bria API."
        )
        if request_id:
            msg += f"\n\nRequest ID: {request_id}"
        return msg

    return f"Bria Nuke FIBO Error: {raw}"


def _parse_structured_prompt(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return json.dumps(parsed, ensure_ascii=False)
    except Exception as exc:
        raise RuntimeError(f"structured_prompt must be valid JSON: {exc}")




def _extension_from_content_type(content_type: str | None) -> str | None:
    ct = (content_type or "").strip().lower()
    if ct == "image/png":
        return ".png"
    if ct in ("image/jpeg", "image/jpg"):
        return ".jpg"
    if ct == "image/webp":
        return ".webp"
    return None


def _resolve_result_save_path(out_path: str, content_type: str | None) -> str:
    ext = _extension_from_content_type(content_type)
    if not ext:
        return out_path
    base, _ = os.path.splitext(out_path)
    return base + ext


def _timestamp_result_path(path_value: str, run_id: str) -> str:
    if not path_value:
        return path_value
    root, ext = os.path.splitext(path_value)
    stem = os.path.basename(root)
    if run_id and run_id in stem:
        return path_value
    return f"{root}_{run_id}{ext}"


def _set_knob_value_safe(node: Any, name: str, value: object) -> None:
    try:
        knob = node.knob(name)
        if knob is not None:
            knob.setValue(value)
    except Exception:
        pass


def _sync_group_preview(node: Any) -> None:
    if node is None:
        return
    output_view = _opt_parm_menu_str(node, "output_view") or "After"
    current_path = _opt_parm_str(node, "result_path") or ""
    _sync_preview_common(
        node=node,
        result_path=current_path,
        output_view=output_view,
        after_node_name="loader_result",
        switch_node_names=("switch_before_after",),
    )


def _sync_group_preview_explicit(node: Any, result_path: str, output_view: str) -> None:
    if node is None:
        return
    _sync_preview_common(
        node=node,
        result_path=result_path,
        output_view=output_view,
        after_node_name="loader_result",
        switch_node_names=("switch_before_after",),
    )


def sync_preview(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        return
    _sync_group_preview(node)


def _variant_path_for(result_path: str, variant_idx: int) -> Optional[str]:
    """Compute the path to variant N given the canonical result_path.

    After a multi-variant run, result_path holds the v01 file (e.g. `foo_v01.png`).
    We strip the trailing `_vNN` suffix, then re-append `_v{idx:02d}`.
    For idx=1, also accept the legacy single-variant filename (no suffix).
    """
    if not result_path:
        return None
    root, ext = os.path.splitext(result_path)
    m = re.match(r"^(.*)_v\d{2}$", root)
    base = m.group(1) if m else root
    if variant_idx <= 1:
        candidate_v01 = f"{base}_v01{ext}"
        if os.path.isfile(candidate_v01):
            return candidate_v01
        return f"{base}{ext}"
    return f"{base}_v{variant_idx:02d}{ext}"


def _apply_multiple_images_visibility(node: Any) -> None:
    """Show/hide the entire multi-image control block (num_images + slider + button)
    based on the use_multiple_images toggle. Visibility is tied to the toggle alone
    so the user sees the controls exist even before running."""
    use_multi = bool(_opt_parm_bool(node, "use_multiple_images"))
    for name in ("num_images", "result_variant", "use_this_image"):
        try:
            k = node.knob(name)
            if k is not None:
                k.setVisible(use_multi)
        except Exception:
            pass


def _read_variant_idx(node: Any) -> int:
    """Read the Image Index slider value as an int (rounded), clamped to [1, count].

    Nuke's Double_Knob slider does not constrain typed values to setRange — a user
    can type 7 even when the slider visually goes 1..3. We enforce the bound here
    by reading the hidden result_variant_count knob (set after each successful run).
    """
    try:
        knob = node.knob("result_variant")
        if knob is None:
            return 1
        raw = knob.value()
        if raw is None:
            return 1
        idx = int(round(float(raw)))
    except (TypeError, ValueError):
        idx = 1
    try:
        count_knob = node.knob("result_variant_count")
        max_count = int(count_knob.value()) if count_knob is not None else 1
    except (TypeError, ValueError):
        max_count = 1
    if max_count < 1:
        max_count = 1
    return max(1, min(idx, max_count))


def _apply_result_variant(node: Any) -> None:
    """When the Image Index slider moves, swap loader_result to the selected variant."""
    variant_idx = _read_variant_idx(node)
    result_path = _opt_parm_str(node, "result_path") or ""
    target = _variant_path_for(result_path, variant_idx)
    if target and os.path.isfile(target):
        try:
            _set_internal_read_file(node, "loader_result", target)
            _set_knob_value_safe(node, "output_view", "After")
        except Exception:
            pass


def on_use_this_image() -> None:
    """Promote the currently-displayed variant to be the canonical result_path."""
    try:
        node = nuke.thisNode()
    except Exception:
        return
    if node is None:
        return
    variant_idx = _read_variant_idx(node)
    result_path = _opt_parm_str(node, "result_path") or ""
    target = _variant_path_for(result_path, variant_idx)
    if not target or not os.path.isfile(target):
        nuke.message(
            f"Variant {variant_idx} not found on disk.\n\n"
            f"Looked for: {target or '<no path>'}"
        )
        return
    _set_result_path(node, target)
    _set_internal_read_file(node, "loader_result", target)
    _set_knob_value_safe(node, "output_view", "After")
    nuke.tprint(f"[Bria] Locked in variant {variant_idx}: {target}")


def on_knob_changed() -> None:
    try:
        node = nuke.thisNode()
        knob = nuke.thisKnob()
    except Exception:
        return

    if node is None or knob is None:
        return

    knob_name = knob.name()

    if knob_name == "showPanel":
        try:
            from common import ensure_prompt_field_heights
            ensure_prompt_field_heights(node, "fibo_generate")
        except Exception:
            pass
        return

    if knob_name == "inputChange":
        _sync_structured_prompt_link(node)
        return

    if knob_name == "history_selected_entry":
        _ensure_generation_history_ui(node)
        _update_generation_history_selection_ui(node)
        return

    if knob_name == "preset_selected":
        _ensure_generation_preset_ui(node)
        current = _opt_parm_menu_str(node, "preset_selected")
        _refresh_generation_preset_ui(node, preferred_name=current)
        return

    if knob_name == "use_negative_prompt":
        neg = node.knob("negative_prompt")
        if neg is not None:
            try:
                neg.setVisible(bool(_opt_parm_bool(node, "use_negative_prompt")))
            except Exception:
                pass
        return

    if knob_name == "use_structured_prompt":
        _sync_prompt_mode_ui(node)
        return

    if knob_name == "model":
        model = (_opt_parm_menu_str(node, "model") or "standard").strip().lower()
        tailored = model == "tailored"
        for k in ("tailored_model_id", "tailored_model_influence"):
            kb = node.knob(k)
            if kb is not None:
                try:
                    kb.setVisible(tailored)
                except Exception:
                    pass
        return

    if knob_name == "output_view":
        _sync_group_preview(node)
        return

    if knob_name == "use_multiple_images":
        _apply_multiple_images_visibility(node)
        return

    if knob_name == "result_variant":
        _apply_result_variant(node)
        return

    if knob_name in {"result_path", "use_global_project_path", "preset_path", "load_presets_from_examples"}:
        _sync_group_preview(node)
        _ensure_generation_preset_ui(node)
        _refresh_generation_preset_ui(node, apply_default_path=(knob_name != "preset_path"))


def fibo_generate_bria(node: Any) -> None:
    t_start_click = time.perf_counter()
    progress = _create_progress_task("Bria FIBO Generate")

    try:
        _ensure_generation_metadata_ui(node)
        _ensure_generation_history_ui(node)
        _ensure_generation_preset_ui(node)
        _on_main_thread(lambda: _set_status(node, "processing"))
        _update_progress(progress, "Preparing request...", 5)

        run_id = str(int(time.time() * 1000))

        use_negative_prompt = bool(_opt_parm_bool(node, "use_negative_prompt"))
        use_structured_prompt_knob = bool(_opt_parm_bool(node, "use_structured_prompt"))
        prompt = None if use_structured_prompt_knob else _opt_parm_str(node, "prompt")

        negative_prompt = _opt_parm_str(node, "negative_prompt") if use_negative_prompt else None
        structured_prompt_raw = _opt_parm_str(node, "structured_prompt") if use_structured_prompt_knob else None
        structured_prompt = _parse_structured_prompt(structured_prompt_raw)

        aspect_ratio = _normalize_aspect_ratio(_opt_parm_menu_str(node, "aspect_ratio"))
        resolution = (_opt_parm_menu_str(node, "resolution") or "1MP").strip().upper() or "1MP"
        tailored_model_id = _opt_parm_str(node, "tailored_model_id")
        preserve_alpha = _opt_parm_bool(node, "preserve_alpha")

        seed = _opt_parm_int(node, "seed")
        guidance_scale = _opt_parm_int(node, "guidance_scale")
        steps_num = _opt_parm_int(node, "steps_num")
        tailored_model_influence = _opt_parm_float(node, "tailored_model_influence")
        sync = _opt_parm_bool(node, "sync")

        # Multi-image generation: only active when the use_multiple_images toggle is on.
        # When off, num_images = 1 regardless of what the (hidden) dropdown holds.
        use_multiple_images = bool(_opt_parm_bool(node, "use_multiple_images"))
        if use_multiple_images:
            num_images_str = _opt_parm_menu_str(node, "num_images") or "2"
            try:
                num_images = max(2, min(4, int(num_images_str)))
            except (TypeError, ValueError):
                num_images = 2
        else:
            num_images = 1

        # 4MP generation requires async processing; override sync knob.
        if resolution == "4MP":
            sync = False

        pipeline = (_opt_parm_menu_str(node, "model") or "standard").strip().lower() or "standard"
        use_structured = bool(structured_prompt)

        payload: Dict[str, object] = {}
        if prompt:
            payload["prompt"] = prompt
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if structured_prompt:
            payload["structured_prompt"] = structured_prompt
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        if resolution:
            payload["resolution"] = resolution
        if preserve_alpha is not None:
            payload["preserve_alpha"] = bool(preserve_alpha)
        if seed is not None:
            # Nuke Int_Knob defaults to 0; treat non-positive as unset.
            if int(seed) > 0:
                payload["seed"] = int(seed)
        if guidance_scale is not None:
            if int(guidance_scale) > 5:
                raise RuntimeError("Guidance Scale must be between 1 and 5.")
            # Treat non-positive defaults as unset.
            if int(guidance_scale) > 0:
                payload["guidance_scale"] = int(guidance_scale)
        if steps_num is not None:
            if int(steps_num) <= 0:
                pass
            else:
                payload["steps_num"] = int(steps_num)
        if sync is not None:
            payload["sync"] = bool(sync)
        if num_images > 1:
            payload["num_results"] = num_images

        if pipeline.strip().lower() == "tailored":
            if not tailored_model_id:
                raise RuntimeError("tailored_model_id is required for tailored FIBO generation.")
            payload["tailored_model_id"] = tailored_model_id
            if tailored_model_influence is not None:
                # Optional; only relevant for tailored pipeline.
                val = float(tailored_model_influence)
                if val <= 0.0:
                    pass
                elif val >= 2.0:
                    _debug_log("tailored_model_influence >= 2.0 detected; clamping to 1.99.")
                    payload["tailored_model_influence"] = 1.99
                    _set_knob_value_safe(node, "tailored_model_influence", 1.99)
                else:
                    payload["tailored_model_influence"] = val

        if not payload.get("prompt") and not payload.get("structured_prompt"):
            raise RuntimeError("Provide at least prompt or structured_prompt.")

        _update_progress(progress, "Resolving config...", 15)
        cfg = load_config()
        out_path = _resolve_output_path(node, cfg, run_id, "fibo_generate")

        use_bearer_auth = bool(cfg.nuke_use_bearer_auth)
        use_env_proxy = cfg.nuke_use_env_proxy
        if use_env_proxy is None:
            use_env_proxy = True

        proxies = resolve_proxies(
            http_proxy=cfg.nuke_http_proxy,
            https_proxy=cfg.nuke_https_proxy,
            use_env_proxy=use_env_proxy,
        )

        if resolution == "4MP":
            _update_progress(progress, "Generating (4MP, async — may take ~30s)...", 25)

        t_dl_start = time.perf_counter()

        # Multi-image generation: Bria's /v2/image/generate endpoint returns ONE image
        # per call regardless of any batch field (confirmed empirically 2026-05-05).
        # Per Bria's documented batch pattern, fire N parallel async calls, each
        # returning a unique request_id; the BriaClient handles status_url polling.
        # Per-variant seed offset gives actual variety when the user has set a seed.
        if num_images > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            base_payload = dict(payload)
            base_payload["sync"] = False  # async pattern: each call returns a status_url
            base_seed = int(base_payload.get("seed") or 0)

            def _one_variant(variant_idx: int) -> tuple:
                per_payload = dict(base_payload)
                if base_seed > 0:
                    # Vary seed per call so variants are actually different.
                    per_payload["seed"] = base_seed + (variant_idx - 1)
                resp = fibo_generate_from_payload(
                    payload=per_payload,
                    pipeline=pipeline,
                    use_structured_prompt=use_structured,
                    api_key=None,
                    api_endpoint=None,
                    use_bearer=use_bearer_auth,
                    proxies=proxies,
                    session=None,
                )
                urls = extract_image_urls(resp)
                if not urls:
                    raise BriaRequestError(
                        f"Variant {variant_idx}: API returned no image_url. Response: {resp}"
                    )
                img_bytes, content_type = download_url(urls[0], timeout_s=300, proxies=proxies)
                base_save_path = _resolve_result_save_path(out_path, content_type)
                base_save_path = _timestamp_result_path(base_save_path, run_id)
                root, ext = os.path.splitext(base_save_path)
                variant_save_path = f"{root}_v{variant_idx:02d}{ext}"
                _ensure_parent_dir(variant_save_path)
                with open(variant_save_path, "wb") as f:
                    f.write(img_bytes)
                if not os.path.exists(variant_save_path) or os.path.getsize(variant_save_path) <= 0:
                    raise RuntimeError(f"Variant {variant_idx}: empty file at {variant_save_path}")
                return variant_idx, variant_save_path, content_type, resp

            _update_progress(
                progress, f"Generating {num_images} variants in parallel...", 30
            )

            results_by_idx: dict = {}
            errors: list = []
            completed = 0
            with ThreadPoolExecutor(max_workers=num_images) as executor:
                futures = {
                    executor.submit(_one_variant, i): i for i in range(1, num_images + 1)
                }
                for fut in as_completed(futures):
                    variant_idx = futures[fut]
                    try:
                        idx, vpath, ctype, vresp = fut.result()
                        results_by_idx[idx] = (vpath, ctype, vresp)
                    except Exception as exc:
                        errors.append((variant_idx, exc))
                    completed += 1
                    _update_progress(
                        progress,
                        f"Variant {completed}/{num_images} done",
                        30 + int(55 * completed / num_images),
                    )

            if not results_by_idx:
                # Every variant failed
                first_err = errors[0][1] if errors else RuntimeError("All variants failed")
                raise first_err

            sorted_keys = sorted(results_by_idx.keys())
            result_paths = [results_by_idx[k][0] for k in sorted_keys]
            save_path = result_paths[0]
            content_type = results_by_idx[sorted_keys[0]][1]
            data = results_by_idx[sorted_keys[0]][2]

            if errors:
                err_summary = ", ".join(f"v{i:02d}: {e}" for i, e in errors)
                nuke.tprint(f"[Bria] Some variants failed: {err_summary}")
        else:
            # Single-image path: one call, one save (no _vNN suffix for backward compat).
            data = fibo_generate_from_payload(
                payload=payload,
                pipeline=pipeline,
                use_structured_prompt=use_structured,
                api_key=None,
                api_endpoint=None,
                use_bearer=use_bearer_auth,
                proxies=proxies,
                session=None,
            )
            _update_progress(progress, "Downloading result image...", 70)
            dl_urls = extract_image_urls(data)
            if not dl_urls:
                raise BriaRequestError(f"Unexpected API response (no image_url): {data}")
            img_bytes, content_type = download_url(dl_urls[0], timeout_s=300, proxies=proxies)
            save_path = _resolve_result_save_path(out_path, content_type)
            save_path = _timestamp_result_path(save_path, run_id)
            _ensure_parent_dir(save_path)
            with open(save_path, "wb") as f:
                f.write(img_bytes)
            if not os.path.exists(save_path) or os.path.getsize(save_path) <= 0:
                raise RuntimeError(f"Bria download produced an empty file: {save_path}")
            result_paths = [save_path]

        t_dl_end = time.perf_counter()

        def _apply_success_ui() -> None:
            try:
                _set_history_debug(node, "success ui entered")
                prev_path = (_opt_parm_str(node, "result_path") or "")
                if prev_path and prev_path != save_path:
                    _set_internal_read_file(node, "loader_before", prev_path)
                _set_generation_metadata(
                    node,
                    {
                        "operation": "fibo_generate",
                        "endpoint": resolve_api_endpoint(cfg),
                        "result_path": save_path,
                        "result_paths": list(result_paths),
                        "response": data,
                        "settings": {
                            "pipeline": pipeline,
                            "aspect_ratio": aspect_ratio,
                            "resolution": resolution,
                            "seed": payload.get("seed"),
                            "guidance_scale": payload.get("guidance_scale"),
                            "steps_num": payload.get("steps_num"),
                            "preserve_alpha": payload.get("preserve_alpha"),
                            "use_structured_prompt": use_structured,
                            "prompt": payload.get("prompt"),
                            "negative_prompt": payload.get("negative_prompt"),
                            "structured_prompt": payload.get("structured_prompt"),
                            "tailored_model_id": payload.get("tailored_model_id"),
                            "tailored_model_influence": payload.get("tailored_model_influence"),
                            "num_images": num_images,
                        },
                    },
                )
                _set_history_debug(node, "generation metadata written")
                _append_generation_history(
                    node,
                    {
                        "operation": "fibo_generate",
                        "endpoint": resolve_api_endpoint(cfg),
                        "result_path": save_path,
                        "result_paths": list(result_paths),
                        "response": data,
                        "settings": {
                            "pipeline": pipeline,
                            "aspect_ratio": aspect_ratio,
                            "resolution": resolution,
                            "seed": payload.get("seed"),
                            "guidance_scale": payload.get("guidance_scale"),
                            "steps_num": payload.get("steps_num"),
                            "preserve_alpha": payload.get("preserve_alpha"),
                            "use_structured_prompt": use_structured,
                            "prompt": payload.get("prompt"),
                            "negative_prompt": payload.get("negative_prompt"),
                            "structured_prompt": payload.get("structured_prompt"),
                            "tailored_model_id": payload.get("tailored_model_id"),
                            "tailored_model_influence": payload.get("tailored_model_influence"),
                            "num_images": num_images,
                        },
                    },
                )
                _set_history_debug(node, "history append returned")
                _set_result_path(node, save_path)
                _set_knob_value_safe(node, "output_view", "After")
                _sync_group_preview_explicit(node, save_path, "After")

                # Update the Image Index slider range to match the ACTUAL number of
                # variants saved. Visibility is owned by the toggle handler — we DO NOT
                # touch it here. Bria's /v2/image/generate may quietly cap num_results,
                # so we log what came back.
                actual_count = len(result_paths)
                if use_multiple_images:
                    nuke.tprint(f"[Bria] Saved {actual_count} variant(s) to disk")
                    if actual_count != num_images:
                        nuke.tprint(
                            f"[Bria] Requested {num_images} variants but API returned "
                            f"{actual_count}. The /image/generate endpoint may not "
                            f"honor num_results > 1 for this pipeline."
                        )
                try:
                    variant_knob = node.knob("result_variant")
                    if variant_knob is not None:
                        variant_knob.setRange(1, max(actual_count, 1))
                        variant_knob.setValue(1)
                    count_knob = node.knob("result_variant_count")
                    if count_knob is not None:
                        count_knob.setValue(str(actual_count))
                    # Re-apply toggle-driven visibility so the slider/button are shown
                    # whenever the toggle is on, regardless of how many came back.
                    _apply_multiple_images_visibility(node)
                except Exception:
                    pass

                _set_status(node, "done")
            except Exception as exc:
                _set_history_debug(node, f"success ui failed: {exc}")
                raise

        _on_main_thread(_apply_success_ui)
        _update_progress(progress, "Finalizing preview...", 95)

        t_final = time.perf_counter()
        total_time = t_final - t_start_click
        api_time = t_dl_start - t_start_click
        dl_time = t_dl_end - t_dl_start

        _debug_log(f"API FIBO Time:    {api_time:.4f} sec")
        _debug_log(f"Image Download:   {dl_time:.4f} sec")
        _debug_log(f"Total Turnaround: {total_time:.4f} sec")
        dim_info = ""
        try:
            ihdr = png_ihdr_info(save_path)
            if ihdr:
                dim_info = f" | dims={ihdr.get('width')}x{ihdr.get('height')}"
        except Exception:
            pass
        _debug_log(
            f"Saved result: {save_path} ({os.path.getsize(save_path)} bytes) | content-type={content_type or 'unknown'}{dim_info}"
        )
        _update_progress(progress, percent=100)

        nuke.tprint(f"Bria Nuke FIBO Complete ({total_time:.2f}s) | Saved: {save_path}")

    except (BriaConfigError, BriaRequestError) as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        raw_msg = f"Bria Nuke FIBO Error: {exc}"
        msg = _friendly_bria_request_error(node, exc)
        _debug_log(raw_msg)
        if _is_batch_mode():
            raise
        nuke.message(msg)
    except Exception as exc:
        _on_main_thread(lambda: _set_status(node, "error"))
        msg = f"Bria Nuke FIBO Exception: {exc}"
        _debug_log(msg)
        if _is_batch_mode():
            raise
        nuke.message(msg)

def on_fibo_generate(node: Any = None) -> None:
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")
    _ensure_generation_metadata_ui(node)
    # Force a fresh sync from any connected Structured Prompt (VGL) node
    # before running. Guards against stale `structured_prompt` state when
    # the upstream SP node was re-run but knobChanged didn't propagate.
    _sync_structured_prompt_link(node)
    fibo_generate_bria(node)
