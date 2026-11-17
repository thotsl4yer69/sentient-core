"""GLB validator and the Blender rename map."""

from __future__ import annotations

from avatar.tools.glb import (
    ARKIT_52,
    MIXAMO_BONES,
    OCULUS_15,
    TRIANGLE_MIN,
    add_zero_morph,
    build_fixture_glb,
    inspect_glb,
)
from avatar.tools.map_shapes import rename_keys


def _complete(**overrides):
    shapes = overrides.pop("shapes", ARKIT_52 + OCULUS_15)
    bones = overrides.pop("bones", MIXAMO_BONES)
    return build_fixture_glb(triangles=TRIANGLE_MIN, bones=bones, shapes=shapes, **overrides)


def test_complete_fixture_passes():
    report = inspect_glb(_complete())
    assert report["missing_bones"] == []
    assert report["missing_arkit"] == []
    assert report["missing_visemes"] == []
    assert report["triangle_ok"] is True
    assert report["ok"] is True
    assert len(ARKIT_52) == 52
    assert len(OCULUS_15) == 15


def test_validator_reports_missing_shapes_bones_and_triangle_band():
    report = inspect_glb(
        build_fixture_glb(
            triangles=1000,
            bones=("Hips",),
            shapes=("jawOpen", "viseme_aa"),
            texture=(4096, 1024),
        )
    )
    assert report["ok"] is False
    assert "Head" in report["missing_bones"]
    assert "Armature" not in report["missing_bones"]
    assert "viseme_sil" in report["missing_visemes"]
    assert "viseme_PP" in report["missing_visemes"]
    assert "mouthSmileLeft" in report["missing_arkit"]
    assert report["triangle_ok"] is False
    assert report["triangles"] == 1000
    assert report["oversized_textures"][0]["width"] == 4096


def test_zero_morph_adds_viseme_sil():
    raw = _complete(shapes=tuple(name for name in ARKIT_52 + OCULUS_15 if name != "viseme_sil"))
    before = inspect_glb(raw)
    assert before["missing_visemes"] == ["viseme_sil"]
    after = inspect_glb(add_zero_morph(raw, "viseme_sil"))
    assert after["missing_visemes"] == []


def test_shape_rename_maps_known_aliases_only():
    updated, renamed = rename_keys(["eyeBlink_L", "jaw_open", "customSculpt", "viseme_PP"])
    assert updated[0] == "eyeBlinkLeft"
    assert updated[1] == "jawOpen"
    assert updated[2] == "customSculpt"
    assert ("viseme_PP", "viseme_PP") not in renamed
    assert ("eyeBlink_L", "eyeBlinkLeft") in renamed
