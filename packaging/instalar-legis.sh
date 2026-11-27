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
#   TRANSCRICAO=s IA=n         transcrição e legendas, IA local (padrão: s e automático). A rádio vem sempre.
#   JITSI=s                    videoconferência própria da Câmara (padrão: sim com 4 GB de RAM)
#   YOUTUBE_CHAVE=xxxx-...     chave de transmissão do YouTube (YouTube Studio → Transmitir ao vivo)
#   YOUTUBE_LINK=https://...   link público da live (ex.: https://www.youtube.com/@suacamara/live)
#   TS_AUTHKEY=tskey-auth-...  publica na internet pelo Tailscale (opcional)
#   SEM_HOSTNAME=1             não troca o nome da máquina
#   DEB=/caminho/legis.deb     usa este pacote em vez de baixar a última versão
#
# Qualquer cidade do Brasil a partir do modelo (cidades/LEIA-ME.md), sem gerar pacote nenhum:
#   sudo bash instalar-legis.sh --modelo camara.json
#   sudo bash instalar-legis.sh --modelo https://raw.githubusercontent.com/<repo>/main/cidades/<cidade>/camara.json
set -euo pipefail

REPO="gilliardleda2018/parlatorio_legis"
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"
TTY=/dev/tty
VERDE=$'\e[32m'; NEGRITO=$'\e[1m'; FIM=$'\e[0m'
PERGUNTAR=0
NOME_DADO="${NOME:-}"
MODELO=""
INICIO=$(date +%s)
while [ $# -gt 0 ]; do
  case "$1" in
    --perguntar) PERGUNTAR=1; shift ;;
    --modelo) MODELO="${2:?Informe o modelo: --modelo camara.json (ou https://…/camara.json)}"; shift 2 ;;
    -h|--help) sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Opção desconhecida: $1 (use --perguntar, --modelo ou --help)"; exit 1 ;;
  esac
done

[ "$(id -u)" = 0 ] || { echo "Rode como administrador: sudo bash instalar-legis.sh"; exit 1; }
# trava: esta máquina já roda o LEGIS em Docker (ex.: o servidor de desenvolvimento ou de outra instalação)
if [ "${FORCAR:-0}" != 1 ] && command -v docker >/dev/null && \
   docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null | grep -qi parlatorio; then
  echo "Esta máquina ($(hostname)) já roda o LEGIS em Docker: instalar o pacote aqui criaria um segundo LEGIS"
  echo "e trocaria o nome da máquina. Rode este comando no servidor da Câmara. (Para forçar: FORCAR=1)"
  exit 1
fi
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

# ---- checagem antes de começar: o que faltar aparece agora, não no meio da instalação
AVISOS=()
LIVRE_GB=$(df -Pk / | awk 'NR==2 {printf "%d", $4/1024/1024}')
[ "${LIVRE_GB:-0}" -ge 4 ] || { echo "Espaço livre em / insuficiente: ${LIVRE_GB} GB (o LEGIS precisa de pelo menos 4 GB; com IA, 10 GB)."; exit 1; }
[ "${LIVRE_GB:-0}" -ge 10 ] || AVISOS+=("pouco espaço em disco (${LIVRE_GB} GB): a IA local fica de fora se faltar")
for alvo in github.com servicodados.ibge.gov.br; do
  curl -fsS -m 8 -o /dev/null "https://$alvo" 2>/dev/null || getent hosts "$alvo" >/dev/null || AVISOS+=("sem acesso a $alvo")
done
for porta in 80 443; do
  if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "(^|:)$porta\$" && ! [ -f /etc/legis/legis.env ]; then
    AVISOS+=("porta $porta já está em uso: o LEGIS usa a 8090/8443")
  fi
done
[ ${#AVISOS[@]} -eq 0 ] || printf '  Aviso: %s\n' "${AVISOS[@]}"

# lista de pacotes: só atualiza se tiver mais de 6 horas (reinstalar ou atualizar fica bem mais rápido)
if [ -z "$(find /var/lib/apt/lists -maxdepth 1 -name '*Packages*' -mmin -360 2>/dev/null | head -1)" ]; then
  echo "Atualizando a lista de pacotes..."
  apt-get update -q >/dev/null
fi
apt-get install -y -q ca-certificates curl python3 debconf >/dev/null

# ---- modelo da cidade: estado, município, nome e preferências já vêm escritos (nenhuma pergunta)
if [ -n "$MODELO" ]; then
  MODELO_DIR=$(mktemp -d)
  if [[ "$MODELO" =~ ^https:// ]]; then
    curl -fsSL --max-filesize 200000 -o "$MODELO_DIR/camara.json" "$MODELO" || { echo "Não consegui baixar o modelo: $MODELO"; exit 1; }
    MODELO_BASE="${MODELO%/*}"
  else
    [ -f "$MODELO" ] || { echo "Modelo não encontrado: $MODELO"; exit 1; }
    cp "$MODELO" "$MODELO_DIR/camara.json"
    MODELO_BASE="$(cd "$(dirname "$MODELO")" && pwd)"
  fi
  VARS_MODELO=$(python3 - "$MODELO_DIR/camara.json" <<'PY'
import json, re, shlex, sys
try:
    m = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError) as e:
    sys.exit(f"Modelo inválido (JSON): {e}")
uf, mun = str(m.get("uf", "")).strip().lower(), " ".join(str(m.get("municipio", "")).split())
if not re.fullmatch(r"[a-z]{2}", uf) or not mun or not all(c.isalnum() or c in " .-'" for c in mun):
    sys.exit("Modelo inválido: informe uf (sigla) e municipio (só letras, números, espaço e . - ').")
nome = re.sub(r"[^a-z0-9-]", "", str(m.get("nome", "")).lower())
brasao = str(m.get("brasao", "")).strip()
if brasao and (brasao != brasao.split("/")[-1] or brasao.startswith(".") or not re.fullmatch(r"[\w.-]+", brasao)):
    sys.exit("Modelo inválido: brasao deve ser só o nome do arquivo ao lado do camara.json.")
rec, pv = m.get("recursos") or {}, m.get("plenarioVirtual") or {}
sn = {"sim": "s", "nao": "n"}
saida = {"M_UF": uf, "M_MUNICIPIO": mun, "M_NOME": nome, "M_BRASAO": brasao,
         "M_YT": str((m.get("youtube") or {}).get("link", "")),
         "M_TRANSCRICAO": sn.get(rec.get("transcricao"), ""), "M_IA": sn.get(rec.get("ia"), ""),
         "M_JITSI": "s" if pv.get("ligado", True) and pv.get("videoconferencia", "propria") == "propria" else "n"}
print("\n".join(f"{k}={shlex.quote(v)}" for k, v in saida.items()))
PY
) || exit 1
  eval "$VARS_MODELO"   # só atribuições (shlex.quote)
  if [ -n "$M_BRASAO" ]; then
    if [[ "$MODELO_BASE" =~ ^https:// ]]; then
      curl -fsSL --max-filesize 1500000 -o "$MODELO_DIR/$M_BRASAO" "$MODELO_BASE/$M_BRASAO" || echo "  Aviso: não consegui baixar o brasão $M_BRASAO"
    else
      cp "$MODELO_BASE/$M_BRASAO" "$MODELO_DIR/$M_BRASAO" 2>/dev/null || echo "  Aviso: brasão $M_BRASAO não encontrado ao lado do modelo"
    fi
  fi
  : "${UF:=$M_UF}" "${MUNICIPIO:=$M_MUNICIPIO}"
  [ -n "$NOME_DADO" ] || { [ -n "$M_NOME" ] && NOME="$M_NOME" && NOME_DADO="$M_NOME"; }
  [ -n "${YOUTUBE_LINK:-}" ] || YOUTUBE_LINK="$M_YT"
  [ -n "${TRANSCRICAO:-}" ] || { [ -n "$M_TRANSCRICAO" ] && TRANSCRICAO="$M_TRANSCRICAO"; }
  [ -n "${IA:-}" ] || { [ -n "$M_IA" ] && IA="$M_IA"; }
  [ -n "${JITSI:-}" ] || JITSI="$M_JITSI"
  install -d -m 0755 /etc/legis/modelo
  cp "$MODELO_DIR"/* /etc/legis/modelo/   # o postinst confere o modelo completo e o servidor aplica na 1ª partida
  echo "  ${VERDE}✔${FIM} modelo da cidade: $MUNICIPIO/${UF^^}"
fi

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
RADIO="${RADIO:-s}"   # a rádio da Câmara vem sempre com o pacote
perguntar TRANSCRICAO "Transcrição dos discursos e legendas ao vivo, cerca de 600 MB? (s/n)" "s"
perguntar IA "IA local (ata, perguntas, assistente), precisa de 8 GB de RAM — esta máquina tem ${MEMORIA_GB} GB? (s/n)" "$([ "$MEMORIA_GB" -ge 7 ] && echo s || echo n)"
perguntar JITSI "Servidor de videoconferência próprio (Jitsi), para a sessão híbrida? (s/n)" "$([ "$MEMORIA_GB" -ge 4 ] && echo s || echo n)"
if [ -z "${YOUTUBE_CHAVE+x}" ] && tem_tty; then
  echo "Chave de transmissão do YouTube (YouTube Studio → Criar → Transmitir ao vivo → Stream), opcional."
  read -rsp "Cole a chave ou aperte Enter para configurar depois no painel: " YOUTUBE_CHAVE < "$TTY" || true; echo
fi
YOUTUBE_CHAVE="${YOUTUBE_CHAVE:-}"
perguntar YOUTUBE_LINK "Link público da live no YouTube (opcional)" ""
if [ -n "${YOUTUBE_LINK:-}" ] && { [[ "$YOUTUBE_LINK" == *$'\n'* ]] || ! LC_ALL=C grep -qxE 'https://[A-Za-z0-9./?=_@%&:+~#-]+' <<<"$YOUTUBE_LINK"; }; then
  echo "Link do YouTube inválido (deve começar com https:// e não ter espaços nem ; \$ \` aspas): $YOUTUBE_LINK"; exit 1
fi
if [ -z "${TS_AUTHKEY+x}" ] && tem_tty; then
  echo "Chave do Tailscale para publicar na internet sem abrir portas (opcional)."
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
  # nome e endereço vêm da API: o "+" da versão aparece como %2B no endereço, mas o .sha256 usa o nome real
  read -r NOME_DEB URL < <(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | python3 -c \
    "import json,sys; a=next(a for a in json.load(sys.stdin)['assets'] if a['name'].endswith('_all.deb')); print(a['name'], a['browser_download_url'])")
  [ -n "${URL:-}" ] || { echo "Não encontrei o pacote na última versão do GitHub."; exit 1; }
  curl -fsSL -o "$TMP/$NOME_DEB" "$URL"
  DEB="$TMP/$NOME_DEB"
  if curl -fsSL -o "$DEB.sha256" "$URL.sha256" 2>/dev/null; then
    (cd "$TMP" && sha256sum -c "$(basename "$DEB").sha256" >/dev/null) || { echo "O arquivo baixado não confere (SHA-256). Tente de novo."; exit 1; }
    echo "  ${VERDE}✔${FIM} $(basename "$DEB") (SHA-256 conferido)"
  elif [ "${LEGIS_SEM_CONFERIR:-0}" = "1" ]; then
    echo "  Aviso: sem o .sha256 da versão; seguindo SEM conferir o pacote (LEGIS_SEM_CONFERIR=1)."
  else
    echo "Não consegui baixar o .sha256 do pacote para conferir o download. Tente de novo"
    echo "(ou, por sua conta e risco, rode com LEGIS_SEM_CONFERIR=1 para instalar sem conferir)."
    exit 1
  fi
fi
[ -f "$DEB" ] || { echo "Pacote não encontrado: $DEB"; exit 1; }
DEB="$(cd "$(dirname "$DEB")" && pwd)/$(basename "$DEB")"
chmod 644 "$DEB"
CIDADE_PACOTE=$(dpkg-deb -f "$DEB" X-Legis-Cidade 2>/dev/null || true)
if [ -n "$CIDADE_PACOTE" ]; then   # pacote personalizado: município, Câmara, brasão e vereadores já vêm dentro
  if [ -z "$NOME_DADO" ]; then NOME=$(basename "$DEB"); NOME=${NOME#legis-}; NOME=${NOME%%_*}; fi
fi
ROTULO="${CIDADE_PACOTE:-${MUNICIPIO:+$MUNICIPIO/${UF^^}}}"
ok() { sim "$1" && echo "sim" || echo "não"; }
echo
echo "${NEGRITO}Instalando:${FIM} ${ROTULO:-cidade escolhida no painel do presidente} · rádio: sim · transcrição: $(ok "$TRANSCRICAO") · IA local: $(ok "$IA") (${MEMORIA_GB} GB de RAM) · videoconferência: $(ok "$JITSI") · YouTube: $([ -n "$YOUTUBE_CHAVE" ] && echo "chave configurada" || echo "chave depois, no painel") · internet: $([ -n "$TS_AUTHKEY" ] && echo "Tailscale" || echo "só rede local") · endereço: https://$NOME.local"

# ---- nome da máquina: https://NOME.local na rede da Câmara
if [ -f /etc/legis/legis.env ] && [ -z "$NOME_DADO" ]; then
  SEM_HOSTNAME=1   # atualização: a máquina já tem o LEGIS e um nome; não troca (o certificado é desse nome)
  NOME=$(hostname)
fi
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
EXTRAS=(avahi-daemon)
sim "$TRANSCRICAO" && EXTRAS+=(python3-venv)
if { sim "$IA" || sim "$JITSI" || [ -n "$TS_AUTHKEY" ]; } && ! command -v docker >/dev/null; then
  EXTRAS+=(docker.io)   # IA, Jitsi e Tailscale rodam em contêiner; não mexe num Docker oficial já instalado
  for p in docker-cli docker-compose; do   # no Debian 13 o comando docker fica em docker-cli
    apt-cache show "$p" >/dev/null 2>&1 && EXTRAS+=("$p")
  done
fi
echo "Instalando o LEGIS e ${EXTRAS[*]} (alguns minutos)..."
apt-get install -y -q --no-install-recommends "$DEB" "${EXTRAS[@]}" 2>&1 | grep -vE "^(Selecting|Preparing|Unpacking|Setting up|Processing|\(Reading)" || true
dpkg -s legis 2>/dev/null | grep -q '^Status: install ok installed' || { echo "A instalação do pacote falhou (veja as mensagens acima)."; exit 1; }
unset TS_AUTHKEY YOUTUBE_CHAVE

echo
echo "${VERDE}${NEGRITO}✔ LEGIS instalado em $(( ($(date +%s) - INICIO) / 60 )) min $(( ($(date +%s) - INICIO) % 60 )) s.${FIM}"
echo "  Certificado dos aparelhos sem internet (pendrive): sudo legis certificado"
[ -n "$MUNICIPIO" ] || echo "  No primeiro acesso ao painel do presidente, escolha a cidade: os vereadores vêm do TSE."
echo "  Transcrição, IA, videoconferência e internet continuam instalando em segundo plano: acompanhe com  sudo legis estado"
