# Native Orin voice USB recovery

When the C-Media microphone disappears, the deployed listener falls back to another PulseAudio input. The previous readiness shortcut then keeps that input forever while it remains listed, even if the USB microphone reconnects. If the fallback produces digital zeros, the kiosk still says to speak the wake phrase.

This update re-evaluates the preferred USB microphone and speaker at every existing two-second inventory check. It restarts capture and resets wake/VAD state when the input changes, instead of mixing samples from two devices. Explicit `ORIN_AUDIO_SOURCE` and `ORIN_AUDIO_SINK` choices remain binding; a missing override reports unavailable. Automatic microphone selection excludes playback monitor sources. An explicitly selected monitor still works for loopback tests.

A PCM health counter flags eight seconds of exact digital zeros and clears as soon as nonzero input returns. It retains only counters. This detects the previously observed digitally silent input; it does not measure speech quality or certify a physical microphone. The kiosk shows **Wake · microphone silent** with the existing error message, and displays a readable reconnecting status during a device switch.

## Target and provenance

The target is the customized `/mnt/orin-ai/orin` runtime owned by the existing `orin-voice` service. `handsfree.py` and `handsfree-controls.js` were taken from the recorded 27 October 2026 deployment snapshot. `manifest.json` pins their canonical LF checksums. Source changes to the generic Sentient Core MQTT services are a separate workstream.

The previous deployment record identifies **Hey Honey** and the **Neon Siren day/night VRMs** as active. This package preserves that voice path, identity, model selection, avatar assets, telemetry and cancellation behavior. There are no model downloads or new Python dependencies.

## Verify and apply on the Nano

From a checkout containing this directory:

```bash
python3 -m unittest discover -s deploy/orin-native-voice/tests -p 'test_*.py' -v
node --test deploy/orin-native-voice/tests/status.test.cjs
python3 deploy/orin-native-voice/install.py preflight
python3 deploy/orin-native-voice/install.py apply
```

Preflight is read-only. Apply checks every package checksum and refuses a runtime that differs from the recorded baseline. It stops only `orin-voice`, makes exact-byte backups under `/mnt/orin-ai/backups`, installs the three files, restarts that service, and requires a fresh status file. If the startup gate fails it restores the backup and starts the original voice service. A successful apply prints the absolute receipt path. Existing dashboard pages need a reload to run the updated control script.

For rollback, pass the receipt path printed by apply to `python3 deploy/orin-native-voice/install.py rollback --receipt`. Rollback checks the current files and every original backup before restoration; it refuses to overwrite later edits. Reapplying an unchanged update is a no-op.

## Validation and deployment status

Local tests exercise the actual patched listener methods with audio IO boundaries simulated, plus PCM health, device choice, real filesystem installation, startup-failure rollback, exact-byte restoration and preservation of later edits. Node executes the actual control script in a minimal DOM to verify the four status presentations.

**Not deployed in this change.** Desktop Commander had no connected devices and direct SSH was blocked before login. The October 27 deployment report is historical evidence, not a current health check. No new Nano latency, microphone or Home Assistant success is claimed. Physical acceptance still requires a real USB unplug/replug test, a spoken Hey Honey turn and confirmation that the configured Home Assistant endpoint is available.
