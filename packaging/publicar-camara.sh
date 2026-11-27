#!/bin/bash
# Gera o pacote de uma Câmara e publica no GitHub, numa release própria (camara-<nome>), com nomes fixos:
# o comando de instalação nunca muda e baixa direto do GitHub, sem depender de nenhum servidor ligado.
#
#   bash packaging/publicar-camara.sh --modelo cidades/arame/camara.json [--forcar]
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
ARGS=() BRASAO="" BRASAO_URL="" NOME="" CAMARA="" YT="" MODELO="" FORCAR=0 CONGELADA=false
if [ "${1:-}" = "--modelo" ]; then   # modelo da cidade (cidades/<nome>/camara.json): tudo vem dele
  MODELO="${2:?Informe o modelo}"; shift 2
  VARS=$(python3 packaging/modelo.py vars "$MODELO") || exit 1
  eval "$VARS"   # shlex.quote: só atribuições
  MUNICIPIO="$MUN" CAMARA="$CAM"
else
  UF="${1:?Uso: bash packaging/publicar-camara.sh --modelo cidades/<nome>/camara.json  ou  UF \"Município\" [opções]}"
  MUNICIPIO="${2:?Informe o município}"
  shift 2
fi
while [ $# -gt 0 ]; do
  case "$1" in
    --forcar) FORCAR=1; shift ;;
    --brasao) BRASAO="$2"; shift 2 ;;
    --brasao-url) BRASAO_URL="$2"; shift 2 ;;
    --nome) NOME="$2"; shift 2 ;;
    --camara) CAMARA="$2"; shift 2 ;;
    --youtube-link) YT="$2"; shift 2 ;;
    *) echo "Opção desconhecida: $1"; exit 1 ;;
  esac
done
# município, Câmara e link entram no instalar.sh que roda como root: só caracteres seguros
python3 - "$MUNICIPIO" "$CAMARA" "$YT" <<'PY'
import re, sys
municipio, camara, yt = sys.argv[1:4]
for rotulo, valor, limite, obrigatorio in (("Município", municipio, 80, True), ("Nome da Câmara", camara, 120, False)):
    valor = " ".join(valor.split())
    if (obrigatorio or valor) and (not valor or len(valor) > limite or not all(c.isalnum() or c in " .-'" for c in valor)):
        sys.exit(f"{rotulo} inválido: use só letras, números, espaço e . - ' (até {limite} caracteres): {valor!r}")
if yt and not re.fullmatch(r"https://[A-Za-z0-9./?=_@%&:+~#-]+", yt, re.ASCII):
    sys.exit(f"Link do YouTube inválido (deve começar com https:// e não ter espaços nem ; $ ` aspas): {yt!r}")
PY
# Câmara congelada (no modelo ou em cidades/<nome>/ do repositório): só republica com --forcar
NOME_CURTO="${NOME:-$(python3 -c "import sys; sys.path.insert(0, 'server'); import modelo_cidade as m; print(m.slug(sys.argv[1]))" "$MUNICIPIO")}"
if [ "$FORCAR" != 1 ] && { [ "$CONGELADA" = true ] || python3 packaging/modelo.py congelada "$NOME_CURTO"; }; then
  echo "A Câmara '$NOME_CURTO' está congelada (cidades/$NOME_CURTO/camara.json): o pacote publicado não foi mexido."
  echo "Para atualizar de propósito: bash packaging/publicar-camara.sh --modelo cidades/$NOME_CURTO/camara.json --forcar"
  exit 0
fi
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

if [ -n "$MODELO" ]; then
  DIST_CIDADES="$OBRA/dist" bash packaging/construir-cidade.sh --modelo "$MODELO" "${ARGS[@]}"
else
  DIST_CIDADES="$OBRA/dist" bash packaging/construir-cidade.sh "$UF" "$MUNICIPIO" "${ARGS[@]}"
fi
PASTA=$(ls -d "$OBRA"/dist/*/ | head -1)
SLUG=$(basename "$PASTA")
DEB=$(ls "$PASTA"legis-"$SLUG"_*_all.deb)
VERSAO=$(basename "$DEB"); VERSAO=${VERSAO#legis-"$SLUG"_}; VERSAO=${VERSAO%_all.deb}
ROTULO=$(dpkg-deb -f "$DEB" X-Legis-Cidade)
[[ "$ROTULO" =~ ^[^\$\`\"\\]+$ ]] && [ "$(printf %s "$ROTULO" | wc -l)" = 0 ] || { echo "Rótulo da Câmara inválido: $ROTULO"; exit 1; }
[[ "$SLUG" =~ ^[a-z0-9-]{1,40}$ ]] || { echo "Nome curto inválido: $SLUG"; exit 1; }

# arquivos com nomes fixos (o .sha256 confere o nome fixo)
SAIDA="$OBRA/publicar"; mkdir -p "$SAIDA"
cp "$DEB" "$SAIDA/legis-${SLUG}_all.deb"
(cd "$SAIDA" && sha256sum "legis-${SLUG}_all.deb" > "legis-${SLUG}_all.deb.sha256")
install -m 0755 packaging/instalar-legis.sh "$SAIDA/instalar-legis.sh"
cp "$PASTA"legis-"$SLUG"-completo-*.tar.gz "$SAIDA/legis-${SLUG}-completo.tar.gz"
BASE="https://github.com/$REPO/releases/download/camara-$SLUG"
SHA_INSTALADOR=$(sha256sum "$SAIDA/instalar-legis.sh" | cut -d' ' -f1)   # conferido antes de executar
cat > "$SAIDA/instalar.sh" <<EOF
#!/bin/bash
# Instala o LEGIS de uma Câmara direto do GitHub, sem perguntas.
#   curl -fsSL $BASE/instalar.sh | sudo bash
set -euo pipefail
ROTULO=$(printf '%q' "$ROTULO")
[ "\$(id -u)" = 0 ] || { echo "Rode como administrador:  curl -fsSL $BASE/instalar.sh | sudo bash"; exit 1; }
baixar() { curl -fsSL -o "\$2" "\$1" 2>/dev/null || wget -qO "\$2" "\$1"; }
T=\$(mktemp -d)
echo "== LEGIS de \$ROTULO: baixando o pacote do GitHub..."
baixar "$BASE/legis-${SLUG}_all.deb" "\$T/legis-${SLUG}_all.deb"
baixar "$BASE/legis-${SLUG}_all.deb.sha256" "\$T/legis-${SLUG}_all.deb.sha256"
(cd "\$T" && sha256sum -c "legis-${SLUG}_all.deb.sha256" >/dev/null) || { echo "O pacote baixado não confere (SHA-256)."; exit 1; }
echo "   pacote conferido (SHA-256), versão $VERSAO"
baixar "$BASE/instalar-legis.sh" "\$T/instalar-legis.sh"
echo "$SHA_INSTALADOR  \$T/instalar-legis.sh" | sha256sum -c - >/dev/null || { echo "O instalador baixado não confere (SHA-256)."; exit 1; }
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
# o modelo completo também vai na release: a regeneração a cada versão usa o mesmo modelo
[ -n "$MODELO" ] && python3 packaging/modelo.py normalizar "$MODELO" "$SAIDA/modelo.json"

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
