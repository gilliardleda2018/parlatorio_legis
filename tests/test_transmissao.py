import base64
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from apoio import Servidor, TesteComServidor


class IcecastFalso:
    """Aceita uma conexão source (PUT) como o Icecast e guarda cabeçalhos e bytes recebidos."""

    def __init__(self, senha_certa="segredo", usuario="dj"):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.porta = self.sock.getsockname()[1]
        self.senha_certa, self.usuario = senha_certa, usuario
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
            esperado = "Basic " + base64.b64encode(f"{self.usuario}:{self.senha_certa}".encode()).decode()
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

    def test_estacao_propria_sem_instalar(self):
        """Sem ESTACAO_SENHA no ambiente, a estação própria não aparece nem pode ser escolhida."""
        s, P = self.s, self.P
        self.assertNotIn("propria", s.req("/api/transmissao", token=P)["presets"])
        self.erro(s.acao(P, "transmissaoConfigurar", preset="propria"), 400, "estação própria")


@unittest.skipUnless(shutil.which("ffmpeg"), "precisa do ffmpeg")
class EstacaoPropria(Transmissao.__base__):
    """Icecast instalado no mesmo servidor (deploy/aws/instalar.sh): endereço e senha vêm do ambiente."""
    SENHA = "senha-da-estacao-no-ambiente"

    @classmethod
    def setUpClass(cls):
        cls.ice = IcecastFalso(senha_certa=cls.SENHA, usuario="source")
        cls.servidor = Servidor(ESTACAO_SENHA=cls.SENHA, ESTACAO_PORTA=str(cls.ice.porta), ESTACAO_MOUNT="/sessao",
                                ESTACAO_PUBLICO="https://camara.exemplo.br/aovivo")
        cls.s = cls.servidor
        cls.P = cls.s.entrar("presidente")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.audio = Path(cls.tmp.name) / "teste.webm"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:a", "libopus",
                        "-f", "webm", str(cls.audio)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.ice.fechar()
        cls.tmp.cleanup()
        super().tearDownClass()

    esperar, enviar_audio = Transmissao.esperar, Transmissao.enviar_audio

    def test_ja_vem_configurada_e_sem_senha_nas_telas(self):
        s, P = self.s, self.P
        estado = s.estado()
        self.assertTrue(estado["transmissao"]["configurada"])
        self.assertEqual(estado["transmissao"]["publico"], "https://camara.exemplo.br/aovivo")
        self.assertNotIn(self.SENHA, str(estado))
        cfg = s.req("/api/transmissao", token=P)
        self.assertEqual((cfg["preset"], cfg["host"], cfg["mount"], cfg["bitrate"]), ("propria", "127.0.0.1", "/sessao", 96))
        self.assertTrue(cfg["estacaoPropria"] and cfg["temSenha"])
        self.assertEqual(next(iter(cfg["presets"])), "propria")
        self.assertNotIn(self.SENHA, str(cfg))

    def test_transmite_para_a_estacao(self):
        s, P = self.s, self.P
        antes = self.ice.recebido
        self.ok(s.acao(P, "transmissaoLigar"))
        try:
            self.esperar(lambda st: st["ativa"])
            self.enviar_audio()
            for _ in range(50):
                if self.ice.recebido > antes + 1000:
                    break
                time.sleep(0.1)
            self.assertGreater(self.ice.recebido, antes + 1000)
            self.assertTrue(self.ice.cabecalhos[-1].startswith("SOURCE /sessao "))
            self.assertTrue(s.req("/api/aovivo")["ativa"])
        finally:
            self.ok(s.acao(P, "transmissaoDesligar"))

    def test_troca_para_outra_radio_e_volta(self):
        s, P = self.s, self.P
        self.ok(s.acao(P, "transmissaoConfigurar", preset="azuracast", host="radio.exemplo.br", porta=8005, mount="/",
                       usuario="dj", senha="da-outra-radio", publico="https://radio.exemplo.br/listen/x/radio.mp3"))
        self.assertEqual(s.req("/api/transmissao", token=P)["host"], "radio.exemplo.br")
        self.ok(s.acao(P, "transmissaoConfigurar", preset="propria", bitrate=64))  # endereço para ouvir volta ao padrão
        cfg = s.req("/api/transmissao", token=P)
        self.assertEqual((cfg["preset"], cfg["host"], cfg["porta"], cfg["publico"]),
                         ("propria", "127.0.0.1", self.ice.porta, "https://camara.exemplo.br/aovivo"))
        self.assertEqual((cfg["outra"]["host"], cfg["outra"]["usuario"], cfg["temSenhaOutra"]), ("radio.exemplo.br", "dj", True))
        self.assertNotIn("da-outra-radio", str(cfg))


if __name__ == "__main__":
    unittest.main()
