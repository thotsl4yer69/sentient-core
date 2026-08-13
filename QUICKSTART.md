# Sentient Core — Quickstart

> **Current maturity: Deployed prototype / active hardening.**  
> This guide is for running and inspecting an existing Sentient Core development node. It does not imply that every historical service is active on every revision. Read [PROJECT_STATUS.md](PROJECT_STATUS.md) and [SECURITY.md](SECURITY.md) first.

## 1. Confirm the target node

Sentient Core is designed for a Linux/Jetson environment. Do not copy historical private IP addresses from old commits. Use the address/hostname assigned to the node you are actually testing.

```bash
hostname
hostname -I
uname -a
```

If this is a fresh machine, follow the deployment/rebuild documentation instead of assuming an old `/opt/sentient-core` installation exists.

## 2. Check required runtime configuration

The current public source does **not** provide a working MQTT password default. Services that use authenticated MQTT require private runtime configuration such as:

```text
MQTT_BROKER
MQTT_PORT
MQTT_USER
MQTT_PASS
```

Do not paste replacement credentials into Git, README files or shared shell history. See [SECURITY.md](SECURITY.md).

## 3. Inspect service state

On an existing systemd-managed development node:

```bash
systemctl list-units 'sentient-*.service' --no-pager
```

Inspect a specific service before restarting it:

```bash
systemctl status sentient-conversation.service --no-pager
journalctl -u sentient-conversation.service -n 100 --no-pager
```

Service names have evolved across revisions. Treat the repository/service files on the tested commit as the source of truth rather than assuming an old service count.

## 4. Check local dependencies

Depending on the selected build, Sentient Core can use infrastructure such as MQTT, Redis and Ollama/local inference.

Typical local checks:

```bash
systemctl status mosquitto --no-pager || true
systemctl status redis-server --no-pager || true
systemctl status ollama --no-pager || true
```

Then use the project health/test tooling for the exact revision under test.

## 5. Launch the available interface

The repository has experimented with web, terminal and voice interfaces. Use the interface documented by the tested revision.

For a web interface, first confirm the service/port locally rather than opening an old hard-coded LAN URL:

```bash
ss -lntp | grep -E ':(3001|8001|8002|8003|9001)\b' || true
```

For a CLI build, inspect `interfaces/` and run the current entry point from the project environment.

Voice/wake-word operation depends on real audio hardware and the active service configuration; software presence alone is not proof that the full microphone-to-TTS path is functioning.

## 6. Verify the core path

A meaningful smoke test should confirm the chain that matters for the selected build:

```text
user input
   ↓
conversation/orchestration
   ↓
memory + state/context
   ↓
local model generation
   ↓
response output
```

Record the exact commit, model, hardware and service state with any performance measurement. Historical latency, memory-count and service-count values elsewhere in the repository are snapshots rather than guarantees.

## 7. Logs and recovery

Use targeted logs before broad restarts:

```bash
journalctl -u sentient-conversation.service -n 100 --no-pager
journalctl -u ollama -n 100 --no-pager
```

If a component is unhealthy, identify whether the failure is configuration, dependency, resource pressure or application code before restarting the entire stack.

A full restart can be useful during development, but it should not substitute for understanding recovery behaviour when assessing release readiness.

## 8. MQTT testing

Do not use the historical password that existed in public Git history. It is compromised and must be rotated at the broker.

For authenticated client testing, supply the **new** credential through private runtime configuration or a client configuration/credential mechanism appropriate to the installed Mosquitto tools. Avoid publishing passwords directly in documentation or committed scripts.

Also verify broker ACLs: a successful login should not automatically imply read/write permission to every `sentient/#` topic.

## 9. Performance expectations

Jetson performance depends on the exact model, quantisation/runtime, thermal/power mode, concurrent services and prompt length. Treat old figures such as “20–40 seconds” or fixed RAM/GPU numbers as historical measurements only.

When benchmarking, capture:

- Jetson model and power mode;
- software commit;
- model/runtime version;
- first-token and complete-response latency;
- CPU/GPU/RAM/thermal state;
- whether the model was cold or already loaded.

## 10. Before calling a build deploy-ready

Use [PRODUCTION_STATUS.md](PRODUCTION_STATUS.md) and [PROJECT_STATUS.md](PROJECT_STATUS.md). Current release gates include repeatable installation, automated acceptance tests, service recovery, secrets/network review, sustained resource testing, tagged releases and rollback/recovery evidence.

## Documentation

- [PROJECT_STATUS.md](PROJECT_STATUS.md) — current maturity/evidence boundary
- [SECURITY.md](SECURITY.md) — credential/network rules and MQTT rotation note
- [PRODUCTION_STATUS.md](PRODUCTION_STATUS.md) — release-readiness gates
- [ARCHITECTURE.md](ARCHITECTURE.md) — architecture snapshot
- [TESTING_GUIDE.md](TESTING_GUIDE.md) — deeper testing workflow
- [CLI_DEPLOYMENT_GUIDE.md](CLI_DEPLOYMENT_GUIDE.md) — deployment/CLI notes

Historical implementation summaries remain in the repository as engineering history. When they conflict with the documents above, the current status/security documents take precedence.
