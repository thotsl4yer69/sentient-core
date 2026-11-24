# TARGET — Sentient Core: Cortana working on nanob

Status: **Direction approved 2026-11-24** — Jack chose "rebuild it completely to the optimal system". Acceptance tests below unchanged from the draft.
Mode: **Diagnose** (a system exists and does not work), with **Build** for the missing pieces.

## Architecture (v3, chosen)

| process | role | why |
|---|---|---|
| `sc-llm` | llama.cpp `llama-server`, Gemma 4 E2B QAT q4_0, CUDA, `--parallel 1` | one model resident; proven on Orin Nano 8 GB (Jarvis-home); OpenAI API; prompt cache |
| `sc-core` | turns (voice first), persona, memory, OpenAI-compatible API | one brain process instead of 9 services + broker; nothing to drift out of sync |
| `sc-stt` | whisper.cpp `whisper-server`, base.en, CUDA | ~300 ms per 3 s of audio on the Orin (reference) |
| `sc-voice` | arecord/aplay, openWakeWord, Silero VAD, Piper | owns the speakerphone; survives USB unplug; barge-in |

Memory is one SQLite file (WAL) instead of Redis. MQTT is not needed for the
core loop; it returns only when an off-box node (pi1 vision, avatar display)
needs it. AIRI is the typed client; sc-voice is the spoken client; both use the
same `/v1/chat/completions` endpoint.

## What "done" means

On the one Jetson Orin Nano 8 GB (`nanob`), with no cloud services involved:

1. You can **type** to Cortana from AIRI (phone or PC browser) and get real, in-character replies from the local model.
2. You can **talk** to Cortana through one USB speakerphone on the Jetson and hear the reply from it.
3. Cortana **remembers** facts across sessions, keeps each person's private facts separate, and still has them after a reboot.
4. It **comes back on its own** after a reboot, a crashed service or the speakerphone being unplugged.
5. Everything that existed before (memory data, AIRI settings, models, avatar assets) is **backed up and restorable** before anything is changed.

Out of scope for this campaign (later campaigns): hologram avatar (#20), pi1 vision/Hailo, RF, robodog, StarNet, Home Assistant control, proactive "contemplation", second Orin.

## Real target

| Item | Value | Verified? |
|---|---|---|
| Board | Jetson Orin Nano 8 GB (dev kit / Super) | no — unreachable since ~2 Nov |
| OS | memory says JetPack 6.2 / Ubuntu 24.04; repo says Ubuntu 22.04.5 / L4T R36.4.7 | **conflict — must check** |
| LAN address | memory says 192.168.1.159; repo says 192.168.1.168 (also used by the Windows ATHENA box) | **conflict — must check** |
| Power mode | MAXN SUPER | no |
| Audio | one USB speakerphone (model TBD) | no |
| Clients | AIRI in a browser on phone/PC on the home LAN | no |

## Acceptance tests (pass/fail, no judgement calls)

| id | test | pass criterion |
|---|---|---|
| A1 | Identity | `hostname`, primary MAC, `/etc/nv_tegra_release`, and SSH host-key SHA256 fingerprint recorded in `campaign/LOG.md`; fingerprint matches on every later session. |
| A2 | Backup before change | `tools/orin_backup.py` archive created **and** `verify` passes into a scratch dir (SQLite integrity ok, Redis RDB header ok, git bundles ok). |
| B1 | Text turns | 10/10 turns via the OpenAI-compatible endpoint return a model-generated reply (model id logged). Turn 2 must use turn 1's context. First token ≤ 2.0 s, ≤ 60-token reply complete ≤ 10 s, both p95. |
| B2 | AIRI | Same 10 turns typed in AIRI succeed, and AIRI still works after a browser refresh. |
| C1 | Voice turns | 10/10 spoken turns: transcript published, reply spoken. End of speech → first audio ≤ 4.0 s median, ≤ 6.0 s p95. Zero self-triggers (Cortana never answers her own voice). |
| C2 | Barge-in | Speaking during a reply stops playback within 1.0 s in 5/5 tries. |
| D1 | Memory recall | 20 facts told over 5 separate sessions; ≥ 18/20 recalled correctly in a 6th session after a reboot. |
| D2 | Memory separation | 5 private facts told by user A in individual sessions are surfaced 0/5 times to user B in B's individual session. |
| E1 | Service recovery | `systemctl kill` of each Sentient service → B1 passes again within 15 s, 3 times per service. |
| E2 | USB recovery | Speakerphone unplugged/replugged 3 times → C1 turn succeeds within 10 s of replug each time. |
| E3 | Cold boot | 10 consecutive power-cycles → B1 passes within 120 s of power-on every time. |
| F1 | Resources | During 1 h of continuous turns: no OOM kill, RAM used ≤ 7.0 GB, SoC temp ≤ 85 °C, no thermal throttle message in `dmesg`. |
| F2 | Soak | 24 h: hourly automated B1 probe passes 24/24; no unit in failed state at the end. |
| G1 | Exposure | `ss -tlnp` on nanob: only SSH and the authenticated gateway listen beyond loopback. MQTT, Redis, model server, STT/TTS are loopback-only. |
| G2 | Secrets | `gitleaks detect` on the release commit finds nothing; credentials live only in root-owned 0600 env files on the device. |

## Constraints

- RAM: 8 GB unified (CPU+GPU share it). Budget: model server ≤ 3.3 GB, speech ≤ 0.9 GB, everything else ≤ 1.0 GB, ≥ 1.0 GB free.
- Local-only: no cloud inference, no public notification server (current config posts to public `ntfy.sh` — must be removed or self-hosted).
- Storage: NVMe; models and data stay on device.
- Don't break what may already be running (an `orin-voice` native stack was last seen working 31 Oct) until its replacement passes the same test.
- No `apt upgrade`, no reflash, no deleting models/data/AIRI settings.

## Budget and stop rules

- Source work (sandbox + CI): until B1/C1/D1 pass as automated tests here against real mosquitto + Redis + a real (small) model.
- Hardware work: blocked until A1 is possible. If nanob can't be reached by **[date Jack sets]**, stop and decide (fresh flash vs keep trying).
- If B1 latency can't be met with Gemma 4 E2B on llama.cpp, stop and choose: smaller model or relaxed latency. Don't add a second model server.
- Kill any option that needs more than 7.0 GB RAM on the real board.
