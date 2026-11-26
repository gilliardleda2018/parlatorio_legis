import base64
import hashlib
import hmac
import json
import time
import unittest

from apoio import Servidor

SEGREDO = "segredo-de-teste-123"


def decodificar(token: str) -> tuple[dict, dict, bool]:
    cab, corpo, assinatura = token.split(".")
    d = lambda s: json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
    certa = base64.urlsafe_b64encode(hmac.new(SEGREDO.encode(), f"{cab}.{corpo}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    return d(cab), d(corpo), hmac.compare_digest(certa, assinatura)


class JitsiProprio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Servidor(JITSI_URL="https://parlatorio.local:8443", JITSI_SEGREDO=SEGREDO)
        cls.P = cls.s.entrar("presidente")
        cls.ids = [p["id"] for p in cls.s.estado()["parls"]]

    @classmethod
    def tearDownClass(cls):
        cls.s.parar()

    def test_token_so_para_logados_e_moderador_so_o_presidente(self):
        s, P = self.s, self.P
        self.assertEqual(s.estado()["remoto"]["servidor"], "https://parlatorio.local:8443")
        self.assertEqual(s.acao(P, "remotoConfigurar", ativo=True), {"ok": True})
        r = s.req("/api/remoto", token=P)
        cab, corpo, ok = decodificar(r["jwt"])
        self.assertTrue(ok)
        self.assertEqual(cab["alg"], "HS256")
        self.assertEqual((corpo["iss"], corpo["aud"], corpo["room"]), ("parlatorio", "jitsi", r["sala"]))
        self.assertTrue(corpo["context"]["user"]["moderator"])
        self.assertGreater(corpo["exp"], time.time() + 3600)
        t = s.entrar("parlamentar", self.ids[2])
        _, corpo_v, ok_v = decodificar(s.req("/api/remoto", token=t)["jwt"])
        self.assertTrue(ok_v)
        self.assertFalse(corpo_v["context"]["user"]["moderator"])
        self.assertEqual(s.req("/api/remoto")["HTTP"], 403)
        self.assertNotIn(SEGREDO, json.dumps(s.estado()))

    def test_outro_servidor_nao_recebe_token(self):
        s, P = self.s, self.P
        s.acao(P, "remotoConfigurar", ativo=True, servidor="https://meet.jit.si")
        self.assertNotIn("jwt", s.req("/api/remoto", token=P))
        s.acao(P, "remotoConfigurar", ativo=True, servidor="https://parlatorio.local:8443")


if __name__ == "__main__":
    unittest.main()
