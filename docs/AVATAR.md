# Avatar pipeline

Talking hologram for the Pepper's ghost box. The Orin stays headless. A Pi 5 inside the box runs Chromium. Prototype on the Pi 500+ (same SoC and GPU). AIRI stays the typed chat client. This renderer has no chat UI.

Gate: display node passes the acceptance targets in the measurement section. Merging this branch is not that gate.

## Steps 1 to 8 (Tripo to GLB)

Jack supplies the Tripo exports. Faceit sculpting is manual.

1. Pick the Tripo source. A-pose or T-pose, neutral face. Reject a fused mouth: it has to be cut in Blender before the face will open. Check for a mouth line, teeth and separate eyeballs.
2. Clean topology. Target 30k to 60k triangles, with edge loops around the mouth and eyes. Use Tripo's quad export or remesh in Blender. Keep textures at 2K (2048) or smaller.
3. Auto-rig in Tripo. Rig v3.0 (`v3.0-20260909`), `rig_type` biped, spec mixamo, export FBX. Run Rig Check first. Mixamo upload is the fallback if Rig Check fails.
4. Fix the rig in Blender. The root object is named `Armature`. Check shoulder, hip and finger weights. Add eye bones (`LeftEye`, `RightEye`). Add hair and skirt bone chains if TalkingHead dynamic bones should move them. Tripo hair is static.
5. Build the face. Faceit generates the 52 ARKit shape keys from landmarks. Sculpt the corrections by hand: smile, smirk, brows, eye squint. This step is the look.
6. Add the 15 Oculus visemes: `viseme_sil`, `viseme_PP`, `viseme_FF`, `viseme_TH`, `viseme_DD`, `viseme_kk`, `viseme_CH`, `viseme_SS`, `viseme_nn`, `viseme_RR`, `viseme_aa`, `viseme_E`, `viseme_I`, `viseme_O`, `viseme_U`. Build them from the ARKit shapes, then fix them. PP lips must seal. FF lip must meet the teeth. `viseme_sil` is a zero-delta key so cancel can close the mouth.
7. Keep the Tripo PBR maps, bake, then compress:

    ```bash
    avatar/tools/optimise_glb.sh in.glb out.glb 2048
    ```

    That pins `@gltf-transform/cli@4.2.1`, meshopt, WebP, and does not decimate. Also export VRM from the same Blender file for the AIRI fallback. The VRM file is not loaded by this renderer.
8. Motion. Idle, listening and talking clips come from Tripo Animate or Mixamo as FBX. TalkingHead plays them and already layers procedural idle, blink and gaze. Drop the clips on the GLB when they exist. The test avatar has no extra clips and still idles.

Check a file before it is shipped:

```bash
python avatar/tools/validate_glb.py model.glb
```

Exit 0 means the Mixamo bone set (plus eyes and `Armature`), all 52 ARKit names, all 15 visemes, a triangle count from 30000 to 60000, and no texture over 2048. Anything else is listed in the JSON. The script does not modify the file.

Where a shape already has a known alias, rename it in Blender without opening the UI:

```bash
blender --background --python avatar/tools/map_shapes.py -- in.blend out.blend
```

Unknown sculpt names are left alone.

## Test avatar

`avatar/renderer/models/mpfb.glb` is the CC0 MPFB human shipped with TalkingHead (`b3e277b3b46f88e557bf28a2c5612a5b04e075c3`, source sha256 `63c645a2a863b9972e9a9c2ed576a1de4c390b8475508e1473e69c87a3ee299c`). `viseme_sil` is added here as a zero morph because the upstream file has the other 14 visemes and not silence. Triangle count stays above 60k, so the validator does not call it a production mesh. It is the stand-in that plays today. Jack's Tripo file replaces `modelUrl` in `config.json`.

## Renderer

`avatar/renderer/` is offline. three.js is pinned at 0.180.0. TalkingHead, the retargeter and dynamic bones are the files at that commit, checked in `vendor/UPSTREAM.sha256`. `build.sh` rebuilds `vendor/avatar.bundle.mjs`. The kiosk loads the bundle. It does not call a CDN.

`config.json` sets the model URL, MQTT WebSocket URL, topic prefix, stream URL and tokens. `mirror` defaults to true (the glass flips left and right). Set it false on a normal monitor. `debug` shows fps, audio-to-mouth lead and cancel time. The clear colour is `#000000`, shadows are off, and the lights are a dim fill, a rim and a back spot.

The page subscribes to `{prefix}/avatar/state` and `{prefix}/turn/cancel`, and publishes `{prefix}/tts/state` with `playback_start` and `playback_end`. Audio arrives on the stream socket. `speakAudio` gets the viseme times. Cancel calls `stopSpeaking` and `streamStop` and zeros the viseme weights. Broker and stream sockets retry on a delay that stays under 10 seconds.

## Orin stream

`sentient/services/avatar_stream/` maps Piper alignment rows (`phoneme`, `num_samples`) to the 15 visemes and serves the WebSocket. The unit is `sentient/services/avatar_stream/sc-avatar-stream.service` (`PartOf=sc.target`, `User=sc`, `/opt/sc/current/venv`). Tokens are `SC_AVATAR_STREAM_TOKEN` and `SC_AVATAR_INGEST_TOKEN` in `/etc/sc/secrets.env`. Optional TLS is `SC_AVATAR_TLS_CERT` and `SC_AVATAR_TLS_KEY`. Optional MQTT cancel subscription is `SC_MQTT_HOST`.

Piper itself is not invoked here. sc-speech (WS5) patches the voice with `python3 -m piper.patch_voice_with_alignment` and synthesises with `include_alignments=True`, then sends this module one turn. See the contract addendum for the ingest shape.

## Display node

From the Pi 500+, with the node's host key already trusted:

```bash
avatar/display/install-node.sh --host hologram.lan
```

The script copies the renderer to `/opt/sc/avatar-display`, installs `sc-avatar-static.service` and `sc-avatar-kiosk.service`, and restarts them. It does not install Raspberry Pi OS. Chromium and labwc must already be on the node. Re-running it is safe. If `/etc/sc/avatar-display.json` exists, its keys overlay `config.json` and the token file is not replaced by the copy in git.

The kiosk is labwc plus Chromium on Wayland, GPU flags on, page at `http://127.0.0.1:8090/`. Set `SC_AVATAR_DEBUG_PORT=9222` on the kiosk unit when you want the measurement script to attach.

## Acceptance measurement

On the Pi, after a real session:

```bash
python avatar/tools/measure.py collect --cdp http://127.0.0.1:9222 --seconds 600 --out trace.json
python avatar/tools/measure.py score trace.json
```

Targets: fps at or above 30 for the whole run (60 is the goal), audio-to-mouth lead from -125 ms to +45 ms (positive means audio is first), cancel under 150 ms, 10 turns with `stuck` false, reconnect at or under 10 s. `score` fails a target that has no samples.

## Tests

```bash
python -m pytest -q tests/unit/test_avatar_stream.py tests/unit/test_avatar_validator.py tests/unit/test_avatar_measure.py
node --test avatar/renderer/tests/protocol.test.mjs avatar/renderer/tests/mqtt.test.mjs
```

The browser test is Playwright Chromium (SwiftShader is fine):

```bash
cd avatar/renderer && npx playwright test
```
