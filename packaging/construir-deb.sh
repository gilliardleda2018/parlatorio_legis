#!/bin/bash
# Gera o instalador dist/legis_<versão>_all.deb a partir do código do repositório.
#   bash packaging/construir-deb.sh
set -euo pipefail
cd "$(dirname "$0")/.."
VERSAO="${VERSAO:-$(date +%Y.%m.%d)+$(git rev-parse --short HEAD 2>/dev/null || echo local)}"
OBRA=$(mktemp -d)
trap 'rm -rf "$OBRA"' EXIT
ARQUIVO="legis${CIDADE_SLUG:+-$CIDADE_SLUG}_${VERSAO}_all.deb"   # pacote de uma Câmara: legis-tuntum_<versão>_all.deb
R="$OBRA/legis_${VERSAO}_all"

mkdir -p "$R/DEBIAN" "$R/opt/legis" "$R/usr/share/doc/legis"
cp -r packaging/deb/raiz/. "$R/"
cp packaging/deb/DEBIAN/{control,templates,config,postinst,prerm,postrm,conffiles} "$R/DEBIAN/"
sed -i "s/@VERSAO@/$VERSAO/" "$R/DEBIAN/control"
if [ -n "${CIDADE_DIR:-}" ]; then   # dados da Câmara (vereadores, fotos, brasão) e perguntas já respondidas
  mkdir -p "$R/usr/share/legis/cidade"
  cp "$CIDADE_DIR/dados.json" "$R/usr/share/legis/cidade/dados.json"
  cp "$CIDADE_DIR/config" "$R/DEBIAN/config"
  # sem sed: | / & \ no nome da Câmara não viram parte da expressão
  python3 - "$R/DEBIAN/control" "${CIDADE_ROTULO:-}" <<'PY'
import sys
from pathlib import Path
arq, rotulo = Path(sys.argv[1]), sys.argv[2]
if not rotulo.strip() or not rotulo.isprintable():
    sys.exit("Rótulo da Câmara inválido.")
linhas = []
for linha in arq.read_text(encoding="utf-8").splitlines():
    if linha.startswith("Description: "):
        linhas.append(f"X-Legis-Cidade: {rotulo}")
    linhas.append(linha)
    if linha == " IA local (Ollama) e publicação na internet pelo Tailscale.":
        linhas += [" .", f" Pacote personalizado: {rotulo}, com os vereadores eleitos e o brasão."]
arq.write_text("\n".join(linhas) + "\n", encoding="utf-8")
PY
fi
for d in server public scripts; do
  cp -r "$d" "$R/opt/legis/"
done
mkdir -p "$R/opt/legis/deploy" && cp -r deploy/tailscale deploy/radio deploy/jitsi "$R/opt/legis/deploy/"
find "$R/opt/legis" \( -name __pycache__ -o -name '*.pyc' \) -prune -exec rm -rf {} +
cp deploy/deb/README.md "$R/usr/share/doc/legis/README.md"
printf 'LEGIS — Sistema de Gestão Parlamentar\nCopyright Aggilli Intelligence.\nCódigo-fonte: https://github.com/gilliardleda2018/parlatorio_legis\n' > "$R/usr/share/doc/legis/copyright"

find "$R/usr" -type d -exec chmod 0755 {} + ; find "$R/usr" -type f -exec chmod 0644 {} +
chmod 0755 "$R/DEBIAN/config" "$R/DEBIAN/postinst" "$R/DEBIAN/prerm" "$R/DEBIAN/postrm" "$R/usr/sbin/legis" "$R/usr/bin/legis-configurar"
find "$R/etc" -type d -exec chmod 0755 {} + ; find "$R/etc" -type f -exec chmod 0644 {} +
find "$R/opt" -type d -exec chmod 0755 {} + ; find "$R/opt" -type f -exec chmod 0644 {} +
chmod 0755 "$R/opt/legis/scripts/"*.sh "$R/opt/legis/deploy/jitsi/"*.sh
INSTALADO=$(du -sk "$R/opt" "$R/usr" | awk '{s+=$1} END {print s}')
echo "Installed-Size: $INSTALADO" >> "$R/DEBIAN/control"

mkdir -p dist
DESTINO="${DESTINO:-dist}"
mkdir -p "$DESTINO"
dpkg-deb --root-owner-group --build "$R" "$DESTINO/$ARQUIVO" >/dev/null
ls -la "$DESTINO/$ARQUIVO"
