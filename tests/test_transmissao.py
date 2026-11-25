import base64
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from apoio import TesteComServidor


class IcecastFalso:
    """Aceita uma conexão source (PUT) como o Icecast e guarda cabeçalhos e bytes recebidos."""

    def __init__(self, senha_certa="segredo"):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.porta = self.sock.getsockname()[1]
        self.senha_certa = senha_certa
        self.cabecalhos, self.recebido = [], 0
        threading.Thread(target=self._servir, daemon=True).start()

    def _servir(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._atender, args=(c,), daemon=True).start()

    def _atender(self, c):
        with c:
            dados = b""
            while b"\r\n\r\n" not in dados:
                parte = c.recv(4096)
                if not parte:
                    return
                dados += parte
            cab, resto = dados.split(b"\r\n\r\n", 1)
            texto = cab.decode(errors="replace")
            self.cabecalhos.append(texto)
            esperado = "Basic " + base64.b64encode(f"dj:{self.senha_certa}".encode()).decode()
            if esperado not in texto:
                c.sendall(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
                return
            if "100-continue" in texto.lower():
                c.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
            c.sendall(b"HTTP/1.1 200 OK\r\n\r\n")
            self.recebido += len(resto)
            while True:
                parte = c.recv(65536)
                if not parte:
                    return
                self.recebido += len(parte)

    def fechar(self):
        self.sock.close()


@unittest.skipUnless(shutil.which("ffmpeg"), "precisa do ffmpeg")
class Transmissao(TesteComServidor):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.audio = Path(cls.tmp.name) / "teste.webm"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:a", "libopus",
                        "-f", "webm", str(cls.audio)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        super().tearDownClass()

    def esperar(self, condicao):
        for _ in range(100):
            st = self.s.estado()["transmissaoStatus"]
            if condicao(st):
                return st
            time.sleep(0.1)
        self.fail(f"transmissão não chegou ao estado esperado: {st}")

    def enviar_audio(self):
        dados = self.audio.read_bytes()
        for i in range(0, len(dados), 6000):
            r = self.s.enviar_bytes("/api/transmissao/parte", dados[i:i + 6000], self.P)
            self.assertTrue(r["ok"])
            time.sleep(0.05)

    def test_transmite_para_o_icecast(self):
        s, P = self.s, self.P
        ice = IcecastFalso()
        try:
            self.assertEqual(s.acao(P, "transmissaoConfigurar", host="radio camara")["HTTP"], 400)
            self.assertEqual(s.acao(P, "transmissaoConfigurar", host="127.0.0.1", porta=ice.porta, mount="/", usuario="dj",
                                    senha="segredo", publico="javascript:alert(1)")["HTTP"], 400)
            self.assertEqual(s.acao(P, "transmissaoConfigurar", host="127.0.0.1", porta=ice.porta, mount="/", usuario="dj",
                                    senha="segredo", publico="https://radio.exemplo.br/listen/camara/radio.mp3",
                                    nome="Câmara ao vivo"), {"ok": True})
            estado = s.estado()
            self.assertTrue(estado["transmissao"]["configurada"])
            self.assertNotIn("segredo", str(estado))
            cfg = s.req("/api/transmissao", token=P)
            self.assertNotIn("senha", cfg)
            self.assertTrue(cfg["temSenha"])
            self.assertEqual(s.req("/api/transmissao")["HTTP"], 403)
            self.assertFalse(s.req("/api/aovivo")["ativa"])
            self.assertEqual(s.acao(P, "transmissaoLigar"), {"ok": True})
            self.esperar(lambda st: st["ativa"])
            self.enviar_audio()
            self.assertTrue(s.req("/api/aovivo")["ativa"])
            for _ in range(50):
                if ice.recebido > 1000:
                    break
                time.sleep(0.1)
            self.assertGreater(ice.recebido, 1000)
            self.assertIn("audio/mpeg", ice.cabecalhos[0])
            self.assertEqual(s.acao(P, "transmissaoDesligar"), {"ok": True})
            self.assertFalse(s.estado()["transmissaoStatus"]["ativa"])
            self.assertEqual(s.enviar_bytes_erro("/api/transmissao/parte", b"x", P), 409)
        finally:
            ice.fechar()

    def test_senha_errada_vira_erro_claro(self):
        s, P = self.s, self.P
        ice = IcecastFalso(senha_certa="outra")
        try:
            self.assertEqual(s.acao(P, "transmissaoConfigurar", host="127.0.0.1", porta=ice.porta, usuario="dj", senha="segredo"),
                             {"ok": True})
            self.assertEqual(s.acao(P, "transmissaoLigar"), {"ok": True})
            st = self.esperar(lambda st: st["erro"])
            self.assertFalse(st["ativa"])
            self.assertIn("usuário ou senha", st["erro"])
            self.assertNotIn("segredo", st["erro"])
        finally:
            s.acao(P, "transmissaoDesligar")
            ice.fechar()


if __name__ == "__main__":
    unittest.main()
