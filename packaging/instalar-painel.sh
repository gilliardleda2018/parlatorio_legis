#!/bin/bash
# Deixa o painel dos pacotes sempre ligado (serviço do usuário) e publicado na internet pelo Tailscale Funnel.
#   bash packaging/instalar-painel.sh            (como o usuário dono do repositório, não como root)
# Endereço: https://<esta máquina>.<tailnet>.ts.net:10000 — pede a chave de acesso em dados/painel/chave.
# A porta 10000 é uma das três que o Funnel publica (443, 8443, 10000); a 443 desta máquina fica livre para outros usos.
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
PORTA_FUNNEL="${PORTA_FUNNEL:-10000}"
[ "$(id -u)" != 0 ] || { echo "Rode como o seu usuário (sem sudo)."; exit 1; }
command -v tailscale >/dev/null || { echo "Instale e conecte o Tailscale nesta máquina antes."; exit 1; }
DNS=$(tailscale status --self --json | python3 -c "import json,sys; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))")
PUBLICO="$DNS:$PORTA_FUNNEL"

mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/legis-painel.service" <<EOF
[Unit]
Description=LEGIS — painel dos pacotes de instalação (https://$PUBLICO)
After=network-online.target

[Service]
WorkingDirectory=$RAIZ
ExecStart=/usr/bin/python3 $RAIZ/packaging/painel.py --sem-navegador
Environment=PAINEL_PUBLICO=$PUBLICO
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now legis-painel.service
systemctl --user restart legis-painel.service
tailscale funnel --bg --https="$PORTA_FUNNEL" http://127.0.0.1:8099

echo
echo "Painel online: https://$PUBLICO"
echo "Chave de acesso: veja com   cat $RAIZ/dados/painel/chave"
if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q yes; then
  echo "Para o painel subir sozinho depois de reiniciar a máquina, mesmo sem ninguém logado:"
  echo "  sudo loginctl enable-linger $USER"
fi
