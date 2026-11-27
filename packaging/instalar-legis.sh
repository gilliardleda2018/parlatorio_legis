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
# legendas, e a IA local se a máquina tiver 8 GB de RAM. A cidade é escolhida no primeiro acesso ao
# painel do presidente (os vereadores vêm do TSE). Para escolher na instalação, use --perguntar
# (sudo bash instalar-legis.sh --perguntar) ou passe as respostas como variáveis:
#   UF=ma MUNICIPIO="Tuntum"   estado e município (vereadores eleitos do TSE, com fotos; população do IBGE)
#   NOME=tuntum                nome curto: https://tuntum.local na rede e https://tuntum.<tailnet>.ts.net na internet
#   RADIO=s TRANSCRICAO=s IA=n rádio da Câmara, transcrição e legendas, IA local (padrão: s, s e automático)
#   TS_AUTHKEY=tskey-auth-...  publica na internet pelo Tailscale (opcional)
#   SEM_HOSTNAME=1             não troca o nome da máquina
#   DEB=/caminho/legis.deb     usa este pacote em vez de baixar a última versão
set -euo pipefail

REPO="gilliardleda2018/parlatorio_legis"
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"
TTY=/dev/tty
VERDE=$'\e[32m'; NEGRITO=$'\e[1m'; FIM=$'\e[0m'
PERGUNTAR=0
for arg in "$@"; do
  case "$arg" in
    --perguntar) PERGUNTAR=1 ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Opção desconhecida: $arg (use --perguntar ou --help)"; exit 1 ;;
  esac
done

[ "$(id -u)" = 0 ] || { echo "Rode como administrador: sudo bash instalar-legis.sh"; exit 1; }
. /etc/os-release
case "${ID:-}:${VERSION_ID:-}" in
  debian:12|debian:13|ubuntu:24.04|ubuntu:24.10|ubuntu:25.04) ;;
  *) echo "Aviso: testado em Debian 12/13 e Ubuntu 24.04; este é ${PRETTY_NAME:-desconhecido}. Continuando." ;;
esac
tem_tty() { [ "$PERGUNTAR" = 1 ] && [ -r "$TTY" ] && { : < "$TTY"; } 2>/dev/null; }
perguntar() {  # perguntar VARIAVEL "texto" [padrão] — lê do terminal mesmo com "curl | sudo bash"
  local atual="${!1:-}"
  if [ -z "${!1+x}" ] && tem_tty; then
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

# ---- perguntas
if tem_tty; then
  echo
  echo "Estado e município: o LEGIS busca no TSE os vereadores eleitos (nome, partido e foto)."
  echo "Pode deixar em branco: o painel do presidente pergunta a cidade no primeiro acesso."
fi
perguntar UF "Estado (sigla, ex.: MA)"
UF="$(echo "$UF" | tr 'A-Z' 'a-z' | tr -cd 'a-z')"
MUNICIPIO_OK=""
if [ -n "$UF" ]; then
  perguntar MUNICIPIO "Município"
  if [ -n "${MUNICIPIO:-}" ]; then
    MUNICIPIO_OK=$(python3 - "$UF" "$MUNICIPIO" <<'PY' || true
import json, sys, unicodedata, urllib.request
s = lambda t: "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").lower().strip()
try:
    lista = json.load(urllib.request.urlopen(f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{sys.argv[1]}/municipios", timeout=20))
except Exception:
    print(sys.argv[2]); sys.exit()
achado = next((m["nome"] for m in lista if s(m["nome"]) == s(sys.argv[2])), "")
parecidos = [m["nome"] for m in lista if s(sys.argv[2])[:4] in s(m["nome"])][:6]
print(achado or ("?" + ", ".join(parecidos)))
PY
)
    if [ "${MUNICIPIO_OK:0:1}" = "?" ]; then
      echo "  Não encontrei \"$MUNICIPIO\" em ${UF^^} no IBGE."
      [ -n "${MUNICIPIO_OK:1}" ] && echo "  Parecidos: ${MUNICIPIO_OK:1}"
      echo "  Deixando em branco: escolha a cidade no painel do presidente depois."
      MUNICIPIO=""
    else
      MUNICIPIO="$MUNICIPIO_OK"
      echo "  ${VERDE}✔${FIM} $MUNICIPIO/${UF^^}"
    fi
  fi
fi
MUNICIPIO="${MUNICIPIO:-}"
[ -n "$MUNICIPIO" ] || UF=""
perguntar NOME "Nome curto do endereço (letras minúsculas)" "$(slug "${MUNICIPIO:-legis}")"
NOME="$(slug "$NOME")"; NOME="${NOME:-legis}"
MEMORIA_GB=$(awk '/MemTotal/ {printf "%d", $2/1024/1024 + 0.5}' /proc/meminfo)
perguntar RADIO "Instalar a rádio da Câmara? (s/n)" "s"
perguntar TRANSCRICAO "Transcrição dos discursos e legendas ao vivo, cerca de 600 MB? (s/n)" "s"
perguntar IA "IA local (ata, perguntas, assistente), precisa de 8 GB de RAM — esta máquina tem ${MEMORIA_GB} GB? (s/n)" "$([ "$MEMORIA_GB" -ge 7 ] && echo s || echo n)"
if [ -z "${TS_AUTHKEY+x}" ] && tem_tty; then
  echo "Chave do Tailscale para publicar na internet sem abrir portas (opcional)."
  read -rsp "Cole a chave (tskey-auth-...) ou aperte Enter para pular: " TS_AUTHKEY < "$TTY" || true; echo
fi
TS_AUTHKEY="${TS_AUTHKEY:-}"
ok() { sim "$1" && echo "sim" || echo "não"; }
echo
echo "${NEGRITO}Instalando:${FIM} cidade: ${MUNICIPIO:+$MUNICIPIO/${UF^^}}${MUNICIPIO:-escolhida no painel do presidente} · rádio: $(ok "$RADIO") · transcrição: $(ok "$TRANSCRICAO") · IA local: $(ok "$IA") (${MEMORIA_GB} GB de RAM) · internet: $([ -n "$TS_AUTHKEY" ] && echo "Tailscale" || echo "só rede local")"

# ---- pacote .deb: o do lado deste script, DEB=…, ou a última versão publicada (com o SHA-256 conferido)
if [ -z "${DEB:-}" ]; then
  DEB=$(ls -t "$AQUI"/legis_*_all.deb 2>/dev/null | head -1 || true)
fi
if [ -z "$DEB" ]; then
  echo "Baixando a última versão do LEGIS..."
  TMP=$(mktemp -d)
  URL=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | python3 -c \
    "import json,sys; print(next(a['browser_download_url'] for a in json.load(sys.stdin)['assets'] if a['name'].endswith('_all.deb')))")
  curl -fsSL -o "$TMP/$(basename "$URL")" "$URL"
  DEB="$TMP/$(basename "$URL")"
  if curl -fsSL -o "$DEB.sha256" "$URL.sha256" 2>/dev/null; then
    (cd "$TMP" && sha256sum -c "$(basename "$DEB").sha256" >/dev/null) || { echo "O arquivo baixado não confere (SHA-256). Tente de novo."; exit 1; }
    echo "  ${VERDE}✔${FIM} $(basename "$DEB") (SHA-256 conferido)"
  fi
fi
[ -f "$DEB" ] || { echo "Pacote não encontrado: $DEB"; exit 1; }
DEB="$(cd "$(dirname "$DEB")" && pwd)/$(basename "$DEB")"
chmod 644 "$DEB"

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
legis legis/tailscale_chave password $TS_AUTHKEY
EOF
EXTRAS=(avahi-daemon)
sim "$RADIO" && EXTRAS+=(liquidsoap)
sim "$TRANSCRICAO" && EXTRAS+=(python3-venv)
if { sim "$IA" || [ -n "$TS_AUTHKEY" ]; } && ! command -v docker >/dev/null; then
  EXTRAS+=(docker.io)   # IA local e Tailscale rodam em contêiner; não mexe num Docker oficial já instalado
fi
echo "Instalando o LEGIS e ${EXTRAS[*]} (alguns minutos)..."
apt-get install -y -q --no-install-recommends "$DEB" "${EXTRAS[@]}" 2>&1 | grep -vE "^(Selecting|Preparing|Unpacking|Setting up|Processing|\(Reading)" || true
dpkg -s legis 2>/dev/null | grep -q '^Status: install ok installed' || { echo "A instalação do pacote falhou (veja as mensagens acima)."; exit 1; }
unset TS_AUTHKEY

echo
echo "${VERDE}${NEGRITO}✔ LEGIS instalado.${FIM}"
[ -n "$MUNICIPIO" ] || echo "  No primeiro acesso ao painel do presidente, escolha a cidade: os vereadores vêm do TSE."
echo "  Transcrição, IA e internet continuam instalando em segundo plano: acompanhe com  sudo legis estado"
