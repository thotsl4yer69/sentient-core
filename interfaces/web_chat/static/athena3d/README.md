# Athena 3D 2.0.0

A real textured human, skeletal rig and facial morphs rendered locally with TalkingHead and Three.js. The supplied MPFB/MakeHuman asset replaces the drawn Canvas2D face. This is not the earlier photoreal concept art, and it is not claimed to be the old Kiriko model.

## Run the complete release

Extract the release ZIP, enter `athena3d`, and run:

```bash
python3 -m http.server 8765 --bind 127.0.0.1
```

Open `http://127.0.0.1:8765/index.html?preview=1` on that machine for a disconnected preview. Omit `?preview=1` to connect to the existing local avatar bridge on port 9001. The settings panel accepts the real bridge endpoint. Browser playback needs a tap on **Enable sound**; this does not change HDMI settings or system audio routing.

The compiled release contains its model and runtime. It needs no internet, Node, Python package install, model download, API key or cloud inference at runtime. Python is only used here as a static file server; the existing Coretana server can serve the same files.

## Dot / existing Orin installation

Do not reset, clean or replace the existing checkout, Python environments, models, voice services or launchers. The archived Orin audit identified **untracked** Kiriko files:

- `/mnt/orin-ai/archive-cortana-v1/frontend/public/avatar.glb`
- `/mnt/orin-ai/archive-cortana-v1/frontend/public/avatar_static.glb`
- neighboring `KIRIKO_AVATAR_WORKING.md`, `KIRIKO_LIPSYNC_FIX_COMPLETE.md` and `KIRIKO_OPTIMIZATION_COMPLETE.md`
- `/mnt/orin-ai/archive-cortana-v1/talkinghead`

Those locations are historical evidence, not a fresh check of the current Orin disk. Run the included recovery tool on the Orin:

```bash
python3 tools/manage.py recover
```

It checks these known locations, reads actual GLB structure, makes hash-verified copies, records `recovery-report.json`, and adds compatible candidates to the model picker. Originals are never altered. Incompatible rigs are preserved with a `REQUIRES_RIG_ADAPTER` result. The validated supplied model stays selected until the recovered model is loaded successfully in the browser. Recovery does not grant new redistribution rights to old assets.

Install into the existing web server:

```bash
python3 tools/manage.py install
```

The tool chooses a single known static directory only when unambiguous. `--static-dir` accepts the actual directory served by the running server. It creates a versioned folder, backs up `athena.html`, and points that kiosk to the new runtime. `--replace-renderer` also replaces the old `avatar-hologram.js` entrypoint with the compatible 3D boot loader; only use this when that dashboard canvas has a visible, sized parent. The old dashboard's deliberately hidden avatar panel must be made visible in that dashboard layout, not by this installer.

Every install prints a receipt path. `python3 tools/manage.py rollback RECEIPT` restores previous entrypoints only if they have not changed subsequently. It leaves versioned assets and other services untouched. No service restart or hardware claim is made by the installer.

## Actual behavior

Idle blinking and subtle head movement come from the real rig. Listening, thinking, seeing, alert and sleep are UI/animation states, not sensor-health assertions. Camera targets use normalized 0..1 coordinates, explicit pixel frame dimensions, or explicit `space: "gaze"` coordinates. Correct normalized centre is `(0.5,0.5)`.

The renderer preserves `window.avatarRenderer` methods used by the previous interface. `playAudio(arrayBuffer, timing)` drives lips from the actual playback clock. Timed phonemes use seconds unless `unit:"milliseconds"` is declared. Without timing data, the jaw follows the playing audio's amplitude, not accurate phonemes. `processPhonemes(...)` without audio is explicitly event-timed. Text arrival never pretends that audio is playing. The supplied kiosk plays `tts_audio` events or local audio files. An older bridge that publishes only speaking flags must also supply audio or actual phoneme timing for moving lips. Display-side playback must not be enabled alongside a second audible host-side player.

Example:

```js
avatarRenderer.setVisionTarget({cx:0.5,cy:0.5,coordinateSpace:'normalized'});
await avatarRenderer.playAudio('/voice/reply.wav', {
  unit:'seconds',
  phonemes:[{viseme:'PP',time:0,duration:0.1},{viseme:'aa',time:0.1,duration:0.3}]
});
```

## Build from repository source

The repository tracks source, not the binary model or generated bundle. `bash build.sh` retrieves the pinned assets, verifies source hashes, installs pinned build tools, runs protocol tests, bundles the renderer and compresses the model. Runtime is still entirely local. Source model hash: `63c645a2a863b9972e9a9c2ed576a1de4c390b8475508e1473e69c87a3ee299c`.

## Licensing and provenance

The supplied MPFB human is CC0 according to the original TalkingHead asset declaration. TalkingHead source is MIT, copyright Mika Suominen. Three.js is MIT. Preserve the included notices. TalkingHead revision: `b3e277b3b46f88e557bf28a2c5612a5b04e075c3`; Three.js `0.180.0`. This release does not include the more restrictive Ready Player Me brunette sample, copyrighted character assets, or font files.

Sources: https://github.com/met4citizen/TalkingHead/tree/b3e277b3b46f88e557bf28a2c5612a5b04e075c3 and https://github.com/mrdoob/three.js/tree/r180

## Verification

`node --test tests/protocol.test.mjs` exercises malformed events, booleans, frame coordinates, phoneme aliases and timing units. `node tests/browser.cjs` exercises the actual WebGL renderer, facial morphs, audio-driven movement and kiosk controls using Chromium and a local fixture server. `python3 -m unittest discover -s tests -p 'test_*.py'` checks asset handling and installation rollback.

See the release's evidence folder for executed results. Browser/software evidence is separate from Dot's physical Orin/display/audio commissioning.
