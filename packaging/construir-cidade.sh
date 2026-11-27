#!/bin/bash
# Gera o pacote do LEGIS personalizado para uma Câmara: um único .deb que já traz o município, o nome da
# Câmara, o brasão e os vereadores eleitos (com fotos, do TSE) e a população (IBGE). Instalar não pergunta
# nada e não depende do TSE na hora: dois cliques no arquivo ou  sudo apt install ./legis-<cidade>_….deb
#
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
UF="${1:?Uso: bash packaging/construir-cidade.sh UF \"Município\" [--brasao arquivo] [--nome curto] [--camara \"Nome\"]}"
MUNICIPIO="${2:?Informe o município}"
shift 2
BRASAO="" NOME="" CAMARA="" YT_LINK=""
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
echo "== Dados oficiais de $MUNICIPIO/${UF^^} (TSE e IBGE)"
python3 - "$UF" "$MUNICIPIO" "$BRASAO" "$NOME" "$CAMARA" "$OBRA" <<'PY'
import json, re, sys, unicodedata
from pathlib import Path
sys.path.insert(0, "server")
import cidade
uf, municipio, brasao, nome, camara, obra = sys.argv[1:7]
d = cidade.buscar(uf, municipio)
semac = "".join(c for c in unicodedata.normalize("NFD", d["municipio"]) if unicodedata.category(c) != "Mn").lower()
d["nome"] = re.sub(r"[^a-z0-9]+", "-", (nome or semac).lower()).strip("-")
d["camara"] = camara.strip() or f"Câmara Municipal de {d['municipio']}"
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

# perguntas do apt já respondidas: estado, município e nome vêm do pacote; o resto segue o padrão
cat > "$OBRA/config" <<EOF
#!/bin/sh
# Pacote do LEGIS de $CAMARA_OK: estado, município e nome já definidos (nenhuma pergunta).
set -e
. /usr/share/debconf/confmodule
db_set legis/uf $(echo "$UF_OK" | tr 'A-Z' 'a-z')
db_set legis/municipio $MUN_OK
db_set legis/nome $SLUG
${YT_LINK:+db_set legis/youtube_link $YT_LINK}
for q in uf municipio nome radio transcricao ia; do db_fset legis/\$q seen true || true; done
db_input low legis/youtube_chave || true
db_input low legis/tailscale_chave || true
db_go || true
EOF

DESTINO="dist/cidades/$SLUG"
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
install -m 0755 packaging/instalar-legis.sh packaging/instalar-legis.py "$OBRA/$PASTA/"
sed "s/^# LEGIS — pacote completo de instalação/# LEGIS — $CAMARA_OK/" packaging/LEIA-ME.md > "$OBRA/$PASTA/LEIA-ME.md"
tar -C "$OBRA" --owner=0 --group=0 -czf "$DESTINO/$PASTA.tar.gz" "$PASTA"
echo
echo "Pronto: $CAMARA_OK ($MUN_OK/$UF_OK)"
ls -la "$DESTINO"
echo
echo "Instalar no servidor da Câmara (Debian 12/13 ou Ubuntu 24.04), sem nenhuma pergunta:"
echo "  sudo apt install ./$NOME_DEB        (ou dois cliques no arquivo)"
