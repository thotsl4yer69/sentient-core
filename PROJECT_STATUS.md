# Project Status — Sentient Core

**Portfolio class:** Flagship  
**Maturity:** **Deployed prototype / active hardening**  
**Primary target:** NVIDIA Jetson Orin Nano  
**Last portfolio review:** 2026-08-13

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
