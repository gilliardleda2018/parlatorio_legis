"""YouTube ao vivo: chave guardada sem aparecer nas telas e a sessão chegando em H.264 + AAC (RTMP local simulado)."""
import json
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from apoio import Servidor

TEM_FFMPEG = bool(shutil.which("ffmpeg")) and "libx264" in subprocess.run(
    ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout if shutil.which("ffmpeg") else False


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ConfigurarYoutube(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Servidor()
        cls.P = cls.s.entrar("presidente")

    @classmethod
    def tearDownClass(cls):
        cls.s.parar()

    def test_chave_validada_e_escondida(self):
        s, P = self.s, self.P
        self.assertEqual(s.acao(P, "youtubeConfigurar", chave="tem espaço")["HTTP"], 400)
        self.assertEqual(s.acao(P, "youtubeConfigurar", chave="abcd-1234", url="http://exemplo.com/x")["HTTP"], 400)
        self.assertEqual(s.acao(P, "youtubeConfigurar", chave="abcd-1234", link="http://youtube.com/x")["HTTP"], 400)
        self.assertEqual(s.acao(P, "youtubeConfigurar", chave="abcd-efgh-ijkl-mnop", link="https://www.youtube.com/@camara/live"),
                         {"ok": True})
        e = s.estado()
        self.assertEqual(e["youtube"], {"configurado": True, "ligado": True, "link": "https://www.youtube.com/@camara/live"})
        self.assertNotIn("abcd-efgh-ijkl-mnop", json.dumps(e))
        cfg = s.req("/api/transmissao", token=P)
        self.assertEqual(cfg["youtube"]["temChave"], True)
        self.assertEqual(cfg["youtube"]["url"], "rtmps://a.rtmps.youtube.com:443/live2")
        self.assertNotIn("abcd-efgh-ijkl-mnop", json.dumps(cfg))
        # salvar sem chave mantém a anterior; remover apaga
        self.assertEqual(s.acao(P, "youtubeConfigurar", chave="", ligado=False), {"ok": True})
        self.assertEqual(s.estado()["youtube"]["configurado"], True)
        self.assertEqual(s.acao(P, "youtubeRemoverChave"), {"ok": True})
        self.assertEqual(s.estado()["youtube"]["configurado"], False)
        self.assertEqual(s.acao(P, "transmissaoLigar")["HTTP"], 400)   # nem rádio nem YouTube


class ChaveDaInstalacao(unittest.TestCase):
    def test_servidor_adota_chave_e_link_do_ambiente(self):
        s = Servidor(LEGIS_YOUTUBE_CHAVE="wxyz-1234-abcd", LEGIS_YOUTUBE_LINK="https://www.youtube.com/@camara/live")
        try:
            e = s.estado()
            self.assertEqual(e["youtube"], {"configurado": True, "ligado": True, "link": "https://www.youtube.com/@camara/live"})
            self.assertNotIn("wxyz-1234-abcd", json.dumps(e))
            self.assertTrue(s.req("/api/transmissao", token=s.entrar("presidente"))["youtube"]["temChave"])
        finally:
            s.parar()


@unittest.skipUnless(TEM_FFMPEG, "precisa do ffmpeg com libx264")
class TransmitirNoYoutube(unittest.TestCase):
    def test_tela_automatica_chega_no_rtmp(self):
        pasta = tempfile.TemporaryDirectory()
        audio = Path(pasta.name) / "audio.webm"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                        "-c:a", "libopus", "-b:a", "96k", "-f", "webm", str(audio)], check=True)
        porta = porta_livre()
        recebido = Path(pasta.name) / "recebido.flv"
        rtmp = subprocess.Popen(["ffmpeg", "-v", "fatal", "-y", "-listen", "1", "-i", f"rtmp://127.0.0.1:{porta}/live2/chave-teste",
                                 "-c", "copy", str(recebido)])
        s = Servidor()
        try:
            P = s.entrar("presidente")
            vereador = s.estado()["parls"][0]
            self.assertEqual(s.acao(P, "youtubeConfigurar", chave="chave-teste", url=f"rtmp://127.0.0.1:{porta}/live2"), {"ok": True})
            self.assertEqual(s.acao(P, "chamar", pid=vereador["id"]), {"ok": True})
            self.assertEqual(s.acao(P, "iniciar"), {"ok": True})
            self.assertEqual(s.acao(P, "transmissaoLigar", camera=False), {"ok": True})   # só YouTube, sem rádio
            for _ in range(50):
                if s.estado()["youtubeStatus"]["ativa"] and s.estado()["transmissaoStatus"]["ativa"]:
                    break
                time.sleep(0.2)
            st = s.estado()["youtubeStatus"]
            self.assertTrue(st["ativa"], st)
            self.assertEqual(st["modo"], "tela")
            dados = audio.read_bytes()
            n = len(dados) // 8
            for i in range(8):
                s.enviar_bytes("/api/transmissao/parte", dados[i * n:(i + 1) * n] if i < 7 else dados[i * n:], P)
                time.sleep(1)
            self.assertEqual(s.req("/api/aovivo")["ativa"], False)   # sem link público, nada muda nas páginas públicas
            self.assertEqual(s.acao(P, "transmissaoDesligar"), {"ok": True})
            self.assertFalse(s.estado()["youtubeStatus"]["ativa"])
            rtmp.wait(20)
            info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height", "-of", "json",
                                   str(recebido)], capture_output=True, text=True).stdout
            codecs = {x["codec_name"]: x for x in json.loads(info)["streams"]}
            self.assertIn("h264", codecs)
            self.assertIn("aac", codecs)
            self.assertEqual((codecs["h264"]["width"], codecs["h264"]["height"]), (1280, 720))
        finally:
            if rtmp.poll() is None:
                rtmp.kill()
            s.parar()
            pasta.cleanup()


if __name__ == "__main__":
    unittest.main()
