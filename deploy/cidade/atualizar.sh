#!/bin/bash
# Atualiza o LEGIS de uma Câmara para a versão mais nova, mantendo dados e configuração.
#   sudo bash /opt/legis/deploy/cidade/atualizar.sh
set -euo pipefail
PASTA="${PASTA:-/opt/legis}"
RAMO="${RAMO:-main}"
cd "$PASTA"
[ "$(id -u)" = 0 ] || { echo "Rode como root: sudo bash atualizar.sh"; exit 1; }
echo "== Backup antes de atualizar =="
mkdir -p dados/backups
python3 - <<'PY'
import sqlite3, gzip, time, shutil, pathlib
origem = pathlib.Path("dados/legis.db")
if origem.exists():
    destino = pathlib.Path(f"dados/backups/antes-atualizar-{time.strftime('%Y%m%d-%H%M%S')}.db")
    s, d = sqlite3.connect(origem), sqlite3.connect(destino)
    s.backup(d); d.close(); s.close()
    with open(destino, "rb") as e, gzip.open(str(destino) + ".gz", "wb") as g:
        shutil.copyfileobj(e, g)
    destino.unlink()
    print("backup:", str(destino) + ".gz")
PY
chown -R 1000:1000 dados
git fetch -q origin "$RAMO" && git reset -q --hard "origin/$RAMO"
docker compose up -d --build
docker compose ps
echo "Atualizado: $(git log --oneline -1)"
