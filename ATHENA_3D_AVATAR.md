# ATHENA — real rigged female 3D companion

**Asset revision:** 2026-10-31, GLB 8,891,316 bytes, SHA-256:
`965246a711760df5028c1b81ba03fe4d8638946d614f345ab39f3acd2bff67d2`

## What is actually built

A genuine glTF 2.0 binary model, **not a rendered concept image**. Source
anatomical mesh is a public-domain (CC0) MakeHuman model published by Innerscene;
the 163-joint skeletal rig, vertex skin weights, facial/jaw/eye/finger bones,
body topology and original bind data are retained. Customizations include
skinned black/ivory cyber suit material regions, a cyan chest trim, a skinned
bob haircut and eyebrows/eyelashes adapted from another CC0 MPFB figure.

- **Rig:** 163 bones including fingers, eyes, jaw, limbs, spine.
- **Geometry:** approximately 118,575 triangles; 10 meshes.
- **Materials:** 13 including embedded hair/eyebrow/eyelash textures and
  standard PBR metallic/roughness suit finishes.
- **Animation:** one `ATHENA_4s_idle_speech` glTF 4-second clip with subtle
  head movement, jaw movement and arm microgesture.
- **Format:** `.glb`, supported by Blender, Godot, Unity, Three.js and
  other glTF importers. The GLB is editable, not a proprietary or image-only file.
- **File:** [ATHENA rigged GLB](https://d2ol7oe51mr4n9.cloudfront.net/user_3J2eeA6Q5aKeLXqP7Sh07VXpkjX/27960480-95de-44de-b869-30080b584828.glb).
- **Actual geometry preview:** [3D renderer screenshot](https://gcdn.picsart.com/editing-temp/2825d386-92d7-46cd-85df-48815ce03750.png) (temporary).

This is a *functional rigged character prototype*, **not a photoreal 1:1
reproduction** of ATHENA's concept sheet. There are no facial viseme morph
targets yet; the jaw bone provides a basic speech approximation. Existing
original skinned-node parenting produces two Khronos validator warnings,
not runtime errors. The model has been opened by a separate importer, and
the import produced 113,695 indexed triangles. Further texturing/retopology,
rigged motion testing and target-hardware load testing are not yet complete.

## Install on the Orin without disrupting the existing holographic orb

From an up-to-date checkout of Sentient Core:

```bash
cd /opt/sentient-core
git pull --ff-only
bash scripts/install-athena-avatar.sh
```

The installer downloads the GLB from the versioned CDN, checks SHA-256,
validates the embedded glTF JSON and expected bones/clip, backs up an existing
installation and atomically installs
`interfaces/web_chat/static/models/athena.glb`.

Open `http://<ORIN_IP>:3001/static/athena.html` in a browser. Drag/touch to
orbit the live model. The page uses Three.js and subscribes to existing
`/ws` speech events; the Test speaking button actuates the jaw locally.
The original UI is unchanged. The Three.js modules currently load from
jsDelivr, so the viewer requires connectivity until they are vendored.

No claim is made that the model or frontend has been installed on the physical
Orin: hardware access was unavailable during authoring.

## Original CC0 geometry

- [Innerscene female base with 163-bone skeleton](https://www.innerscene.com/tools/library/3d-parts/female-base-mesh-3d-person-rigged-a-pose-dba97ed5)
- [Innerscene Sage CC0 hairstyle source](https://www.innerscene.com/tools/library/3d-parts/sage-3d-person-woman-walking-a458e4c3)

Both sources state CC0 public domain. Source geometric content was adapted,
not falsely represented as custom sculpted photoreal geometry.
