#!/bin/bash
# Gera o pacote completo de instalação para um Debian limpo:
#   dist/legis_<versão>_all.deb (+ .sha256)
#   dist/instalar-legis.sh
#   dist/legis-completo-<versão>.tar.gz  (instalador + .deb + LEIA-ME, para levar num pendrive)
#     bash packaging/construir-pacote.sh
set -euo pipefail
cd "$(dirname "$0")/.."
rm -f dist/legis_*_all.deb dist/legis_*_all.deb.sha256 dist/legis-completo-*.tar.gz dist/instalar-legis.sh dist/instalar-legis.py
bash packaging/construir-deb.sh >/dev/null
DEB=$(ls dist/legis_*_all.deb)
NOME_DEB=$(basename "$DEB")
VERSAO=${NOME_DEB#legis_}; VERSAO=${VERSAO%_all.deb}
(cd dist && sha256sum "$NOME_DEB" > "$NOME_DEB.sha256")
install -m 0755 packaging/instalar-legis.sh packaging/instalar-legis.py dist/

PASTA="legis-completo-$VERSAO"
OBRA=$(mktemp -d)
trap 'rm -rf "$OBRA"' EXIT
mkdir "$OBRA/$PASTA"
cp "$DEB" "dist/$NOME_DEB.sha256" "$OBRA/$PASTA/"
install -m 0755 packaging/instalar-legis.sh packaging/instalar-legis.py "$OBRA/$PASTA/"
cp packaging/LEIA-ME.md "$OBRA/$PASTA/LEIA-ME.md"
tar -C "$OBRA" --owner=0 --group=0 -czf "dist/$PASTA.tar.gz" "$PASTA"
ls -la dist/
