#!/bin/bash
# Instala o Parlatório Legis num servidor Ubuntu 24.04 novo (AWS EC2 ou qualquer VPS), com HTTPS
# automático (Caddy + Let's Encrypt) no domínio informado.
#
# Uso, como root, na máquina nova:
#   DOMINIO=legis.aggillintelligence.com.br bash instalar.sh
# Também serve como "user data" da EC2 (cole o arquivo inteiro e ajuste o DOMINIO abaixo).
#
# Variáveis opcionais:
#   TRANSCRICAO=1        instala a transcrição local dos discursos (precisa de 4 GB de RAM)
#   REPO, RAMO           de onde baixar o código (padrão: GitHub, ramo main)
set -euo pipefail

DOMINIO="${DOMINIO:-legis.aggillintelligence.com.br}"
REPO="${REPO:-https://github.com/gilliardleda2018/parlatorio_legis.git}"
RAMO="${RAMO:-main}"
TRANSCRICAO="${TRANSCRICAO:-0}"
APP=/opt/parlatorio_legis
DADOS=/var/lib/parlatorio

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv ffmpeg git caddy tzdata unattended-upgrades
timedatectl set-timezone America/Sao_Paulo || true

id parlatorio >/dev/null 2>&1 || useradd --system --home "$DADOS" --shell /usr/sbin/nologin parlatorio
mkdir -p "$DADOS"
if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch -q origin "$RAMO" && git -C "$APP" reset -q --hard "origin/$RAMO"
else
  git clone -q --branch "$RAMO" "$REPO" "$APP"
fi
chown -R parlatorio:parlatorio "$DADOS"

# configuração do serviço: edite /etc/parlatorio.env e rode "systemctl restart parlatorio"
if [ ! -f /etc/parlatorio.env ]; then
  cat > /etc/parlatorio.env <<EOF
PARLATORIO_BANCO=$DADOS/parlatorio.db
HOST=127.0.0.1
PORTA=8090
HTTPS=0
PROXY_CONFIAVEL=1
TZ=America/Sao_Paulo
# PIN inicial do painel do presidente (depois da primeira troca vale o PIN salvo):
PIN_PRESIDENTE=$(python3 -c "import secrets; print(str(secrets.randbelow(10**8)).zfill(8))")
# Só em período de testes: TROCA_PIN=0 deixa entrar com o PIN padrão sem obrigar a troca.
# Em servidor aberto na internet, deixe a troca obrigatória (padrão).
EOF
  chmod 600 /etc/parlatorio.env
fi

cat > /etc/systemd/system/parlatorio.service <<EOF
[Unit]
Description=Parlatório Legis
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
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DADOS
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/caddy/Caddyfile <<EOF
$DOMINIO {
	# /eventos é o canal ao vivo (SSE): sem gzip, senão o Caddy segura os dados e as telas não atualizam
	handle /eventos {
		reverse_proxy 127.0.0.1:8090 {
			flush_interval -1
		}
	}
	handle {
		encode gzip
		request_body {
			max_size 64MB
		}
		reverse_proxy 127.0.0.1:8090 {
			flush_interval -1
		}
	}
}
EOF

systemctl daemon-reload
systemctl enable --now parlatorio
systemctl restart caddy

if [ "$TRANSCRICAO" = "1" ]; then
  sudo -u parlatorio env PARLATORIO_DADOS="$DADOS" HOME="$DADOS" sh "$APP/scripts/instalar_transcricao.sh" \
    && echo "TRANSCRICAO_PYTHON=$DADOS/whisper/bin/python" >> /etc/parlatorio.env \
    && echo "HF_HOME=$DADOS/.cache/huggingface" >> /etc/parlatorio.env \
    && systemctl restart parlatorio
fi

echo
echo "Pronto. Aponte o DNS de $DOMINIO para o IP desta máquina e abra https://$DOMINIO"
echo "PIN inicial do presidente: grep PIN_PRESIDENTE /etc/parlatorio.env"
