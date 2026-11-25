"""Backup do Parlatório Legis.

* Banco SQLite (estado, imagens, sessões, projetos, auditoria): cópia consistente pela
  API de backup do SQLite, compactada (`parlatorio-AAAAMMDD-HHMM.db.gz`), guardando as
  últimas N cópias.
* Gravações: espelho dos arquivos de áudio (copia só os novos).
* Nextcloud (opcional): envia o .db.gz por WebDAV. Configure por variáveis de ambiente
  (nunca no banco): NEXTCLOUD_URL (ex.: https://nuvem.exemplo/remote.php/dav/files/usuario),
  NEXTCLOUD_USUARIO, NEXTCLOUD_SENHA (senha de app) e NEXTCLOUD_PASTA (padrão Parlatorio/backups).

Para restaurar: pare o servidor, descompacte o .db.gz sobre o arquivo do banco
(`gunzip -c parlatorio-....db.gz > dados/parlatorio.db`) e copie a pasta gravacoes de volta.
"""
import base64
import gzip
import os
import shutil
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _nextcloud() -> dict | None:
    url, usuario, senha = (os.environ.get(k, "").strip() for k in ("NEXTCLOUD_URL", "NEXTCLOUD_USUARIO", "NEXTCLOUD_SENHA"))
    if not (url and usuario and senha):
        return None
    return {"url": url.rstrip("/"), "usuario": usuario, "senha": senha,
            "pasta": os.environ.get("NEXTCLOUD_PASTA", "Parlatorio/backups").strip("/")}


def _webdav(cfg: dict, metodo: str, caminho: str, dados: bytes | None = None) -> int:
    url = cfg["url"] + "/" + "/".join(urllib.parse.quote(p) for p in caminho.split("/") if p)
    req = urllib.request.Request(url, data=dados, method=metodo)
    cred = base64.b64encode(f"{cfg['usuario']}:{cfg['senha']}".encode()).decode()
    req.add_header("Authorization", "Basic " + cred)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status
    except urllib.error.HTTPError as e:
        if metodo == "MKCOL" and e.code == 405:  # a pasta já existe
            return 405
        raise


def enviar_nextcloud(arquivo: Path) -> str:
    cfg = _nextcloud()
    if not cfg:
        return "desligado"
    atual = ""
    for parte in cfg["pasta"].split("/"):
        atual = f"{atual}/{parte}" if atual else parte
        _webdav(cfg, "MKCOL", atual)
    _webdav(cfg, "PUT", f"{cfg['pasta']}/{arquivo.name}", arquivo.read_bytes())
    return "enviado"


def fazer_backup(banco: Path, gravacoes: Path, destino: Path, manter: int = 14) -> dict:
    destino.mkdir(parents=True, exist_ok=True)
    carimbo = time.strftime("%Y%m%d-%H%M%S")
    tmp = destino / f".parlatorio-{carimbo}.db"
    origem = sqlite3.connect(banco)
    copia = sqlite3.connect(tmp)
    try:
        origem.backup(copia)  # cópia consistente mesmo com o servidor gravando
    finally:
        copia.close()
        origem.close()
    final = destino / f"parlatorio-{carimbo}.db.gz"
    with open(tmp, "rb") as f, gzip.open(final, "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g)
    tmp.unlink()
    antigos = sorted(destino.glob("parlatorio-*.db.gz"))[:-manter]
    for a in antigos:
        a.unlink()
    novas = 0
    if gravacoes.exists():
        alvo = destino / "gravacoes"
        alvo.mkdir(exist_ok=True)
        for f in gravacoes.iterdir():
            if f.is_file() and not f.name.startswith(".") and not (alvo / f.name).exists():
                shutil.copy2(f, alvo / f.name)
                novas += 1
    return {"arquivo": final.name, "tamanho": final.stat().st_size, "gravacoesNovas": novas, "removidos": len(antigos),
            "copias": len(list(destino.glob("parlatorio-*.db.gz")))}
