# STATUS — 2026-11-24

**v3 is built, runs here end to end, and passes CI. Nothing has run on nanob.**
Highest level reached: **3 — CI green** (PR #21 at e77356b: unit, end-to-end
with real llama-server/whisper-server/Piper/Silero/openWakeWord on Python 3.10,
source tests, dry-run safety). Every hardware acceptance test in TARGET.md is
still NOT TESTED.

| Component | Ladder | Evidence / gap |
|---|---|---|
| sc-core (turns, persona, memory, API) | 3 | 86 sc tests incl. real llama-server and real MiniLM; green in CI on Python 3.10 |
| sc-voice (wake, VAD, STT, TTS, barge-in) | 3 | real models in real time with a scripted mic, green in CI; no real microphone or speaker yet |
| Memory import from legacy Redis | 2 | against real redis-server with the legacy layout; real nanob data never seen |
| Installer + units | 2 | installer run for real here (systemd stubbed); units pass `systemd-analyze verify`; never run on a Jetson |
| sc-llm container on Jetson | 1 | image + flags copied from scripts/jetson-models.sh; unconfirmed (H16) |
| whisper.cpp CUDA build | 1 | CPU build verified; CUDA flags (sm_87) unverified |
| TLS for LAN/AIRI | 2 | real HTTPS process test with local CA |
| AIRI → sc-core | 2 | AIRI's own client library (xsAI 0.5.1) streams with context against real llama-server; CORS preflight checked; never with the AIRI app itself (B2) |
| Backup + restore (A2) | 2 | real Redis/SQLite/git/age backup, verify, Redis snapshot restored 52/52 keys, then imported by sc.migrate; never on nanob |
| Acceptance tool | 2 | exercised here; A1/G1 correctly fail off-Jetson |
| nanob | unknown | not reachable; identity/IP/OS unverified (H9, H10) |

## Next, on nanob (deploy/v3/README.md)

1. Link a LAN machine to this chat (or run the commands yourself).
2. A1 identity, A2 backup + verify.
3. Stop (not disable) the old stack, install, start sc-llm + sc-core: confirm H16.
4. Acceptance: B1, D1/D2 (with a reboot), C1 (10 spoken turns), E1–E3, F1, G1; then F2 (24 h).

## Known limits

- No speaker identification: the speakerphone treats the room as the whole household (privacy-safe default).
- Wake phrase is "Hey Jarvis" until a "Hey Cortana" model is trained.
- Avatar (#20), pi1 vision, Home Assistant and proactive features are out of this campaign.
