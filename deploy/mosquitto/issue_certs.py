#!/usr/bin/env python3
"""Issue a local CA and a server certificate for the Mosquitto TLS listener.

The certificate covers localhost, nanob, nanob.lan, and 127.0.0.1. Private
keys are written mode 0600. Nothing is printed. The destination must sit
outside this repository.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from apply_acl import AclError, assert_dest_outside_repo


def issue(dest: Path) -> None:
    assert_dest_outside_repo(dest)
    if shutil.which("openssl") is None:
        raise AclError("openssl is required to issue the local CA")
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    ca_key = dest / "ca.key"
    ca_cert = dest / "ca.crt"
    server_key = dest / "server.key"
    server_cert = dest / "server.crt"
    csr = dest / "server.csr"
    serial = dest / "ca.srl"
    ext = dest / "server.ext"
    _run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-days",
            "3650",
            "-nodes",
            "-keyout",
            str(ca_key),
            "-out",
            str(ca_cert),
            "-subj",
            "/CN=sc-local-ca",
        ]
    )
    _run(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-nodes",
            "-keyout",
            str(server_key),
            "-out",
            str(csr),
            "-subj",
            "/CN=nanob.lan",
        ]
    )
    ext.write_text(
        "\n".join(
            [
                "subjectAltName = DNS:localhost,DNS:nanob,DNS:nanob.lan,IP:127.0.0.1",
                "extendedKeyUsage = serverAuth",
                "basicConstraints = CA:FALSE",
                "keyUsage = digitalSignature, keyEncipherment",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(csr),
            "-CA",
            str(ca_cert),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(server_cert),
            "-days",
            "825",
            "-sha256",
            "-extfile",
            str(ext),
        ]
    )
    verify = subprocess.run(
        ["openssl", "verify", "-CAfile", str(ca_cert), str(server_cert)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if verify.returncode != 0:
        raise AclError("issued server certificate did not verify against the local CA")
    for path in (ca_key, server_key):
        os.chmod(path, 0o600)
    for path in (ca_cert, server_cert):
        os.chmod(path, 0o644)
    csr.unlink(missing_ok=True)
    ext.unlink(missing_ok=True)
    serial.unlink(missing_ok=True)


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise AclError("openssl failed while issuing the local CA")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Issue the local Mosquitto CA")
    parser.add_argument("--dest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        issue(args.dest)
    except AclError as exc:
        print(f"sc-mosquitto: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
