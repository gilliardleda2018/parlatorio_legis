"""Rádio da Câmara no próprio servidor: /radio.mp3 repassado do Liquidsoap e programação enviada pelo painel."""
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from apoio import Servidor


class LiquidsoapFalso(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/radio.mp3":
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.end_headers()
        for _ in range(20):
            self.wfile.write(b"\xff\xfb" + b"\x00" * 1000)

    def log_message(self, *a):
        pass


class RadioLocal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), LiquidsoapFalso)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        cls.pasta = tempfile.TemporaryDirectory()
        cls.s = Servidor(RADIO_LOCAL="1", RADIO_PORTA=str(cls.fake.server_address[1]), RADIO_DIR=cls.pasta.name)
        cls.P = cls.s.entrar("presidente")

    @classmethod
    def tearDownClass(cls):
        cls.s.parar()
        cls.fake.shutdown()
        cls.fake.server_close()
        cls.pasta.cleanup()

    def test_radio_ja_configurada_e_repassada(self):
        s = self.s
        e = s.estado()
        self.assertTrue(e["radioLocal"])
        self.assertTrue(e["transmissao"]["configurada"])
        self.assertEqual(e["transmissao"]["publico"], "/radio.mp3")
        self.assertEqual(s.req("/api/aovivo")["radio"], "/radio.mp3")
        with urllib.request.urlopen(s.base + "/radio.mp3", timeout=10) as r:
            self.assertEqual(r.headers["Content-Type"], "audio/mpeg")
            self.assertEqual(len(r.read()), 20 * 1002)

    def test_programacao_pelo_painel(self):
        s, P = self.s, self.P
        r = s.enviar_bytes("/api/radio/arquivo?nome=Vinheta%20da%20C%C3%A2mara.mp3", b"ID3" + b"x" * 5000, P, "application/octet-stream")
        self.assertEqual(r["nome"], "Vinheta da Câmara.mp3")
        self.assertTrue((Path(self.pasta.name) / "Vinheta da Câmara.mp3").is_file())
        lista = s.req("/api/radio", token=P)
        self.assertEqual([a["nome"] for a in lista["arquivos"]], ["Vinheta da Câmara.mp3"])
        self.assertEqual(s.enviar_bytes_erro("/api/radio/arquivo?nome=virus.exe", b"MZ", P), 400)
        self.assertEqual(s.enviar_bytes_erro("/api/radio/arquivo?nome=../../fora.mp3", b"ID3", P), 200)  # vira só o nome
        self.assertFalse((Path(self.pasta.name).parent / "fora.mp3").exists())
        self.assertEqual(s.req("/api/radio")["HTTP"], 401)
        self.assertEqual(s.acao(P, "radioRemover", nome="Vinheta da Câmara.mp3"), {"ok": True})
        self.assertEqual(s.acao(P, "radioRemover", nome="../teste.db")["HTTP"], 400)
        self.assertFalse((Path(self.pasta.name) / "Vinheta da Câmara.mp3").exists())


class SemRadio(unittest.TestCase):
    def test_sem_radio_local(self):
        s = Servidor()
        try:
            self.assertEqual(s.req("/radio.mp3")["HTTP"], 404)
            self.assertIsNone(s.req("/api/aovivo")["radio"])
        finally:
            s.parar()


if __name__ == "__main__":
    unittest.main()
