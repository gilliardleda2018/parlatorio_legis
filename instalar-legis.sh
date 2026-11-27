#!/bin/bash
# LEGIS — instalação completa num servidor Debian 12/13 (ou Ubuntu 24.04) limpo.
#
#   curl -fsSLO https://github.com/gilliardleda2018/parlatorio_legis/releases/latest/download/instalar-legis.sh
#   sudo bash instalar-legis.sh
#
# Ou, do pacote completo (pendrive, sem baixar o .deb de novo):
#   tar xzf legis-completo-<versão>.tar.gz && cd legis-completo-<versão> && sudo bash instalar-legis.sh
#
# Totalmente automático: não pergunta nada. Instala o LEGIS, a rádio da Câmara, a transcrição e as
# legendas, e a IA local se a máquina tiver 8 GB de RAM. No final, escolha o município no terminal.
# Sem terminal ou com --sem-perguntar, escolha no painel do presidente. Para ajustar os extras,
# use --perguntar ou passe as respostas como variáveis:
#   UF=ma MUNICIPIO="Tuntum"   estado e município (vereadores eleitos do TSE, com fotos; população do IBGE)
#   NOME=tuntum                nome curto: https://tuntum.local na rede e https://tuntum.<tailnet>.ts.net na internet
#   TRANSCRICAO=s IA=n         transcrição e legendas, IA local (padrão: s e automático). A rádio vem sempre.
#   JITSI=s                    videoconferência própria da Câmara (padrão: sim com 4 GB de RAM)
#   YOUTUBE_CHAVE=xxxx-...     chave de transmissão do YouTube (YouTube Studio → Transmitir ao vivo)
#   YOUTUBE_LINK=https://...   link público da live (ex.: https://www.youtube.com/@suacamara/live)
#   AZURACAST=n                mantém só a rádio Liquidsoap incluída
#   TS_AUTHKEY=tskey-auth-...   entra na rede Tailscale sem aprovação no navegador
#   TAILSCALE_PUBLICO=s        publica o Legis via Funnel, se permitido na rede Tailscale
#   SEM_HOSTNAME=1             não troca o nome da máquina
#   DEB=/caminho/legis.deb     usa este pacote em vez de baixar a última versão
set -euo pipefail

REPO="gilliardleda2018/parlatorio_legis"
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"
TTY=/dev/tty
VERDE=$'\e[32m'; NEGRITO=$'\e[1m'; FIM=$'\e[0m'
PERGUNTAR=0
ESCOLHER_CIDADE=1
NOME_DADO="${NOME:-}"
for arg in "$@"; do
  case "$arg" in
    --perguntar) PERGUNTAR=1 ;;
    --sem-perguntar) ESCOLHER_CIDADE=0 ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Opção desconhecida: $arg (use --perguntar, --sem-perguntar ou --help)"; exit 1 ;;
  esac
done

[ "$(id -u)" = 0 ] || { echo "Rode como administrador: sudo bash instalar-legis.sh"; exit 1; }
. /etc/os-release
case "${ID:-}:${VERSION_ID:-}" in
  debian:12|debian:13|ubuntu:24.04|ubuntu:24.10|ubuntu:25.04) ;;
  *) echo "Aviso: testado em Debian 12/13 e Ubuntu 24.04; este é ${PRETTY_NAME:-desconhecido}. Continuando." ;;
esac
tem_tty() { { : < "$TTY"; } 2>/dev/null; }
perguntar() {  # perguntar VARIAVEL "texto" [padrão] — lê do terminal mesmo com "curl | sudo bash"
  local atual="${!1:-}"
  if [ -z "${!1+x}" ] && [ "$PERGUNTAR" = 1 ] && tem_tty; then
    read -rp "$2${3:+ [$3]}: " atual < "$TTY" || true
  fi
  printf -v "$1" '%s' "${atual:-${3:-}}"
}
sim() { case "${1,,}" in 1|s|sim|y|yes|true) return 0 ;; *) return 1 ;; esac; }
slug() { python3 -c "import re,sys,unicodedata as u; t=''.join(c for c in u.normalize('NFD',sys.argv[1]) if u.category(c)!='Mn').lower(); print(re.sub(r'[^a-z0-9]+','-',t).strip('-'))" "$1"; }

echo "${NEGRITO}== LEGIS — Sistema de Gestão Parlamentar: instalação ==${FIM}"
export DEBIAN_FRONTEND=noninteractive
echo "Atualizando a lista de pacotes..."
apt-get update -q >/dev/null
apt-get install -y -q ca-certificates curl python3 debconf >/dev/null

# A cidade também pode vir preenchida para instalação em lote.
UF="${UF:-}"
UF="${UF,,}"
MUNICIPIO="${MUNICIPIO:-}"
if [[ -n "$UF" || -n "$MUNICIPIO" ]]; then
  [[ "$UF" =~ ^[a-z]{2}$ && -n "$MUNICIPIO" && "$MUNICIPIO" != *$'\n'* && "$MUNICIPIO" != *$'\r'* ]] ||
    { echo 'Informe UF e MUNICIPIO juntos (ex.: UF=MA MUNICIPIO="Tuntum").'; exit 1; }
fi
perguntar NOME "Nome curto do endereço (letras minúsculas)" "$(slug "${MUNICIPIO:-legis}")"
NOME="$(slug "$NOME")"; NOME="${NOME:-legis}"
MEMORIA_GB=$(awk '/MemTotal/ {printf "%d", $2/1024/1024 + 0.5}' /proc/meminfo)
RADIO="${RADIO:-s}"   # a rádio da Câmara vem sempre com o pacote
perguntar TRANSCRICAO "Transcrição dos discursos e legendas ao vivo, cerca de 600 MB? (s/n)" "s"
perguntar IA "IA local (ata, perguntas, assistente), precisa de 8 GB de RAM — esta máquina tem ${MEMORIA_GB} GB? (s/n)" "$([ "$MEMORIA_GB" -ge 7 ] && echo s || echo n)"
perguntar JITSI "Servidor de videoconferência próprio (Jitsi), para a sessão híbrida? (s/n)" "$([ "$MEMORIA_GB" -ge 4 ] && echo s || echo n)"
if [ -z "${YOUTUBE_CHAVE+x}" ] && [ "$PERGUNTAR" = 1 ] && tem_tty; then
  echo "Chave de transmissão do YouTube (YouTube Studio → Criar → Transmitir ao vivo → Stream), opcional."
  read -rsp "Cole a chave ou aperte Enter para configurar depois no painel: " YOUTUBE_CHAVE < "$TTY" || true; echo
fi
YOUTUBE_CHAVE="${YOUTUBE_CHAVE:-}"
perguntar YOUTUBE_LINK "Link público da live no YouTube (opcional)" ""
if [ -z "${TS_AUTHKEY+x}" ] && [ "$PERGUNTAR" = 1 ] && tem_tty; then
  echo "Chave do Tailscale para conectar este servidor à rede da Câmara (opcional)."
  read -rsp "Cole a chave (tskey-auth-...) ou aperte Enter para pular: " TS_AUTHKEY < "$TTY" || true; echo
fi
TS_AUTHKEY="${TS_AUTHKEY:-}"

# ---- pacote .deb: o do lado deste script, DEB=…, ou a última versão publicada (com o SHA-256 conferido)
if [ -z "${DEB:-}" ]; then
  DEB=$(ls -t "$AQUI"/legis*_all.deb 2>/dev/null | head -1 || true)   # legis_… ou o pacote de uma Câmara (legis-tuntum_…)
fi
if [ -z "$DEB" ]; then
  echo "Baixando a última versão do LEGIS..."
  TMP=$(mktemp -d)
  trap 'rm -rf "$TMP"' EXIT
  read -r NOME_DEB URL < <(curl -fsSL --retry 2 "https://api.github.com/repos/$REPO/releases/latest" | python3 -c \
    "import json,sys; a=next((a for a in json.load(sys.stdin)['assets'] if a['name'].startswith('legis_') and a['name'].endswith('_all.deb')), {}); print(a.get('name',''), a.get('browser_download_url',''))") ||
    { echo "Não encontrei o pacote na última versão do GitHub."; exit 1; }
  [[ "$NOME_DEB" =~ ^legis_[a-zA-Z0-9.+~_-]+_all\.deb$ && -n "$URL" ]] ||
    { echo "A última versão do GitHub não contém o pacote genérico do Legis."; exit 1; }
  chmod 755 "$TMP"
  curl -fsSL --retry 2 -o "$TMP/$NOME_DEB" "$URL"
  chmod 644 "$TMP/$NOME_DEB"
  DEB="$TMP/$NOME_DEB"
  curl -fsSL --retry 2 -o "$DEB.sha256" "$URL.sha256" ||
    { echo "Não encontrei o SHA-256 da versão publicada."; exit 1; }

fi
[ -f "$DEB" ] || { echo "Pacote não encontrado: $DEB"; exit 1; }
DEB="$(cd "$(dirname "$DEB")" && pwd)/$(basename "$DEB")"
if [ -f "$DEB.sha256" ]; then
  (cd "$(dirname "$DEB")" && sha256sum --strict -c "$(basename "$DEB").sha256" >/dev/null) ||
    { echo "O pacote não confere com o SHA-256. Baixe o arquivo novamente."; exit 1; }
  echo "  ${VERDE}✔${FIM} $(basename "$DEB") (SHA-256 conferido)"
fi
[ "$(dpkg-deb -f "$DEB" Package)" = legis ] || { echo "O arquivo indicado não é um pacote do Legis."; exit 1; }
CIDADE_PACOTE=$(dpkg-deb -f "$DEB" X-Legis-Cidade 2>/dev/null || true)
if [ -n "$CIDADE_PACOTE" ]; then   # pacote personalizado: município, Câmara, brasão e vereadores já vêm dentro
  if [ -z "$NOME_DADO" ]; then NOME=$(basename "$DEB"); NOME=${NOME#legis-}; NOME=${NOME%%_*}; fi
fi
ROTULO="${CIDADE_PACOTE:-${MUNICIPIO:+$MUNICIPIO/${UF^^}}}"
ok() { sim "$1" && echo "sim" || echo "não"; }
echo
echo "${NEGRITO}Instalando:${FIM} ${ROTULO:-cidade escolhida no painel do presidente} · rádio: sim · transcrição: $(ok "$TRANSCRICAO") · IA local: $(ok "$IA") (${MEMORIA_GB} GB de RAM) · videoconferência: $(ok "$JITSI") · YouTube: $([ -n "$YOUTUBE_CHAVE" ] && echo "chave configurada" || echo "chave depois, no painel") · internet: $([ -n "$TS_AUTHKEY" ] && echo "Tailscale" || echo "só rede local") · endereço: https://$NOME.local"

# ---- nome da máquina: https://NOME.local na rede da Câmara
if [ "${SEM_HOSTNAME:-0}" != "1" ] && [ "$(hostname)" != "$NOME" ]; then
  hostnamectl set-hostname "$NOME" 2>/dev/null || hostname "$NOME" 2>/dev/null || true
  if grep -q '^127\.0\.1\.1' /etc/hosts; then sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$NOME/" /etc/hosts; else echo -e "127.0.1.1\t$NOME" >> /etc/hosts; fi
  echo "Nome da máquina: $NOME (https://$NOME.local na rede da Câmara)"
fi

# ---- respostas do instalador e pacotes escolhidos
debconf-set-selections <<EOF
legis legis/uf string $UF
legis legis/municipio string $MUNICIPIO
legis legis/nome string $NOME
legis legis/radio boolean $(sim "$RADIO" && echo true || echo false)
legis legis/transcricao boolean $(sim "$TRANSCRICAO" && echo true || echo false)
legis legis/ia boolean $(sim "$IA" && echo true || echo false)
legis legis/jitsi boolean $(sim "$JITSI" && echo true || echo false)
legis legis/youtube_chave password $YOUTUBE_CHAVE
legis legis/youtube_link string ${YOUTUBE_LINK:-}
legis legis/tailscale_chave password $TS_AUTHKEY
EOF
# Docker precisa estar disponível antes de o .deb disparar o provisionamento em segundo plano.
docker_local() {
  command -v docker >/dev/null && timeout 5 docker --host unix:///var/run/docker.sock info >/dev/null 2>&1
}
if ! docker_local; then
  PACOTES_DOCKER=()
  if ! systemctl cat docker.service >/dev/null 2>&1 && ! command -v dockerd >/dev/null; then
    PACOTES_DOCKER+=(docker.io)
  fi
  if ! command -v docker >/dev/null; then
    if dpkg-query -W -f='${db:Status-Status}' docker-ce 2>/dev/null | grep -qx installed; then
      PACOTES_DOCKER+=(docker-ce-cli)
    elif apt-cache show docker-cli >/dev/null 2>&1; then
      PACOTES_DOCKER+=(docker-cli)
    elif [ "${#PACOTES_DOCKER[@]}" = 0 ]; then
      PACOTES_DOCKER+=(docker.io)
    fi
  fi
  if [ "${#PACOTES_DOCKER[@]}" -gt 0 ]; then
    echo "Instalando Docker: ${PACOTES_DOCKER[*]}..."
    apt-get install -y -q "${PACOTES_DOCKER[@]}"
  fi
  systemctl enable --now docker.service ||
    { echo "O Docker não iniciou. Veja: sudo journalctl -u docker.service -n 40 --no-pager"; exit 1; }
  for _ in $(seq 1 15); do
    docker_local && break
    sleep 1
  done
  docker_local || { echo "O Docker não respondeu no socket local."; exit 1; }
fi
if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null; then
  if apt-cache show docker-compose-plugin >/dev/null 2>&1; then COMPOSE_PACOTE=docker-compose-plugin
  elif apt-cache show docker-compose-v2 >/dev/null 2>&1; then COMPOSE_PACOTE=docker-compose-v2
  else COMPOSE_PACOTE=docker-compose
  fi
  echo "Instalando Docker Compose..."
  apt-get install -y -q "$COMPOSE_PACOTE"
fi
docker compose version >/dev/null 2>&1 || docker-compose version >/dev/null 2>&1 ||
  { echo "O Docker Compose não respondeu."; exit 1; }
echo "Docker e Compose prontos."

EXTRAS=(avahi-daemon)
sim "$TRANSCRICAO" && EXTRAS+=(python3-venv)
echo "Instalando o LEGIS e ${EXTRAS[*]} (alguns minutos)..."
LEGIS_CONFIGURADOR=1 apt-get install -y -q --no-install-recommends "$DEB" "${EXTRAS[@]}" ||
  { echo "O APT não concluiu a instalação. Confira a mensagem acima."; exit 1; }
dpkg -s legis 2>/dev/null | grep -q '^Status: install ok installed' || { echo "A instalação do pacote falhou (veja as mensagens acima)."; exit 1; }
unset TS_AUTHKEY YOUTUBE_CHAVE

echo
legis verificar-instalacao
echo "${VERDE}${NEGRITO}✔ LEGIS instalado e respondendo.${FIM}"
if legis cidade-pendente; then
  if [ -n "$MUNICIPIO" ] || [ -n "$CIDADE_PACOTE" ]; then
    echo "A importação não foi concluída. Para tentar novamente, use: sudo legis configurar-cidade"
    exit 1
  fi
  if [ "$ESCOLHER_CIDADE" = 1 ] && tem_tty; then
    legis configurar-cidade < "$TTY"
  else
    echo "Escolha a cidade no painel do presidente ou execute: sudo legis configurar-cidade"
  fi
fi
RADIO_AZURA=nao
sim "${AZURACAST:-s}" && RADIO_AZURA=sim
MODO_TAILSCALE=serve
sim "${TAILSCALE_PUBLICO:-n}" && MODO_TAILSCALE=funnel
legis preparar-radio "$RADIO_AZURA" "$MODO_TAILSCALE"
legis resumo
echo "  Acompanhe os componentes adicionais com: sudo legis estado"
