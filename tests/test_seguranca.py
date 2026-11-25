import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import seguranca  # noqa: E402
from apoio import Servidor, TesteComServidor  # noqa: E402


class RegrasDePin(unittest.TestCase):
    def test_hash_e_verificacao(self):
        h = seguranca.hash_pin("482913")
        self.assertTrue(h.startswith("pbkdf2$"))
        self.assertNotIn("482913", h)
        self.assertTrue(seguranca.verificar_pin("482913", h))
        self.assertFalse(seguranca.verificar_pin("482914", h))

    def test_pins_fracos(self):
        for p in ("0000", "1234", "4321", "1111", "123456", "987654"):
            self.assertTrue(seguranca.pin_fraco(p), p)
        self.assertFalse(seguranca.pin_fraco("4829"))
        self.assertIsNotNone(seguranca.validar_novo_pin("12a4"))
        self.assertIsNotNone(seguranca.validar_novo_pin("4829", minimo=6))
        self.assertIsNone(seguranca.validar_novo_pin("482913", minimo=6))


class AcessoPeloServidor(TesteComServidor):
    def test_pins_guardados_com_hash(self):
        with sqlite3.connect(self.s.dir.name + "/teste.db") as banco:
            estado = json.loads(banco.execute("select json from estado").fetchone()[0])
        banco.close()
        self.assertTrue(all(v.startswith("pbkdf2$") for v in estado["_pins"].values()))
        self.assertNotIn("_pins", self.s.estado())
        self.assertNotIn("_tokens", self.s.estado())

    def test_pin_padrao_obriga_troca(self):
        pid = self.ids[5]
        r = self.s.req("/api/entrar", {"papel": "parlamentar", "pid": pid, "pin": "0000"})
        self.assertTrue(r["ator"]["deveTrocar"])
        self.erro(self.s.acao(r["token"], "presenca"), 403, "Troque o PIN")
        self.erro(self.s.req("/api/trocar-pin", {"atual": "0000", "novo": "1111"}, r["token"]), 400, "fácil")
        self.erro(self.s.req("/api/trocar-pin", {"atual": "9999", "novo": "4829"}, r["token"]), 403)
        self.ok(self.s.req("/api/trocar-pin", {"atual": "0000", "novo": "4829"}, r["token"]))
        self.ok(self.s.acao(r["token"], "presenca"))
        self.assertEqual(self.s.req("/api/entrar", {"papel": "parlamentar", "pid": pid, "pin": "0000"})["HTTP"], 403)
        self.assertIn("token", self.s.req("/api/entrar", {"papel": "parlamentar", "pid": pid, "pin": "4829"}))

    def test_bloqueio_por_tentativas(self):
        pid = self.ids[9]
        for _ in range(5):
            self.assertEqual(self.s.req("/api/entrar", {"papel": "parlamentar", "pid": pid, "pin": "7777"})["HTTP"], 403)
        r = self.s.req("/api/entrar", {"papel": "parlamentar", "pid": pid, "pin": "0000"})
        self.assertEqual(r["HTTP"], 429, r)  # até o PIN certo é recusado durante o bloqueio

    def test_permissoes(self):
        t = self.s.entrar("parlamentar", self.ids[1])
        self.erro(self.s.acao(t, "proximo"), 403)
        self.erro(self.s.acao("token-falso", "proximo"), 401)
        self.assertIsNone(self.s.req("/api/eu", token="token-falso")["ator"])


class PinDoPresidentePorAmbiente(unittest.TestCase):
    def test_pin_forte_no_ambiente_nao_obriga_troca(self):
        s = Servidor(PIN_PRESIDENTE="904172")
        try:
            r = s.req("/api/entrar", {"papel": "presidente", "pin": "904172"})
            self.assertFalse(r["ator"]["deveTrocar"])
        finally:
            s.parar()


if __name__ == "__main__":
    unittest.main()
