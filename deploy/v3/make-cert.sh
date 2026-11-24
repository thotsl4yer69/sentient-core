#!/usr/bin/env bash
# Local CA + server certificate for sc-core on the LAN.
#
#   sudo deploy/v3/make-cert.sh 192.168.1.159 nanob nanob.local
#
# Writes /etc/sc/tls/{ca.crt,ca.key,server.crt,server.key}. Install ca.crt on
# the phone/PC that runs AIRI (it's the only file you copy off the Orin), then
# set in /etc/sc/sc.toml:
#   bind_host = "0.0.0.0"
#   tls_cert = "/etc/sc/tls/server.crt"
#   tls_key  = "/etc/sc/tls/server.key"
# Re-running keeps the CA and re-issues the server certificate.
set -Eeuo pipefail
(( EUID == 0 )) || { echo "run with sudo" >&2; exit 1; }
(( $# >= 1 )) || { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
command -v openssl >/dev/null || { echo "openssl missing" >&2; exit 1; }

DIR=${SC_TLS_DIR:-/etc/sc/tls}
install -d -m 0750 -o root -g "$(getent group sc >/dev/null && echo sc || echo root)" "$DIR"
cd "$DIR"
umask 077

san=""
for name in "$@"; do
  if [[ "$name" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ || "$name" == *:* ]]; then
    san+="IP:$name,"
  elif [[ "$name" =~ ^[A-Za-z0-9.-]+$ ]]; then
    san+="DNS:$name,"
  else
    echo "not an IP or hostname: $name" >&2; exit 2
  fi
done
san+="IP:127.0.0.1,DNS:localhost"

if [[ ! -f ca.key ]]; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
    -keyout ca.key -out ca.crt -subj "/CN=Sentient Core local CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
fi
openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
  -keyout server.key -out server.csr -subj "/CN=$1" 2>/dev/null
printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature\nextendedKeyUsage=serverAuth\n' "$san" > ext.cnf
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 \
  -extfile ext.cnf -out server.crt 2>/dev/null
rm -f server.csr ext.cnf ca.srl
openssl verify -CAfile ca.crt server.crt >/dev/null
chmod 0644 ca.crt server.crt
chmod 0600 ca.key
if getent group sc >/dev/null; then chgrp sc server.key; chmod 0640 server.key; else chmod 0600 server.key; fi
echo "server.crt for: $san"
echo "copy $DIR/ca.crt to the AIRI device and trust it"
