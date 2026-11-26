#!/bin/bash
# Gera o instalador dist/legis_<versão>_all.deb a partir do código do repositório.
#   bash packaging/construir-deb.sh
set -euo pipefail
cd "$(dirname "$0")/.."
VERSAO="${VERSAO:-$(date +%Y.%m.%d)+$(git rev-parse --short HEAD 2>/dev/null || echo local)}"
OBRA=$(mktemp -d)
trap 'rm -rf "$OBRA"' EXIT
R="$OBRA/legis_${VERSAO}_all"

mkdir -p "$R/DEBIAN" "$R/opt/legis" "$R/usr/share/doc/legis"
cp -r packaging/deb/raiz/. "$R/"
cp packaging/deb/DEBIAN/{control,templates,config,postinst,prerm,postrm} "$R/DEBIAN/"
sed -i "s/@VERSAO@/$VERSAO/" "$R/DEBIAN/control"
for d in server public scripts; do
  cp -r "$d" "$R/opt/legis/"
done
mkdir -p "$R/opt/legis/deploy" && cp -r deploy/tailscale deploy/radio "$R/opt/legis/deploy/"
find "$R/opt/legis" \( -name __pycache__ -o -name '*.pyc' \) -prune -exec rm -rf {} +
cp deploy/deb/README.md "$R/usr/share/doc/legis/README.md"
printf 'LEGIS — Sistema de Gestão Parlamentar\nCopyright Aggilli Intelligence.\nCódigo-fonte: https://github.com/gilliardleda2018/parlatorio_legis\n' > "$R/usr/share/doc/legis/copyright"

find "$R/usr" -type d -exec chmod 0755 {} + ; find "$R/usr" -type f -exec chmod 0644 {} +
chmod 0755 "$R/DEBIAN/config" "$R/DEBIAN/postinst" "$R/DEBIAN/prerm" "$R/DEBIAN/postrm" "$R/usr/sbin/legis"
find "$R/opt" -type d -exec chmod 0755 {} + ; find "$R/opt" -type f -exec chmod 0644 {} +
chmod 0755 "$R/opt/legis/scripts/"*.sh
INSTALADO=$(du -sk "$R/opt" "$R/usr" | awk '{s+=$1} END {print s}')
echo "Installed-Size: $INSTALADO" >> "$R/DEBIAN/control"

mkdir -p dist
dpkg-deb --root-owner-group --build "$R" "dist/legis_${VERSAO}_all.deb" >/dev/null
ls -la "dist/legis_${VERSAO}_all.deb"
