"""Legendas ao vivo da sessão transmitida.

Enquanto a transmissão está no ar, junta ~6 s do MP3 que vai para a rádio, manda para o processo de
legendas (scripts/legendar.py, modelo Whisper "base" carregado uma vez só) e publica o texto nas telas
(telão, faixa "Ao vivo" das páginas públicas). Precisa da transcrição local instalada.
LEGENDAS=0 desliga; LEGENDAS_MODELO troca o modelo (tiny é mais rápido, small mais preciso).
"""
import json
import os
import struct
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SEGUNDOS = 6


class Legendador:
    def __init__(self, transmissor, python: str | None, ao_legenda):
        self.transmissor = transmissor
        self.python = python
        self.ao_legenda = ao_legenda
        self.recentes: deque = deque(maxlen=30)
        self.erro = None
        self.ligado = bool(python) and os.environ.get("LEGENDAS", "1") != "0"
        self.proc = None

    def descricao(self) -> str:
        return "ligadas" if self.ligado else "desligadas (precisa da transcrição local)"

    def iniciar(self):
        if self.ligado:
            threading.Thread(target=self._laco, daemon=True).start()

    def _processo(self):
        if self.proc and self.proc.poll() is None:
            return self.proc
        self.proc = subprocess.Popen([self.python, str(RAIZ / "scripts" / "legendar.py"), "--modelo",
                                      os.environ.get("LEGENDAS_MODELO", "base")],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        pronto = self.proc.stdout.readline()  # espera o modelo carregar
        if b"pronto" not in pronto:
            self.erro = "O processo de legendas não iniciou."
            raise RuntimeError(self.erro)
        return self.proc

    def _laco(self):
        while True:
            if not self.transmissor.ativa:
                time.sleep(2)
                continue
            try:
                self._processo()  # carrega o modelo ANTES de ouvir, para não acumular atraso
            except Exception as e:
                self.erro = str(e)[:200]
                time.sleep(10)
                continue
            blocos = self.transmissor.ouvir()
            if blocos is None:
                continue
            limite = int(self.transmissor_bitrate() * 1000 / 8 * SEGUNDOS)
            buffer = bytearray()
            try:
                for bloco in blocos:
                    buffer.extend(bloco)
                    if len(buffer) >= limite:
                        self._legendar(bytes(buffer))
                        buffer.clear()
            except Exception as e:  # o legendador nunca derruba a transmissão
                self.erro = str(e)[:200]
                time.sleep(3)

    def transmissor_bitrate(self) -> int:
        return 128

    def _legendar(self, mp3: bytes):
        proc = self._processo()
        proc.stdin.write(struct.pack(">I", len(mp3)) + mp3)
        proc.stdin.flush()
        linha = proc.stdout.readline()
        texto = (json.loads(linha or b"{}").get("texto") or "").strip()
        if texto:
            item = {"ts": int(time.time() * 1000), "texto": texto}
            self.recentes.append(item)
            self.ao_legenda(item)

    def desde(self, ts: int) -> list[dict]:
        return [x for x in self.recentes if x["ts"] > ts]
