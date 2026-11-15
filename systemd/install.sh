#!/usr/bin/env bash
# RETIRED 2026-11: legacy script bulk-enabled nine competing services.
set -euo pipefail
cat >&2 <<'MESSAGE'
BLOCKED: legacy bulk installation has been retired.
The previous script copied and enabled the entire Sentient Core service set,
including a redundant web frontend. The installation state of the real Orin
Nano must be inventoried before any deliberate change.

Use: python3 tools/orin_reconcile.py inventory --output ./orin-inventory.json
See: docs/ORIN_AIRI_CONSOLIDATION.md
No services were changed by this script.
MESSAGE
exit 64
