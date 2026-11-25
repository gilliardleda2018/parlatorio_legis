"""Transcrição automática dos discursos gravados.

Escolha do motor pela variável TRANSCRICAO:

* ``auto`` (padrão): usa o motor local se existir o ambiente ``dados/whisper`` criado por
  ``scripts/instalar_transcricao.sh`` (ou se TRANSCRICAO_PYTHON indicar um Python com faster-whisper).
* ``local``: faster-whisper rodando no próprio servidor. Depois de baixar o modelo, não usa internet.
* ``api``: serviço compatível com ``/v1/audio/transcriptions`` (TRANSCRICAO_URL, TRANSCRICAO_CHAVE).
  Atenção: o áudio sai do servidor da câmara.
* ``comando``: TRANSCRICAO_CMD recebe o caminho do áudio e imprime JSON ou texto puro.
* ``0``: desligado (a transcrição ainda pode ser digitada à mão no painel).

Todos devolvem o mesmo formato: {"texto", "segmentos": [{"ini", "fim", "texto"}], "idioma", "motor"}.
O servidor continua só com a biblioteca padrão; o faster-whisper roda num processo à parte.
"""
import json
import os
import secrets
import shlex
import subprocess
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
LIMITE_SEGMENTOS = 3000


class Motor:
    def __init__(self, pasta_dados: Path):
        modo = os.environ.get("TRANSCRICAO", "auto").strip().lower()
        self.idioma = os.environ.get("TRANSCRICAO_IDIOMA", "pt")
        self.modelo = os.environ.get("TRANSCRICAO_MODELO", "")
        self.nome = "desligado"
        self.cmd: list[str] | None = None
        self.url = self.chave = ""
        if modo in ("0", "off", "desligado", "nao", "não"):
            return
        if os.environ.get("TRANSCRICAO_CMD") and modo in ("auto", "comando"):
            self.nome, self.cmd = "comando", shlex.split(os.environ["TRANSCRICAO_CMD"])
            return
        if modo == "api":
            self.url = os.environ.get("TRANSCRICAO_URL", "").strip()
            self.chave = os.environ.get("TRANSCRICAO_CHAVE", "")
            if self.url.startswith(("https://", "http://")):
                self.nome = "api"
            return
        py = os.environ.get("TRANSCRICAO_PYTHON") or str(pasta_dados / "whisper" / "bin" / "python")
        if Path(py).exists() or (modo == "local" and os.environ.get("TRANSCRICAO_PYTHON")):
            self.nome = "local"
            self.cmd = [py, str(RAIZ / "scripts" / "transcrever.py"), "--modelo", self.modelo or "small",
                        "--idioma", self.idioma]

    @property
    def ligado(self) -> bool:
        return self.nome != "desligado"

    def descricao(self) -> str:
        return {"local": f"local (faster-whisper {self.modelo or 'small'})", "api": "serviço externo",
                "comando": "comando configurado", "desligado": "desligada"}[self.nome]

    def transcrever(self, arquivo: Path, duracao_seg: float = 0) -> dict:
        if not arquivo.exists():
            raise RuntimeError("Arquivo de áudio não encontrado.")
        if self.nome == "api":
            return self._api(arquivo)
        if self.cmd:
            limite = max(600, int(duracao_seg * 4) + 120)  # CPU modesta: até ~4x a duração da fala
            p = subprocess.run(self.cmd + [str(arquivo)], capture_output=True, timeout=limite)
            if p.returncode != 0:
                raise RuntimeError((p.stderr.decode(errors="replace").strip().splitlines() or ["falhou"])[-1][:300])
            return padronizar(p.stdout.decode(errors="replace"), self.descricao())
        raise RuntimeError("Transcrição desligada.")

    def _api(self, arquivo: Path) -> dict:
        fronteira = "----parlatorio" + secrets.token_hex(8)
        campos = {"model": self.modelo or "whisper-1", "language": self.idioma, "response_format": "verbose_json"}
        corpo = b"".join(f'--{fronteira}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                         for k, v in campos.items())
        corpo += (f'--{fronteira}\r\nContent-Disposition: form-data; name="file"; filename="{arquivo.name}"\r\n'
                  f"Content-Type: application/octet-stream\r\n\r\n").encode() + arquivo.read_bytes()
        corpo += f"\r\n--{fronteira}--\r\n".encode()
        cab = {"Content-Type": f"multipart/form-data; boundary={fronteira}"}
        if self.chave:
            cab["Authorization"] = "Bearer " + self.chave
        with urllib.request.urlopen(urllib.request.Request(self.url, corpo, cab), timeout=900) as r:
            return padronizar(r.read().decode(errors="replace"), "serviço externo")


def padronizar(saida: str, motor: str) -> dict:
    """Aceita o JSON do nosso script, o verbose_json do padrão OpenAI ou texto puro."""
    saida = saida.strip()
    d = None
    for candidato in (saida, (saida.splitlines() or [""])[-1]):  # avisos de bibliotecas podem vir antes do JSON
        try:
            d = json.loads(candidato)
            break
        except json.JSONDecodeError:
            continue
    if d is None:
        d = {"texto": saida}
    if not isinstance(d, dict):
        d = {"texto": str(d)}
    segs = d.get("segmentos") or [{"ini": s.get("start", 0), "fim": s.get("end", 0), "texto": s.get("text", "")}
                                  for s in d.get("segments") or []]
    segs = [{"ini": round(float(s["ini"]), 2), "fim": round(float(s["fim"]), 2), "texto": str(s["texto"]).strip()}
            for s in segs[:LIMITE_SEGMENTOS] if str(s.get("texto", "")).strip()]
    texto = str(d.get("texto") or d.get("text") or " ".join(s["texto"] for s in segs)).strip()
    return {"texto": texto, "segmentos": segs, "idioma": d.get("idioma") or d.get("language") or "",
            "motor": d.get("motor") or motor}
