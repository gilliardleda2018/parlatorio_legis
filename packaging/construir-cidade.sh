#!/bin/bash
# Gera o pacote do LEGIS personalizado para uma Câmara: um único .deb que já traz o município, o nome da
# Câmara, o brasão e os vereadores eleitos (com fotos, do TSE) e a população (IBGE). Instalar não pergunta
# nada e não depende do TSE na hora: dois cliques no arquivo ou  sudo apt install ./legis-<cidade>_….deb
#
#   bash packaging/construir-cidade.sh --modelo cidades/tuntum/camara.json   (modelo da cidade: ver cidades/LEIA-ME.md)
#   bash packaging/construir-cidade.sh MA "Tuntum"
#   bash packaging/construir-cidade.sh MA "Barra do Corda" --brasao brasao.png --nome barradocorda \
#        --camara "Câmara Municipal de Barra do Corda" --youtube-link https://www.youtube.com/@camarabdc/live
#
# Já vem pronto: rádio da Câmara, videoconferência própria (Jitsi), transmissão para o YouTube (a chave do
# canal entra na instalação, YOUTUBE_CHAVE=…, ou no painel; nunca vai dentro do pacote).
#
# Sai em dist/cidades/<nome>/: o .deb, o .sha256 e legis-<nome>-completo-<versão>.tar.gz (com o instalador).
set -euo pipefail
cd "$(dirname "$0")/.."
BRASAO="" NOME="" CAMARA="" YT_LINK="" MODELO="" M_RADIO="" M_JITSI="" M_TRANSCRICAO="" M_IA=""
if [ "${1:-}" = "--modelo" ]; then   # tudo vem do modelo da cidade; opções depois dele ainda podem sobrepor
  MODELO="${2:?Informe o arquivo do modelo (cidades/<nome>/camara.json)}"; shift 2
  VARS=$(python3 packaging/modelo.py vars "$MODELO") || exit 1
  eval "$VARS"   # saída com shlex.quote: só atribuições
  UF="$UF" MUNICIPIO="$MUN" NOME="$NOME" CAMARA="$CAM" YT_LINK="$YT" BRASAO="$BRASAO"
  M_RADIO="$RADIO" M_JITSI="$JITSI" M_TRANSCRICAO="$TRANSCRICAO" M_IA="$IA"
else
  UF="${1:?Uso: bash packaging/construir-cidade.sh --modelo cidades/<nome>/camara.json  ou  UF \"Município\" [--brasao arquivo] [--nome curto] [--camara \"Nome\"]}"
  MUNICIPIO="${2:?Informe o município}"
  shift 2
fi
while [ $# -gt 0 ]; do
  case "$1" in
    --brasao) BRASAO="$2"; shift 2 ;;
    --nome) NOME="$2"; shift 2 ;;
    --camara) CAMARA="$2"; shift 2 ;;
    --youtube-link) YT_LINK="$2"; shift 2 ;;
    *) echo "Opção desconhecida: $1"; exit 1 ;;
  esac
done
[ -z "$BRASAO" ] || [ -f "$BRASAO" ] || { echo "Brasão não encontrado: $BRASAO"; exit 1; }

OBRA=$(mktemp -d)
trap 'rm -rf "$OBRA"' EXIT
if [ -n "$MODELO" ]; then   # o modelo vai dentro do pacote: o servidor aplica as preferências na primeira partida
  python3 packaging/modelo.py normalizar "$MODELO" "$OBRA/modelo.json"
  [ -n "$BRASAO" ] && cp "$BRASAO" "$OBRA/$(basename "$BRASAO")"
fi
echo "== Dados oficiais de $MUNICIPIO/${UF^^} (TSE e IBGE)"
python3 - "$UF" "$MUNICIPIO" "$BRASAO" "$NOME" "$CAMARA" "$OBRA" "$YT_LINK" <<'PY'
import json, re, sys, unicodedata
from pathlib import Path
sys.path.insert(0, "server")
import cidade
uf, municipio, brasao, nome, camara, obra, yt = sys.argv[1:8]


def nome_valido(rotulo: str, valor: str, limite: int) -> str:
    """Município e Câmara vão para scripts que rodam como root: só letras (com acento), dígitos, espaço e .-'"""
    valor = " ".join(valor.split())
    if not valor or len(valor) > limite or not all(c.isalnum() or c in " .-'" for c in valor):
        sys.exit(f"{rotulo} inválido: use só letras, números, espaço e . - ' (até {limite} caracteres): {valor!r}")
    return valor


municipio = nome_valido("Município", municipio, 80)
if camara.strip():
    camara = nome_valido("Nome da Câmara", camara, 120)
if yt and not re.fullmatch(r"https://[A-Za-z0-9./?=_@%&:+~#-]+", yt, re.ASCII):
    sys.exit(f"Link do YouTube inválido (deve começar com https:// e não ter espaços nem ; $ ` aspas): {yt!r}")
d = cidade.buscar(uf, municipio)
d["municipio"] = nome_valido("Município", d["municipio"], 80)
semac = "".join(c for c in unicodedata.normalize("NFD", d["municipio"]) if unicodedata.category(c) != "Mn").lower()
d["nome"] = re.sub(r"[^a-z0-9]+", "-", (nome or semac).lower()).strip("-")
d["camara"] = camara.strip() or f"Câmara Municipal de {d['municipio']}"
if not re.fullmatch(r"[A-Za-z]{2}", d["uf"]) or not re.fullmatch(r"[a-z0-9-]{1,40}", d["nome"]):
    sys.exit("Estado ou nome curto inválido.")
if brasao:
    b = Path(brasao).read_bytes()
    mime = "image/png" if b[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg" if b[:2] == b"\xff\xd8" else \
           "image/webp" if b[8:12] == b"WEBP" else None
    if not mime:
        sys.exit("Brasão: use PNG, JPEG ou WebP.")
    if len(b) > 1_500_000:
        sys.exit("Brasão grande demais (máximo 1,5 MB).")
    d["logo"] = {"mime": mime, "dados": b}
    print(f"Brasão: {Path(brasao).name} ({len(b) // 1024} KB)")
(Path(obra) / "dados.json").write_text(cidade.para_json(d), encoding="utf-8")
(Path(obra) / "info").write_text(f"{d['uf']}\n{d['municipio']}\n{d['nome']}\n{d['camara']}\n", encoding="utf-8")
print(f"{d['camara']}: {len(d['eleitos'])} vereadores, {sum(1 for e in d['eleitos'] if e.get('foto'))} com foto")
PY
{ read -r UF_OK; read -r MUN_OK; read -r SLUG; read -r CAMARA_OK; } < "$OBRA/info"

# valor entre aspas simples para sh (o ' vira '\''): o config roda como root
aspas() { printf "'%s'" "$(printf %s "$1" | sed "s/'/'\\\\''/g")"; }

# perguntas do apt já respondidas: estado, município e nome vêm do pacote; o resto segue o padrão
cat > "$OBRA/config" <<EOF
#!/bin/sh
# Pacote do LEGIS de uma Câmara: estado, município e nome já definidos (nenhuma pergunta).
set -e
. /usr/share/debconf/confmodule
db_set legis/uf $(aspas "$(echo "$UF_OK" | tr 'A-Z' 'a-z')")
db_set legis/municipio $(aspas "$MUN_OK")
db_set legis/nome $(aspas "$SLUG")
${YT_LINK:+db_set legis/youtube_link $(aspas "$YT_LINK")}
${M_RADIO:+db_set legis/radio $M_RADIO}
${M_JITSI:+db_set legis/jitsi $M_JITSI}
${M_TRANSCRICAO:+db_set legis/transcricao $M_TRANSCRICAO}
${M_IA:+db_set legis/ia $M_IA}
for q in uf municipio nome radio transcricao ia; do db_fset legis/\$q seen true || true; done
db_input low legis/youtube_chave || true
db_input low legis/tailscale_chave || true
db_go || true
EOF

DESTINO="${DIST_CIDADES:-dist/cidades}/$SLUG"
rm -rf "$DESTINO" && mkdir -p "$DESTINO"
echo "== Pacote"
CIDADE_DIR="$OBRA" CIDADE_SLUG="$SLUG" CIDADE_ROTULO="$CAMARA_OK ($MUN_OK/$UF_OK)" DESTINO="$DESTINO" \
  bash packaging/construir-deb.sh >/dev/null
DEB=$(ls "$DESTINO"/legis-"$SLUG"_*_all.deb)
NOME_DEB=$(basename "$DEB")
VERSAO=${NOME_DEB#legis-"$SLUG"_}; VERSAO=${VERSAO%_all.deb}
(cd "$DESTINO" && sha256sum "$NOME_DEB" > "$NOME_DEB.sha256")

PASTA="legis-$SLUG-completo-$VERSAO"
mkdir "$OBRA/$PASTA"
cp "$DEB" "$DEB.sha256" "$OBRA/$PASTA/"
install -m 0755 packaging/instalar-legis.sh "$OBRA/$PASTA/instalar-legis.sh"
python3 - "$CAMARA_OK" packaging/LEIA-ME.md "$OBRA/$PASTA/LEIA-ME.md" <<'PY'   # sem sed: o nome não vira expressão
import sys
from pathlib import Path
camara, origem, destino = sys.argv[1:4]
t = Path(origem).read_text(encoding="utf-8")
Path(destino).write_text(t.replace("# LEGIS — pacote completo de instalação", f"# LEGIS — {camara}", 1), encoding="utf-8")
PY
tar -C "$OBRA" --owner=0 --group=0 -czf "$DESTINO/$PASTA.tar.gz" "$PASTA"
echo
echo "Pronto: $CAMARA_OK ($MUN_OK/$UF_OK)"
ls -la "$DESTINO"
echo
echo "Instalar no servidor da Câmara (Debian 12/13 ou Ubuntu 24.04), sem nenhuma pergunta:"
echo "  sudo apt install ./$NOME_DEB        (ou dois cliques no arquivo)"
