# AIRI on Jetson Orin Nano — release candidate handoff
**Build prepared:** 2026-10-25  
**Branch:** `fix/voice-routing-wake-skills-20261025` / PR #6  
**Release maturity:** **Source CI candidate only. Not deployed or hardware-certified.**

## What is implemented
- Maintained **Hey Jarvis** wake selector, single-model verification, 5-chunk capture queue, echo/active-turn gating, 300ms rearm and monotonic cooldown.
- Correct `STT_AUDIO_INPUT` → `STT_OUTPUT` → `VOICE_TRANSCRIPTION` → `CHAT_OUTPUT` → `TTS_SPEAK` → `TTS_STARTED`/`TTS_COMPLETED` contracts, with unique voice `turn_id`.
- Local faster-whisper STT worker (`sentient/services/voice/stt_worker.py`); accepts validated WAV 16 kHz PCM16, emits linked transcript/error, and never auto-downloads a model.
- Local Piper speech worker (`sentient/services/voice/tts_worker.py`); local `.onnx` voice, `piper` plus local player, cancellation and explicit completion/error event.
- MQTT dispatch uses bounded concurrent tasks so a slow speech/LLM request no longer starves unrelated events. Transcript and response handlers reject stale turn IDs. A voice watchdog re-arms if no completion arrives.
- Fast direct time/date from the host clock and a discoverable skills catalog; ordinary supportive questions use the fast single-LLM path rather than expensive multi-perspective chains.
- Home Assistant light/switch/media/scene/script discovery, color/brightness changes, scoped follow-up target and sequential compound commands. No hardcoded devices. **Named music playback** requires an actual HA script/scene: it is not an unverified arbitrary Alexa media command.
- Refreshed fast/streaming prompts to identify **AIRI** and the distinct wake phrase **Hey Jarvis**, without obsolete hardcoded LAN IP.
- Read-only host inventory and SHA-pinned one-service systemd canary script with automatic rollback on a failed restart.
- Machine-testable unit and two-turn end-to-end simulated MQTT speech tests, plus more than 60 spoken intent fixtures.

## Runtime dependency boundaries
A working pre-existing Nano installation may have alternate STT/TTS services, USB audio assignments, a different installed model, GPU stack, avatar, Home Assistant token and model files. **Do not overwrite or restart that working stack from an unverified template.**

**For the STT worker:** environment `SENTIENT_STT_MODEL` must name an existing local faster-whisper/CTranslate2 model directory; `SENTIENT_STT_DEVICE` and `SENTIENT_STT_COMPUTE_TYPE` must be set from observed device capabilities. The Python interpreter must have `faster-whisper`, `numpy`, `aiomqtt` and project dependencies. No model downloads or GPU runtime replacement are included.

**For Piper:** `SENTIENT_PIPER_MODEL` must point at a local voice `.onnx`; `SENTIENT_PIPER_BIN` and `SENTIENT_AUDIO_PLAYER` are optional paths to tested Piper and speaker-player executables. These must be chosen from *actual Nano audio output*, not guessed.

**For HA:** `SENTIENT_HOME_ASSISTANT_URL` and `SENTIENT_HOME_ASSISTANT_TOKEN` must come from a valid local instance, stored only in the existing protected service environment file. Avoid printing credentials. Direct Alexa controls are not independently supported unless exposed via HA.

**For wake:** `SENTIENT_WAKE_WORD_MODEL=hey_jarvis` selects the installed wake classifier; custom models require a local `.onnx` or `.tflite` path. Retain the already working model if its key/path differs from the repository example.

## When the Nano becomes reachable — authorized deployment sequence
1. **Read-only inventory:** remotely run `tools/nano_inventory.py` from the code repository on the Nano. Capture real service units and overrides, actual source SHAs, audio source/sink, NVIDIA/JetPack, CPU/GPU pressure, model, Python venv and HA connection. No secret values need be exported.
2. **Inventory reconciliation:** identify which running components are from this repo and which are external/local; do not install duplicate STT/TTS producers. Preserve existing Hey Jarvis classifier and live dashboard/avatar assets.
3. **Staged release:** create an isolated clean checkout of the passing PR SHA. Choose the interpreter, current existing STT model, Piper voice and a backup destination. Run the **default dry-run** of `tools/nano_canary.py` against each candidate service; it rejects wrong SHA or dirty checkout.
4. **Canary order:** stage/verify STT and TTS transport first; then conversation and voice; wake last. Apply only one service at a time and verify that source is correct before progressing. Canary modifies only its targeted service's systemd drop-in and automatically reverts it if service startup fails.
5. **Hardware acceptance:** at least 50 consecutively spoken wake-to-reply turns, 20 interruptions or failed STT turns, multiple speaker-playback wake tests, timed stages through first audio, 30-minute soak, power/restart recoveries, actual HA device discovery and multi-action commands. Confirm speech and avatar lip sync together.
6. **Promote only after acceptance:** merge/tag the verified commit, save service/run logs without secrets, retain a known-good rollback source and 14-day backup retention.

## Current validation
The GitHub Action `Sentient Core source and unit checks` compiles source and runs the unit and simulated transport suite. The passing run must correspond to the final branch SHA; earlier passing runs do not prove subsequent commits.

**Not yet validated:** real Jarvis mic thresholds, physical STT and Piper, real MQTT broker, local LLM context/latency or GPU offload, Home Assistant credentials/scenes, Alexa playback, avatar audio sync, remote access and disaster restore.

## Acceptance measurement target
- 50/50 sequential wake → transcribe → respond → playback → rearm cycles.
- No mistaken action when spoken device is ambiguous.
- All actual actions report real success or error; no invented playback confirmations.
- p50/p95 voice-to-first-audio, latency per stage and peak GPU/CPU/memory/temperature captured from Nano.
- Failed-process restart and rollback tested without deleting working files/models.

## Turn-by-turn latency diagnostics (source instrumentation, not a live measurement)

The wake, VAD, STT, conversation/context, LLM-first-token, Piper
synthesis and playback-request stages now emit `AIRI_TIMING` JSON log records
with the same `turn_id` and host monotonic timestamp (nanoseconds).
No audio, transcript, prompt or credentials appear in these records.

On the **verified** Nano checkout, observe a small number of actual turns and
capture a combined service journal (read-only):

```bash
journalctl --since "30 minutes ago" -o cat --no-pager \
  -u sentient-voice -u sentient-whisper-stt \
  -u sentient-conversation -u sentient-piper-tts \
  | python3 tools/voice_latency_report.py
```

The report provides each turn's stage timeline and p50/p95 from wake received
to the **playback request**. It separately shows context wait and Piper
synthesis time when those markers exist. Report a timeout/missing stage as
missing evidence rather than silently treating it as zero. TTS_STARTED is
issued **before** the speaker player starts, so playback requested is a
proxy—not actual first-audio-byte/speaker loopback latency. To measure
acoustic first audio, use a loopback/microphone reference capture on the Nano.

Do **not** change context length, CUDA layers, model quantisation or model
server based on the old approximate 30-second number. First collect measured
stages. Potential delays include 1.5-second end-of-utterance VAD, a 30-second
memory-recall HTTP timeout, inference prefill/generation and Piper synthesis.
Only tune the measured bottleneck and rerun the same utterance/recovery suite.

The source now also rearms on a matching successful `TTS_COMPLETED` event
even when the earlier `TTS_STARTED` event is dropped. This is covered by
a regression test; it still needs physical second-turn validation.
