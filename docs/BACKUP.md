# Orin backup and restore

**Status:** host-free tests passed. **Not run on the physical Orin (nanob).**

This is the Gate A backup for the NVIDIA Jetson Orin Nano. It writes one age-encrypted archive and can restore that archive into a scratch directory, then check every restored file against a sha256 manifest. `tools/orin_reconcile.py` only records whether backup directories exist. It does not create a restorable copy. This tool does.

Python 3.10+, standard library only. `age`, `git`, and `redis-cli` are required system tools. No Python package was added.

## What a backup contains

- Each SQLite database named with `--sqlite`, copied with the SQLite online backup API (the same operation as the `.backup` command). The source is opened read-only. An uncommitted transaction is left out. The copy must pass `integrity_check`.
- A Redis RDB snapshot. `redis-cli --rdb` asks loopback Redis for the snapshot a BGSAVE produces and writes that file under the backup destination.
- The unit file and drop-in directory for every `--unit`, found under the systemd roots you name.
- Every file or directory named with `--etc`: service configs and private env files.
- The AIRI settings and assets directory. Gate A discovers the path. Pass it with `--airi-path`. There is no built-in AIRI location.
- The runtime tree. On nanob that is `/mnt/orin-ai/orin`.
- A `git bundle --all` for every named checkout and for every git repository found under the discovery roots, including `/opt/sentient-core`. Each checkout also stores `git diff --binary HEAD` and the untracked files that are not ignored. A clean checkout stores an empty diff file so the manifest still lists it.
- `manifest.json` and `SHA256SUMS` inside the archive, plus a `<archive>.sha256` sidecar beside the ciphertext.

## What stays out of the archive

- Private SSH host keys. Name a file with `--etc` only when you mean to encrypt that file into the archive. Do not name `/etc/ssh`.
- Gitignored files. The manifest records how many were left out. An ignored file whose name looks like a secret (`.env`, `.env.*` other than examples, `sentient.env`, `secrets.env`, `credentials`, `credentials.json`, `id_rsa`, `id_ed25519`, `id_ecdsa`, `*.pem`, `*.key`) fails the run until you also pass that path with `--etc` or `--sqlite`.
- Submodule contents. A checkout that contains a gitlink fails. Pass the submodule checkout with its own `--git-checkout`.
- The age identity. Encryption uses the public recipient only.
- The bytes behind a symlink that sits inside a tree. The link text is stored. The directory you name (`--airi-path`, `--runtime-path`, and the other roots) is resolved once, and that resolved tree is what gets copied.

## Read-only rule

The tool reads the live system. It writes under `--dest` (the archive, the sha256 sidecar, and a temporary directory that is removed when the run finishes). The Redis snapshot is the other write: the server builds an RDB and `redis-cli` stores it under `--dest`.

`--dry-run` runs the same existence, readability, git, age, and Redis PING checks, prints a JSON plan, and writes nothing. It does not ask Redis for a snapshot.

A named path that is missing, unreadable, or not the kind of object the tool expects fails the run. The message lists those components. There is no flag that skips one.

## Age key

Create the key pair on a trusted machine, off the Jetson:

```sh
age-keygen -o nanob-backup-identity.txt
```

The comment line `# public key: age1...` is the recipient. Copy that public key to the Jetson. Leave the identity file offline. Do not commit the identity, and do not place it inside a directory you back up.

The unit tests were run with age 1.2.1. The ciphertext starts with `age-encryption.org/v1`.

## Commands

Dry-run the strict nanob set once Gate A knows the AIRI directory and the database files:

```sh
python3 tools/orin_backup.py backup \
  --dest /mnt/orin-ai/backups \
  --profile nanob \
  --airi-path /PATH/FROM/GATE/A \
  --sqlite /PATH/TO/memory.db \
  --redis-password-file /PATH/TO/redis.pass \
  --age-recipient age1... \
  --dry-run
```

Drop `--dry-run` to write `orin-backup-<UTC>.tar.age` (mode `0600`) in a destination directory created mode `0700` when it is new. Repeat `--sqlite`, `--unit`, `--etc`, `--git-checkout`, and `--age-recipient` for further values. When you pass one of those flags, it replaces the profile list for that field.

`--profile nanob` requires all of the following to exist:

| Item | Path or name |
|---|---|
| Runtime | `/mnt/orin-ai/orin` |
| Git checkout | `/opt/sentient-core` |
| Units | `airi.service`, `orin-voice.service`, `ollama.service`, `mosquitto.service`, `redis-server.service`, `sentient-core.target`, `sentient-web-chat.service`, `sentient-avatar.service`, `sentient-voice.service`, `sentient-conversation.service`, `sentient-wake-word.service` |
| Config files | `/etc/mosquitto/mosquitto.conf`, `/etc/redis/redis.conf`, `/etc/nvpmodel.conf`, `/etc/nv_tegra_release` |
| systemd roots | `/etc/systemd/system`, `/usr/lib/systemd/system`, `/lib/systemd/system` |
| Redis | `127.0.0.1:6379` |

Discovery roots, recorded even when absent, are `/opt/sentient-core`, `/opt/sentient`, `/opt/sc`, `/opt/airi`, `/home/cortana/sentient-core`, `/home/cortana/cortana/backend`, `/home/cortana/airi`, `/home/nano/airi`, and `/mnt/orin-ai`. The tool checks each root and its immediate child directories. A missing root is a manifest entry with state `absent`. A child that is a git repository is bundled. `--no-discover` turns this scan off. Add `--discover-root` or `--git-checkout` for a checkout Gate A finds outside the list.

If the installed unit set is smaller than the profile, write a spec from the inventory and pass `--spec`. A spec and `--profile` together are refused. Unknown spec keys are refused. Schema is `1`.

```json
{
  "schema": 1,
  "sqlite": ["/mnt/orin-ai/orin/data/memory.db"],
  "airi_path": "/PATH/FROM/GATE/A",
  "runtime_path": "/mnt/orin-ai/orin",
  "git_checkouts": ["/opt/sentient-core"],
  "systemd_roots": ["/etc/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system"],
  "units": ["sentient-voice.service", "redis-server.service"],
  "etc_paths": ["/etc/redis/redis.conf", "/opt/sentient-core/config/sentient.env"],
  "redis_host": "127.0.0.1",
  "redis_port": 6379,
  "redis_password_file": "/etc/sc/redis.pass",
  "discover_roots": ["/opt/sentient-core", "/mnt/orin-ai"],
  "age_recipients": ["age1..."]
}
```

`redis_password_file` may be JSON `null`. `redis_cli` and `age_bin` may be omitted. The password file is read into `REDISCLI_AUTH` for `redis-cli`. It is not placed on the command line.

```sh
python3 tools/orin_backup.py backup \
  --spec /path/orin-backup-spec.json \
  --dest /mnt/orin-ai/backups
```

## Verify into a scratch directory

```sh
python3 tools/orin_backup.py verify \
  --archive /mnt/orin-ai/backups/orin-backup-YYYYMMDDTHHMMSSZ.tar.age \
  --scratch /mnt/orin-ai/backups/restore-test \
  --identity /offline/nanob-backup-identity.txt
```

`--scratch` must not already exist. Verify decrypts the archive, extracts it, recomputes a sha256 for every restored file and symlink, and compares those digests to `SHA256SUMS` and to `manifest.json`. It then runs SQLite `integrity_check`, checks that the Redis file starts with the RDB header `REDIS`, clones each git bundle, and runs `git apply --check` when the diff is non-empty.

When `<archive>.sha256` sits beside the archive, that sidecar is compared to the ciphertext before decrypt. A missing sidecar leaves the inner manifest check in place.

Exit 0 with `"ok": true` means the scratch copy matched the manifest. Exit 1 names the path or component that failed. The scratch directory contains plaintext. Delete it after the check. Do not commit it.

## Archive layout

```text
manifest.json
SHA256SUMS
sqlite/000-<database name>
redis/dump.rdb
systemd/<index>/<unit and drop-ins>
etc/<index>-<name>
airi/...
runtime/...
git/<index>/repo.bundle
git/<index>/dirty.diff
git/<index>/untracked/...
git/<index>/meta.json
git/<index>/HEAD
```

Symlink entries in `SHA256SUMS` are the sha256 of the link text, not the file the link points at.

## Failure behavior

Exit 1. The reason is printed on stderr and is a list when several components are missing. If this run created the destination and then failed, that directory is removed. A destination that already existed keeps unrelated files; a partial archive from the failed run is removed.

The destination must sit outside every source tree, so a backup of `/mnt/orin-ai/orin` cannot be written inside that tree. `/mnt/orin-ai/backups` is a suitable destination.

## Tests

```sh
python3 -m unittest tests.unit.test_orin_backup -v
```

The tests use temporary directories, real SQLite, real git, real age, and a fake `redis-cli`. They do not contact systemd, the network, or a Jetson. `age` must be on `PATH` (the suite was run with age 1.2.1). Existing unit tests are unchanged.

## What nanob still has to prove

Gate A is an on-device check. The unit tests do not substitute for it.

- Confirm the host (MAC, SSH host key fingerprint, L4T R36.4.7) before trusting any path.
- Use the AIRI directory, SQLite files, and unit list that inventory actually found. `--profile nanob` fails closed when a default unit or `/etc` file is absent; a spec from that inventory is the backup contract.
- Run the backup against the live loopback Redis, including `requirepass` if it is set.
- Include `/opt/sentient-core` and every other dirty checkout, with uncommitted files.
- Confirm `/mnt/orin-ai/orin` fits on the destination filesystem. The tool refuses the run when free space is below the size of the named files plus 32 MiB. A git bundle of a long history can be larger than that floor.
- Restore into a scratch directory on the device, or on a trusted machine that holds the offline identity, and require a matching manifest.

Do not put the archive or the identity in git. The archive can contain service config and secrets. Merging the tool is source work. The Gate A restore on nanob is the acceptance check.
