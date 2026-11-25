"""Apoio aos testes: sobe um servidor isolado (porta livre, banco temporário) e fala com a API."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Servidor:
    def __init__(self, **env_extra):
        self.dir = tempfile.TemporaryDirectory(prefix="parlatorio-teste-")
        self.porta = porta_livre()
        self.base = f"http://127.0.0.1:{self.porta}"
        env = {**os.environ, "PORTA": str(self.porta), "PARLATORIO_BANCO": f"{self.dir.name}/teste.db", "HTTPS": "0",
               "BACKUP_HORA": "", "BACKUP_DIR": f"{self.dir.name}/backups", "PIN_PRESIDENTE": "1234", **env_extra}
        for k in ("NEXTCLOUD_URL", "NEXTCLOUD_USUARIO", "NEXTCLOUD_SENHA"):
            env.pop(k, None)
        self.proc = subprocess.Popen([sys.executable, str(RAIZ / "server" / "servidor.py")], env=env, cwd=RAIZ,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                urllib.request.urlopen(self.base + "/", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            self.parar()
            raise RuntimeError("servidor não subiu: " + self.proc.stderr.read().decode()[-2000:])
        self.pins = {}

    def parar(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc.stderr.close()
        self.dir.cleanup()

    def enviar_bytes(self, caminho, dados: bytes, token, tipo="audio/webm"):
        r = urllib.request.Request(self.base + caminho, dados, {"Content-Type": tipo, "Authorization": "Bearer " + token})
        with urllib.request.urlopen(r, timeout=30) as x:
            return json.loads(x.read())

    # ---- HTTP
    def req(self, caminho, corpo=None, token=None, bruto=False, metodo=None):
        dados = json.dumps(corpo).encode() if corpo is not None else None
        r = urllib.request.Request(self.base + caminho, dados, {"Content-Type": "application/json",
                                                               **({"Authorization": "Bearer " + token} if token else {})},
                                   method=metodo)
        try:
            with urllib.request.urlopen(r, timeout=30) as x:
                conteudo = x.read()
                return conteudo if bruto else json.loads(conteudo)
        except urllib.error.HTTPError as e:
            return {"HTTP": e.code, **json.loads(e.read() or b"{}")}

    def estado(self):
        with urllib.request.urlopen(self.base + "/eventos", timeout=10) as r:
            for linha in r:
                linha = linha.decode().strip()
                if linha.startswith("data:"):
                    m = json.loads(linha[5:])
                    if m["tipo"] == "estado":
                        return m["estado"]

    def entrar(self, papel, pid=None):
        """Entra; se o PIN for o padrão, troca por um PIN forte (como o sistema exige)."""
        chave = (papel, pid)
        pin = self.pins.get(chave, "1234" if papel == "presidente" else "0000")
        r = self.req("/api/entrar", {"papel": papel, "pid": pid, "pin": pin})
        if "token" not in r:
            raise AssertionError(f"login falhou: {r}")
        if r["ator"]["deveTrocar"]:
            novo = "735915" if papel == "presidente" else "8%03d" % (len(self.pins) * 37 % 1000)
            t = self.req("/api/trocar-pin", {"atual": pin, "novo": novo}, r["token"])
            assert t.get("ok"), t
            self.pins[chave] = novo
        return r["token"]

    def acao(self, token, nome, **args):
        return self.req("/api/acao", {"nome": nome, "args": args}, token)


class TesteComServidor(unittest.TestCase):
    servidor: Servidor

    @classmethod
    def setUpClass(cls):
        cls.servidor = Servidor()
        cls.s = cls.servidor
        cls.P = cls.s.entrar("presidente")
        cls.ids = [p["id"] for p in cls.s.estado()["parls"]]

    @classmethod
    def tearDownClass(cls):
        cls.servidor.parar()

    def ok(self, r):
        self.assertEqual(r, {"ok": True}, r)

    def erro(self, r, codigo=None, contem=None):
        self.assertIn("HTTP", r, r)
        if codigo:
            self.assertEqual(r["HTTP"], codigo, r)
        if contem:
            self.assertIn(contem, r.get("erro", ""), r)
