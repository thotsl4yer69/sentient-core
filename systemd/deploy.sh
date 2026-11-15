#!/usr/bin/env bash
# RETIRED 2026-11: this used to stop sentient-core.target and enable every unit.
# That is incompatible with the AIRI-first single-frontend cutover.
set -euo pipefail
cat >&2 <<'MESSAGE'
BLOCKED: legacy bulk deployment has been retired.
This command previously stopped the Sentient Core target and enabled every
service, including competing web/chat interfaces. It cannot safely determine
which installed services are needed by the existing AIRI deployment.

Use these NON-MUTATING commands instead:
  python3 tools/orin_reconcile.py inventory --output ./orin-inventory.json
  bash tools/orin_read_only_audit.sh

For retiring one VERIFIED redundant legacy frontend AFTER 10 real AIRI chat
passes, use tools/orin_reconcile.py prepare -> apply -> accept (or rollback).
For installing or updating backend services, perform a device-specific
reviewed deployment after capturing a real physical baseline and rollback.
No services were changed by this script.
MESSAGE
exit 64
