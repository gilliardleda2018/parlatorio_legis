#!/bin/bash
# Jitsi Meet próprio da Câmara, pré-configurado para o LEGIS (docker-jitsi-meet oficial).
#
# - Só entra na videoconferência quem está logado no LEGIS: o Jitsi exige um token (JWT)
#   que o servidor do LEGIS emite; o presidente entra como moderador.
# - Usa o mesmo certificado HTTPS do LEGIS (aparelhos que já confiam no
#   LEGIS confiam no vídeo).
# - Endereço: https://<nome>.local:8443 (porta configurável).
#
# No pacote .deb: chamado por "legis extras" (segundo plano), com JITSI=true em /etc/legis/instalacao.conf.
# À mão, como root:   bash /opt/legis/deploy/jitsi/instalar-jitsi.sh
# Variáveis opcionais:
#   JITSI_PORTA=8443      porta HTTPS do Jitsi
#   JITSI_DIR=/opt/jitsi  onde ficam os arquivos do Jitsi
#   IP_PUBLICO=x.x.x.x    para vereadores fora da rede: IP público da Câmara (abra no roteador a
#                         porta TCP JITSI_PORTA e a UDP 10000 para este servidor)
#   SEM_PARLATORIO=1      só instala o Jitsi (não mexe na configuração do LEGIS) — usado em testes
#   ENVF=/etc/legis/legis.env  SERVICO=legis   configuração e serviço do LEGIS a atualizar
set -euo pipefail

NOME="${NOME:-$(hostname)}"
JITSI_DIR="${JITSI_DIR:-/var/lib/legis/jitsi}"
ENVF="${ENVF:-/etc/legis/legis.env}"
SERVICO="${SERVICO:-legis}"
porta_livre() { ! (ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "(^|:)$1\$"); }
if [ -z "${JITSI_PORTA:-}" ]; then   # 8443, ou a próxima livre (o LEGIS pode estar na 8443)
  JITSI_PORTA=8443
  [ -f "$JITSI_DIR/.env" ] && JITSI_PORTA=$(grep -E '^HTTPS_PORT=' "$JITSI_DIR/.env" | cut -d= -f2- || echo 8443)
  if [ ! -f "$JITSI_DIR/.env" ]; then
    LEGIS_HTTPS=$(grep -E '^PORTA_HTTPS=' "$ENVF" 2>/dev/null | cut -d= -f2- || true)
    while [ "$JITSI_PORTA" = "$LEGIS_HTTPS" ] || ! porta_livre "$JITSI_PORTA"; do JITSI_PORTA=$((JITSI_PORTA + 1)); done
  fi
fi
JITSI_HTTP="${JITSI_HTTP:-8180}"
JVB_PORTA="${JVB_PORTA:-10000}"
DADOS="${DADOS:-/var/lib/legis}"
TLS_DIR="${TLS_DIR:-$DADOS/tls}"
IP_LOCAL="${IP_LOCAL:-$(hostname -I | awk '{print $1}')}"
[ -f "$ENVF" ] && DOMINIO="${DOMINIO:-$(grep -E '^DOMINIO=' "$ENVF" | cut -d= -f2- || true)}"
HOST_JITSI="${HOST_JITSI:-${DOMINIO:-$NOME.local}}"
URL="https://$HOST_JITSI:$JITSI_PORTA"

export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null; then
  apt-get install -y -q docker.io
  command -v docker >/dev/null || apt-get install -y -q docker-cli   # Debian 13: o comando docker é outro pacote
fi
if docker compose version >/dev/null 2>&1; then COMPOSE=(docker compose)
else
  command -v docker-compose >/dev/null || apt-get install -y -q docker-compose
  if docker compose version >/dev/null 2>&1; then COMPOSE=(docker compose); else COMPOSE=(docker-compose); fi
fi
systemctl enable --now docker 2>/dev/null || true
for _ in $(seq 1 60); do [ -f "$TLS_DIR/servidor.crt" ] && break; sleep 2; done   # o LEGIS cria o certificado ao subir
[ -f "$TLS_DIR/servidor.crt" ] || { echo "Certificado do LEGIS não encontrado em $TLS_DIR."; exit 1; }

mkdir -p "$JITSI_DIR"
if [ ! -f "$JITSI_DIR/docker-compose.yml" ]; then
  # versão fixa (a release define também a versão das imagens); para atualizar, troque JITSI_VERSAO
  JITSI_VERSAO="${JITSI_VERSAO:-stable-9955}"
  echo "Baixando o docker-jitsi-meet $JITSI_VERSAO..."
  curl -fsSL "https://github.com/jitsi/docker-jitsi-meet/archive/refs/tags/$JITSI_VERSAO.tar.gz" | tar -xz -C "$JITSI_DIR" --strip-components=1
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
# versões novas do docker-jitsi-meet leem o certificado de /storage/keys (config/storage/web/keys)
mkdir -p config/storage/web/keys
cp config/web/keys/cert.crt config/web/keys/cert.key config/storage/web/keys/
chmod 600 config/storage/web/keys/cert.key
[ "$(id -u)" = 0 ] && chown -R 1000:1000 config/web/keys config/storage/web

"${COMPOSE[@]}" up -d

if [ "${SEM_PARLATORIO:-0}" != "1" ] && [ -f "$ENVF" ]; then
  for k in JITSI_URL JITSI_SEGREDO JITSI_APP_ID; do sed -i "/^$k=/d" "$ENVF"; done
  { echo "JITSI_URL=$URL"; echo "JITSI_APP_ID=parlatorio"; echo "JITSI_SEGREDO=$SEGREDO"; } >> "$ENVF"
  [ "${SEM_REINICIAR:-0}" = 1 ] || systemctl restart "$SERVICO" 2>/dev/null || true
fi

echo
echo "Jitsi da Câmara: $URL"
echo "A sessão híbrida do LEGIS já aponta para ele (Painel → Plenário virtual → Sessão híbrida)."
if [ -n "${IP_PUBLICO:-}" ]; then
  echo "Para vereadores fora da rede: no roteador, encaminhe TCP $JITSI_PORTA e UDP $JVB_PORTA para $IP_LOCAL."
fi
