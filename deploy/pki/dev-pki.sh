#!/usr/bin/env bash
#
# CA de DESARROLLO: raíz, intermedia y certificados para el broker, los
# servicios y los equipos. Todo en deploy/pki/out/, que está en .gitignore.
#
#   dev-pki.sh init [host...]     raíz + intermedia + certificado del broker
#   dev-pki.sh device <id>        certificado de un equipo (CN = id)
#   dev-pki.sh service <nombre>   certificado de un servicio (CN = svc:<nombre>)
#
# En producción la raíz NO vive en una máquina conectada: se genera offline, se
# usa sólo para firmar intermedias y se guarda. Las claves de los equipos se
# generan dentro del equipo (o vienen en su chip seguro) y nunca pasan por acá:
# lo que se firma es un CSR. Este script hace las dos cosas mal a propósito,
# porque es para probar en una mesa.

set -euo pipefail

OUT="${AUTOCANN_PKI_DIR:-$(cd "$(dirname "$0")" && pwd)/out}"
DAYS_CA=3650
DAYS_LEAF=825

new_key() { openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$1" 2>/dev/null; chmod 600 "$1"; }

# sign <dir> <nombre> <CN> <extensiones>
sign() {
    local dir=$1 name=$2 cn=$3 ext=$4
    mkdir -p "$OUT/$dir"
    new_key "$OUT/$dir/$name.key"
    openssl req -new -key "$OUT/$dir/$name.key" -subj "/O=Autocann dev/CN=$cn" -out "$OUT/$dir/$name.csr"
    openssl x509 -req -in "$OUT/$dir/$name.csr" -CA "$OUT/intermediate.pem" -CAkey "$OUT/intermediate.key" \
        -CAcreateserial -days "$DAYS_LEAF" -sha256 -extfile <(printf '%s\n' "$ext") -out "$OUT/$dir/$name.pem" 2>/dev/null
    rm "$OUT/$dir/$name.csr"
    echo "✅ $OUT/$dir/$name.pem (CN=$cn)"
}

require_init() {
    [[ -f "$OUT/intermediate.pem" ]] || { echo "❌ Primero: $0 init" >&2; exit 1; }
}

case "${1:-}" in
init)
    shift
    if [[ -f "$OUT/root.pem" ]]; then
        echo "❌ Ya existe una CA en $OUT. Borrala a mano si de verdad querés otra." >&2
        exit 1
    fi
    mkdir -p "$OUT"
    new_key "$OUT/root.key"
    openssl req -x509 -new -key "$OUT/root.key" -subj "/O=Autocann dev/CN=Autocann dev root" -days "$DAYS_CA" -sha256 \
        -addext "basicConstraints=critical,CA:true,pathlen:1" -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -out "$OUT/root.pem"
    new_key "$OUT/intermediate.key"
    openssl req -new -key "$OUT/intermediate.key" -subj "/O=Autocann dev/CN=Autocann dev intermediate" \
        -out "$OUT/intermediate.csr"
    openssl x509 -req -in "$OUT/intermediate.csr" -CA "$OUT/root.pem" -CAkey "$OUT/root.key" -CAcreateserial \
        -days "$DAYS_CA" -sha256 -out "$OUT/intermediate.pem" 2>/dev/null \
        -extfile <(printf '%s\n' "basicConstraints=critical,CA:true,pathlen:0" "keyUsage=critical,keyCertSign,cRLSign")
    rm "$OUT/intermediate.csr"
    cat "$OUT/intermediate.pem" "$OUT/root.pem" > "$OUT/ca-chain.pem"
    echo "✅ CA en $OUT (cadena: ca-chain.pem)"

    san="DNS:localhost,IP:127.0.0.1"
    for host in "$@"; do
        if [[ $host =~ ^[0-9.]+$ ]]; then san+=",IP:$host"; else san+=",DNS:$host"; fi
    done
    sign broker broker "autocann-broker" "basicConstraints=CA:false
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
subjectAltName=$san"
    ;;
device)
    require_init
    id=${2:-}
    # El mismo formato que DeviceId en crates/protocol.
    if ! [[ $id =~ ^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$ ]]; then
        echo "❌ id de equipo inválido: '$id' (minúsculas, dígitos y '-', de 3 a 64)" >&2
        exit 1
    fi
    sign devices "$id" "$id" "basicConstraints=CA:false
keyUsage=critical,digitalSignature
extendedKeyUsage=clientAuth"
    ;;
service)
    require_init
    name=${2:-}
    if ! [[ $name =~ ^[a-z0-9-]+$ ]]; then
        echo "❌ nombre de servicio inválido: '$name'" >&2
        exit 1
    fi
    # El ':' es lo que impide que un equipo se llame igual que un servicio.
    sign services "$name" "svc:$name" "basicConstraints=CA:false
keyUsage=critical,digitalSignature
extendedKeyUsage=clientAuth"
    ;;
*)
    sed -n '3,9p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
