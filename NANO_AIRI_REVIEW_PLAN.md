# AIRI / Jetson Orin Nano — engineering review and execution plan
**Review date:** 2026-10-25 (Australia/Melbourne)  
**Scope:** Sentient Core GitHub source, service definitions, MQTT routes, wake/audio, inference, tools, memory, Home Assistant, UI and release readiness.  
**Evidence boundary:** This is a repository-level review plus prior reported device symptoms, **not** a completed live Jetson audit. On 2026-10-25 Desktop Commander reported no connected device. The actual installed code, model, runtime, microphones, audio routing and live service configuration **are unverified**. Do not claim physical completion on the basis of CI.

## Executive verdict
**Release state: prototype / active hardening.** Don't replace a working Hey Jarvis installation or switch models until live inventory and rollback are established. The right order is **identify running topology → restore reliable consecutive speech turns → measure understanding/latency → add skill depth → resilience and UI finishing**.

## Verified evidence
1. GitHub PR #6 (branch `fix/voice-routing-wake-skills-20261025`) contains source changes for the selected Hey Jarvis wake model, bounded wake queue, TTS/active-turn gate, corrected `VOICE_TRANSCRIPTION` routing, no post-wake 300ms delay, async mic reads, direct time/date and dynamic Home Assistant commands. **GitHub Actions: 31 unit tests passed, compile succeeded**. Tests mock actual mic, wake classifier, STT/TTS, MQTT and HA; this is not a live commissioning result.
2. `sentient/services/voice/wake_word.py` and `sentient/services/voice/pipeline.py` each open a PyAudio input stream. This *may* be a problem with the real USB microphone/audio driver; test simultaneous capture, ALSA device selection, channel mapping and release/rearm on hardware.
3. `sentient/services/voice/pipeline.py` publishes `STT_AUDIO_INPUT`, expects `STT_OUTPUT` and waits for `TTS_STARTED`/`TTS_COMPLETED`. No corresponding tracked Python sources exist at the exact paths in `systemd/sentient-whisper-stt.service` (`services/whisper_stt_server.py`) and `systemd/sentient-piper-tts.service` (`services/piper_tts.py`). The live machine may have generated/untracked alternate implementations. **Do not deploy these stale units blindly.** Verify actual installed unit overrides, processes and messaging first.
4. `systemd/deploy.sh` stops the target and copies/enables *all* unit files. Its effects are too broad for a first repair. `systemd/install.sh` and `systemd/deploy.sh` do not install the same service set; deployment paths mix `/opt/sentient-core` and `/home/cortana/sentient-core`. Select an explicit single topology and deploy only the services being changed.
5. The repository default model in `config/cortana.toml` is `llama3.2:3b`, while `ARCHITECTURE.md` names Qwen3 4B and the previously reported active device stack used Qwen3. Treat the *running model* and GPU offload as unknown until queried. Do not pull a model or change runtime on faith.
6. `sentient/common/service_base.py` awaits each MQTT handler inline in the subscription loop. Since voice, memory and conversation handlers can do slow work, investigate dispatch starvation and turn-correlation rather than attributing every delay to the LLM.
7. `sentient/services/conversation/orchestrator.py` keeps an in-memory conversation history, uses a 180-second inference timeout, and selects deep contemplation based on phrase patterns. `sentient/services/contemplation/engine.py` separately formats context/history and contains hardcoded historical LAN/IP references. Audit for stale context, low-quality clarifications and unwanted multiple LLM calls.
8. The new Home Assistant skill supports a *single* device/scene/script action with real entity discovery; it does **not** yet implement compound instructions (e.g. lights violet **and** TV sleep music), Alexa-specific speaker control, discovery/auth setup UI or scheduled routines. Advertise those as pending, not working.
9. Memory engine has episodic/working/core tiers, but meaningful user-scoped recall, forget/delete propagation, retrieval relevance, backup/restore and long-term durability are not verified by the current 31 tests.
10. The source has dashboard/avatar, vision, RF, notifications, proactive service and system tools, but current camera health, RF data, HA connectivity, speaker endpoints and avatar lip-sync are not known without a live inventory.

## P0 — Make the existing assistant reliable before expanding

### A. Read-only actual-device inventory and rollback baseline
- Connect directly to the authorized Nano (or via Athena). Capture `hostnamectl`, JetPack/L4T, source path+git SHA, `systemctl cat` and `systemctl show` for each live AIRI service, ports, mic/speaker ALSA/PipeWire selection, process list, Docker services, actual LLM model/quantization/context/offload, GPU/memory/thermal metrics, HA and MQTT connectivity.
- Map **each production publisher/subscriber** for wake detected, VAD/audio captured, STT input/output, conversation input/output, TTS request/started/completed and avatar state. Note exactly which codebase each process runs from.
- Record config/secrets *paths only* (do not log secrets). Back up app/config/unit files and data, generate a restore manifest/checksums. Keep previous service units and models intact.
- **Exit:** verified deployed topology and a rehearsed per-service rollback; no live stack interrupted by the audit.

### B. Consecutive-turn voice integrity
- Verify the selected **Hey Jarvis** classifier already working on the Nano; configure threshold against real room noise. Validate a newly selected model without discarding the active one.
- Keep wake/capture queues bounded; capture a short pre-roll so the first word after wake is never lost. Avoid shared-mic double-open conflict. Apply TTS echo/hold/rearm and deterministic microphone release.
- Give every wake → STT → LLM → TTS request a unique `turn_id`; ignore delayed outputs from earlier turns; set finite stage timeouts and explicit failure/rearm path even if TTS never emits `started`/`completed`.
- Measure wake timestamp, speech start, VAD end, STT duration/confidence/text, first model token, first audio byte, playback end and wake rearmed. Keep opt-in bounded audio-debug WAV retention; do not silently retain unrelated room audio.
- **Exit:** 50 consecutive spoken turns without getting stuck, 20 interrupted/aborted turns, 10 deliberate service errors and successful rearm, wake tested in quiet/noisy/speaker-playback conditions.

### C. STT/TTS actual implementations and deployment repair
- Identify real Whisper and Piper executables and restore source-controlled entrypoints/units matching actual deployment. Confirm STT MQTT schema/topic, sample rate and TTS completion contract end-to-end.
- Remove dependency/order ambiguities; deploy one service at a time, verify health and rollback, then verify a full voice turn.
- **Exit:** reproducible single-command service start/restart and all required messages observed from the real runtime.

## P1 — Better understanding, usable AI skills and less latency

### D. Model and prompt evaluation
- Benchmark actual installed 4B-class model against **one** compatible alternative with identical prompts/quantisation/context. Read actual GPU offload; don't assume CUDA layers or migrate runtime as an experiment during active use.
- Introduce typed intent+slot parsing for common commands, confidence thresholds and focused clarification when rooms/devices/targets are ambiguous. Preserve conversational context across follow-up instructions such as 'make it dimmer'.
- Reconcile identity/name/profile and remove static IP/user assertions from the system prompt; use dynamic system facts. Keep fast commands outside the LLM.
- Use streaming inference only where it measurably helps first-audio latency; profile preprocessing, memory retrieval, model prefill, tokens/sec and TTS separately.
- **Exit:** regression suite 50+ utterances (time/date, follow-ups, short fragments, pronouns, memory, home control, device diagnosis) reaches ≥95% correct route and no fabricated action success; report p50/p95 speech-to-first-audio latency.

### E. Skill registry and tool use
- Keep existing diagnostics, alarms/reminders, memory, basic chat, avatar and HA; expose an actual **skill catalog** with discoverable names/availability and explicit result states (`success`, `ambiguous`, `unavailable`, `error`).
- Add home scenes, scripts, dynamic device/area discovery, light colors/brightness, playback, volume, media sources and **compound** routines. Example: "Hey Jarvis, make the bedroom lights violet and play sleep music on the TV" should resolve both targets from HA and report *each* actual outcome.
- Handle Alexa only through a **proven working integration** (e.g. HA-exposed media_player/scenes/routines). Never claim arbitrary direct Alexa control without a supported endpoint.
- Extend system/device tool skills (safe service health, GPU/RAM/CPU, Home Assistant controls, timers, local files/media, sensor summary). Require clear confirmation for risky/destructive actions, and return actual tool results rather than plausible text.
- **Exit:** can query 'what can you do?', discover available devices, run individual + combined actions, understand follow-up references and distinguish unsupported capabilities.

### F. Memory and conversation
- Verify per-user ownership and filtering; retrieve true semantic relevance with recency, confirm 'remember' and 'forget' persistence, and prevent forgotten facts reappearing via history/cache.
- Make context carry over between turns without concatenating stale or unrelated memories. Auto-summarise older history for context budget rather than increasing prompt size indiscriminately.
- **Exit:** 20-turn context regression, remember/recall/forget scenarios and restore test pass without an invented recollection.

## P2 — Reliability, integration and user experience

### G. Services, network, RF, vision and backup
- Add heartbeat/watchdog and clear degraded-state reporting for wake, STT, TTS, conversation, memory, avatar, HA and sensors. Avoid unsafe automatic restarts of a busy LLM.
- Isolate network endpoints and protect tokens; review binding on `0.0.0.0`, auth and least privilege. Check the RF/vision telemetry quality before promoting events to spoken alerts.
- Nightly memory and config backups to a configured secondary host, 14-day retention, plus an isolated restore demonstration.
- **Exit:** survive power-cycle, mic unplug/replug, MQTT disconnect, transient STT failure and sleep/wake without losing normal operation.

### H. AIRI dashboard/avatar and mobile
- Keep existing adult AIRI avatar assets. Verify real speech events drive the avatar; measure sync with audio and device playback; clear stuck listening/thinking states.
- Replace ambiguous controls with direct Wake/Listen/Mute/Settings/Skills actions; show speech transcript, actual active model, current devices and service health. Check phone LAN and remote access.
- **Exit:** 30-minute soak with stable avatar, repeatable playback and working mobile access.

## Release gates (do not skip)
1. **Source:** CI compile and unit/integration suite green; changed files reviewed and pinned against a release SHA.
2. **Shadow test:** staging services observe MQTT but do not issue commands to home devices or speakers.
3. **Canary:** backup/rollback then one real service at a time; full two-turn test after each change.
4. **Acceptance:** 50-turn speech suite, 30-minute soak, error/recovery, GPU/thermal/latency and combined HA skill test.
5. **Finish:** tag release, save evidence, make working build default and archive rollback artifact.

## Work status as of review
- **Done in source branch:** PR #6 reliability changes + direct time/date and basic HA discovery/action routing; its 31 mocked/source tests passed in GitHub Actions.
- **Not done:** merging PR #6, deploying it, real mic/voice/LLM/TTS/HA tests, actual home/Alexa compound routines, advanced context/skills, watchdogs, memory restoration, finished avatar/mobile verification.
- **Immediate external blocker:** authorized direct Nano/Athena device connection not available through Desktop Commander as of 2026-10-25. The user should not be asked to install/test incomplete builds; resume the live work automatically when access is established in a future interactive session.
