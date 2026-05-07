"""Nuke Bria FIBO Edit Recipes node logic.

A FIBO Edit node variant with categorized preset prompts for style transfers,
weather, seasons, time of day, camera effects, lighting, compositing, cleanup,
AI corrections, and object edits. Users pick a category and preset, then the
prompt auto-fills. Object-edit and compositing presets support targeting via
fixed target slots (Nuke has no MultiparmBlock equivalent).

This module delegates API execution to fibo_edit.on_fibo_edit() — the recipes
node is FIBO Edit plus a prompt-assembly layer on top.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

import nuke  # type: ignore

from node_utils import opt_parm_menu_str as _opt_parm_menu_str
from node_utils import opt_parm_str as _opt_parm_str

import fibo_edit as _fibo_edit


# Number of fixed target/element slots (Nuke has no dynamic MultiparmBlock).
MAX_OBJECT_SLOTS = 5
MAX_COMP_SLOTS = 5


# ---------------------------------------------------------------------------
# Category definitions: token → display label
# ---------------------------------------------------------------------------
CATEGORY_ORDER = [
    ("custom",          "Custom"),
    ("style",           "Style"),
    ("weather",         "Weather"),
    ("seasons",         "Seasons"),
    ("time_of_day",     "Time of Day"),
    ("camera",          "Camera"),
    ("lighting",        "Lighting"),
    ("compositing",     "Compositing"),
    ("clean",           "Clean / Artifacts"),
    ("ai_corrections",  "AI Corrections"),
    ("object_edits",    "Object Edits"),
]


# ---------------------------------------------------------------------------
# Object-targeting presets: structured constraint backbones
# ---------------------------------------------------------------------------
TARGETED_OBJECT_PRESETS = frozenset({
    "delete_object", "replace_object", "change_object_color",
    "change_object_material", "isolate_object",
})

# Presets whose action templates do NOT reference {mod1} — modifier is optional.
# Multi-target action template uses {obj_list} (just names) instead of {obj_mod_list}.
_PRESETS_WITHOUT_MOD = frozenset({"delete_object", "isolate_object"})

_OBJ_ACTIONS = {
    "delete_object": {
        "fallback": "Delete the specified object from the scene.",
        "single": "Delete {obj1} from the scene.",
        "multi": "Delete the following objects from the scene: {obj_list}.",
    },
    "replace_object": {
        "fallback": "Replace the specified object with the described replacement.",
        "single": "Replace {obj1} with {mod1}.",
        "multi": "Replace the following objects: {obj_mod_list}.",
    },
    "change_object_color": {
        "fallback": "Change the color of the specified object to the described color.",
        "single": "Change the color of {obj1} to {mod1}.",
        "multi": "Change the colors of the following objects: {obj_mod_list}.",
    },
    "change_object_material": {
        "fallback": "Change the material of the specified object to the described material.",
        "single": "Change the material of {obj1} to {mod1}.",
        "multi": "Change the materials of the following objects: {obj_mod_list}.",
    },
    "isolate_object": {
        "fallback": "Isolate the specified object from the scene by removing everything else.",
        "single": "Isolate {obj1} from the scene by removing everything else.",
        "multi": "Isolate the following objects from the scene by removing everything else: {obj_list}.",
    },
}

_OBJ_BODIES = {
    "delete_object": (
        "STRICT LOCKS (non-negotiable):\n"
        "Do NOT move, warp, scale, rotate, or reshape any remaining objects.\n"
        "Do NOT change the overall color grade, lighting, or artistic style.\n"
        "Do NOT alter any objects other than those specified for deletion.\n"
        "Scope of work:\n"
        "Completely remove the specified object(s) from the scene.\n"
        "Seamlessly inpaint the revealed area behind each deleted object, reconstructing the background naturally.\n"
        "Match the inpainted region's texture, lighting, and perspective to the surrounding scene.\n"
        "CRITICAL RULES:\n"
        "All edits must be confined to the deleted object(s) and the area directly behind them.\n"
        "Do NOT remove or modify any other objects in the scene.\n"
        "Do NOT change the background outside the inpainted regions.\n"
        "The inpainted area must be indistinguishable from the original background.\n"
        "Result requirement:\n"
        "The output should appear as if the deleted object(s) were never present. "
        "If the specified object cannot be identified, return the image unchanged."
    ),
    "replace_object": (
        "STRICT LOCKS (non-negotiable):\n"
        "Do NOT move, warp, scale, rotate, or reshape any objects other than the one(s) being replaced.\n"
        "Do NOT change the overall color grade, lighting direction, or artistic style.\n"
        "Do NOT alter the background or surrounding scene elements.\n"
        "Scope of work:\n"
        "Remove the specified object(s) and place the described replacement(s) in the same position.\n"
        "Match the replacement's lighting, perspective, scale, and shadow to the scene.\n"
        "Blend the replacement naturally into the surrounding environment.\n"
        "CRITICAL RULES:\n"
        "All edits must be confined to the replaced object(s) and their immediate surroundings.\n"
        "The replacement must occupy approximately the same spatial footprint as the original.\n"
        "Do NOT change any other objects or scene elements.\n"
        "Preserve the scene's existing lighting direction and shadow behavior.\n"
        "Result requirement:\n"
        "The output should appear as if the replacement object(s) were always part of the original scene. "
        "If the specified object cannot be identified, return the image unchanged."
    ),
    "change_object_color": (
        "STRICT LOCKS (non-negotiable):\n"
        "Do NOT move, warp, scale, rotate, or reshape any objects.\n"
        "Do NOT change the overall color grade, lighting, or artistic style.\n"
        "Do NOT alter any objects other than those specified for color change.\n"
        "Scope of work:\n"
        "Change only the color of the specified object(s) to the described target color(s).\n"
        "Preserve each object's shape, texture detail, material properties, and surface finish.\n"
        "Maintain correct lighting response \u2014 highlights, shadows, and reflections should adapt naturally to the new color.\n"
        "CRITICAL RULES:\n"
        "All edits must be confined to the surface color of the specified object(s).\n"
        "Do NOT change the object's shape, texture pattern, or material type.\n"
        "Do NOT affect the background or any other objects.\n"
        "Do NOT alter the scene's lighting or shadows beyond natural color-dependent changes.\n"
        "Result requirement:\n"
        "The output should appear as if the object(s) were always the target color, with natural lighting response. "
        "If the specified object cannot be identified, return the image unchanged."
    ),
    "change_object_material": (
        "STRICT LOCKS (non-negotiable):\n"
        "Do NOT move, warp, scale, rotate, or reshape any objects.\n"
        "Do NOT change the overall color grade, lighting direction, or artistic style.\n"
        "Do NOT alter any objects other than those specified for material change.\n"
        "Scope of work:\n"
        "Change only the material and surface texture of the specified object(s) to the described target material(s).\n"
        "Preserve each object's shape and silhouette exactly.\n"
        "Apply correct material properties \u2014 reflectivity, roughness, transparency, and texture pattern appropriate to the new material.\n"
        "Maintain natural lighting response for the new material.\n"
        "CRITICAL RULES:\n"
        "All edits must be confined to the surface material of the specified object(s).\n"
        "Do NOT change the object's shape, size, or position.\n"
        "Do NOT affect the background or any other objects.\n"
        "Lighting and shadow changes should only reflect the new material's natural properties.\n"
        "Result requirement:\n"
        "The output should appear as if the object(s) were always made of the target material, with correct surface properties. "
        "If the specified object cannot be identified, return the image unchanged."
    ),
    "isolate_object": (
        "STRICT LOCKS (non-negotiable):\n"
        "Do NOT move, warp, scale, rotate, or reshape the specified object(s).\n"
        "Do NOT alter the specified object(s)' color, texture, material, lighting, or shadows.\n"
        "Preserve the specified object(s)' silhouette, edges, and fine detail exactly.\n"
        "Scope of work:\n"
        "Remove EVERYTHING except the specified object(s) from the scene.\n"
        "Replace the entire background and all other objects with a clean, flat, neutral medium gray backdrop (approximately RGB 128,128,128 / #808080).\n"
        "Keep any natural contact shadows or cast shadows directly attached to the specified object(s) to preserve grounding, rendered softly on the gray backdrop.\n"
        "CRITICAL RULES:\n"
        "The specified object(s) must appear pixel-identical to the original \u2014 same position, same scale, same colors, same lighting.\n"
        "The gray backdrop must be completely flat and uniform \u2014 no gradients, no texture, no vignetting, no visible seams.\n"
        "Do NOT add any new objects, props, or environmental elements.\n"
        "Do NOT include any remnants of the original background, other objects, or scene elements.\n"
        "Clean, sharp edges around the isolated object(s) \u2014 no halos, no fringing, no bleeding from the removed background.\n"
        "Result requirement:\n"
        "The output should appear as if the specified object(s) were photographed against a seamless neutral gray studio backdrop. "
        "This flat gray background is optimized for downstream background removal (RMBG) \u2014 it should be easily separable from the subject. "
        "If the specified object cannot be identified, return the image unchanged."
    ),
}


def _obj_fallback_prompt(key: str) -> str:
    return _OBJ_ACTIONS[key]["fallback"] + "\n" + _OBJ_BODIES[key]


# ---------------------------------------------------------------------------
# Compositing-element-targeting presets: structured constraint backbones
# ---------------------------------------------------------------------------
TARGETED_COMP_PRESETS = frozenset({
    "harmonize_lighting", "match_shadows", "blend_edges", "color_harmonize",
    "fix_reflections", "integrate_elements", "match_ambient", "create_mask",
    "create_soft_mask", "remove_spill",
})

# Mask presets that show the Premultiply/Edge Softness controls.
_MASK_PRESETS = frozenset({"create_mask", "create_soft_mask"})

_COMP_ACTIONS = {
    "harmonize_lighting": {
        "fallback": "Perform a targeted lighting harmonization pass on this composite image.",
        "single": "Perform a targeted lighting harmonization pass on this composite image, focusing on {elem1}.",
        "multi": "Perform a targeted lighting harmonization pass on this composite image, focusing on the following composited elements: {elem_list}.",
    },
    "match_shadows": {
        "fallback": "Perform a targeted shadow correction pass on this composite image.",
        "single": "Perform a targeted shadow correction pass on this composite image, focusing on {elem1}.",
        "multi": "Perform a targeted shadow correction pass on this composite image, focusing on the following composited elements: {elem_list}.",
    },
    "blend_edges": {
        "fallback": "Perform a targeted edge blending pass on this composite image.",
        "single": "Perform a targeted edge blending pass on this composite image, focusing on the edges of {elem1}.",
        "multi": "Perform a targeted edge blending pass on this composite image, focusing on the edges of the following composited elements: {elem_list}.",
    },
    "color_harmonize": {
        "fallback": "Perform a targeted color harmonization pass on this composite image.",
        "single": "Perform a targeted color harmonization pass on this composite image, focusing on {elem1}.",
        "multi": "Perform a targeted color harmonization pass on this composite image, focusing on the following composited elements: {elem_list}.",
    },
    "fix_reflections": {
        "fallback": "Perform a targeted reflection correction pass on this composite image.",
        "single": "Perform a targeted reflection correction pass on this composite image, focusing on reflections of {elem1}.",
        "multi": "Perform a targeted reflection correction pass on this composite image, focusing on reflections of the following composited elements: {elem_list}.",
    },
    "integrate_elements": {
        "fallback": "Perform a targeted compositing cleanup pass on the image.",
        "single": "Perform a targeted compositing cleanup pass on this image, focusing on {elem1}.",
        "multi": "Perform a targeted compositing cleanup pass on this image, focusing on the following composited elements: {elem_list}.",
    },
    "match_ambient": {
        "fallback": "Perform a targeted ambient light matching pass on this composite image.",
        "single": "Perform a targeted ambient light matching pass on this composite image, focusing on {elem1}.",
        "multi": "Perform a targeted ambient light matching pass on this composite image, focusing on the following composited elements: {elem_list}.",
    },
    "create_mask": {
        "fallback": "Output a flat-fill rotoscope holdout matte for compositing: render the specified element as a solid white silhouette filled completely on a pure black background, pixel-aligned with the input image. Output is two-tone only \u2014 pure white and pure black, no other colors.",
        "single": "Output a flat-fill rotoscope holdout matte for compositing: render {elem1} as a solid white silhouette filled completely on a pure black background, pixel-aligned with the input image. Output is two-tone only \u2014 pure white and pure black, no other colors.",
        "multi": "Output a flat-fill rotoscope holdout matte for compositing: render the following elements as solid white silhouettes filled completely on a pure black background, pixel-aligned with the input image: {elem_list}. Output is two-tone only \u2014 pure white and pure black, no other colors.",
    },
    "remove_spill": {
        "fallback": "Remove all green-screen and blue-screen spill, fringing, and color contamination from the specified element(s), blending them naturally into the surrounding scene.",
        "single": "Remove all green-screen and blue-screen spill, fringing, and color contamination from {elem1}, blending {elem1} naturally into the surrounding scene.",
        "multi": "Remove all green-screen and blue-screen spill, fringing, and color contamination from the following elements: {elem_list}. Blend each element naturally into the surrounding scene.",
    },
    "create_soft_mask": {
        "fallback": "Output a grayscale density matte for compositing: render the specified soft element with intermediate gray values mapping its local density across the image, pixel-aligned with the input.",
        "single": "Output a grayscale density matte for compositing: render {elem1} with intermediate gray values mapping its local density across the image \u2014 pure white where {elem1} is at maximum density, pure black where {elem1} is absent, gray gradients tracking partial density and feathered edges. Pixel-aligned with the input.",
        "multi": "Output a grayscale density matte for compositing: render the following soft elements with density-mapped gray values, pixel-aligned with the input: {elem_list}. White = maximum density, black = absent, gray = partial density.",
    },
}

_COMP_BODIES = {
    "harmonize_lighting": (
        "STRICT LOCKS (non-negotiable):\n"
        "The image must remain pixel-identical in layout and composition.\n"
        "Do NOT move, warp, scale, rotate, or reshape any objects.\n"
        "Do NOT change the overall color grade or artistic style.\n"
        "Scope of work (local fixes only):\n"
        "Identify regions where composited elements have inconsistent lighting compared to the scene, including:\n"
        "- light direction mismatch (highlights/shadows facing wrong way)\n"
        "- intensity mismatch (element too bright or too dark for the scene)\n"
        "- color temperature mismatch (warm element in cool scene or vice versa)\n"
        "- missing or incorrect light falloff on composited objects\n"
        "Apply ONLY minimal, localized corrections:\n"
        "- adjust highlight and shadow placement on affected elements to match scene light direction\n"
        "- correct brightness/exposure ONLY on mismatched elements to blend with surroundings\n"
        "- shift color temperature ONLY on affected elements to match the scene's dominant light\n"
        "CRITICAL RULES:\n"
        "All edits must be confined to composited elements that show lighting inconsistency.\n"
        "Do NOT re-light the entire scene or change the background lighting.\n"
        "Do NOT add new light sources or remove existing ones.\n"
        "Do NOT alter textures, materials, or shapes.\n"
        "Result requirement:\n"
        "The output should appear as if all elements were photographed under the same lighting conditions. If no lighting issues are detected, return the image unchanged."
    ),
    "match_shadows": (
        "STRICT LOCKS (non-negotiable):\n"
        "The image must remain pixel-identical in layout and composition.\n"
        "Do NOT move, warp, scale, rotate, or reshape any objects.\n"
        "Do NOT change global lighting, color balance, or overall image appearance.\n"
        "Scope of work (local fixes only):\n"
        "Identify regions where composited elements have incorrect or missing shadows, including:\n"
        "- shadow direction inconsistent with the scene's primary light source\n"
        "- missing contact shadows where objects meet surfaces\n"
        "- shadow softness/hardness not matching the scene's light quality\n"
        "- shadow density too strong or too weak relative to scene shadows\n"
        "Apply ONLY minimal, localized corrections:\n"
        "- add or reposition contact shadows at object bases to match scene light direction\n"
        "- adjust shadow softness to match the scene's diffusion level\n"
        "- correct shadow density/opacity to be consistent with existing scene shadows\n"
        "- blend shadow edges naturally into the ground plane\n"
        "CRITICAL RULES:\n"
        "All edits must be confined to shadow regions of composited elements.\n"
        "Do NOT modify the objects themselves, only their shadows.\n"
        "Do NOT change scene lighting or add new light sources.\n"
        "Do NOT affect areas that already have correct shadows.\n"
        "Result requirement:\n"
        "The output should have consistent shadow behavior across all elements as if lit by the same source. If no shadow issues are detected, return the image unchanged."
    ),
    "blend_edges": (
        "STRICT LOCKS (non-negotiable):\n"
        "The image must remain pixel-identical in layout and composition.\n"
        "Do NOT move, warp, scale, rotate, or reshape any objects.\n"
        "Do NOT change global lighting, shadows, or color balance.\n"
        "Do NOT modify the overall image appearance or grade.\n"
        "Scope of work (local fixes only):\n"
        "Identify regions where composited elements have visible edge artifacts, including:\n"
        "- hard cutout edges with aliasing or jagged pixels\n"
        "- visible halos, fringing, or matte lines around composited objects\n"
        "- unnatural sharp boundaries between foreground elements and background\n"
        "- color spill or edge contamination from the original background\n"
        "Apply ONLY minimal, localized corrections:\n"
        "- soften and anti-alias hard edges to remove visible matte lines\n"
        "- remove halos and fringing artifacts along element boundaries\n"
        "- gently blend transitions between foreground edges and background\n"
        "- clean color spill at edges to match adjacent background pixels\n"
        "CRITICAL RULES:\n"
        "All edits must be confined to the edge regions of composited elements (within a few pixels of boundaries).\n"
        "Do NOT affect the interior of any element or the background.\n"
        "Do NOT blur or soften the overall image.\n"
        "Do NOT change the shape or silhouette of any object.\n"
        "Result requirement:\n"
        "The output should have clean, natural-looking edges on all composited elements with no visible cutout artifacts. If no edge issues are detected, return the image unchanged."
    ),
    "color_harmonize": (
        "STRICT LOCKS (non-negotiable):\n"
        "The image must remain pixel-identical in layout and composition.\n"
        "Do NOT move, warp, scale, rotate, or reshape any objects.\n"
        "Do NOT change the lighting direction, shadow placement, or overall exposure.\n"
        "Scope of work (local fixes only):\n"
        "Identify composited elements whose color characteristics do not match the scene, including:\n"
        "- color temperature mismatch (element shot under different lighting than the background)\n"
        "- saturation mismatch (element more or less saturated than surroundings)\n"
        "- contrast curve mismatch (element has different tonal range than scene)\n"
        "- overall color cast inconsistency between elements and background\n"
        "Apply ONLY minimal, localized corrections:\n"
        "- shift color temperature of affected elements to match the scene's dominant color\n"
        "- adjust saturation ONLY on mismatched elements to blend with surroundings\n"
        "- match contrast and tonal curve of elements to the background's tonal range\n"
        "- remove conflicting color casts from composited elements\n"
        "CRITICAL RULES:\n"
        "All edits must be confined to composited elements that show color inconsistency.\n"
        "Do NOT apply a new color grade to the entire image.\n"
        "Do NOT change the background's color characteristics.\n"
        "Do NOT alter textures, materials, or object identity.\n"
        "Result requirement:\n"
        "The output should appear as if all elements share the same color environment and were captured in the same scene. If no color issues are detected, return the image unchanged."
    ),
    "fix_reflections": (
        "STRICT LOCKS (non-negotiable):\n"
        "The image must remain pixel-identical in layout and composition.\n"
        "Do NOT move, warp, scale, rotate, or reshape any objects.\n"
        "Do NOT change global lighting, shadows, or color balance.\n"
        "Do NOT modify the overall image appearance or grade.\n"
        "Scope of work (local fixes only):\n"
        "Identify regions where composited elements are missing reflections or have incorrect reflections on nearby reflective surfaces, including:\n"
        "- missing reflections on water, glass, polished floors, or metal surfaces\n"
        "- reflection angle inconsistent with the object's position and the camera\n"
        "- reflection intensity or blur not matching the surface's reflective properties\n"
        "- reflection color not matching the reflected object\n"
        "Apply ONLY minimal, localized corrections:\n"
        "- add subtle reflections where composited objects meet reflective surfaces\n"
        "- adjust existing reflections to match correct angle and perspective\n"
        "- match reflection blur and intensity to the surface's material properties\n"
        "- ensure reflection color and brightness are consistent with the scene\n"
        "CRITICAL RULES:\n"
        "All edits must be confined to reflective surface areas near composited elements.\n"
        "Do NOT add reflections to non-reflective surfaces.\n"
        "Do NOT modify the objects themselves, only their reflections.\n"
        "Do NOT change the reflective surface's material or appearance.\n"
        "Result requirement:\n"
        "The output should have consistent reflection behavior for all composited elements on nearby reflective surfaces. If no reflection issues are detected, return the image unchanged."
    ),
    "integrate_elements": (
        "STRICT LOCKS (non-negotiable):\n"
        "The image must remain pixel-identical in layout and composition.\n"
        "Do NOT move, warp, scale, rotate, or reshape any objects.\n"
        "Do NOT change global lighting, shadows, or color balance.\n"
        "Do NOT modify the overall image appearance or grade.\n"
        "Scope of work (local fixes only):\n"
        "Identify small regions where elements appear poorly composited, including:\n"
        "- edge artifacts (aliasing, halos, fringing, cutout edges)\n"
        "- minor color mismatch against immediate surroundings\n"
        "- slight exposure or contrast mismatch\n"
        "- missing or weak contact grounding at object boundaries\n"
        "Apply ONLY minimal, localized corrections:\n"
        "- clean and soften edges to remove visible matte lines\n"
        "- subtly adjust color/exposure ONLY within affected elements to match adjacent pixels\n"
        "- add or refine very subtle contact shadowing only at object boundaries if clearly missing\n"
        "- gently blend transitions between foreground and background\n"
        "CRITICAL RULES:\n"
        "All edits must be confined to small, specific regions.\n"
        "Do NOT affect large areas of the image.\n"
        "Do NOT introduce new lighting, new shadows, or re-light the scene.\n"
        "Do NOT change textures, materials, or shapes.\n"
        "Do NOT enhance or stylize \u2014 this is a corrective pass only.\n"
        "Result requirement:\n"
        "The output should appear almost identical to the original image, with only subtle fixes visible upon close inspection. If no issues are detected, return the image unchanged."
    ),
    "match_ambient": (
        "STRICT LOCKS (non-negotiable):\n"
        "The image must remain pixel-identical in layout and composition.\n"
        "Do NOT move, warp, scale, rotate, or reshape any objects.\n"
        "Do NOT change the primary directional lighting or shadow placement.\n"
        "Do NOT modify the overall image grade or artistic style.\n"
        "Scope of work (local fixes only):\n"
        "Identify composited elements whose ambient illumination does not match the scene, including:\n"
        "- shadow regions too dark or too light compared to scene's ambient fill\n"
        "- missing environmental bounce light that the scene provides\n"
        "- ambient color in shadow areas not matching the scene's ambient color (e.g., blue sky fill, warm ground bounce)\n"
        "- overall ambient exposure level on element inconsistent with surroundings\n"
        "Apply ONLY minimal, localized corrections:\n"
        "- adjust shadow fill brightness on affected elements to match scene's ambient level\n"
        "- tint ambient/shadow areas of elements to match the scene's environmental color\n"
        "- add subtle bounce light influence where the scene clearly provides it\n"
        "- balance ambient-to-direct light ratio on elements to match surroundings\n"
        "CRITICAL RULES:\n"
        "All edits must be confined to ambient/fill regions of composited elements.\n"
        "Do NOT change the direct/key lighting on any element.\n"
        "Do NOT alter the background's ambient characteristics.\n"
        "Do NOT change the overall exposure or brightness of the image.\n"
        "Result requirement:\n"
        "The output should have consistent ambient illumination across all elements as if they exist in the same environment. If no ambient mismatch is detected, return the image unchanged."
    ),
    "remove_spill": (
        "STRICT LOCKS (non-negotiable):\n"
        "Do NOT move, warp, scale, rotate, reshape, or reposition the specified element(s) or anything else in the image.\n"
        "Preserve the specified element(s)' silhouette, pose, position, and scale EXACTLY as they appear in the input image. The output must align pixel-for-pixel with the input.\n"
        "Do NOT regenerate, redraw, or reinterpret the scene's geometry, layout, or composition.\n"
        "Do NOT change the background, environment, lighting setup, or other elements that are not affected by green/blue-screen spill.\n"
        "Do NOT alter materials, textures, or fundamental colors that are NOT spill-related.\n"
        "Scope of work:\n"
        "Identify and remove green-screen and blue-screen spill on the specified element(s). Spill is the unwanted color contamination caused by the chroma key environment reflecting onto the subject during the original capture, including:\n"
        "- green or yellow-green color tint on skin, hair, and edges\n"
        "- green halos or fringing along the silhouette boundary of the specified element(s)\n"
        "- green-tinted highlights and reflections on shiny or reflective surfaces (armor, helmets, glass, metal, plastic)\n"
        "- green color cast on white or light-colored clothing, fabric, hair, and props\n"
        "- green ambient bounce in shadow regions of the specified element(s)\n"
        "Apply targeted, localized despill to the specified element(s):\n"
        "- neutralize unwanted green/blue color casts back to the element's natural color\n"
        "- restore correct skin tone, hair color, and surface color where spill was present\n"
        "- clean fringing along the element silhouette to remove green/blue edge contamination\n"
        "- adjust reflections and highlights so they pick up the surrounding scene's color rather than the original chroma green/blue\n"
        "CRITICAL RULES:\n"
        "All edits must be confined to the specified element(s). Do NOT recolor or relight the background or any other elements.\n"
        "Do NOT remove naturally-occurring green or blue colors (e.g., a green shirt, foliage, signage, sky, or water that is supposed to be green/blue). Only remove green/blue contamination that is clearly chroma-key spill on the specified element(s).\n"
        "Do NOT shift the specified element(s)' overall color grade beyond what is needed to remove the unwanted spill.\n"
        "Preserve natural shadows, highlights, material properties, and texture detail on the specified element(s).\n"
        "Pixel-accurate registration with the input image is mandatory.\n"
        "Result requirement:\n"
        "The output should look as if the specified element(s) were originally photographed in the current background environment, with no remaining trace of green-screen or blue-screen color contamination. If no spill is detected on the specified element(s), return the image unchanged."
    ),
    "create_mask": (
        "ALIGNMENT LOCK (highest priority, non-negotiable):\n"
        "Treat this as a tracing task, not a generation task. The input image is the absolute ground truth for ALL geometry, position, scale, pose, and silhouette boundaries. Your only job is to trace the input.\n"
        "The output must align PIXEL-FOR-PIXEL with the input image. The dimensions, the specified element(s)' positions, their silhouette outlines, and every edge must match the input exactly.\n"
        "Do NOT move, warp, scale, rotate, shift, reshape, repose, or reposition the specified element(s) or anything else by even one pixel.\n"
        "Do NOT regenerate, redraw, reinterpret, restage, or re-imagine any element.\n"
        "Do NOT apply any creative interpretation \u2014 you are tracing, not creating.\n"
        "OUTPUT FORMAT (non-negotiable):\n"
        "This is a ROTOSCOPE HOLDOUT MATTE for compositing \u2014 the kind of flat-fill silhouette a roto artist paints to use as an alpha channel. It is NOT a stylized illustration, line drawing, halftone, or comic-book rendering of the subject.\n"
        "The output is two-tone: pure white (RGB 255,255,255) inside the silhouette of the named element(s); pure black (RGB 0,0,0) outside. The subject's actual color, texture, and visual appearance must NOT be visible \u2014 only its filled silhouette.\n"
        "Render the matte at FULL native resolution of the input image with anti-aliased silhouette edges. Edges should be smooth and crisp, not pixel-staircase aliased. Anti-aliasing gradient values are permitted ONLY at the 1-2 pixel boundary where the silhouette meets black; the interior of the silhouette must remain solid pure white.\n"
        "WHITE pixels: cover the silhouette of the specified element(s) in the input \u2014 edge to edge, fully filled, no interior detail. Treat each named element as one solid filled-in shape, like a paper cutout. NO internal panel lines, NO armor seams, NO shadows, NO line work, NO holes, NO black pixels at all inside the silhouette.\n"
        "BLACK pixels: everywhere else.\n"
        "EXCLUDE from the white silhouette \u2014 these MUST remain pure black in the matte even when they overlap, surround, or touch the named element(s):\n"
        "- atmospheric elements: smoke, fog, dust, haze, mist, vapor, steam\n"
        "- particles: sparks, embers, debris dust, droplets, splashes, water\n"
        "- motion blur trails, motion streaks, lens flares, light glows, lens artifacts\n"
        "- any other transparent or semi-transparent secondary VFX elements\n"
        "The named element(s) are SOLID PHYSICAL OBJECTS only. If smoke or atmosphere overlaps or wraps around the silhouette of a named element, the smoke must be black and the named element must remain a clean filled silhouette underneath \u2014 do NOT carve notches into the named element where it meets atmospherics.\n"
        "EDGE BEHAVIOR:\n"
        "Edges of the white silhouette follow the EXACT outline of the specified element(s) in the input. Sharp by default. 1-2 pixel feather only at boundaries that are genuinely soft in the input (hair, fur, motion blur, atmospheric edges).\n"
        "Areas of green-screen or blue-screen contamination on the named element(s) are STILL PART OF the named element(s) \u2014 include them as white in the matte. Do NOT exclude spill-affected areas from the silhouette.\n"
        "EXPLICIT ANTI-PATTERNS \u2014 do NOT do any of these:\n"
        "Do NOT output the original image with a black background (that is isolation, not masking).\n"
        "Do NOT output a stylized line drawing, illustration, halftone, or comic-book rendering of the subject.\n"
        "Do NOT output color, grayscale, or any non-binary values inside the silhouette.\n"
        "Do NOT add new content, props, environmental details, or speculative geometry.\n"
        "Result requirement:\n"
        "The output is a clean flat-fill binary matte aligned pixel-perfectly with the input \u2014 solid white over the silhouette of the specified element(s) at their actual positions in the input, solid black everywhere else, with NO internal detail of any kind. "
        "If the specified element(s) cannot be identified, return a fully black image of the same dimensions as the input."
    ),
    "create_soft_mask": (
        "ALIGNMENT LOCK (highest priority, non-negotiable):\n"
        "Treat this as a tracing task, not a generation task. The input image is the absolute ground truth for ALL geometry, position, scale, and density distribution. Your only job is to trace the input.\n"
        "The output must align PIXEL-FOR-PIXEL with the input image. Every visible feature of the named soft element(s) must be at its EXACT pixel position from the input.\n"
        "Do NOT move, warp, scale, rotate, shift, or reposition any element by even one pixel.\n"
        "Do NOT regenerate, redraw, reinterpret, or restage any element.\n"
        "OUTPUT FORMAT (non-negotiable):\n"
        "This is a GRAYSCALE DENSITY MATTE for compositing \u2014 the kind of soft alpha matte used for atmospherics (smoke, fire, fog, rain, dust, mist, vapor, splashes) and other semi-transparent VFX elements where each pixel's brightness represents how dense or opaque the named element is at that location.\n"
        "The output is grayscale: pure black (RGB 0,0,0), pure white (RGB 255,255,255), and the full continuous range of intermediate gray values. It is NOT binary, NOT a hard cutout, NOT an artistic stylization, NOT a line drawing, NOT a comic-book rendering.\n"
        "The matte is monochromatic \u2014 the subject's color, texture, and visual appearance must NOT be visible in the output, only its density distribution as gray values.\n"
        "Render the matte at FULL native resolution of the input image with smooth anti-aliased gradients between density levels.\n"
        "DENSITY MAPPING:\n"
        "- Pure white: where the named element(s) are at maximum density / fully opaque in the input image\n"
        "- Pure black: where the named element(s) are completely absent\n"
        "- Intermediate gray: scales linearly with the local density of the named element(s) \u2014 denser regions are lighter gray approaching white; wispier, sparser, more transparent regions are darker gray approaching black\n"
        "- Edges and boundaries: smooth gradient falloff matching the natural softness of the named element(s) in the input\n"
        "INCLUDE in the matte (these get gray-to-white values based on their density in the input):\n"
        "- The named element(s), at their ACTUAL pixel positions and density distribution from the input\n"
        "EXCLUDE from the matte \u2014 these MUST be PURE BLACK regardless of where they are or what they overlap:\n"
        "- Solid physical objects (people, props, vehicles, set pieces, sets, environments) unless explicitly named\n"
        "- Background, sky, ground, walls, surfaces unless explicitly named\n"
        "- Any element not in the named list\n"
        "If a solid object overlaps or is partially occluded by the named soft element, the solid object stays black; only the soft element contributes gray/white density on top of it in the matte.\n"
        "CRITICAL RULES:\n"
        "Pixel-accurate registration with the input image is mandatory. Output dimensions and density positions must match the input exactly.\n"
        "Do NOT add new content, atmospherics, or VFX not present in the input.\n"
        "Do NOT shift the position, scale, shape, or density distribution of the named element(s).\n"
        "Do NOT introduce color, hue tints, or non-grayscale values \u2014 the output is monochromatic gray only.\n"
        "Result requirement:\n"
        "The output is a smooth grayscale density matte aligned pixel-perfectly with the input \u2014 white where the named element(s) are most dense, black where they are absent or where solid non-named objects exist, gray gradients faithfully tracking partial density. Usable directly as an alpha channel for compositing the soft element(s) into a new scene with proper transparency falloff. "
        "If the specified element(s) cannot be identified, return a fully black image of the same dimensions as the input."
    ),
}


def _comp_fallback_prompt(key: str) -> str:
    return _COMP_ACTIONS[key]["fallback"] + "\n" + _COMP_BODIES[key]


# ---------------------------------------------------------------------------
# Full preset catalog
# ---------------------------------------------------------------------------
PRESET_CATEGORIES = {
    # ---- Style (18) ----
    "style": [
        ("hand_drawn_classic", "Hand-Drawn Classic",
         "Restyle this image as a hand-drawn animated scene with soft watercolor backgrounds, expressive flowing outlines, painterly surface textures, and the charm of golden-age 2D animation"),
        ("3d_animated", "3D Animated",
         "Restyle this image in a modern 3D animated feature style with smooth stylized forms, soft subsurface skin shading, vivid saturated colors, and cinematic character-focused lighting"),
        ("japanese_fantasy_anime", "Japanese Fantasy Anime",
         "Restyle this image as a lush hand-painted Japanese fantasy anime scene with richly detailed natural environments, soft atmospheric depth, translucent light filtering through foliage, and a warm luminous storybook quality"),
        ("cel_shaded_anime", "Cel-Shaded Anime",
         "Restyle this image in a clean cel-shaded anime style with bold outlines, flat color fills, crisp shadow shapes, and vivid high-contrast animation styling"),
        ("film_noir", "Film Noir",
         "Restyle this image as classic film noir with stark black-and-white contrast, dramatic chiaroscuro lighting, deep oppressive shadows, hard-edged rim light, and moody atmospheric grain"),
        ("aaa_game", "AAA Game",
         "Restyle this image as a photorealistic AAA game scene with high-fidelity materials, realistic surface detail, volumetric lighting, cinematic depth, and polished next-generation rendering"),
        ("blockbuster_cinematic", "Blockbuster Cinematic",
         "Restyle this image with a blockbuster cinematic look featuring anamorphic lens character, dramatic teal-and-orange grading, shallow depth of field, lens flares, and epic large-scale lighting"),
        ("indie_film", "Indie Film",
         "Restyle this image with an indie film aesthetic featuring naturalistic lighting, muted slightly desaturated color, subtle film grain, intimate focus, and a grounded lived-in mood"),
        ("tv_sitcom", "TV Sitcom",
         "Restyle this image as a multi-camera TV sitcom scene with bright even studio lighting, warm neutral color balance, sharp focus throughout, and a clean polished broadcast look"),
        ("oil_painting", "Oil Painting",
         "Restyle this image as a classical oil painting with visible brushwork, rich impasto texture, layered glazing, deep tonal contrast, and dramatic old-master lighting"),
        ("watercolor", "Watercolor",
         "Restyle this image as a delicate watercolor painting with transparent washes, wet-on-wet blending, visible paper texture, soft color transitions, and gently feathered edges"),
        ("comic_book", "Comic Book",
         "Restyle this image as a comic book illustration with bold ink outlines, halftone shading, strong graphic color, punchy contrast, and dynamic printed-comic energy"),
        ("pencil_sketch", "Pencil Sketch",
         "Restyle this image as a detailed pencil sketch with fine cross-hatching, soft graphite shading, subtle tonal gradation, and the textured surface of drawing paper"),
        ("retro_pixel", "Retro Pixel Art",
         "Restyle this image as retro pixel art with a limited color palette, visible pixel structure, dithering patterns, simplified forms, and the nostalgic look of 8-bit and 16-bit graphics"),
        ("cyberpunk", "Cyberpunk",
         "Restyle this image with a cyberpunk aesthetic featuring dark moody shadows, vivid neon magenta and cyan accent lighting, holographic overlays, glossy reflective surfaces, and a high-tech dystopian atmosphere"),
        ("steampunk", "Steampunk",
         "Restyle this image with a steampunk aesthetic featuring brass and copper materials, warm sepia tones, Victorian industrial design, mechanical detailing, and soft gaslit ambience"),
        ("pop_art", "Pop Art",
         "Restyle this image as bold pop art with bright flat colors, thick graphic outlines, halftone dot patterns, strong contrast, and playful poster-like visual impact"),
        ("art_nouveau", "Art Nouveau",
         "Restyle this image in an Art Nouveau style with flowing organic curves, elegant ornamental design, floral motifs, soft pastel color, and refined decorative linework"),
    ],

    # ---- Weather (8) ----
    "weather": [
        ("sunny_clear", "Sunny & Clear",
         "Change the weather to a bright clear sunny day with blue skies, crisp shadows, high visibility, and warm natural sunlight"),
        ("overcast", "Overcast",
         "Change the weather to an overcast day with a soft gray sky, diffused light, muted colors, and a calm evenly lit atmosphere"),
        ("rainy", "Rainy",
         "Change the weather to rain with visible falling raindrops, wet reflective surfaces, puddles, softened contrast, and a cool overcast atmosphere"),
        ("thunderstorm", "Thunderstorm",
         "Change the weather to a heavy thunderstorm with dark storm clouds, flashes of lightning, intense rainfall, turbulent skies, and dramatic storm energy"),
        ("snowy", "Snowy",
         "Change the weather to snowfall with drifting snowflakes, snow accumulation on surfaces, softened ambience, and a cold wintry atmosphere"),
        ("foggy", "Foggy / Misty",
         "Change the weather to dense fog or mist with reduced visibility, soft atmospheric diffusion, muted contrast, and an ethereal atmospheric veil"),
        ("windy", "Windy",
         "Change the weather to strong wind with blowing leaves and debris, swaying foliage, windswept hair or clothing where visible, and fast-moving turbulent clouds"),
        ("hazy_humid", "Hazy / Humid",
         "Change the weather to a hazy humid day with warm air softness, gentle light scatter, slight atmospheric diffusion, and an oppressive summer heat feel"),
    ],

    # ---- Seasons (4) ----
    "seasons": [
        ("spring", "Spring",
         "Change this scene to spring with fresh green growth, blooming flowers, blossoms, soft warm daylight, and a renewed lively atmosphere"),
        ("summer", "Summer",
         "Change this scene to peak summer with lush deep green vegetation, bright intense sunlight, vivid saturated color, and subtle heat shimmer"),
        ("autumn", "Autumn",
         "Change this scene to autumn with orange, red, and gold foliage, scattered fallen leaves, warm angled sunlight, and a cozy harvest-season atmosphere"),
        ("winter", "Winter",
         "Change this scene to winter with bare branches, frost-covered surfaces, cold blue-white light, and a crisp frozen seasonal mood"),
    ],

    # ---- Time of Day (8) ----
    "time_of_day": [
        ("dawn", "Dawn",
         "Change the time of day to early dawn with soft pink and lavender sky gradients, first light on the horizon, long quiet shadows, and a calm waking atmosphere"),
        ("morning", "Morning",
         "Change the time of day to morning with warm fresh sunlight, clear air, gentle shadows, and bright uplifting natural light"),
        ("golden_hour", "Golden Hour",
         "Change the time of day to golden hour with rich amber sunlight, long dramatic shadows, glowing edge light, and a warm cinematic atmosphere"),
        ("midday", "Midday",
         "Change the time of day to bright midday with overhead sun, short defined shadows, strong direct light, and intense daylight clarity"),
        ("blue_hour", "Blue Hour",
         "Change the time of day to blue hour with deep cobalt sky tones, cool ambient twilight, softly emerging practical lights, and a serene transitional mood"),
        ("dusk", "Dusk",
         "Change the time of day to dusk with warm sunset color on the horizon, deepening purples overhead, silhouetted forms, and a fading evening glow"),
        ("night", "Night",
         "Change the time of day to night with a dark sky, moonlight or ambient night illumination, deep shadows, and pools of warm artificial light where appropriate"),
        ("starry_night", "Starry Night",
         "Change the time of day to a clear starry night with a vast star-filled sky, subtle moonlit ambience, deep darkness, and a calm expansive nighttime atmosphere"),
    ],

    # ---- Camera (8) ----
    "camera": [
        ("shallow_dof", "Shallow Depth of Field",
         "Apply a shallow depth of field with the main subject in sharp focus and a softly blurred background with smooth creamy bokeh"),
        ("tilt_shift", "Tilt Shift",
         "Apply a tilt-shift miniature look with selective focus bands, compressed scale perception, and saturated toy-like visual character"),
        ("wide_angle", "Wide Angle",
         "Apply a wide-angle lens look with expanded perspective, subtle barrel distortion, and exaggerated spatial depth"),
        ("macro_closeup", "Macro Close-Up",
         "Apply a macro close-up look with extreme detail, very shallow focus, and strong emphasis on fine surface texture"),
        ("birds_eye", "Bird's Eye View",
         "Reframe this image as an overhead bird's-eye view, emphasizing layout, geometry, spatial patterns, and a top-down perspective"),
        ("dutch_angle", "Dutch Angle",
         "Apply a Dutch angle with a deliberate camera tilt, creating diagonal framing, psychological unease, and dynamic visual tension"),
        ("long_exposure", "Long Exposure",
         "Apply a long-exposure photography effect with motion blur on moving elements, smooth flowing motion, light trails where appropriate, and crisp static details"),
        ("fisheye", "Fisheye",
         "Apply a fisheye lens effect with extreme wide-angle distortion, curved perspective, and a strongly warped spherical field of view"),
    ],

    # ---- Lighting (8) ----
    "lighting": [
        ("dramatic_side", "Dramatic Side Light",
         "Apply dramatic side lighting with a strong directional source from one side, sculpted highlights, deep shadow contrast, and pronounced dimensional form"),
        ("rim_backlight", "Rim / Backlight",
         "Apply rim and backlighting with glowing edge highlights, bright rear illumination, strong subject separation, and a dramatic luminous outline"),
        ("soft_diffused", "Soft Diffused",
         "Apply soft diffused lighting with gentle shadow falloff, even illumination, flattering contrast, and a clean polished appearance"),
        ("neon_glow", "Neon Glow",
         "Apply vibrant neon lighting with glowing magenta, cyan, and purple light sources, colorful reflections, and saturated luminous ambience"),
        ("candlelight", "Candlelight",
         "Apply warm candlelight illumination with soft flickering orange glow, intimate shadowing, gentle contrast, and a cozy low-light atmosphere"),
        ("studio_three_point", "Studio Three-Point",
         "Apply professional three-point studio lighting with balanced key, fill, and back light for clean controlled illumination and clear subject separation"),
        ("volumetric_rays", "Volumetric Rays",
         "Apply volumetric light rays with visible shafts of light, atmospheric particles, depth through haze, and dramatic illuminated atmosphere"),
        ("harsh_flash", "Harsh Flash",
         "Apply a direct harsh flash look with frontal lighting, sharp cast shadows, strong specular highlights, and a stark paparazzi-style photographic effect"),
    ],

    # ---- Clean / Artifacts (8) ----
    "clean": [
        ("color_correction", "Color Correction",
         "Apply professional color correction with balanced exposure, corrected color cast, natural-looking contrast, and clean accurate color throughout the image"),
        ("gamma_correction", "Gamma Correction",
         "Adjust gamma to improve midtone brightness, reveal detail in the middle tonal range, and enhance perceptual contrast without blowing highlights or crushing shadows"),
        ("fix_artifacts", "Fix Artifacts",
         "Remove visual artifacts such as compression blocking, banding, ringing, and digital distortion while preserving true image detail and edge integrity"),
        ("remove_noise", "Remove Noise",
         "Remove visible noise and grain while preserving fine detail, natural texture, and clean edge definition"),
        ("sharpen", "Sharpen",
         "Sharpen the image to enhance fine detail and edge clarity without introducing halos, ringing, or oversharpened artifacts"),
        ("fix_white_balance", "Fix White Balance",
         "Correct the white balance to remove unwanted color casts and restore neutral whites and believable scene color"),
        ("fix_exposure", "Fix Exposure",
         "Recover blown highlights and crushed shadows, restoring detail at both tonal extremes while maintaining natural midtone brightness"),
        ("reduce_chromatic_aberration", "Fix Chromatic Aberration",
         "Remove chromatic aberration and color fringing along high-contrast edges for cleaner optical accuracy throughout the image"),
    ],

    # ---- AI Corrections (8) ----
    "ai_corrections": [
        ("fix_face", "Fix Face Distortion",
         "Correct facial distortions in this AI-generated image.\n"
         "STRICT LOCKS (non-negotiable):\n"
         "Do NOT change the person's identity, expression, or apparent age.\n"
         "Do NOT alter the image composition, background, or non-facial elements.\n"
         "Do NOT change the overall color grade, lighting, or artistic style.\n"
         "Scope of work:\n"
         "Identify and correct facial generation artifacts, including:\n"
         "- asymmetrical or misaligned facial features (eyes, nose, mouth, ears)\n"
         "- warped or melted facial geometry\n"
         "- unnatural facial proportions (too wide, too narrow, misshapen)\n"
         "- duplicated or blended facial features\n"
         "- uncanny valley smoothness or plasticity in facial structure\n"
         "Apply corrections that restore natural human facial anatomy while preserving the intended appearance.\n"
         "CRITICAL RULES:\n"
         "All edits must be confined to the face and immediate surrounding area.\n"
         "Do NOT alter clothing, body, hands, or background.\n"
         "Do NOT change hairstyle, hair color, or facial hair.\n"
         "Preserve the person's apparent identity, gender, ethnicity, and age.\n"
         "Result requirement:\n"
         "The output should have natural, anatomically correct facial features that look like a real photograph. If no facial distortions are detected, return the image unchanged."),
        ("fix_eyes", "Fix Eyes",
         "Correct eye-related defects in this AI-generated image.\n"
         "STRICT LOCKS (non-negotiable):\n"
         "Do NOT change the person's identity, expression, or eye color.\n"
         "Do NOT alter the image composition, background, or non-eye elements.\n"
         "Do NOT change the overall color grade, lighting, or artistic style.\n"
         "Scope of work:\n"
         "Identify and correct eye generation artifacts, including:\n"
         "- crossed eyes or misaligned gaze direction\n"
         "- pupils pointing in different directions\n"
         "- uneven eye size or shape between left and right\n"
         "- deformed eyelids, missing eyelashes, or distorted eye sockets\n"
         "- unnatural iris patterns or reflections\n"
         "- extra or merged eyes\n"
         "Apply corrections that restore natural, aligned eye anatomy with consistent gaze direction.\n"
         "CRITICAL RULES:\n"
         "All edits must be confined to the eye region (eyes, eyelids, eyebrows).\n"
         "Do NOT alter facial structure, nose, mouth, or other features.\n"
         "Preserve the person's eye color, apparent gaze intent, and expression.\n"
         "Both eyes should appear to look in a consistent, natural direction.\n"
         "Result requirement:\n"
         "The output should have natural, symmetrical eyes with aligned gaze that look like a real photograph. If no eye defects are detected, return the image unchanged."),
        ("fix_hands", "Fix Hands & Fingers",
         "Correct hand and finger anatomy errors in this AI-generated image.\n"
         "STRICT LOCKS (non-negotiable):\n"
         "Do NOT change the hand pose, gesture intent, or position in the scene.\n"
         "Do NOT alter the image composition, background, or non-hand elements.\n"
         "Do NOT change the overall color grade, lighting, or artistic style.\n"
         "Scope of work:\n"
         "Identify and correct hand generation artifacts, including:\n"
         "- wrong finger count (more or fewer than five per hand)\n"
         "- fused, split, or duplicated fingers\n"
         "- fingers bending in anatomically impossible directions\n"
         "- twisted or malformed joints and knuckles\n"
         "- unnatural finger length ratios or hand proportions\n"
         "- missing or extra thumbs\n"
         "Apply corrections that restore natural human hand anatomy with five fingers per hand and correct joint articulation.\n"
         "CRITICAL RULES:\n"
         "All edits must be confined to the hands, fingers, and wrists.\n"
         "Do NOT alter arms, clothing, or any other body parts.\n"
         "Preserve the intended hand pose and gesture as closely as possible.\n"
         "Each hand must have exactly five fingers with natural proportions.\n"
         "Result requirement:\n"
         "The output should have anatomically correct hands with proper finger count, natural joint angles, and realistic proportions. If no hand defects are detected, return the image unchanged."),
        ("fix_teeth_mouth", "Fix Teeth & Mouth",
         "Correct teeth and mouth defects in this AI-generated image.\n"
         "STRICT LOCKS (non-negotiable):\n"
         "Do NOT change the person's expression, smile intent, or facial identity.\n"
         "Do NOT alter the image composition, background, or non-mouth elements.\n"
         "Do NOT change the overall color grade, lighting, or artistic style.\n"
         "Scope of work:\n"
         "Identify and correct mouth and dental generation artifacts, including:\n"
         "- too many or too few teeth\n"
         "- misaligned, overlapping, or impossibly arranged teeth\n"
         "- distorted jaw shape or asymmetric mouth\n"
         "- unnatural gum exposure or gum texture\n"
         "- blurred or melted lip boundaries\n"
         "- teeth that appear fused, floating, or duplicated\n"
         "Apply corrections that restore natural dental and mouth anatomy consistent with the person's expression.\n"
         "CRITICAL RULES:\n"
         "All edits must be confined to the mouth, lips, teeth, and jaw region.\n"
         "Do NOT alter the rest of the face, nose, eyes, or other features.\n"
         "Preserve the intended expression (smile, open mouth, etc.).\n"
         "Teeth should appear natural and properly aligned.\n"
         "Result requirement:\n"
         "The output should have natural-looking teeth and mouth anatomy appropriate to the expression. If no mouth defects are detected, return the image unchanged."),
        ("fix_body_anatomy", "Fix Body Anatomy",
         "Correct body anatomy errors in this AI-generated image.\n"
         "STRICT LOCKS (non-negotiable):\n"
         "Do NOT change the person's pose intent, clothing, or position in the scene.\n"
         "Do NOT alter the image composition, background, or facial features.\n"
         "Do NOT change the overall color grade, lighting, or artistic style.\n"
         "Scope of work:\n"
         "Identify and correct body generation artifacts, including:\n"
         "- limbs bending in anatomically impossible directions\n"
         "- extra or missing limbs\n"
         "- unnatural body proportions (too long, too short, mismatched segments)\n"
         "- twisted torso or impossible body contortions\n"
         "- joints appearing in wrong locations\n"
         "- body parts clipping through each other or through clothing\n"
         "Apply corrections that restore natural human body anatomy while preserving the intended pose.\n"
         "CRITICAL RULES:\n"
         "All edits must be confined to the body anatomy regions showing defects.\n"
         "Do NOT alter the face, background, or overall scene.\n"
         "Preserve clothing, accessories, and the intended pose as closely as possible.\n"
         "Body proportions should follow natural human anatomy.\n"
         "Result requirement:\n"
         "The output should have anatomically plausible body proportions and joint articulation. If no body anatomy errors are detected, return the image unchanged."),
        ("fix_skin_texture", "Fix Skin Texture",
         "Correct unnatural skin texture in this AI-generated image.\n"
         "STRICT LOCKS (non-negotiable):\n"
         "Do NOT change the person's identity, features, or skin tone.\n"
         "Do NOT alter the image composition, background, or non-skin elements.\n"
         "Do NOT change the overall color grade, lighting, or artistic style.\n"
         "Scope of work:\n"
         "Identify and correct skin texture generation artifacts, including:\n"
         "- waxy or plastic-looking skin surface\n"
         "- uncanny valley over-smoothing with no visible pores or texture\n"
         "- inconsistent skin texture between adjacent areas\n"
         "- artificial-looking skin sheen or reflectivity\n"
         "- patchy or blotchy texture transitions\n"
         "- skin that appears painted or airbrushed rather than photographic\n"
         "Apply corrections that restore natural photographic skin texture with appropriate pore detail, subtle imperfections, and realistic surface quality.\n"
         "CRITICAL RULES:\n"
         "All edits must be confined to visible skin surfaces.\n"
         "Do NOT alter clothing, hair, background, or facial features/structure.\n"
         "Preserve the person's skin tone, freckles, and natural markings.\n"
         "Do NOT add blemishes \u2014 aim for natural but clean skin texture.\n"
         "Result requirement:\n"
         "The output should have realistic photographic skin texture that avoids the plastic or waxy AI look. If no skin texture issues are detected, return the image unchanged."),
        ("fix_hair", "Fix Hair",
         "Correct hair generation artifacts in this AI-generated image.\n"
         "STRICT LOCKS (non-negotiable):\n"
         "Do NOT change the hairstyle, hair color, or hair length.\n"
         "Do NOT alter the image composition, background, or non-hair elements.\n"
         "Do NOT change the overall color grade, lighting, or artistic style.\n"
         "Scope of work:\n"
         "Identify and correct hair generation artifacts, including:\n"
         "- floating or disconnected hair strands\n"
         "- hair clumps that merge into solid masses without strand detail\n"
         "- unnatural growth direction or impossible hair physics\n"
         "- hair texture that abruptly changes between regions\n"
         "- hair clipping through face, ears, or clothing\n"
         "- bald patches or missing hair in areas that should have coverage\n"
         "Apply corrections that restore natural hair texture, strand detail, and consistent growth patterns.\n"
         "CRITICAL RULES:\n"
         "All edits must be confined to the hair and immediate hairline region.\n"
         "Do NOT alter the face, ears, clothing, or background.\n"
         "Preserve the intended hairstyle, color, and overall silhouette.\n"
         "Hair should show natural strand variation and consistent texture.\n"
         "Result requirement:\n"
         "The output should have natural-looking hair with consistent texture and realistic strand detail. If no hair artifacts are detected, return the image unchanged."),
        ("fix_background_coherence", "Fix Background Coherence",
         "Correct background coherence errors in this AI-generated image.\n"
         "STRICT LOCKS (non-negotiable):\n"
         "Do NOT change the foreground subjects, people, or main elements.\n"
         "Do NOT alter the overall color grade, lighting direction, or artistic style.\n"
         "Do NOT move, resize, or reposition any foreground objects.\n"
         "Scope of work:\n"
         "Identify and correct background generation artifacts, including:\n"
         "- repeating or tiling patterns that break spatial logic\n"
         "- impossible geometry (stairs to nowhere, walls that don't connect, floating structures)\n"
         "- perspective inconsistencies (vanishing points that don't align)\n"
         "- objects that abruptly cut off, merge, or duplicate\n"
         "- text or signage that appears garbled or nonsensical\n"
         "- seamless blending artifacts where background regions were stitched together\n"
         "Apply corrections that restore spatial coherence and logical consistency in the background.\n"
         "CRITICAL RULES:\n"
         "All edits must be confined to background regions showing coherence issues.\n"
         "Do NOT alter foreground subjects, people, or primary objects.\n"
         "Preserve the intended scene type, setting, and atmosphere.\n"
         "Corrections should make the background appear naturally photographed.\n"
         "Result requirement:\n"
         "The output should have a spatially coherent background with consistent perspective and no impossible geometry. If no background coherence issues are detected, return the image unchanged."),
    ],

    # ---- Compositing (8) ----
    "compositing": [
        ("harmonize_lighting", "Harmonize Lighting", _comp_fallback_prompt("harmonize_lighting")),
        ("match_shadows", "Match Shadows", _comp_fallback_prompt("match_shadows")),
        ("blend_edges", "Blend Edges", _comp_fallback_prompt("blend_edges")),
        ("color_harmonize", "Color Harmonize", _comp_fallback_prompt("color_harmonize")),
        ("fix_reflections", "Fix Reflections", _comp_fallback_prompt("fix_reflections")),
        ("integrate_elements", "Integrate Elements", _comp_fallback_prompt("integrate_elements")),
        ("match_ambient", "Match Ambient Light", _comp_fallback_prompt("match_ambient")),
        ("create_mask", "Create Mask", _comp_fallback_prompt("create_mask")),
        ("create_soft_mask", "Create Soft Mask", _comp_fallback_prompt("create_soft_mask")),
        ("remove_spill", "Remove Greenscreen Spill", _comp_fallback_prompt("remove_spill")),
        ("depth_consistency", "Depth Consistency",
         "Perform a targeted depth consistency pass on this composite image.\n"
         "STRICT LOCKS (non-negotiable):\n"
         "The image must remain pixel-identical in layout and composition.\n"
         "Do NOT move, warp, scale, rotate, or reshape any objects.\n"
         "Do NOT change global lighting, shadows, or color balance.\n"
         "Do NOT modify the overall image appearance or grade.\n"
         "Scope of work (local fixes only):\n"
         "Identify composited elements whose depth cues are inconsistent with their position in the scene, including:\n"
         "- element too sharp or too soft for its apparent distance from the camera\n"
         "- missing atmospheric haze or aerial perspective for distant elements\n"
         "- contrast and saturation not reduced appropriately for depth\n"
         "- scale inconsistency suggesting wrong depth placement\n"
         "Apply ONLY minimal, localized corrections:\n"
         "- apply subtle focus softening to elements that should appear farther from the camera\n"
         "- add gentle atmospheric haze or desaturation to distant composited elements\n"
         "- reduce contrast slightly on far elements to match the scene's depth falloff\n"
         "- sharpen near elements that appear too soft for their position\n"
         "CRITICAL RULES:\n"
         "All edits must be confined to composited elements that show depth inconsistency.\n"
         "Do NOT apply depth-of-field blur to the entire image.\n"
         "Do NOT change the background's existing depth characteristics.\n"
         "Do NOT modify the position or scale of any objects.\n"
         "Result requirement:\n"
         "The output should have consistent depth cues across all elements matching their spatial position in the scene. If no depth issues are detected, return the image unchanged."),
    ],

    # ---- Object Edits (13) ----
    "object_edits": [
        ("delete_object", "Delete Object", _obj_fallback_prompt("delete_object")),
        ("replace_object", "Replace Object", _obj_fallback_prompt("replace_object")),
        ("isolate_object", "Isolate Object", _obj_fallback_prompt("isolate_object")),
        ("change_object_color", "Change Object Color", _obj_fallback_prompt("change_object_color")),
        ("change_object_material", "Change Object Material", _obj_fallback_prompt("change_object_material")),
        ("add_vegetation", "Add Vegetation",
         "Add natural-looking vegetation appropriate to the scene, including plants, grass, shrubs, trees, and foliage where visually suitable"),
        ("add_people", "Add People",
         "Add natural-looking people appropriate to the scene's setting, scale, perspective, and context"),
        ("add_clouds", "Add Clouds",
         "Add natural cloud formations appropriate to the sky, such as wispy cirrus or soft cumulus, to enrich the atmosphere and sky detail"),
        ("remove_people", "Remove People",
         "Remove all visible people from the scene and naturally reconstruct the background and surrounding environment behind them"),
        ("add_water_reflection", "Add Water Reflection",
         "Add a water surface with realistic reflections, subtle ripples, and natural reflective behavior integrated into the scene"),
        ("age_weathering", "Add Aging / Weathering",
         "Add realistic aging and weathering with patina, rust, wear, faded paint, surface erosion, and the visual character of time"),
        ("modernize", "Modernize",
         "Modernize the scene by replacing dated visual elements with contemporary equivalents, including clean surfaces, modern materials, updated design details, and current-era objects where appropriate"),
        ("add_text_overlay", "Add Text Overlay",
         "Add a stylized typographic or graphic overlay integrated into the composition, suited to the image's mood, visual balance, and overall design"),
    ],
}

# Flat lookup: preset_token -> prompt text
ALL_PRESETS = {}
for _cat_presets in PRESET_CATEGORIES.values():
    for _token, _label, _prompt in _cat_presets:
        ALL_PRESETS[_token] = _prompt


def _preset_labels(category: str) -> List[str]:
    """Return the display labels (with token prefix) for a category's preset dropdown."""
    entries = PRESET_CATEGORIES.get(category, [])
    return [label for (_token, label, _prompt) in entries]


def _preset_token_by_label(category: str, label: str) -> str:
    for (tok, lab, _prompt) in PRESET_CATEGORIES.get(category, []):
        if lab == label:
            return tok
    return ""


# ---------------------------------------------------------------------------
# Target collection + prompt assembly
# ---------------------------------------------------------------------------

def _collect_object_targets(node: Any) -> List[Tuple[str, str]]:
    """Read fixed 5 object target slots, skipping empty ones."""
    targets: List[Tuple[str, str]] = []
    for i in range(1, MAX_OBJECT_SLOTS + 1):
        name = (_opt_parm_str(node, f"obj_target_name_{i}") or "").strip()
        mod = (_opt_parm_str(node, f"obj_target_mod_{i}") or "").strip()
        if name:
            targets.append((name, mod))
    return targets


def _join_natural(items: List[str]) -> Optional[str]:
    """Join with natural English: 'a', 'a and b', 'a, b, and c'."""
    items = [s for s in items if s]
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _possessive(name: str) -> str:
    """English possessive: cat -> cat's, cats -> cats'."""
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _format_object_phrase(targets: List[Tuple[str, str]]) -> Optional[str]:
    return _join_natural([name for name, _ in targets if name])


def _format_object_possessive(targets: List[Tuple[str, str]]) -> Optional[str]:
    return _join_natural([_possessive(name) for name, _ in targets if name])


def _format_modifier_phrase(targets: List[Tuple[str, str]]) -> Optional[str]:
    return _join_natural([mod for _, mod in targets if mod])


# Phrase tables: each (needle, possessive?) pair is a placeholder we look for in
# body text. Possessive needles are replaced with the possessive form of the
# object phrase; non-possessive with the bare phrase. Order matters — longer
# needles must come first so they match before any shorter substring of them.
_OBJ_POSSESSIVE_NEEDLES = (
    "the specified object(s)'",
    "the specified objects'",
    "the specified object's",
    "specified object(s)'",
    "specified objects'",
    "specified object's",
    "the specified element(s)'",
    "the specified elements'",
    "the specified element's",
    "specified element(s)'",
    "specified elements'",
    "specified element's",
)
_OBJ_NEEDLES = (
    "the specified object(s)",
    "the specified objects",
    "the specified object",
    "The specified object(s)",
    "The specified objects",
    "The specified object",
    "the isolated object(s)",
    "the isolated objects",
    "the isolated object",
    "specified object(s)",
    "specified objects",
    "specified object",
    "the specified element(s)",
    "the specified elements",
    "the specified element",
    "The specified element(s)",
    "The specified elements",
    "The specified element",
    "specified element(s)",
    "specified elements",
    "specified element",
)

_MOD_NEEDLES_BY_PRESET = {
    "replace_object": (
        "the described replacement(s)",
        "the described replacement",
        "the replacement object(s)",
        "the replacement(s)",
        "the replacement",
    ),
    "change_object_color": (
        "the described target color(s)",
        "the described target color",
        "the target color(s)",
        "the new color(s)",
        "the new color",
        "the target color",
    ),
    "change_object_material": (
        "the described target material(s)",
        "the described target material",
        "the target material(s)",
        "the new material(s)",
        "the new material",
        "the target material",
    ),
}


def _substitute_body(body: str, preset_key: str, targets: List[Tuple[str, str]]) -> str:
    """Replace generic 'specified object(s)' / replacement / target color phrases in
    the body text with the user's actual object names and modifiers.

    Without this, the long body keeps saying 'the specified object(s)' even after
    the user has named a target — the model gets ambiguous targeting and tends to
    return generic results regardless of the named object.
    """
    obj_phrase = _format_object_phrase(targets)
    obj_poss = _format_object_possessive(targets)
    mod_phrase = _format_modifier_phrase(targets)

    if obj_phrase:
        for needle in _OBJ_POSSESSIVE_NEEDLES:
            body = body.replace(needle, obj_poss or obj_phrase)
        for needle in _OBJ_NEEDLES:
            body = body.replace(needle, obj_phrase)

    if mod_phrase:
        for needle in _MOD_NEEDLES_BY_PRESET.get(preset_key, ()):
            body = body.replace(needle, mod_phrase)

    return body


def _build_object_prompt(preset_key: str, targets: List[Tuple[str, str]]) -> Optional[str]:
    if preset_key not in TARGETED_OBJECT_PRESETS or not targets:
        return None

    actions = _OBJ_ACTIONS[preset_key]
    body = _OBJ_BODIES[preset_key]

    if len(targets) == 1:
        obj1, mod1 = targets[0]
        action = actions["single"].replace("{obj1}", obj1)
        if mod1:
            action = action.replace("{mod1}", mod1)
        elif preset_key not in _PRESETS_WITHOUT_MOD:
            # replace/color/material need a modifier; isolate/delete don't.
            return None
    else:
        if preset_key in _PRESETS_WITHOUT_MOD:
            obj_list = ", ".join(name for name, _ in targets)
            action = actions["multi"].replace("{obj_list}", obj_list)
        else:
            pairs = []
            for name, mod in targets:
                if mod:
                    pairs.append(f"{name} to {mod}")
                else:
                    pairs.append(name)
            obj_mod_list = "; ".join(pairs)
            action = actions["multi"].replace("{obj_mod_list}", obj_mod_list)

    body = _substitute_body(body, preset_key, targets)
    return action + "\n" + body


def _collect_comp_elements(node: Any) -> List[str]:
    elements: List[str] = []
    for i in range(1, MAX_COMP_SLOTS + 1):
        desc = (_opt_parm_str(node, f"comp_element_desc_{i}") or "").strip()
        if desc:
            elements.append(desc)
    return elements


def _build_comp_prompt(preset_key: str, elements: List[str]) -> Optional[str]:
    if preset_key not in TARGETED_COMP_PRESETS or not elements:
        return None

    actions = _COMP_ACTIONS[preset_key]
    body = _COMP_BODIES[preset_key]

    if len(elements) == 1:
        action = actions["single"].replace("{elem1}", elements[0])
    else:
        elem_list = _join_natural(elements) or ", ".join(elements)
        action = actions["multi"].replace("{elem_list}", elem_list)

    # Substitute generic "specified element(s)" / "specified object(s)" patterns
    # in the body too. Existing comp presets (harmonize_lighting etc.) don't use
    # these phrases, so this is a no-op for them; create_mask relies on it.
    body = _substitute_body(body, preset_key, [(e, "") for e in elements])
    return action + "\n" + body


def _user_has_edited(node: Any, preset_key: str) -> bool:
    """Return True if the prompt knob differs from last auto-generated prompt and fallback."""
    prompt_knob = node.knob("prompt")
    auto_knob = node.knob("_auto_prompt")
    if prompt_knob is None or auto_knob is None:
        return False
    current = (prompt_knob.value() or "").strip()
    auto = (auto_knob.value() or "").strip()
    fallback = ALL_PRESETS.get(preset_key, "").strip()
    return bool(current and current != auto and current != fallback)


def _sync_object_prompt(node: Any, preset_key: str) -> None:
    if _user_has_edited(node, preset_key):
        return
    targets = _collect_object_targets(node)
    new_prompt = _build_object_prompt(preset_key, targets)
    if new_prompt is None:
        new_prompt = ALL_PRESETS.get(preset_key, "")
    _set_prompt(node, new_prompt)


def _sync_comp_prompt(node: Any, preset_key: str) -> None:
    if _user_has_edited(node, preset_key):
        return
    elements = _collect_comp_elements(node)
    new_prompt = _build_comp_prompt(preset_key, elements)
    if new_prompt is None:
        new_prompt = ALL_PRESETS.get(preset_key, "")
    _set_prompt(node, new_prompt)


def _set_prompt(node: Any, text: str) -> None:
    prompt_knob = node.knob("prompt")
    auto_knob = node.knob("_auto_prompt")
    if prompt_knob is not None:
        try:
            prompt_knob.setValue(text or "")
        except Exception:
            pass
    if auto_knob is not None:
        try:
            auto_knob.setValue(text or "")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Knob visibility management
# ---------------------------------------------------------------------------

# Reverse lookup: display label -> category token
_LABEL_TO_CATEGORY = {label: token for token, label in CATEGORY_ORDER}


def _current_category(node: Any) -> str:
    """Return the active category token (e.g. 'style'). Maps display label to token."""
    label = _opt_parm_menu_str(node, "category") or "Custom"
    return _LABEL_TO_CATEGORY.get(label, "custom")


def _current_preset_token(node: Any, category: str) -> str:
    if category == "custom":
        return ""
    label = _opt_parm_menu_str(node, f"preset_{category}") or ""
    return _preset_token_by_label(category, label)


def _apply_visibility(node: Any) -> None:
    """Show only the active category's preset knob and the appropriate target slots."""
    category = _current_category(node)

    # Show only the active preset dropdown
    for cat_token, _cat_label in CATEGORY_ORDER:
        if cat_token == "custom":
            continue
        knob = node.knob(f"preset_{cat_token}")
        if knob is not None:
            try:
                knob.setVisible(cat_token == category)
            except Exception:
                pass

    preset_token = _current_preset_token(node, category)
    show_obj = category == "object_edits" and preset_token in TARGETED_OBJECT_PRESETS
    show_comp = category == "compositing" and preset_token in TARGETED_COMP_PRESETS

    # Object target section
    obj_header = node.knob("object_targets_header")
    if obj_header is not None:
        try:
            obj_header.setVisible(show_obj)
        except Exception:
            pass
    for i in range(1, MAX_OBJECT_SLOTS + 1):
        for suffix in ("name", "mod"):
            k = node.knob(f"obj_target_{suffix}_{i}")
            if k is not None:
                try:
                    k.setVisible(show_obj)
                except Exception:
                    pass

    # Compositing element section
    comp_header = node.knob("comp_elements_header")
    if comp_header is not None:
        try:
            comp_header.setVisible(show_comp)
        except Exception:
            pass
    for i in range(1, MAX_COMP_SLOTS + 1):
        k = node.knob(f"comp_element_desc_{i}")
        if k is not None:
            try:
                k.setVisible(show_comp)
            except Exception:
                pass


def _populate_preset_dropdowns(node: Any) -> None:
    """Populate each per-category preset Enumeration_Knob with its labels if empty."""
    for cat_token, _cat_label in CATEGORY_ORDER:
        if cat_token == "custom":
            continue
        knob = node.knob(f"preset_{cat_token}")
        if knob is None:
            continue
        try:
            current_values = knob.values()
        except Exception:
            current_values = []
        labels = _preset_labels(cat_token)
        if labels and list(current_values) != labels:
            try:
                knob.setValues(labels)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Public callbacks
# ---------------------------------------------------------------------------

def on_category_changed(node: Any) -> None:
    category = _current_category(node)
    if category == "custom":
        _set_prompt(node, "")
        _apply_visibility(node)
        return

    presets = PRESET_CATEGORIES.get(category, [])
    if not presets:
        _apply_visibility(node)
        return

    # Reset the per-category preset dropdown to index 0
    preset_knob = node.knob(f"preset_{category}")
    if preset_knob is not None:
        try:
            preset_knob.setValue(0)
        except Exception:
            pass

    first_token, _first_label, first_prompt = presets[0]
    _set_prompt(node, first_prompt)

    # Auto-prompt tracking: only mark as "auto" if the preset has targeting
    auto_knob = node.knob("_auto_prompt")
    if auto_knob is not None:
        is_targeted = (
            (category == "object_edits" and first_token in TARGETED_OBJECT_PRESETS)
            or (category == "compositing" and first_token in TARGETED_COMP_PRESETS)
        )
        try:
            auto_knob.setValue(first_prompt if is_targeted else "")
        except Exception:
            pass

    _apply_visibility(node)


def on_preset_changed(node: Any) -> None:
    category = _current_category(node)
    if category == "custom":
        return
    preset_token = _current_preset_token(node, category)
    if not preset_token:
        return

    prompt_text = ALL_PRESETS.get(preset_token, "")
    targeting_active = False

    if preset_token in TARGETED_OBJECT_PRESETS:
        targets = _collect_object_targets(node)
        injected = _build_object_prompt(preset_token, targets)
        if injected:
            prompt_text = injected
        targeting_active = True

    if preset_token in TARGETED_COMP_PRESETS:
        elements = _collect_comp_elements(node)
        injected = _build_comp_prompt(preset_token, elements)
        if injected:
            prompt_text = injected
        targeting_active = True

    _set_prompt(node, prompt_text)

    auto_knob = node.knob("_auto_prompt")
    if auto_knob is not None:
        try:
            auto_knob.setValue(prompt_text if targeting_active else "")
        except Exception:
            pass

    _apply_visibility(node)


def on_target_changed(node: Any) -> None:
    """Called when any object-target or comp-element slot changes."""
    category = _current_category(node)
    preset_token = _current_preset_token(node, category)
    if category == "object_edits" and preset_token in TARGETED_OBJECT_PRESETS:
        _sync_object_prompt(node, preset_token)
    elif category == "compositing" and preset_token in TARGETED_COMP_PRESETS:
        _sync_comp_prompt(node, preset_token)


# ---------------------------------------------------------------------------
# Knob changed dispatcher + run entry point
# ---------------------------------------------------------------------------

_OBJ_SLOT_KNOB_NAMES = {
    f"obj_target_{suffix}_{i}"
    for suffix in ("name", "mod")
    for i in range(1, MAX_OBJECT_SLOTS + 1)
}
_COMP_SLOT_KNOB_NAMES = {
    f"comp_element_desc_{i}" for i in range(1, MAX_COMP_SLOTS + 1)
}
_PRESET_KNOB_NAMES = {
    f"preset_{cat_token}" for cat_token, _lab in CATEGORY_ORDER if cat_token != "custom"
}


def on_knob_changed() -> None:
    try:
        node = nuke.thisNode()
        knob = nuke.thisKnob()
    except Exception:
        return
    if node is None or knob is None:
        return

    knob_name = knob.name()

    # Populate dropdowns and apply visibility when the panel first opens
    if knob_name == "showPanel":
        _populate_preset_dropdowns(node)
        _apply_visibility(node)
        try:
            from common import ensure_prompt_field_heights
            ensure_prompt_field_heights(node, "fibo_edit_recipes")
        except Exception:
            pass
        return

    if knob_name == "category":
        on_category_changed(node)
        return

    if knob_name in _PRESET_KNOB_NAMES:
        on_preset_changed(node)
        return

    if knob_name in _OBJ_SLOT_KNOB_NAMES or knob_name in _COMP_SLOT_KNOB_NAMES:
        on_target_changed(node)
        return

    # Delegate everything else (negative prompt toggle, preview, preset save/load, etc.)
    # to the FIBO Edit knob-changed handler by re-dispatching.
    _fibo_edit.on_knob_changed()


def on_fibo_edit_recipes(node: Any = None) -> None:
    """Entry point for the Run button. Resolves the final prompt from category/preset
    state, then delegates to fibo_edit.on_fibo_edit() for actual API execution."""
    if node is None:
        try:
            node = nuke.thisNode()
        except Exception:
            node = None
    if node is None:
        raise ValueError("Missing node")

    # Ensure the prompt knob reflects the current preset/target state before running.
    # Users may have opened the panel, picked presets, and clicked Run without
    # triggering intermediate knobChanged events.
    category = _current_category(node)
    if category != "custom":
        preset_token = _current_preset_token(node, category)
        if preset_token in TARGETED_OBJECT_PRESETS:
            _sync_object_prompt(node, preset_token)
        elif preset_token in TARGETED_COMP_PRESETS:
            _sync_comp_prompt(node, preset_token)

    _fibo_edit.on_fibo_edit(node)


def on_load_structured_prompt(node: Any = None) -> None:
    """Delegate to FIBO Edit's structured prompt loader."""
    _fibo_edit.on_load_structured_prompt(node)
