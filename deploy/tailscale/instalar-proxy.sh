#!/bin/bash
# Inicia um nó dedicado da Câmara e configura os proxies locais depois da autenticação.
set -euo pipefail
DADOS=$1
ETC=$2
NOME=$3
PORTA_LEGIS=$4
PORTA_AZURA=${5:-}
MODO=${6:-serve}
CONTAINER=legis-tailscale

mkdir -p "$DADOS/tailscale"
chmod 700 "$DADOS/tailscale"
SEGREDOS="$ETC/tailscale-chave"
install -d -m 700 "$SEGREDOS"
CHAVE="$SEGREDOS/authkey"
if [ -s "$ETC/.tailscale-chave" ]; then
  mv "$ETC/.tailscale-chave" "$CHAVE"
fi
if ! docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  OPCAO_CHAVE=()
  if [ -s "$CHAVE" ]; then
    OPCAO_CHAVE=(--mount "type=bind,src=$SEGREDOS,dst=/run/legis-key,readonly" -e TS_AUTHKEY=file:/run/legis-key/authkey)
  fi
  docker run -d --name "$CONTAINER" --restart unless-stopped --network host \
    -e TS_HOSTNAME="$NOME" -e TS_USERSPACE=true -e TS_AUTH_ONCE=true \
    -e TS_STATE_DIR=/var/lib/tailscale "${OPCAO_CHAVE[@]}" \
    -v "$DADOS/tailscale:/var/lib/tailscale" tailscale/tailscale:stable >/dev/null
else
  docker start "$CONTAINER" >/dev/null
fi

estado() { docker exec "$CONTAINER" tailscale status --json 2>/dev/null; }
dns=""
for tentativa in $(seq 1 24); do
  dns=$(estado | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("Self") or {}).get("DNSName","").rstrip(".") if d.get("BackendState") == "Running" else "")' 2>/dev/null || true)
  [ -n "$dns" ] && break
  if [ ! -s "$CHAVE" ] && [ "$tentativa" -ge 3 ]; then break; fi
  sleep 5
done
if [ -z "$dns" ]; then
  echo "Tailscale aguarda a autorização desta Câmara. O Legis e o AzuraCast continuam locais."
  docker logs "$CONTAINER" 2>&1 | sed -n 's|.*\(https://login\.tailscale\.com/a/[A-Za-z0-9_-]\+\).*|Abra \1 para autorizar este servidor.|p' | tail -1
  exit 2
fi

rm -f "$CHAVE"
docker exec "$CONTAINER" tailscale "$MODO" --bg --https=443 "https+insecure://127.0.0.1:$PORTA_LEGIS"
printf 'https://%s\n' "$dns" > "$ETC/endereco-internet"
if [ -n "$PORTA_AZURA" ]; then
  docker exec "$CONTAINER" tailscale serve --bg --https=8443 "http://127.0.0.1:$PORTA_AZURA"
  printf 'https://%s:8443\n' "$dns" > "$ETC/endereco-azuracast"
fi
