# Sentient Core

**Local-first edge AI system for NVIDIA Jetson hardware**

[![Status](https://img.shields.io/badge/status-deployed%20prototype-blue)](PROJECT_STATUS.md)
![Platform](https://img.shields.io/badge/platform-Jetson%20Orin%20Nano-orange)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

> **Maturity: Deployed prototype / active hardening.** Sentient Core has run on target Jetson hardware, but this repository deliberately does **not** claim production readiness. Current evidence, limitations and release gates are tracked in [PROJECT_STATUS.md](PROJECT_STATUS.md).

## What it is

Sentient Core is an independent edge-AI system built around local inference, voice, memory, service orchestration and a browser/PWA interface. The project is intended to make a Jetson-class device useful as a persistent local assistant rather than a thin client for a cloud chatbot.

The engineering focus is the integration layer: **Linux services + local models + state + message bus + speech + tools + UI**.

## Core architecture

```text
                  ┌──────────────────────┐
                  │   Web / Voice / CLI  │
                  └──────────┬───────────┘
                             │
                  ┌──────────▼───────────┐
                  │ Conversation / Tools │
                  └──────┬───────┬──────┘
                         │       │
              ┌──────────▼─┐   ┌─▼────────────┐
              │ Local LLM  │   │ Memory/State │
              │  (Ollama)  │   │ Redis/Vectors│
              └──────────┬─┘   └─┬────────────┘
                         │       │
                  ┌──────▼───────▼──────┐
                  │   MQTT event spine   │
                  └───┬──────────────┬──┘
                      │              │
              ┌───────▼──────┐ ┌────▼─────────┐
              │ Speech I/O   │ │ Perception / │
              │ STT + Piper  │ │ System state │
              └──────────────┘ └──────────────┘
```

For the deeper service map and data flow, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Demonstrated engineering work

- local LLM inference on Jetson-class hardware through Ollama;
- FastAPI/async Python services;
- MQTT-based service messaging;
- Redis-backed state and memory components;
- semantic-memory experiments using embeddings;
- Whisper/open-source STT and Piper TTS integration;
- wake-word and streaming-response work;
- systemd-managed Linux services;
- browser/PWA chat, status and avatar interfaces;
- system/service diagnostics and tool-registry work;
- iterative deployment, recovery and latency optimisation on the target node.

## What the status means

**Deployed prototype** means substantial parts of the system have been installed and exercised on the intended Jetson environment. It does **not** mean every service, performance number or optional subsystem is verified in every commit.

Numbers such as token throughput, embedding counts, response latency and active-service counts are snapshots only when accompanied by current test output. Historical documents may contain older measurements or stronger language; [PROJECT_STATUS.md](PROJECT_STATUS.md) is the portfolio source of truth.

## Current hardening priorities

- repeatable clean-install verification;
- automated acceptance tests across the core service path;
- failure/recovery testing;
- secrets and configuration audit;
- network-exposure review;
- sustained resource and thermal testing;
- tagged releases with rollback instructions;
- demo/test evidence tied to exact revisions.

## Quick start

The repository contains deployment and quick-start documentation rather than pretending one command is guaranteed across every JetPack/Ubuntu state.

Start with:

1. [QUICKSTART.md](QUICKSTART.md)
2. [CLI_DEPLOYMENT_GUIDE.md](CLI_DEPLOYMENT_GUIDE.md)
3. [TESTING_GUIDE.md](TESTING_GUIDE.md)
4. [ARCHITECTURE.md](ARCHITECTURE.md)

The project assumes a Linux/Jetson environment with the required local services and models installed. Validate configuration before enabling services on a fresh node.

## Documentation

| Document | Purpose |
|---|---|
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | Current maturity, evidence boundary and release gates |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Service architecture and data flow |
| [QUICKSTART.md](QUICKSTART.md) | Setup/start path |
| [CLI_DEPLOYMENT_GUIDE.md](CLI_DEPLOYMENT_GUIDE.md) | Deployment guidance |
| [TESTING_GUIDE.md](TESTING_GUIDE.md) | Verification workflow |
| [DOCUMENTATION_MANIFEST.md](DOCUMENTATION_MANIFEST.md) | Documentation index |
| [REBUILD_PLAN.md](REBUILD_PLAN.md) | Rebuild/refactor planning |

## Development provenance

Sentient Core is an authored MAZLABZ project developed with **AI coding agents as part of the normal engineering workflow**. AI is used aggressively for implementation, research, refactoring and testing. System definition, architecture, hardware selection, integration, debugging, verification and deployment remain the project owner's responsibility.

That distinction matters: this repository is intended to demonstrate the ability to define and integrate a complex edge system, not to imply every line was typed manually or that model/runtime projects used by Sentient Core were authored here.

## Portfolio significance

Sentient Core is the primary edge-AI case study in this account because it brings together several recurring skills in one system:

**edge compute · Linux · Python · local AI · MQTT · Redis · APIs · speech · service management · UI · systems integration**

---

**MAZLABZ applied R&D — build, integrate, test, document.**
