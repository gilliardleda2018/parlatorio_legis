#!/bin/bash
# LEGIS — instalação completa para uma nova Câmara Municipal, num servidor Debian 12/13 novo.
#
#   curl -fsSL https://raw.githubusercontent.com/gilliardleda2018/parlatorio_legis/main/deploy/cidade/instalar.sh -o instalar.sh
#   sudo bash instalar.sh
#
# Pergunta o que faltar. Também aceita variáveis (instalação sem perguntas):
#   UF=ma MUNICIPIO="Tuntum"      estado e município (os vereadores eleitos vêm do TSE, com fotos)
#   NOME=tuntum                   nome curto: https://tuntum.local na rede e https://tuntum.<tailnet>.ts.net na internet
#   TS_AUTHKEY=tskey-auth-...     chave do Tailscale para publicar na internet (opcional; sem ela, só rede local)
#   IA=1                          IA local (rascunho da ata, perguntas, assistente) — precisa de 8 GB de RAM
#   TRANSCRICAO=1                 transcrição dos discursos e legendas ao vivo
#   PASTA=/opt/legis              onde instalar
set -euo pipefail

REPO="${REPO:-https://github.com/gilliardleda2018/parlatorio_legis.git}"
RAMO="${RAMO:-main}"
PASTA="${PASTA:-/opt/legis}"

[ "$(id -u)" = 0 ] || { echo "Rode como root: sudo bash instalar.sh"; exit 1; }
perguntar() {  # perguntar VARIAVEL "texto" [padrão]
  local atual="${!1:-}"
  if [ -z "$atual" ]; then
    if [ -t 0 ]; then read -rp "$2${3:+ [$3]}: " atual; fi
    atual="${atual:-${3:-}}"
  fi
  printf -v "$1" '%s' "$atual"
}
slug() { python3 -c "import re,sys,unicodedata as u; t=''.join(c for c in u.normalize('NFD',sys.argv[1]) if u.category(c)!='Mn').lower(); print(re.sub(r'[^a-z0-9]+','-',t).strip('-'))" "$1"; }

echo "== LEGIS — Sistema de Gestão Parlamentar: instalação para uma nova Câmara =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q ca-certificates curl git python3 avahi-daemon openssl

perguntar UF "Estado (sigla, ex.: ma)"
perguntar MUNICIPIO "Município (como no TSE, ex.: Tuntum)"
UF="$(echo "$UF" | tr 'A-Z' 'a-z')"
[ -n "$UF" ] && [ -n "$MUNICIPIO" ] || { echo "Informe UF e MUNICIPIO."; exit 1; }
perguntar NOME "Nome curto do endereço" "$(slug "$MUNICIPIO")"
NOME="$(slug "$NOME")"
MEMORIA_GB=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
perguntar IA "Instalar a IA local? (1 = sim, 0 = não)" "$([ "$MEMORIA_GB" -ge 7 ] && echo 1 || echo 0)"
perguntar TRANSCRICAO "Transcrição dos discursos e legendas ao vivo? (1/0)" "1"
if [ -z "${TS_AUTHKEY:-}" ] && [ -t 0 ]; then
  read -rsp "Chave do Tailscale para publicar na internet (Enter para pular): " TS_AUTHKEY; echo
fi

# ---- Docker (pacotes do Debian; se faltar o "docker compose", usa o repositório oficial da Docker)
if ! command -v docker >/dev/null; then apt-get install -y -q docker.io; fi
if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y -q docker-compose >/dev/null 2>&1 || true
fi
if ! docker compose version >/dev/null 2>&1; then
  . /etc/os-release
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q && apt-get install -y -q docker-compose-plugin
fi
systemctl enable --now docker

# ---- código
if [ -d "$PASTA/.git" ]; then
  git -C "$PASTA" fetch -q origin "$RAMO" && git -C "$PASTA" reset -q --hard "origin/$RAMO"
else
  git clone -q --branch "$RAMO" "$REPO" "$PASTA"
fi
cd "$PASTA"
mkdir -p dados

# ---- nome na rede local (https://NOME.local)
if [ "${SEM_HOSTNAME:-0}" != "1" ] && [ "$(hostname)" != "$NOME" ]; then
  hostnamectl set-hostname "$NOME"
  sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$NOME/" /etc/hosts || true
fi
systemctl enable --now avahi-daemon

# ---- rede Docker própria, numa faixa livre (o LEGIS confia no IP real que vem do túnel só a partir dela)
REDE=""
for n in $(seq 213 250); do
  if ! ip route | grep -q "^10\.$n\."; then REDE="10.$n.0.0/24"; break; fi
done
IPS=$(hostname -I | tr ' ' ',' | sed 's/,$//')

if [ ! -f dados/parlatorio.env ]; then
  PIN=$(python3 -c "import secrets; print(str(secrets.randbelow(10**8)).zfill(8))")
  cat > dados/parlatorio.env <<EOF
# Configuração desta Câmara (fora do Git). Depois de mudar: docker compose up -d
PARLATORIO_BANCO=/dados/legis.db
PIN_PRESIDENTE=$PIN
TLS_NOMES=$NOME.local,$NOME,$IPS
PROXY_CONFIAVEL=1
PROXY_IPS=$REDE
EOF
  if [ "$IA" = "1" ]; then
    printf 'IA=ollama\nOLLAMA_URL=http://ollama:11434\nIA_MODELO=qwen2.5:3b\n' >> dados/parlatorio.env
  fi
  chmod 600 dados/parlatorio.env
fi
PERFIS=""
[ "$IA" = "1" ] && PERFIS="ia"
cat > .env <<EOF
PORTA=80
PORTA_HTTPS=443
TRANSCRICAO=$TRANSCRICAO
COMPOSE_PROFILES=$PERFIS
EOF

# ---- override desta máquina: rede fixa e, com a chave, o nó do Tailscale (https://NOME.<tailnet>.ts.net)
{
  echo "# Gerado pelo instalador (fora do Git)"
  echo "services:"
  if [ -n "${TS_AUTHKEY:-}" ] || [ -d dados/tailscale ]; then
    cat <<EOF
  tailscale:
    image: tailscale/tailscale:stable
    hostname: $NOME
    restart: unless-stopped
    environment:
      TS_AUTHKEY: \${TS_AUTHKEY:-}
      TS_HOSTNAME: $NOME
      TS_STATE_DIR: /var/lib/tailscale
      TS_USERSPACE: "true"
      TS_SERVE_CONFIG: /config/serve.json
    volumes:
      - ./dados/tailscale:/var/lib/tailscale
      - ./deploy/tailscale/serve.json:/config/serve.json:ro
EOF
  else
    echo "  parlatorio: {}"
  fi
  cat <<EOF
networks:
  default:
    ipam:
      config:
        - subnet: $REDE
EOF
} > docker-compose.override.yml

chown -R 1000:1000 dados   # o LEGIS roda no contêiner como usuário comum (uid 1000)

echo "== Construindo a imagem do LEGIS (alguns minutos) =="
docker compose build -q parlatorio

if [ ! -f dados/legis.db ]; then
  echo "== Buscando os vereadores eleitos no TSE e a população no IBGE =="
  docker compose run --rm --no-deps -e PARLATORIO_BANCO=/dados/legis.db parlatorio \
    python scripts/tse_eleitos.py "$UF" "$MUNICIPIO" /dados/legis.db
fi

TS_AUTHKEY="${TS_AUTHKEY:-}" docker compose up -d
if [ "$IA" = "1" ]; then
  echo "== Baixando o modelo de IA local (cerca de 2 GB, uma vez só) =="
  sleep 5
  docker compose exec -T ollama ollama pull qwen2.5:3b >/dev/null
fi

URL_INTERNET=""
if docker compose ps --services 2>/dev/null | grep -q '^tailscale$'; then
  for _ in $(seq 1 30); do
    URL_INTERNET=$(docker compose exec -T tailscale tailscale status --json 2>/dev/null \
      | python3 -c "import json,sys; print('https://' + json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))" 2>/dev/null || true)
    [ -n "$URL_INTERNET" ] && break
    sleep 3
  done
fi

IP=$(hostname -I | awk '{print $1}')
PIN=$(grep '^PIN_PRESIDENTE=' dados/parlatorio.env | cut -d= -f2)
cat <<EOF

==========================================================================
 LEGIS instalado para $MUNICIPIO/${UF^^}
--------------------------------------------------------------------------
 Na rede da Câmara:   https://$NOME.local   (ou https://$IP)
 Na internet:         ${URL_INTERNET:-não publicado (rode de novo com TS_AUTHKEY=...)}
 Painel do presidente: /presidente — PIN inicial: $PIN
 Vereadores:          /parlamentar — PIN inicial 0000 (troca obrigatória no 1º acesso)

 Próximos passos (checklist completo em deploy/cidade/README.md):
  1. Em cada aparelho da rede: http://$IP/certificado (instalar uma vez)
  2. Painel → Configurações: brasão, cor, rodapé com os contatos da Câmara
  3. Troque o PIN do presidente (botão Trocar PIN)
  4. Mesa de som: modelo e IP em Configurações → Mesa de som; canais em Plenário
  5. Transmissão: Configurações → Transmissão ao vivo
 Atualizar depois: sudo bash $PASTA/deploy/cidade/atualizar.sh
==========================================================================
EOF
