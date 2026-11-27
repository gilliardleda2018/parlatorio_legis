#!/bin/bash
# Publica uma versão nova do LEGIS no GitHub: roda os testes, gera o pacote completo e cria a release
# (a mais recente) com o .deb, o .sha256, o instalar-legis.sh e o legis-completo-<versão>.tar.gz.
#   bash packaging/publicar-versao.sh "Título da novidade"
# Precisa do gh autenticado. Não publica com mudanças sem commit nem com testes falhando.
set -euo pipefail
cd "$(dirname "$0")/.."
TITULO="${1:?Uso: bash packaging/publicar-versao.sh \"Título da novidade\"}"
[ -z "$(git status --porcelain)" ] || { echo "Há mudanças sem commit. Faça o commit antes de publicar."; exit 1; }
[ "${CI:-}" = "true" ] || git push -q   # no GitHub Actions o código já está lá
echo "== testes"; (cd tests && python3 -m unittest -q) || { echo "Testes falharam: nada publicado."; exit 1; }
echo "== pacote"; bash packaging/construir-pacote.sh >/dev/null
DEB=$(ls dist/legis_*_all.deb); NOME_DEB=$(basename "$DEB")
VERSAO=${NOME_DEB#legis_}; VERSAO=${VERSAO%_all.deb}
N=1; TAG="v$(date +%Y.%m.%d)"
while gh release view "$TAG" >/dev/null 2>&1; do N=$((N + 1)); TAG="v$(date +%Y.%m.%d)-$N"; done
NOTAS=$(mktemp)
cat > "$NOTAS" <<EOF
## $TITULO

## Instalar num servidor Debian 12/13 ou Ubuntu 24.04 limpo

\`\`\`bash
curl -fsSLO https://github.com/gilliardleda2018/parlatorio_legis/releases/latest/download/instalar-legis.sh
sudo bash instalar-legis.sh
\`\`\`

Automático: instala o LEGIS, a rádio da Câmara, a transcrição e as legendas, e a IA local se houver 8 GB de RAM. A cidade é escolhida no primeiro acesso ao painel do presidente. Para escolher tudo na instalação: \`sudo bash instalar-legis.sh --perguntar\`.

Sem internet boa no local: leve o **legis-completo-$VERSAO.tar.gz** num pendrive, descompacte e rode \`sudo bash instalar-legis.sh\` na pasta.

Já tem o LEGIS? Instale por cima: \`sudo apt install ./$NOME_DEB\`. Dados e configuração são mantidos.

**SHA-256 do .deb:** \`$(cut -d' ' -f1 "dist/$NOME_DEB.sha256")\`
EOF
gh release create "$TAG" "$DEB" "dist/$NOME_DEB.sha256" dist/instalar-legis.sh "dist/legis-completo-$VERSAO.tar.gz" \
  --target "$(git rev-parse HEAD)" --title "LEGIS $VERSAO — $TITULO" --notes-file "$NOTAS" --latest
rm -f "$NOTAS"
echo "== conferindo o download"
sleep 5
T=$(mktemp -d)
curl -fsSL -o "$T/$NOME_DEB" "https://github.com/gilliardleda2018/parlatorio_legis/releases/download/$TAG/$NOME_DEB"
(cd dist && sha256sum "$NOME_DEB") | sed "s|  |  $T/|" | sha256sum -c - && rm -rf "$T"
echo "Publicada: https://github.com/gilliardleda2018/parlatorio_legis/releases/tag/$TAG"
