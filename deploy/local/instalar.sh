#!/bin/bash
# Parlatório Legis on-premise: instala num servidor Debian (12 ou 13) dentro da rede do plenário.
#
# O servidor local conduz a sessão sem depender da internet: painel do presidente, telão,
# celulares dos vereadores, votação, gravação e o CONTROLE DA MESA DE SOM (que precisa estar na
# mesma rede). A transmissão ao vivo usa a internet só para enviar o áudio à rádio.
#
# Uso, como root:
#   bash instalar.sh
# Variáveis opcionais:
#   NOME=parlatorio      nome na rede: os aparelhos abrem https://parlatorio.local
#   TRANSCRICAO=1        instala a transcrição local dos discursos e as legendas ao vivo (8 GB de RAM)
#   IA=1                 IA local (Ollama em Docker, modelo qwen2.5:3b): rascunho da ata, perguntas sobre os
#                        discursos e assistente do cidadão, sem mandar dados para fora (+4 GB de RAM)
#   DOMINIO=sessao.suacamara.ma.leg.br   acesso também pela internet (vereadores em casa, sessão
#                        híbrida): certificado Let's Encrypt gratuito e renovado sozinho. Antes, aponte o
#                        domínio para o IP público da Câmara e encaminhe no roteador as portas TCP 80 e 443
#                        (e, com o Jitsi, TCP 8443 e UDP 10000) para este servidor. EMAIL= recebe avisos.
#   REPO, RAMO           de onde baixar o código (padrão: GitHub, ramo main)
set -euo pipefail

NOME="${NOME:-parlatorio}"
REPO="${REPO:-https://github.com/gilliardleda2018/parlatorio_legis.git}"
RAMO="${RAMO:-main}"
TRANSCRICAO="${TRANSCRICAO:-0}"
APP=/opt/parlatorio_legis
DADOS=/var/lib/parlatorio

[ "$(id -u)" = 0 ] || { echo "Rode como root (sudo bash instalar.sh)."; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv ffmpeg openssl git tzdata avahi-daemon unattended-upgrades curl
timedatectl set-timezone America/Sao_Paulo || true

# nome na rede local (mDNS): https://parlatorio.local funciona em Windows, macOS, iPhone e na maioria dos Android
if [ "$(hostname)" != "$NOME" ]; then
  hostnamectl set-hostname "$NOME"
  sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$NOME/" /etc/hosts || true
fi
systemctl enable --now avahi-daemon

id parlatorio >/dev/null 2>&1 || useradd --system --home "$DADOS" --shell /usr/sbin/nologin parlatorio
mkdir -p "$DADOS"
if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch -q origin "$RAMO" && git -C "$APP" reset -q --hard "origin/$RAMO"
else
  git clone -q --branch "$RAMO" "$REPO" "$APP"
fi
chown -R parlatorio:parlatorio "$DADOS"

if [ ! -f /etc/parlatorio.env ]; then
  IPS=$(hostname -I | tr ' ' ',' | sed 's/,$//')
  cat > /etc/parlatorio.env <<EOF
PARLATORIO_BANCO=$DADOS/parlatorio.db
# HTTP na 80 (redireciona) e HTTPS na 443 com a autoridade certificadora própria da Câmara
PORTA=80
PORTA_HTTPS=443
HTTPS=auto
TLS_NOMES=$NOME.local,$NOME,$IPS
TZ=America/Sao_Paulo
# PIN inicial do painel do presidente (depois da primeira troca vale o PIN salvo):
PIN_PRESIDENTE=$(python3 -c "import secrets; print(str(secrets.randbelow(10**8)).zfill(8))")
# Backup diário às 03:00 em $DADOS/backups (para enviar ao Nextcloud, veja o README).
EOF
  chmod 600 /etc/parlatorio.env
fi

cat > /etc/systemd/system/parlatorio.service <<EOF
[Unit]
Description=Parlatório Legis (on-premise)
After=network-online.target
Wants=network-online.target

[Service]
User=parlatorio
Group=parlatorio
WorkingDirectory=$APP
EnvironmentFile=/etc/parlatorio.env
ExecStart=/usr/bin/python3 $APP/server/servidor.py
Restart=always
RestartSec=3
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DADOS
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable parlatorio
systemctl restart parlatorio

if [ -n "${DOMINIO:-}" ]; then
  apt-get install -y -q certbot
  mkdir -p "$DADOS/acme/.well-known/acme-challenge" "$DADOS/tls-publico" /etc/letsencrypt/renewal-hooks/deploy
  chown -R parlatorio:parlatorio "$DADOS/acme" "$DADOS/tls-publico"
  GANCHO=/etc/letsencrypt/renewal-hooks/deploy/parlatorio.sh
  {
    echo '#!/bin/sh'
    echo '# copia o certificado renovado para o Parlatório e para o Jitsi e reinicia os dois'
    echo 'set -e'
    echo "D=/etc/letsencrypt/live/$DOMINIO"
    echo "install -o parlatorio -g parlatorio -m 644 \"\$D/fullchain.pem\" $DADOS/tls-publico/fullchain.pem"
    echo "install -o parlatorio -g parlatorio -m 600 \"\$D/privkey.pem\" $DADOS/tls-publico/privkey.pem"
    echo 'if [ -d /opt/jitsi/config/web/keys ]; then'
    echo '  cp "$D/fullchain.pem" /opt/jitsi/config/web/keys/cert.crt'
    echo '  cp "$D/privkey.pem" /opt/jitsi/config/web/keys/cert.key'
    echo '  (cd /opt/jitsi && docker compose restart web) || true'
    echo 'fi'
    echo 'systemctl restart parlatorio'
  } > "$GANCHO"
  chmod 755 "$GANCHO"
  sleep 2
  if [ -n "${EMAIL:-}" ]; then CONTA=(-m "$EMAIL" --no-eff-email); else CONTA=(--register-unsafely-without-email); fi
  certbot certonly --webroot -w "$DADOS/acme" -d "$DOMINIO" --non-interactive --agree-tos "${CONTA[@]}" --deploy-hook "$GANCHO"
  sed -i '/^TLS_CERT=/d;/^TLS_KEY=/d;/^DOMINIO=/d' /etc/parlatorio.env
  { echo "DOMINIO=$DOMINIO"; echo "TLS_CERT=$DADOS/tls-publico/fullchain.pem"; echo "TLS_KEY=$DADOS/tls-publico/privkey.pem"; } >> /etc/parlatorio.env
  systemctl restart parlatorio
fi

if [ "${IA:-0}" = "1" ]; then
  apt-get install -y -q docker.io
  systemctl enable --now docker
  mkdir -p "$DADOS/ollama"
  docker inspect ollama >/dev/null 2>&1 || docker run -d --name ollama --restart unless-stopped -e OLLAMA_KEEP_ALIVE=30m \
    -p 127.0.0.1:11434:11434 -v "$DADOS/ollama:/root/.ollama" ollama/ollama
  sleep 5
  docker exec ollama ollama pull qwen2.5:3b
  sed -i '/^IA=/d;/^OLLAMA_URL=/d;/^IA_MODELO=/d' /etc/parlatorio.env
  { echo "IA=ollama"; echo "OLLAMA_URL=http://127.0.0.1:11434"; echo "IA_MODELO=qwen2.5:3b"; } >> /etc/parlatorio.env
  systemctl restart parlatorio
fi

if [ "$TRANSCRICAO" = "1" ]; then
  sudo -u parlatorio env PARLATORIO_DADOS="$DADOS" HOME="$DADOS" sh "$APP/scripts/instalar_transcricao.sh" \
    && systemctl restart parlatorio
fi

sleep 3
IP=$(hostname -I | awk '{print $1}')
echo
echo "Pronto. Na rede do plenário:"
echo "  1. Em cada aparelho, abra http://$IP/certificado e instale o certificado da Câmara (uma vez)."
if [ -n "${DOMINIO:-}" ]; then
  echo "  Endereço: https://$DOMINIO (certificado público: os aparelhos não precisam instalar nada)."
else
  echo "  2. Depois use https://$NOME.local (ou https://$IP)."
fi
echo "PIN inicial do presidente: grep PIN_PRESIDENTE /etc/parlatorio.env"
echo "Mesa de som: ligue-a na mesma rede e configure em Painel → Configurações → Mesa de som."
