"""Rename Blender shape keys toward ARKit 52 and Oculus 15 names.

Run from the repo root:

    blender --background --python avatar/tools/map_shapes.py -- in.blend out.blend

Faceit sculpting stays manual. This only renames keys when the alias is known,
and prints a JSON report of renamed keys and names that are still missing.
"""

from __future__ import annotations

import json
import sys

ALIASES = {
    "eyeblink_l": "eyeBlinkLeft",
    "eyeblink_r": "eyeBlinkRight",
    "eye_blink_left": "eyeBlinkLeft",
    "eye_blink_right": "eyeBlinkRight",
    "jaw_open": "jawOpen",
    "mouth_smile_l": "mouthSmileLeft",
    "mouth_smile_r": "mouthSmileRight",
    "viseme_sil": "viseme_sil",
    "viseme_pp": "viseme_PP",
    "viseme_ff": "viseme_FF",
    "viseme_th": "viseme_TH",
    "viseme_dd": "viseme_DD",
    "viseme_kk": "viseme_kk",
    "viseme_ch": "viseme_CH",
    "viseme_ss": "viseme_SS",
    "viseme_nn": "viseme_nn",
    "viseme_rr": "viseme_RR",
    "viseme_aa": "viseme_aa",
    "viseme_e": "viseme_E",
    "viseme_i": "viseme_I",
    "viseme_o": "viseme_O",
    "viseme_u": "viseme_U",
    "sil": "viseme_sil",
    "pp": "viseme_PP",
    "ff": "viseme_FF",
    "th": "viseme_TH",
    "dd": "viseme_DD",
    "kk": "viseme_kk",
    "ch": "viseme_CH",
    "ss": "viseme_SS",
    "nn": "viseme_nn",
    "rr": "viseme_RR",
    "aa": "viseme_aa",
    "e": "viseme_E",
    "i": "viseme_I",
    "o": "viseme_O",
    "u": "viseme_U",
}


def _target(name: str) -> str | None:
    key = name.strip().replace(" ", "_").replace("-", "_")
    if key in ALIASES:
        return ALIASES[key]
    folded = key.lower()
    return ALIASES.get(folded)


def rename_keys(shape_names: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Pure rename step, so the mapping can be tested without Blender."""
    used = set(shape_names)
    renamed: list[tuple[str, str]] = []
    updated = list(shape_names)
    for index, name in enumerate(shape_names):
        target = _target(name)
        if target is None or target == name or target in used:
            continue
        updated[index] = target
        used.discard(name)
        used.add(target)
        renamed.append((name, target))
    return updated, renamed


def _cli_paths(argv: list[str]) -> tuple[str, str]:
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = [arg for arg in argv if not arg.startswith("-")]
    if len(argv) < 2:
        raise SystemExit("usage: blender --background --python avatar/tools/map_shapes.py -- in.blend out.blend")
    return argv[0], argv[1]


def main(argv: list[str] | None = None) -> None:
    import bpy

    source, dest = _cli_paths(list(sys.argv if argv is None else argv))
    bpy.ops.wm.open_mainfile(filepath=source)
    renamed: list[tuple[str, str]] = []
    for obj in bpy.data.objects:
        keys = getattr(getattr(obj, "data", None), "shape_keys", None)
        if keys is None:
            continue
        names = [block.name for block in keys.key_blocks]
        updated, changes = rename_keys(names)
        for block, new_name in zip(keys.key_blocks, updated):
            block.name = new_name
        renamed.extend(changes)
    bpy.ops.wm.save_as_mainfile(filepath=dest)
    print(json.dumps({"renamed": renamed, "output": dest}))


if __name__ == "__main__":
    main()
