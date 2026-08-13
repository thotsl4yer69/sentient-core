# Sentient Core — Release Readiness

> **Current maturity: Deployed prototype / active hardening**  
> **Production status: Not yet claimed**  
> **Reviewed: 2026-08-13**

This file replaces an older “production status” document whose wording was too strong for the available evidence. It is retained at the same path so older links resolve to the corrected status.

## Demonstrated

- installed and exercised on Jetson-class target hardware;
- local LLM/service integration;
- MQTT and Redis-backed service architecture;
- local speech pipeline work;
- browser/PWA interface work;
- Linux/systemd deployment and troubleshooting;
- persistent memory/state experiments;
- system diagnostics/tool integration.

## Required before a production claim

- [ ] clean-install test from documented prerequisites on a fresh target;
- [ ] automated core-path acceptance suite;
- [ ] service crash/restart and dependency-failure tests;
- [ ] secrets/configuration audit;
- [ ] network exposure/security review;
- [ ] sustained load, thermal and memory-pressure test;
- [ ] versioned configuration/migration strategy;
- [ ] tagged release with immutable test evidence;
- [ ] rollback/recovery procedure exercised on target hardware;
- [ ] current user-facing demo tied to the tagged release.

## Claims policy

Historical documents may contain service counts, latency measurements, memory counts or model throughput values. Treat those as **snapshots**, not current guarantees, unless the number is reproduced by test evidence on the referenced commit/hardware.

Use [PROJECT_STATUS.md](PROJECT_STATUS.md) as the portfolio source of truth and [TESTING_GUIDE.md](TESTING_GUIDE.md) for verification work.
