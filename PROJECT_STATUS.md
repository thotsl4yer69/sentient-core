# Project Status — Sentient Core

**Portfolio class:** Flagship  
**Maturity:** **Deployed prototype / active hardening**  
**Primary target:** NVIDIA Jetson Orin Nano  
**Last portfolio review:** 2026-08-13

## AIRI-first source safety update — 16 November 2026

- The default branch now includes a read-only Orin inventory, a one-unit legacy-UI cutover helper with state capture and rollback, and regression tests.
- `systemd/deploy.sh` and `systemd/install.sh` are intentionally blocked; they no longer auto-enable the legacy UI/service stack from this revision.
- GitHub safety and source workflows passed on commit `4cc3c87e954bd1c83e8152028eacc2d76967d1eb`.
- **Physical acceptance remains NOT TESTED:** Desktop Commander access is offline and the user-reported Jetson LAN IP `192.168.1.168` is not identity-verified; it has also been used historically by the separate Windows ATHENA host.
- See [AIRI consolidation operator runbook](docs/ORIN_AIRI_CONSOLIDATION.md) before any hardware mutation.

## What is demonstrated

- Local-first conversational AI stack running on Jetson-class edge hardware.
- FastAPI/async service architecture with MQTT and Redis integration.
- Local LLM inference through Ollama.
- Persistent memory and conversation-state components.
- Voice pipeline work using Whisper/open-source STT and Piper TTS.
- systemd-managed services and Linux deployment/debugging workflows.
- Web/PWA interface and avatar/dashboard experimentation.

## Evidence boundary

The repository previously used blanket **production ready** language. For portfolio purposes, the defensible status is **deployed prototype**: substantial functionality has run on target hardware, but the system remains under active development and hardening.

Specific service counts, latency numbers, model throughput, memory counts and “all services active” claims should be treated as snapshots only when backed by current test output from the target node.

## Known work remaining before a production claim

- repeatable clean-install/deployment verification;
- consolidated automated acceptance tests;
- service recovery/failure-mode verification;
- secrets/configuration audit;
- network exposure review;
- resource/thermal testing under sustained load;
- versioned release process and rollback path;
- current screenshots/demo evidence tied to a tagged build.

## Authorship / AI assistance

This is an authored MAZLABZ system developed with AI coding agents as part of the engineering workflow. AI has been used for implementation, research, refactoring and testing; system definition, architecture, hardware selection, integration, debugging and deployment remain the project owner's responsibility.

## Portfolio takeaway

The strongest evidence here is not a claim of senior ML research. It is **systems integration**: local inference, Linux services, voice, state, APIs, message buses and UI composed into a single edge-AI product.

## Fleet reconciliation — 2 November 2026

**Observed state:** Deployed Jetson prototype historically verified on 31 October (Ubuntu 22.04.5 / L4T R36.4.7, local MQTT/voice/LLM/dashboard/media). Connected `nano-desktop` was offline at 2 November review; current service/process status and exact installed revision remain unknown. The Oct 30–31 merged inference/rigged-avatar PRs (#10–#12) are **source/asset confirmed only**, not deployed on hardware. Open voice input/wake/older avatar work (#9, #6, #5, #2, #1) must be reconciled without disturbing existing runtime. Next: verify host identity and current working service baseline, record hashes/rollback and test deployment on actual Orin. See [fleet audit](https://github.com/thotsl4yer69/fleet/blob/main/FLEET_STATE_2026-11-02.md).
