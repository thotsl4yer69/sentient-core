# ATHENA NIGHTFALL — rigged 3D redesign

**Release:** 31 October 2026 · **13,020,640 bytes** · SHA-256:
`dfa20cd758b1e15c73c085f8978f90f581b95889a3002a41db5f538ab0f360df`

[Download the actual ATHENA NIGHTFALL GLB](https://d2ol7oe51mr4n9.cloudfront.net/user_3J2eeA6Q5aKeLXqP7Sh07VXpkjX/7291dd86-fccb-48d0-8cf4-8eeb0b397193.glb)

## Design revision

The prior figure had a bulky white/black robot outfit, a fringe over her face, stiff arms, irregular exposed feet and a weak silhouette. This build replaces those with:

- A waist- and hip-shaped, form-fitting obsidian satin bodysuit with a neckline exposing the collarbones and upper chest, plus discreet side waist cutouts.
- Smoothly blended garment colour instead of jagged triangle-based colour cutoffs, patent dark lower-leg panels, fitted ankle-boot geometry, restrained metallic/violet detailing.
- Clear face, swept-back textured ponytail, reworked eyelid/brow lines, skinned irises, pupils and glints, and warm rose lips.
- Relaxed, slightly tilted rest pose rather than a rigid A-pose. All arm/hand/face bones remain available for retargeting.

**These are actual mesh, material, vertex-colour and skeletal changes**, not an AI picture.

## Delivered 3D data

- glTF 2.0 binary .GLB with original **163 bones** (face, jaw, eyes, fingers, full body).
- **19 mesh objects**, approximately **131,256 triangles** as checked by a separate importer; **18 PBR materials**, one embedded hair image texture and one four-second idle/speaking animation.
- Original MakeHuman anatomical mesh and skin weights retained, with bespoke geometric modifications and vertex-colour clothing overlaid.
- Fully self-contained binary asset; there are no externally referenced texture files.
- The asset passed separate 3D model import and rendering. Khronos validation returned **two original source skinned-child parenting warnings**, no structural errors once animation sampler buffer targets and bounds were corrected.
- Caveat: this is a substantially improved stylized 3D prototype. It is **not yet photorealistic** and lip-synchronisation currently uses a jaw joint rather than production phoneme blendshapes. Deformation in aggressive poses and performance on the actual Orin hardware are not yet verified.

## Deploy on the Jetson Orin Nano

Run from an updated Sentient Core checkout:

```bash
cd /opt/sentient-core
git pull --ff-only
bash scripts/install-athena-avatar.sh
```

The installer downloads the revised file to
`interfaces/web_chat/static/models/athena.glb`, validates its checksum,
bone count and animation, backs up the previous GLB, and swaps the new file
atomically. The existing original holographic avatar interface is untouched.
The new 3D viewer is available at
`http://<ORIN_IP>:3001/static/athena.html`.

The viewer uses Three.js loaded from a CDN, meaning browser access to jsDelivr
is currently required. This project commit does not imply that the physical
Orin has been updated: device access is required for final deployment.

## Licences / original CC0 models

- [Innerscene rigged MakeHuman female](https://www.innerscene.com/tools/library/3d-parts/female-base-mesh-3d-person-rigged-a-pose-dba97ed5)
- [Innerscene Amara character](https://www.innerscene.com/tools/library/3d-parts/amara-3d-person-standing-office-2a16c294) — ponytail and ankle-boot geometry
- [Innerscene Sage character](https://www.innerscene.com/tools/library/3d-parts/sage-3d-person-woman-walking-a458e4c3) — evaluated as a previous design iteration

Assets are public-domain CC0; preserve source credit in derivative project documentation for traceability.
