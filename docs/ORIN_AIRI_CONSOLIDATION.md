# Orin Nano: AIRI-first consolidation (operator runbook)

**Status:** tooling implemented and host-free tests passed. **NOT run on the physical Orin.**
**Hardware:** the one NVIDIA Jetson Orin Nano. User-reported LAN IP `192.168.1.168` (not a verified live address).
**Goal:** AIRI is the only operator-facing AI interface. Background components remain only if a real feature needs them.

## Decision and scope

This project accumulated independent `services/`, `sentient/services/`, Web Chat,
Cortana, avatar viewers, and AIRI character-card experiments. It also has
historical `systemd/install.sh` and `systemd/deploy.sh` paths that enabled the
whole legacy service set. Those scripts are **retired and fail closed** in the
repo. **Do not reinstall everything to try to fix an unidentified problem.**

The current `airi/nano/` directory is **a character card, not an AIRI server**.
The installed AIRI stage, its actual URL and model settings must be discovered
from the physical host. **Do not infer its port from this documentation.**

Official AIRI documentation supports an Ollama provider or an OpenAI-compatible
endpoint in `Settings -> Providers -> Chat`, followed by selection in
`Settings -> Modules -> Consciousness`. Speech and hearing are separate modules.
For a remote browser, `localhost` means the browser's device, not necessarily
the Jetson. Use authenticated private connectivity and narrowly scoped CORS,
never expose unauthenticated Ollama, MQTT or Redis to the public Internet.

AIRI docs:
- https://airi.moeru.ai/docs/en/docs/manual/config/llm
- https://airi.moeru.ai/docs/en/docs/manual/web/

## Explicit non-goals

- No flashing, OS reinstall, resetting SSH host keys, Docker prune, blanket service disable, or installing another AIRI instance.
- No claims that local hardware changed when only GitHub code changed.
- No disabling voice, memory, Ollama, MQTT, Redis, agents, or the SSH/remote-management transport from an inferred dependency map.
- No removing model weights, volume data, AIRI user settings, or character/voice/assets.
- No automatic promotion to `production-ready` from source-level CI.

## 0. Establish identity and connectivity

1. Power on the actual Orin; identify it at a physical keyboard/display or your router's DHCP leases.
2. The last reported address was `192.168.1.168`, which the historical fleet file *also* associated with the Windows ATHENA machine. Verify the MAC/host before accepting an SSH fingerprint.
3. From a trusted computer on the same LAN:

   ```sh
   ssh -o StrictHostKeyChecking=ask nano@192.168.1.168
   ```

   If **connection refused**, SSH is not accepting connections at that address. Rechecking passwords will not fix it. Verify power/IP/port from the machine locally. Never delete `known_hosts` entries or disable strict host-key checking to get around a changed fingerprint.
4. On the confirmed Jetson, verify `hostname`, `hostname -I`, `uname -m`, `/etc/nv_tegra_release` and `df -h` before any write.
5. Reconnect the existing authorized remote session if needed; remote agent installation is a separate, reviewed action.

## 1. Read-only inventory, reproducible

From a **checked-out revision** of this repository on the verified Jetson:

```sh
python3 tools/orin_reconcile.py inventory --output ./orin-inventory.json
bash tools/orin_read_only_audit.sh
```

The Python collector records **metadata**, not environment variables, secrets,
`docker inspect`, process arguments or raw journals. It discovers relevant
device-tree model and SSH public host-key fingerprints, relevant service names,
listener-to-process metadata, model names, containers,
name-based frontend candidates, mic/speaker enumeration, and SHA-256 fingerprints
for small known voice-runtime files without following symlinks. The fingerprints
distinguish the `sentient/services/voice` stack from the separate
`/mnt/orin-ai/orin` native listener before either pending branch is considered.
It also records shallow counts, timestamps and direct-file byte totals for the
known recovery-plan and native-voice backup directories, without exposing child
names or following symlinks. This confirms whether rollback material exists; it
does not prove that a backup is complete or restorable.
Use the public host-key fingerprints to match the pinned SSH identity before
trusting a recycled LAN address; private host keys and key comments are never read.
Candidates are
**investigation leads**, never permission to retire any component.
The shell audit supplies supporting host metadata. Both collect **locally**, and
reports can expose host/service names: review them before sharing. Do not commit
the resulting files. `docker` and audio commands may be unavailable or denied;
absence is not proof that a component does not exist.

**Manual investigation required:** match each listening web port to its actual
unit/container, record its real URL and data/config paths, and label it:

| Component | Disposition | Evidence required |
|---|---|---|
| Installed AIRI web/desktop stage | **KEEP** (sole AI UI) | Version/commit, URL, settings/data location, actual chat screenshot |
| Ollama or currently selected local model endpoint | **KEEP ONE** | model ID, real completion, memory/time readings |
| Sentient Web Chat and legacy Cortana UIs | **CANDIDATE** | exact `systemctl` unit/container and dependency/test map |
| Older ATHENA viewer/orb | **ASSET / DIAGNOSTIC ONLY** | not falsely presented as an AIRI renderer |
| STT / TTS / wake-word | **ONE ACTIVE PATH** | real mic and speaker tests, reconnect recovery |
| Memory, MQTT, Redis, tools, StarNet | **BACKEND ONLY IF USED** | named AIRI/agent consumer and real end-to-end test |
| Unknown ports/containers | **DO NOT TOUCH** | inspect owner, data, consumers, security first |

## 2. Protect existing data and startup behavior

Before mutating anything, create an offline backup/snapshot of:

- AIRI app settings and character/avatar assets, selected model IDs, and the exact installed version/commit.
- Relevant unit files and overrides (`systemctl cat UNIT`), current enable/running states, and service account details.
- Docker Compose definitions and persistent volumes **if that is how AIRI actually runs**.
- Sentient Core memory and databases, model cache metadata, private runtime configuration and keys; copy securely and verify restoration without publishing credentials.
- The git working tree including uncommitted local changes. `main` may **not** represent the current installed build.

Confirm sufficient free disk and ability to restore. **Do not place secret-bearing backups in GitHub**.

## 3. Get AIRI chat working (independent of legacy UI)

1. Identify *existing* AIRI installation and URL. Determine whether it renders on Jetson or in a phone/desktop browser.
2. Identify *existing* Ollama/runtime (`ollama list`, `ollama ps`, local `/api/tags` only if appropriate). **Do not add a second inference server** just to make the UI talk.
3. AIRI `Settings -> Providers -> Chat` -> Ollama (or documented OpenAI Compatible). Use only a reachable private endpoint. AIRI `Settings -> Modules -> Consciousness` -> select the exact installed model.
4. Obtain **10 distinct real model responses**, including an immediate follow-up with context, a timeout/retry and a longer prompt. Keep actual timing, failures and model ID. A working HTML page, green CI, or canned response is insufficient.
5. Confirm AIRI still works after a browser refresh and after reconnecting the actual client.

**Gate:** 10/10 real AIRI text turns; no broken auth/CORS; existing memory/assets intact. Until then, do not retire any UI.

## 4. Reversible one-unit-at-a-time cutover

The cutover helper is intentionally **not** a bulk-cleanup tool. It permits
staging only these exact, ordinary `systemd` service names:

- `sentient-web-chat.service`
- `cortana-web.service`
- `cortana-dashboard.service`
- `open-webui.service`

Any of these may **still be needed** by another service or may be absent. They
are *candidates*, not proof of redundancy. No container mutation is implemented.
If the legacy UI is a Docker container, **do not use this tool to stop it**:
first map its Compose stack, volumes and dependency graph and prepare a distinct
reversible, reviewed change.

Example **only after the exact service is confirmed redundant and AIRI is working**:

```sh
# Determine actual AIRI URL/port from inventory. Below port is illustrative,
# NOT a claim that your AIRI instance listens on 3000.
sudo python3 tools/orin_reconcile.py prepare \
  --unit sentient-web-chat.service \
  --airi-url http://127.0.0.1:3000/
# Prints a private /var/lib/orin-airi-recovery/...json plan and exact Plan ID.

# Check the plan, and independently verify 10 real AIRI replies.
sudo python3 tools/orin_reconcile.py status --plan /PATH/TO/ACTUAL/PLAN.json

# Disable ONLY the named service if genuinely redundant.
sudo python3 tools/orin_reconcile.py apply \
  --plan /PATH/TO/ACTUAL/PLAN.json --confirm ACTUAL_PLAN_ID --airi-chat-tested

# Retest 10 REAL AIRI replies AFTER the cutover, plus its voice/tools dependents.
# If successful, record final acceptance:
sudo python3 tools/orin_reconcile.py accept \
  --plan /PATH/TO/ACTUAL/PLAN.json --confirm ACTUAL_PLAN_ID --airi-chat-tested

# If anything breaks, restore the recorded original enabled/running state:
sudo python3 tools/orin_reconcile.py rollback \
  --plan /PATH/TO/ACTUAL/PLAN.json --confirm ACTUAL_PLAN_ID
```

A `prepare` step makes a `0600` plan file with exact existing `systemd` state,
unit-definition SHA-256 fingerprint and host identity. The helper refuses units
whose `Also=` / `Alias=` install relations, reverse binding/part-of relations,
stop propagation, or socket/timer/path activation could affect other services
or restart the candidate unexpectedly. An altered unit definition invalidates
the staged plan. `apply` checks state drift and the AIRI **HTTP** endpoint before
mutating a single allowlisted service. It disables and stops that one service,
rechecks AIRI HTTP, and attempts **automatic rollback on any error**. The plan
stays **pending acceptance** until a human confirms 10 live post-cutover chat
responses through `accept`. The endpoint check **cannot prove local inference,
voice, avatar, memory, or agent correctness**. Rollback remains available even
after acceptance. It restores the prior `active/inactive` and
`enabled/disabled` states, not an assumed default.

**Limitations:** A sudden power loss while an apply is in progress cannot be
atomically rolled back by an ordinary Python process. Read the persisted phase
and use the matching `rollback` command after the host recovers. If rollback
itself fails, stop all further changes and repair that exact unit manually.

## 5. Voice, avatar, memory and StarNet—in that order

- Enable one real microphone input and one speaker output in AIRI Hearing/Speech. Investigate open USB-reconnect and wake-word PRs (#5, #6, #9); do not merge overlapping code without physical audio test and branch reconciliation.
- Select ONE wake-word owner and ONE transcription/TTS owner. Confirm 10 spoken turns, 10 playback completions and three USB unplug/replug recovery cycles. No duplicated hot-mic streams.
- Retain AIRI character card; import assets only after actual format compatibility tests. A glTF GLB avatar is not automatically a valid VRM character.
- Map memory/tool APIs to named consumers. Do not publicly expose unauthenticated MQTT/Redis; use a narrow authenticated integration. Prefer running background agents on the Nano without more operator-facing dashboards.
- Validate ATHENA/StarNet reachability/authentication and one recorded real job completion; don't conflate Windows ATHENA with the Jetson IP address.

## 6. Final acceptance and state

| Gate | Pass criterion | Current evidence |
|---|---|---|
| Physical identity | Real Jetson + exact revision, port ownership | **Not tested** (offline) |
| One AIRI UI | 10 real model-backed text turns; no duplicate enabled UI | **Not tested** |
| Audio / avatar | 10 real voice turns, 3 USB recoveries; avatar in AIRI | **Not tested** |
| Background services | Tool and StarNet job with actual correlated result | **Not tested** |
| Stability | Three cold boots; restart recovery; 24h monitored run | **Not tested** |
| Source safety | No bulk startup from old installers; transactional cutover tests | **Implemented; host-free tests** |

Only after all real hardware gates pass do we consider an actual release. Until
then: **source hardening completed; physical consolidation pending access**.

