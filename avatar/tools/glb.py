"""Read and build GLB files with the stdlib. No glTF library required."""

from __future__ import annotations

import json
import struct
from typing import Any

TRIANGLE_MIN = 30_000
TRIANGLE_MAX = 60_000
TEXTURE_MAX = 2048

ARKIT_52 = (
    "browDownLeft",
    "browDownRight",
    "browInnerUp",
    "browOuterUpLeft",
    "browOuterUpRight",
    "cheekPuff",
    "cheekSquintLeft",
    "cheekSquintRight",
    "eyeBlinkLeft",
    "eyeBlinkRight",
    "eyeLookDownLeft",
    "eyeLookDownRight",
    "eyeLookInLeft",
    "eyeLookInRight",
    "eyeLookOutLeft",
    "eyeLookOutRight",
    "eyeLookUpLeft",
    "eyeLookUpRight",
    "eyeSquintLeft",
    "eyeSquintRight",
    "eyeWideLeft",
    "eyeWideRight",
    "jawForward",
    "jawLeft",
    "jawOpen",
    "jawRight",
    "mouthClose",
    "mouthDimpleLeft",
    "mouthDimpleRight",
    "mouthFrownLeft",
    "mouthFrownRight",
    "mouthFunnel",
    "mouthLeft",
    "mouthLowerDownLeft",
    "mouthLowerDownRight",
    "mouthPressLeft",
    "mouthPressRight",
    "mouthPucker",
    "mouthRight",
    "mouthRollLower",
    "mouthRollUpper",
    "mouthShrugLower",
    "mouthShrugUpper",
    "mouthSmileLeft",
    "mouthSmileRight",
    "mouthStretchLeft",
    "mouthStretchRight",
    "mouthUpperUpLeft",
    "mouthUpperUpRight",
    "noseSneerLeft",
    "noseSneerRight",
    "tongueOut",
)

OCULUS_15 = (
    "viseme_sil",
    "viseme_PP",
    "viseme_FF",
    "viseme_TH",
    "viseme_DD",
    "viseme_kk",
    "viseme_CH",
    "viseme_SS",
    "viseme_nn",
    "viseme_RR",
    "viseme_aa",
    "viseme_E",
    "viseme_I",
    "viseme_O",
    "viseme_U",
)

# Mixamo names TalkingHead retargets, plus the eye bones showAvatar reads.
MIXAMO_BONES = (
    "Hips",
    "Spine",
    "Spine1",
    "Spine2",
    "Neck",
    "Head",
    "LeftShoulder",
    "LeftArm",
    "LeftForeArm",
    "LeftHand",
    "RightShoulder",
    "RightArm",
    "RightForeArm",
    "RightHand",
    "LeftUpLeg",
    "LeftLeg",
    "LeftFoot",
    "LeftToeBase",
    "RightUpLeg",
    "RightLeg",
    "RightFoot",
    "RightToeBase",
    "LeftHandThumb1",
    "LeftHandThumb2",
    "LeftHandThumb3",
    "LeftHandIndex1",
    "LeftHandIndex2",
    "LeftHandIndex3",
    "LeftHandMiddle1",
    "LeftHandMiddle2",
    "LeftHandMiddle3",
    "LeftHandRing1",
    "LeftHandRing2",
    "LeftHandRing3",
    "LeftHandPinky1",
    "LeftHandPinky2",
    "LeftHandPinky3",
    "RightHandThumb1",
    "RightHandThumb2",
    "RightHandThumb3",
    "RightHandIndex1",
    "RightHandIndex2",
    "RightHandIndex3",
    "RightHandMiddle1",
    "RightHandMiddle2",
    "RightHandMiddle3",
    "RightHandRing1",
    "RightHandRing2",
    "RightHandRing3",
    "RightHandPinky1",
    "RightHandPinky2",
    "RightHandPinky3",
    "LeftEye",
    "RightEye",
)


def parse_glb(data: bytes) -> tuple[dict[str, Any], bytes]:
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError("not a GLB")
    _magic, version, length = struct.unpack_from("<4sII", data, 0)
    if version != 2:
        raise ValueError(f"unsupported glTF version {version}")
    if length > len(data):
        raise ValueError("GLB length exceeds the file")
    offset = 12
    document: dict[str, Any] | None = None
    blob = b""
    while offset + 8 <= length:
        chunk_len, chunk_type = struct.unpack_from("<I4s", data, offset)
        start = offset + 8
        chunk = data[start : start + chunk_len]
        if chunk_type == b"JSON":
            document = json.loads(chunk)
        elif chunk_type == b"BIN\x00":
            blob = chunk
        offset = start + chunk_len
    if document is None:
        raise ValueError("GLB has no JSON chunk")
    return document, blob


def write_glb(document: dict[str, Any], blob: bytes) -> bytes:
    if document.get("buffers"):
        document["buffers"][0]["byteLength"] = len(blob)
    raw = json.dumps(document, separators=(",", ":")).encode("utf-8")
    raw += b" " * ((4 - len(raw) % 4) % 4)
    blob = blob + (b"\x00" * ((4 - len(blob) % 4) % 4))
    total = 12 + 8 + len(raw) + 8 + len(blob)
    out = struct.pack("<4sII", b"glTF", 2, total)
    out += struct.pack("<I4s", len(raw), b"JSON") + raw
    out += struct.pack("<I4s", len(blob), b"BIN\x00") + blob
    return out


def _png_size(blob: bytes) -> tuple[int, int] | None:
    if len(blob) < 24 or blob[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", blob[16:24])


def _jpeg_size(blob: bytes) -> tuple[int, int] | None:
    if len(blob) < 4 or blob[:2] != b"\xff\xd8":
        return None
    offset = 2
    while offset + 8 < len(blob):
        if blob[offset] != 0xFF:
            offset += 1
            continue
        marker = blob[offset + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack_from(">HH", blob, offset + 5)
            return width, height
        if marker == 0xD8 or marker == 0xD9:
            offset += 2
            continue
        if offset + 4 > len(blob):
            break
        segment = struct.unpack_from(">H", blob, offset + 2)[0]
        offset += 2 + segment
    return None


def _webp_size(blob: bytes) -> tuple[int, int] | None:
    if len(blob) < 30 or blob[:4] != b"RIFF" or blob[8:12] != b"WEBP":
        return None
    format_tag = blob[12:16]
    if format_tag == b"VP8X" and len(blob) >= 30:
        width = 1 + int.from_bytes(blob[24:27], "little")
        height = 1 + int.from_bytes(blob[27:30], "little")
        return width, height
    if format_tag == b"VP8 " and len(blob) >= 30:
        width, height = struct.unpack_from("<HH", blob, 26)
        return width & 0x3FFF, height & 0x3FFF
    if format_tag == b"VP8L" and len(blob) >= 25:
        bits = int.from_bytes(blob[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def image_size(blob: bytes) -> tuple[int, int, str] | None:
    png = _png_size(blob)
    if png:
        return png[0], png[1], "image/png"
    jpeg = _jpeg_size(blob)
    if jpeg:
        return jpeg[0], jpeg[1], "image/jpeg"
    webp = _webp_size(blob)
    if webp:
        return webp[0], webp[1], "image/webp"
    return None


def _image_bytes(document: dict[str, Any], blob: bytes, image: dict[str, Any]) -> bytes:
    if "bufferView" not in image:
        return b""
    view = document["bufferViews"][image["bufferView"]]
    start = view.get("byteOffset", 0)
    return blob[start : start + view["byteLength"]]


def triangle_count(document: dict[str, Any]) -> int:
    accessors = document.get("accessors", [])
    total = 0
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            mode = primitive.get("mode", 4)
            if mode != 4:
                continue
            if "indices" in primitive:
                total += accessors[primitive["indices"]].get("count", 0) // 3
            elif "POSITION" in primitive.get("attributes", {}):
                total += accessors[primitive["attributes"]["POSITION"]].get("count", 0) // 3
    return total


def morph_names(document: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for mesh in document.get("meshes", []):
        for name in mesh.get("extras", {}).get("targetNames", []):
            found.add(name)
    return found


def node_names(document: dict[str, Any]) -> set[str]:
    return {node.get("name", "") for node in document.get("nodes", [])}


def texture_report(document: dict[str, Any], blob: bytes) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for image in document.get("images", []):
        raw = _image_bytes(document, blob, image)
        sniffed = image_size(raw)
        row: dict[str, object] = {
            "name": image.get("name") or image.get("uri") or "",
            "mime": image.get("mimeType") or (sniffed[2] if sniffed else ""),
            "bytes": len(raw),
            "width": sniffed[0] if sniffed else None,
            "height": sniffed[1] if sniffed else None,
        }
        rows.append(row)
    return rows


def inspect_glb(data: bytes) -> dict[str, Any]:
    """Report missing Mixamo bones, ARKit shapes, Oculus visemes, triangles and textures."""
    document, blob = parse_glb(data)
    names = node_names(document)
    shapes = morph_names(document)
    triangles = triangle_count(document)
    textures = texture_report(document, blob)
    missing_bones = [bone for bone in MIXAMO_BONES if bone not in names]
    if "Armature" not in names:
        missing_bones = ["Armature", *missing_bones]
    missing_arkit = [shape for shape in ARKIT_52 if shape not in shapes]
    missing_visemes = [shape for shape in OCULUS_15 if shape not in shapes]
    oversized = []
    unknown_size = []
    for texture in textures:
        width = texture["width"]
        height = texture["height"]
        if not isinstance(width, int) or not isinstance(height, int):
            unknown_size.append(texture["name"] or texture["mime"] or "image")
            continue
        if width > TEXTURE_MAX or height > TEXTURE_MAX:
            oversized.append({"name": texture["name"], "width": width, "height": height})
    triangle_ok = TRIANGLE_MIN <= triangles <= TRIANGLE_MAX
    notes: list[str] = []
    if not textures:
        notes.append("no embedded textures")
    if unknown_size:
        notes.append("texture size unreadable: " + ", ".join(str(item) for item in unknown_size))
    ok = not missing_bones and not missing_arkit and not missing_visemes and triangle_ok and not oversized
    return {
        "ok": ok,
        "triangles": triangles,
        "triangle_min": TRIANGLE_MIN,
        "triangle_max": TRIANGLE_MAX,
        "triangle_ok": triangle_ok,
        "missing_bones": missing_bones,
        "missing_arkit": missing_arkit,
        "missing_visemes": missing_visemes,
        "textures": textures,
        "oversized_textures": oversized,
        "notes": notes,
    }


def _append_vec3(document: dict[str, Any], blob: bytearray, count: int) -> int:
    payload = b"\x00" * (count * 12)
    while len(blob) % 4:
        blob.append(0)
    view_index = len(document.setdefault("bufferViews", []))
    document["bufferViews"].append(
        {"buffer": 0, "byteOffset": len(blob), "byteLength": len(payload), "target": 34962}
    )
    blob.extend(payload)
    accessor_index = len(document.setdefault("accessors", []))
    document["accessors"].append(
        {
            "bufferView": view_index,
            "componentType": 5126,
            "count": count,
            "type": "VEC3",
            "min": [0, 0, 0],
            "max": [0, 0, 0],
        }
    )
    return accessor_index


def add_zero_morph(data: bytes, name: str) -> bytes:
    """Add a zero-delta morph target named ``name`` on every mesh that already has targets."""
    document, blob_bytes = parse_glb(data)
    blob = bytearray(blob_bytes)
    for mesh in document.get("meshes", []):
        primitives = mesh.get("primitives", [])
        if not any(primitive.get("targets") for primitive in primitives):
            continue
        extras = mesh.setdefault("extras", {})
        names = list(extras.get("targetNames") or [])
        if name in names:
            continue
        for primitive in primitives:
            targets = primitive.get("targets")
            if not targets:
                continue
            position = document["accessors"][primitive["attributes"]["POSITION"]]
            count = int(position["count"])
            keys = set()
            for target in targets:
                keys.update(target.keys())
            added = {}
            for key in sorted(keys):
                added[key] = _append_vec3(document, blob, count)
            targets.append(added)
        names.append(name)
        extras["targetNames"] = names
    if not document.get("buffers"):
        document["buffers"] = [{"byteLength": len(blob)}]
    return write_glb(document, bytes(blob))


def png_rgb(width: int, height: int, rgb: tuple[int, int, int] = (0, 0, 0)) -> bytes:
    import zlib

    def chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def build_fixture_glb(
    *,
    triangles: int = 30_000,
    bones: tuple[str, ...] | None = None,
    shapes: tuple[str, ...] | None = None,
    texture: tuple[int, int] | None = (64, 64),
    include_armature: bool = True,
) -> bytes:
    """Small GLB for validator tests. Indices repeat three vertices ``triangles`` times."""
    bone_names = list(bones if bones is not None else ("Hips",))
    shape_names = list(shapes or ())
    nodes: list[dict[str, Any]] = []
    if include_armature:
        nodes.append({"name": "Armature", "children": list(range(1, 1 + len(bone_names)))})
    for bone in bone_names:
        nodes.append({"name": bone})
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    indices = struct.pack("<" + "I" * (triangles * 3), *([0, 1, 2] * triangles))
    blob = bytearray(positions)
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962}]
    accessors: list[dict[str, Any]] = [
        {
            "bufferView": 0,
            "componentType": 5126,
            "count": 3,
            "type": "VEC3",
            "min": [0, 0, 0],
            "max": [1, 1, 0],
        }
    ]
    views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(indices), "target": 34963})
    blob.extend(indices)
    accessors.append({"bufferView": 1, "componentType": 5125, "count": triangles * 3, "type": "SCALAR"})
    targets = []
    for _shape in shape_names:
        offset = len(blob)
        zeros = b"\x00" * 36
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(zeros), "target": 34962})
        blob.extend(zeros)
        accessors.append(
            {
                "bufferView": len(views) - 1,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
                "min": [0, 0, 0],
                "max": [0, 0, 0],
            }
        )
        targets.append({"POSITION": len(accessors) - 1})
    images: list[dict[str, Any]] = []
    textures: list[dict[str, Any]] = []
    materials: list[dict[str, Any]] = [{}]
    if texture is not None:
        encoded = png_rgb(texture[0], texture[1])
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(encoded)})
        blob.extend(encoded)
        images.append({"mimeType": "image/png", "name": "fixture", "bufferView": len(views) - 1})
        textures.append({"source": 0})
        materials = [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
    primitive: dict[str, Any] = {
        "attributes": {"POSITION": 0},
        "indices": 1,
        "mode": 4,
        "material": 0,
    }
    if targets:
        primitive["targets"] = targets
    mesh: dict[str, Any] = {"name": "fixture", "primitives": [primitive]}
    if shape_names:
        mesh["extras"] = {"targetNames": shape_names}
    document: dict[str, Any] = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": views,
        "accessors": accessors,
        "meshes": [mesh],
        "nodes": nodes + [{"name": "fixture", "mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
        "materials": materials,
    }
    if images:
        document["images"] = images
        document["textures"] = textures
    return write_glb(document, bytes(blob))
