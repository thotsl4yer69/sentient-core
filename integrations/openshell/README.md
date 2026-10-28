# OpenShell integration — Sentient Core / Orin Nano

**Current status:** code implemented and unit-tested, **not installed on a physical gateway or Nano**.

Architecture: a separate compatible Linux host runs OpenShell, while the existing Orin Nano continues serving its local Ollama model. A key-authenticated SSH tunnel transports inference requests without enabling a new public or LAN-accessible model endpoint. This does **not** change JetPack, CUDA, the avatar, wake-word detection, vision or existing services.

    OpenShell gateway (kernel >= 6.2 / Landlock ABI >= 3 / Docker >= 28)
       | OpenShell isolated Python diagnostic agent
       | provider whitelist: host.openshell.internal:11435
       | SSH Docker-bridge-only listener: host-gateway:11435 -> Nano 127.0.0.1:11434
       +-------------------------------------> Orin Nano Ollama (unchanged)

## Gateway steps (when an authorised machine is reachable)

1. On a separate, suitable Linux host, use `python3 integrations/openshell/preflight.py`. This is read-only and refuses older kernels, disabled Landlock, or unsupported Docker. Use Docker Engine 28 or later.
2. Confirm the Nano's Ollama daemon answers from the Nano at `127.0.0.1:11434` and the gateway already has trusted host-key and SSH-key access. Do not expose Ollama on `0.0.0.0`. The tunnel listens **only on the Docker default bridge gateway IP**, not on the machine's LAN or wildcard interface. On Linux, `host.openshell.internal` must resolve to the same Docker host-gateway address; confirm with the sandbox smoke test.
3. On the gateway host set `NANO_SSH_HOST` and `NANO_SSH_USER` to the live, verified Nano SSH endpoint; run `bash integrations/openshell/ssh-tunnel.sh` under an appropriate service manager. The script requires Docker's default `bridge` network and a pre-trusted SSH host key; it refuses passwords and new host keys. The tunnel refuses new SSH host keys and password prompts; it never alters the Nano.
4. The deploy script installs **pinned OpenShell v0.1.5** on the compatible gateway if absent, using NVIDIA's [official installer](https://docs.nvidia.com/openshell/latest/about/installation/). Do not install directly onto an unsupported JetPack 6 kernel.
5. With the tunnel available, run `bash integrations/openshell/deploy-gateway.sh` on that host. It qualifies the host, builds a small CPU-only image, lints/imports a narrowly-scoped provider, creates a sandbox once, inspects effective policy, and executes an Ollama inventory smoke test.
6. Run a bounded local-model inference probe with `openshell sandbox exec --name mazlabz-orin-diagnostics -- python3 /opt/mazlabz/diagnostic_agent.py --prompt 'Summarise the available local inference capabilities.'`.

The diagnostic agent is deliberately **read-only** except for model inference. It does not execute shell commands, access credentials, operate RF hardware, control Home Assistant, or edit files. It selects an already-installed suitable Ollama model, without automatically downloading weights.

## Validation and rollback

Unit tests: `python3 -m unittest discover -s integrations/openshell/tests -v`; Bash syntax: `bash -n integrations/openshell/*.sh`. Runtime proof still requires a real reachable OpenShell host and Nano. Read-only tests are not evidence of successful physical installation.

To roll back, stop the gateway-host SSH tunnel and inspect `openshell sandbox list` and `openshell provider list` before removing **only** `mazlabz-orin-diagnostics` and `mazlabz-orin-ollama` with the matching OpenShell deletion commands. The Nano's original software and configuration are unchanged.

Source documentation: [NVIDIA support matrix](https://docs.nvidia.com/openshell/latest/about/support-matrix/), [self-hosted inference profiles](https://docs.nvidia.com/openshell/latest/how-it-works/inference/).
