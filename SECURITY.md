# Security Policy — Sentient Core

**Reviewed:** 2026-08-13  
**Project maturity:** Deployed prototype / active hardening

Sentient Core is a public repository for a system that runs on private edge hardware. Public source code and private deployment configuration must be treated as separate trust domains.

## 2026-08-13 MQTT credential rotation required

During repository cleanup, a literal MQTT password was found reused across source code and documentation.

Because the repository is public and the value exists in Git history, **that password must be treated as compromised even after it is removed from the current tree**.

Required operational response on every Sentient node that used the historical credential:

1. create a new high-entropy MQTT password;
2. update the Mosquitto password database / broker authentication configuration;
3. provide the new value to services through private runtime configuration such as systemd credentials/environment files with restrictive permissions;
4. restart dependent services in a controlled order;
5. verify publish/subscribe operation;
6. revoke/remove the old password from the broker;
7. do not paste the replacement into Git, README files, shell history or public logs.

History rewriting can reduce accidental discovery of the old value but **does not revoke it**. Rotation is the required security action.

## Runtime configuration

Credentials must not have functional default values in public source.

Use environment/runtime configuration for values such as:

```text
MQTT_BROKER
MQTT_PORT
MQTT_USER
MQTT_PASS
```

A typical systemd deployment should keep secrets outside the unit file committed to Git. Options include a root-readable environment file, systemd credential facilities, or another host secret store appropriate to the deployment.

Do not commit the resulting secret files.

## Network exposure

Sentient services are primarily designed for a private edge node. Before exposing any API, WebSocket, MQTT broker, dashboard or media stream beyond the intended local network:

- identify whether the service has authentication;
- remove default/shared credentials;
- bind to the narrowest interface required;
- restrict ingress with host/network firewall rules;
- use TLS or a trusted private tunnel for sensitive traffic;
- avoid treating source-IP or local-network presence as authentication;
- inspect whether logs or MQTT payloads contain private conversation/sensor data.

## MQTT

For a broker that requires authentication:

- disable anonymous access unless intentionally required;
- use per-service or per-role credentials where practical rather than one account for every node;
- use ACLs to restrict publish/subscribe topics;
- rotate credentials independently;
- avoid putting passwords directly on command lines where process listings/history may expose them;
- consider TLS or a protected overlay/private network when MQTT crosses hosts.

## Public documentation

Older implementation summaries are retained as engineering history and may contain stale claims or configuration examples. Current sources of truth are:

- `PROJECT_STATUS.md` for maturity;
- `PRODUCTION_STATUS.md` for release readiness;
- this `SECURITY.md` for credential/network boundaries.

Historical phrases such as “production-ready” do not override the current **deployed prototype / active hardening** status.

## Secret handling

Never commit:

- passwords or bearer tokens;
- API keys;
- `.env` or secret environment files;
- SSH private keys;
- TLS private keys;
- cloud service-account credentials;
- OAuth refresh/access tokens;
- signing keys;
- production database dumps or private memory stores.

The repository `.gitignore` helps prevent accidents but does not replace staged-file review and secret scanning.

## If a secret reaches Git

1. Assume it is compromised.
2. Revoke/rotate it immediately at the issuing system.
3. Remove it from the current tree.
4. Search for copies in other files, releases, artifacts and sibling repositories.
5. Consider history rewriting for hygiene only after rotation.
6. Review logs/activity for unexpected use where the issuing system supports it.
7. Document the incident without repeating the secret value.

## Reporting

For security issues in this personal R&D repository, avoid opening a public issue containing credentials, private host details or exploit-ready sensitive configuration. Revoke exposed credentials first, then document a redacted remediation record.
