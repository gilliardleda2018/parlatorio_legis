#!/bin/bash
# Jitsi Meet próprio da Câmara, pré-configurado para o Parlatório Legis (docker-jitsi-meet oficial).
#
# - Só entra na videoconferência quem está logado no Parlatório: o Jitsi exige um token (JWT)
#   que o servidor do Parlatório emite; o presidente entra como moderador.
# - Usa o mesmo certificado HTTPS da Câmara do Parlatório (aparelhos que já confiam no
#   Parlatório confiam no vídeo).
# - Endereço: https://<nome>.local:8443 (porta configurável).
#
# Uso, como root, depois do instalar.sh:   bash jitsi.sh
# Variáveis opcionais:
#   JITSI_PORTA=8443      porta HTTPS do Jitsi
#   JITSI_DIR=/opt/jitsi  onde ficam os arquivos do Jitsi
#   IP_PUBLICO=x.x.x.x    para vereadores fora da rede: IP público da Câmara (abra no roteador a
#                         porta TCP JITSI_PORTA e a UDP 10000 para este servidor)
#   SEM_PARLATORIO=1      só instala o Jitsi (não mexe em /etc/parlatorio.env) — usado em testes
set -euo pipefail

NOME="${NOME:-$(hostname)}"
JITSI_DIR="${JITSI_DIR:-/opt/jitsi}"
JITSI_PORTA="${JITSI_PORTA:-8443}"
JITSI_HTTP="${JITSI_HTTP:-8180}"
JVB_PORTA="${JVB_PORTA:-10000}"
DADOS="${DADOS:-/var/lib/parlatorio}"
TLS_DIR="${TLS_DIR:-$DADOS/tls}"
IP_LOCAL="${IP_LOCAL:-$(hostname -I | awk '{print $1}')}"
[ -f /etc/parlatorio.env ] && DOMINIO="${DOMINIO:-$(grep -E '^DOMINIO=' /etc/parlatorio.env | cut -d= -f2- || true)}"
HOST_JITSI="${HOST_JITSI:-${DOMINIO:-$NOME.local}}"
URL="https://$HOST_JITSI:$JITSI_PORTA"

if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q docker.io docker-compose curl python3
  systemctl enable --now docker
fi
[ -f "$TLS_DIR/servidor.crt" ] || { echo "Certificado do Parlatório não encontrado em $TLS_DIR (rode o instalar.sh antes)."; exit 1; }

mkdir -p "$JITSI_DIR"
if [ ! -f "$JITSI_DIR/docker-compose.yml" ]; then
  echo "Baixando a versão estável do docker-jitsi-meet..."
  TAR=$(curl -fsSL https://api.github.com/repos/jitsi/docker-jitsi-meet/releases/latest | python3 -c "import json,sys; print(json.load(sys.stdin)['tarball_url'])")
  curl -fsSL "$TAR" | tar -xz -C "$JITSI_DIR" --strip-components=1
fi
cd "$JITSI_DIR"
if [ ! -f .env ]; then
  cp env.example .env
  ./gen-passwords.sh
fi

# segredo do token compartilhado com o Parlatório (mantido nas reinstalações)
SEGREDO=$(grep -E '^JWT_APP_SECRET=.+' .env | cut -d= -f2- || true)
[ -n "$SEGREDO" ] || SEGREDO=$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')

definir() {  # definir CHAVE VALOR: troca a linha (comentada ou não) ou acrescenta
  python3 - "$1" "$2" <<'PY'
import re, sys
chave, valor = sys.argv[1], sys.argv[2]
linhas = open(".env").read().splitlines()
novo = f"{chave}={valor}"
for i, l in enumerate(linhas):
    if re.match(rf"^#?\s*{re.escape(chave)}=", l):
        linhas[i] = novo
        break
else:
    linhas.append(novo)
open(".env", "w").write("\n".join(linhas) + "\n")
PY
}
definir CONFIG "$JITSI_DIR/config"
definir HTTP_PORT "$JITSI_HTTP"
definir HTTPS_PORT "$JITSI_PORTA"
definir JVB_PORT "$JVB_PORTA"
definir JVB_COLIBRI_PORT "${JVB_COLIBRI:-18080}"  # 8080 costuma estar ocupada (AzuraCast, painéis)
definir TZ America/Sao_Paulo
definir PUBLIC_URL "$URL"
definir JVB_ADVERTISE_IPS "${IP_PUBLICO:+$IP_PUBLICO,}$IP_LOCAL"
definir ENABLE_LETSENCRYPT 0
definir ENABLE_HTTP_REDIRECT 0
definir ENABLE_AUTH 1
definir AUTH_TYPE jwt
definir ENABLE_GUESTS 0
definir JWT_APP_ID parlatorio
definir JWT_APP_SECRET "$SEGREDO"
definir JWT_ACCEPTED_AUDIENCES jitsi
definir JWT_ALLOW_EMPTY 0
definir ENABLE_AUTO_OWNER 0
definir ENABLE_WELCOME_PAGE 0
definir ENABLE_PREJOIN_PAGE 0
definir ENABLE_RECORDING 0
definir DEFAULT_LANGUAGE ptBR
chmod 600 .env

mkdir -p config/{web/keys,web/custom-ca,transcripts,prosody/config,prosody/custom-ca,prosody/prosody-plugins-custom,jicofo/custom-ca,jvb/custom-ca,jigasi,jibri,storage/prosody,storage/transcripts,storage/web,tmp/web-load-test}
# os contêineres rodam como uid 1000 e precisam escrever nessas pastas
[ "$(id -u)" = 0 ] && chown -R 1000:1000 config
# mesmo certificado do Parlatório: o público do domínio, se houver; senão o da autoridade da Câmara
if [ -n "${DOMINIO:-}" ] && [ -f "$DADOS/tls-publico/fullchain.pem" ]; then
  cp "$DADOS/tls-publico/fullchain.pem" config/web/keys/cert.crt
  cp "$DADOS/tls-publico/privkey.pem" config/web/keys/cert.key
else
  cat "$TLS_DIR/servidor.crt" "$TLS_DIR/ca.crt" > config/web/keys/cert.crt
  cp "$TLS_DIR/servidor.key" config/web/keys/cert.key
fi
chmod 600 config/web/keys/cert.key

docker compose up -d

if [ "${SEM_PARLATORIO:-0}" != "1" ] && [ -f /etc/parlatorio.env ]; then
  for k in JITSI_URL JITSI_SEGREDO JITSI_APP_ID; do sed -i "/^$k=/d" /etc/parlatorio.env; done
  { echo "JITSI_URL=$URL"; echo "JITSI_APP_ID=parlatorio"; echo "JITSI_SEGREDO=$SEGREDO"; } >> /etc/parlatorio.env
  grep -q "$HOST_JITSI" /etc/parlatorio.env || true
  systemctl restart parlatorio
fi

echo
echo "Jitsi da Câmara: $URL"
echo "A sessão remota do Parlatório já aponta para ele (Painel → Plenário virtual → Sessão híbrida)."
if [ -n "${IP_PUBLICO:-}" ]; then
  echo "Para vereadores fora da rede: no roteador, encaminhe TCP $JITSI_PORTA e UDP $JVB_PORTA para $IP_LOCAL."
fi
