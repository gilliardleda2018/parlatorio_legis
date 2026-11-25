import sys
import time
import unittest
from pathlib import Path

from apoio import Servidor, TesteComServidor

FALSO = f"{sys.executable} {Path(__file__).resolve().parent / 'transcritor_falso.py'}"


class Transcricao(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Servidor(TRANSCRICAO_CMD=FALSO)
        cls.P = cls.s.entrar("presidente")
        cls.ids = [p["id"] for p in cls.s.estado()["parls"]]

    @classmethod
    def tearDownClass(cls):
        cls.s.parar()

    def gravar(self):
        s, P = self.s, self.P
        g = s.req("/api/gravacao/iniciar", {"pid": self.ids[0], "inicio": 1, "mime": "audio/webm"}, P)
        s.enviar_bytes(f"/api/gravacao/{g['id']}/parte", b"\x1aE\xdf\xa3" + b"x" * 2000, P)
        s.req(f"/api/gravacao/{g['id']}/fim", {}, P)
        for _ in range(100):
            t = next(x for x in s.estado()["gravacoes"] if x["id"] == g["id"]).get("transcricao") or {}
            if t.get("status") in ("pronta", "erro"):
                return g["id"], t
            time.sleep(0.1)
        self.fail("transcrição não terminou")

    def test_transcreve_revisa_e_publica(self):
        s, P = self.s, self.P
        gid, t = self.gravar()
        self.assertEqual(t["status"], "pronta", t)
        self.assertEqual(t["palavras"], 17)
        self.assertNotIn("texto", t)  # o estado transmitido a todos não leva o texto
        self.assertFalse(t["publicada"])
        # antes de publicar: só o presidente lê; a busca pública não acha
        self.assertEqual(s.req(f"/api/transcricao/{gid}")["HTTP"], 404)
        self.assertIn("saúde", s.req(f"/api/transcricao/{gid}", token=P)["transcricao"]["texto"])
        self.assertEqual(s.req("/api/discursos?q=saude")["total"], 0)
        # revisão manual e publicação
        texto = "Senhor presidente, a saúde pública de Tuntum precisa de um posto de saúde no povoado Cocalinho."
        self.assertEqual(s.acao(P, "transcricaoSalvar", id=gid, texto=texto, publicada=True), {"ok": True})
        r = s.req("/api/discursos?q=SAUDE+povoado")
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["itens"][0]["ocorrencias"], 3)
        self.assertTrue(r["itens"][0]["revisada"])
        self.assertEqual(s.req(f"/api/discursos?pid={self.ids[1]}")["total"], 0)
        publico = s.req(f"/api/transcricao/{gid}")
        self.assertEqual(publico["transcricao"]["texto"], texto)
        self.assertNotIn("publicada", publico["transcricao"])
        # parlamentar não edita
        t = s.entrar("parlamentar", self.ids[2])
        self.assertEqual(s.acao(t, "transcricaoSalvar", id=gid, texto="x")["HTTP"], 403)
        # a ata traz a íntegra
        s.acao(P, "presenca", pid=self.ids[0], presente=True)
        pdf = s.req("/api/ata/atual.pdf", token=P, bruto=True)
        self.assertIn(b"NTEGRA DOS PRONUNCIAMENTOS", pdf)
        self.assertIn(b"Cocalinho", pdf)

    def test_despublicar_e_refazer(self):
        s, P = self.s, self.P
        gid, _ = self.gravar()
        self.assertEqual(s.acao(P, "transcricaoSalvar", id=gid, publicada=True), {"ok": True})
        self.assertEqual(s.acao(P, "transcricaoSalvar", id=gid, publicada=False), {"ok": True})
        self.assertEqual(s.req(f"/api/transcricao/{gid}")["HTTP"], 404)
        self.assertEqual(s.acao(P, "transcricaoRefazer", id=gid), {"ok": True})
        for _ in range(100):
            st = next(x for x in s.estado()["gravacoes"] if x["id"] == gid)["transcricao"]["status"]
            if st == "pronta":
                break
            time.sleep(0.1)
        self.assertEqual(st, "pronta")


class TranscricaoDesligada(TesteComServidor):
    def test_sem_motor_permite_digitar(self):
        s, P = self.s, self.P
        self.assertEqual(s.estado()["transcricaoMotor"], "desligada")
        g = s.req("/api/gravacao/iniciar", {"pid": self.ids[0], "inicio": 1, "mime": "audio/webm"}, P)
        s.enviar_bytes(f"/api/gravacao/{g['id']}/parte", b"\x1aE\xdf\xa3" + b"x" * 100, P)
        s.req(f"/api/gravacao/{g['id']}/fim", {}, P)
        self.assertIn("desligada", s.acao(P, "transcricaoRefazer", id=g["id"])["erro"])
        self.assertIn("publicar", s.acao(P, "transcricaoSalvar", id=g["id"], publicada=True)["erro"])
        self.assertEqual(s.acao(P, "transcricaoSalvar", id=g["id"], texto="Discurso digitado pela secretaria."), {"ok": True})
        grav = next(x for x in s.estado()["gravacoes"] if x["id"] == g["id"])
        self.assertEqual((grav["transcricao"]["status"], grav["transcricao"]["revisada"]), ("pronta", True))


if __name__ == "__main__":
    unittest.main()
