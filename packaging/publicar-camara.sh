#!/bin/bash
# Gera o pacote de uma Câmara e publica no GitHub, numa release própria (camara-<nome>), com nomes fixos:
# o comando de instalação nunca muda e baixa direto do GitHub, sem depender de nenhum servidor ligado.
#
#   bash packaging/publicar-camara.sh MA "Arame" [--camara "Câmara Municipal de Arame"] [--nome arame]
#        [--youtube-link https://…] [--brasao arquivo.png | --brasao-url https://…/brasao.png]
#
# Instalar a Câmara (no servidor dela):
#   curl -fsSL https://github.com/<repo>/releases/download/camara-<nome>/instalar.sh | sudo bash
#
# Usado pelo GitHub Actions (formulário "Pacote de uma Câmara" e a cada versão nova). Precisa do gh autenticado.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
UF="${1:?Uso: bash packaging/publicar-camara.sh UF \"Município\" [opções]}"
MUNICIPIO="${2:?Informe o município}"
shift 2
ARGS=() BRASAO="" BRASAO_URL="" NOME="" CAMARA="" YT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --brasao) BRASAO="$2"; shift 2 ;;
    --brasao-url) BRASAO_URL="$2"; shift 2 ;;
    --nome) NOME="$2"; shift 2 ;;
    --camara) CAMARA="$2"; shift 2 ;;
    --youtube-link) YT="$2"; shift 2 ;;
    *) echo "Opção desconhecida: $1"; exit 1 ;;
  esac
done
OBRA=$(mktemp -d)
trap 'rm -rf "$OBRA"' EXIT

if [ -n "$BRASAO_URL" ]; then
  [[ "$BRASAO_URL" =~ ^https:// ]] || { echo "O endereço do brasão deve começar com https://"; exit 1; }
  curl -fsSL --max-filesize 1500000 -o "$OBRA/brasao.img" "$BRASAO_URL" || { echo "Não consegui baixar o brasão de $BRASAO_URL"; exit 1; }
  BRASAO="$OBRA/brasao.img"
fi
[ -n "$NOME" ] && ARGS+=(--nome "$NOME")
[ -n "$CAMARA" ] && ARGS+=(--camara "$CAMARA")
[ -n "$YT" ] && ARGS+=(--youtube-link "$YT")
[ -n "$BRASAO" ] && ARGS+=(--brasao "$BRASAO")

DIST_CIDADES="$OBRA/dist" bash packaging/construir-cidade.sh "$UF" "$MUNICIPIO" "${ARGS[@]}"
PASTA=$(ls -d "$OBRA"/dist/*/ | head -1)
SLUG=$(basename "$PASTA")
DEB=$(ls "$PASTA"legis-"$SLUG"_*_all.deb)
VERSAO=$(basename "$DEB"); VERSAO=${VERSAO#legis-"$SLUG"_}; VERSAO=${VERSAO%_all.deb}
ROTULO=$(dpkg-deb -f "$DEB" X-Legis-Cidade)

# arquivos com nomes fixos (o .sha256 confere o nome fixo)
SAIDA="$OBRA/publicar"; mkdir -p "$SAIDA"
cp "$DEB" "$SAIDA/legis-${SLUG}_all.deb"
(cd "$SAIDA" && sha256sum "legis-${SLUG}_all.deb" > "legis-${SLUG}_all.deb.sha256")
install -m 0755 packaging/instalar-legis.sh "$SAIDA/instalar-legis.sh"
cp "$PASTA"legis-"$SLUG"-completo-*.tar.gz "$SAIDA/legis-${SLUG}-completo.tar.gz"
BASE="https://github.com/$REPO/releases/download/camara-$SLUG"
cat > "$SAIDA/instalar.sh" <<EOF
#!/bin/bash
# Instala o LEGIS de $ROTULO direto do GitHub, sem perguntas.
#   curl -fsSL $BASE/instalar.sh | sudo bash
set -euo pipefail
[ "\$(id -u)" = 0 ] || { echo "Rode como administrador:  curl -fsSL $BASE/instalar.sh | sudo bash"; exit 1; }
baixar() { curl -fsSL -o "\$2" "\$1" 2>/dev/null || wget -qO "\$2" "\$1"; }
T=\$(mktemp -d)
echo "== LEGIS de $ROTULO: baixando o pacote do GitHub..."
baixar "$BASE/legis-${SLUG}_all.deb" "\$T/legis-${SLUG}_all.deb"
baixar "$BASE/legis-${SLUG}_all.deb.sha256" "\$T/legis-${SLUG}_all.deb.sha256"
(cd "\$T" && sha256sum -c "legis-${SLUG}_all.deb.sha256" >/dev/null) || { echo "O pacote baixado não confere (SHA-256)."; exit 1; }
echo "   pacote conferido (SHA-256), versão $VERSAO"
baixar "$BASE/instalar-legis.sh" "\$T/instalar-legis.sh"
DEB="\$T/legis-${SLUG}_all.deb" bash "\$T/instalar-legis.sh"
EOF
# configuração da Câmara, para o GitHub regenerar o pacote a cada versão nova
python3 - "$UF" "$MUNICIPIO" "$SLUG" "$CAMARA" "$YT" "$( [ -n "$BRASAO" ] && echo sim )" > "$SAIDA/camara.json" <<'PY'
import json, sys
uf, municipio, nome, camara, yt, brasao = sys.argv[1:7]
print(json.dumps({"uf": uf.upper(), "municipio": municipio, "nome": nome, "camara": camara, "youtube": yt,
                  "brasao": "brasao" if brasao else ""}, ensure_ascii=False, indent=2))
PY
[ -n "$BRASAO" ] && cp "$BRASAO" "$SAIDA/brasao"

TAG="camara-$SLUG"
NOTAS="$OBRA/notas.md"
cat > "$NOTAS" <<EOF
## $ROTULO

Pacote do LEGIS desta Câmara, com o município, a Câmara, o brasão e os vereadores eleitos (com foto) já dentro. Atualizado automaticamente a cada versão nova do LEGIS (agora: **$VERSAO**).

**Instalar** no servidor da Câmara (Debian 12/13 ou Ubuntu 24.04), sem perguntas:

\`\`\`bash
curl -fsSL $BASE/instalar.sh | sudo bash
\`\`\`

O mesmo comando também atualiza uma instalação existente, mantendo os dados.
EOF
if gh release view "$TAG" -R "$REPO" >/dev/null 2>&1; then
  gh release edit "$TAG" -R "$REPO" --title "LEGIS — $ROTULO" --notes-file "$NOTAS" --latest=false >/dev/null
else
  gh release create "$TAG" -R "$REPO" --title "LEGIS — $ROTULO" --notes-file "$NOTAS" --latest=false >/dev/null
fi
gh release upload "$TAG" -R "$REPO" --clobber "$SAIDA"/* >/dev/null
echo
echo "Publicado: $ROTULO — versão $VERSAO"
echo "Instalar:  curl -fsSL $BASE/instalar.sh | sudo bash"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  printf '## %s\n\nVersão **%s**\n\nInstalar no servidor da Câmara:\n\n```bash\ncurl -fsSL %s/instalar.sh | sudo bash\n```\n' \
    "$ROTULO" "$VERSAO" "$BASE" >> "$GITHUB_STEP_SUMMARY"
fi
