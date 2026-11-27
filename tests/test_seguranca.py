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
        inicial = self.s.pin_inicial(pid)
        self.assertNotEqual(inicial, "0000")  # cada vereador tem um PIN inicial próprio e aleatório
        self.assertEqual(self.s.req("/api/entrar", {"papel": "parlamentar", "pid": pid, "pin": "0000"})["HTTP"], 403)
        r = self.s.req("/api/entrar", {"papel": "parlamentar", "pid": pid, "pin": inicial})
        self.assertTrue(r["ator"]["deveTrocar"])
        self.erro(self.s.acao(r["token"], "presenca"), 403, "Troque o PIN")
        # com o PIN inicial, nenhuma outra rota responde (só /api/eu e a troca)
        self.assertEqual(self.s.req("/api/sessoes", token=r["token"]).get("codigo"), "trocarPin")
        self.assertTrue(self.s.req("/api/eu", token=r["token"])["ator"]["deveTrocar"])
        self.erro(self.s.req("/api/trocar-pin", {"atual": inicial, "novo": "1111"}, r["token"]), 400, "fácil")
        self.erro(self.s.req("/api/trocar-pin", {"atual": "9999", "novo": "4829"}, r["token"]), 403)
        self.ok(self.s.req("/api/trocar-pin", {"atual": inicial, "novo": "4829"}, r["token"]))
        self.ok(self.s.acao(r["token"], "presenca"))
        self.assertNotIn(pid, self.s.req("/api/pins-iniciais", token=self.P)["pins"])
        self.assertEqual(self.s.req("/api/entrar", {"papel": "parlamentar", "pid": pid, "pin": inicial})["HTTP"], 403)
        self.assertIn("token", self.s.req("/api/entrar", {"papel": "parlamentar", "pid": pid, "pin": "4829"}))

    def test_bloqueio_por_tentativas(self):
        pid = self.ids[9]
        for _ in range(5):
            self.assertEqual(self.s.req("/api/entrar", {"papel": "parlamentar", "pid": pid, "pin": "7777"})["HTTP"], 403)
        r = self.s.req("/api/entrar", {"papel": "parlamentar", "pid": pid, "pin": self.s.pin_inicial(pid)})
        self.assertEqual(r["HTTP"], 429, r)  # até o PIN certo é recusado durante o bloqueio

    def test_permissoes(self):
        t = self.s.entrar("parlamentar", self.ids[1])
        self.erro(self.s.acao(t, "proximo"), 403)
        self.erro(self.s.acao("token-falso", "proximo"), 401)
        self.assertIsNone(self.s.req("/api/eu", token="token-falso")["ator"])


class AuditoriaESegredos(TesteComServidor):
    def _banco(self, sql):
        with sqlite3.connect(self.s.dir.name + "/teste.db") as banco:
            linhas = banco.execute(sql).fetchall()
        banco.close()
        return linhas

    def test_voto_secreto_fora_da_auditoria(self):
        s, P, ids = self.s, self.P, self.ids
        pauta = s.estado()["pauta"]
        self.ok(s.acao(P, "presenca", pid=ids[3], presente=True))
        self.ok(s.acao(P, "presenca", pid=ids[4], presente=True))
        t = s.entrar("parlamentar", ids[3])
        self.ok(s.acao(P, "votacaoAbrir", materia_id=pauta[0]["id"], tipo="secreta", quorum="simples"))
        self.ok(s.acao(t, "votar", valor="nao"))
        self.ok(s.acao(P, "votarPor", pid=ids[4], valor="sim"))
        self.ok(s.acao(P, "votacaoEncerrar"))
        self.ok(s.acao(P, "votacaoLimpar"))
        for (args,) in self._banco("select args from eventos where acao in ('votar', 'votarPor')"):
            a = json.loads(args)
            self.assertNotIn("valor", a, a)
            self.assertTrue(a.get("votou"))

    def test_tokens_guardados_so_como_hash(self):
        t = self.s.entrar("parlamentar", self.ids[6])
        estado = self._banco("select json from estado")[0][0]
        self.assertNotIn(t, estado)
        self.assertNotIn(self.P, estado)
        self.ok(self.s.acao(t, "presenca"))  # e o login continua valendo

    def test_mesa_de_som_fora_das_telas_publicas(self):
        e = self.s.estado()
        self.assertNotIn("host", e["mesa"])
        self.assertNotIn("url", e["mesa"])
        self.assertNotIn("pasta", e["backup"])
        self.assertEqual(self.s.req("/api/mesa")["HTTP"], 403)
        self.assertIn("host", self.s.req("/api/mesa", token=self.P))
        self.assertEqual(self.s.req("/api/pins-iniciais")["HTTP"], 403)

    def test_cabecalhos_de_seguranca(self):
        import urllib.request
        with urllib.request.urlopen(self.s.base + "/presidente", timeout=10) as r:
            csp = r.headers.get("Content-Security-Policy", "")
            self.assertIn("frame-ancestors 'self'", csp)
            self.assertEqual(r.headers.get("X-Frame-Options"), "SAMEORIGIN")

    def test_content_length_negativo_nao_trava(self):
        import socket
        with socket.create_connection(("127.0.0.1", self.s.porta), timeout=10) as c:
            c.sendall(b"POST /api/perguntar HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                      b"Content-Length: -1\r\n\r\n{}")
            self.assertIn(b" 400 ", c.recv(200))


class ForcaBrutaNoPresidente(unittest.TestCase):
    def test_pid_aleatorio_nao_escapa_do_bloqueio(self):
        s = Servidor()
        try:
            for k in range(5):
                r = s.req("/api/entrar", {"papel": "presidente", "pin": "99999%d" % k, "pid": f"x{k}"})
                self.assertEqual(r["HTTP"], 403, r)
            r = s.req("/api/entrar", {"papel": "presidente", "pin": "1234", "pid": "outro"})
            self.assertEqual(r["HTTP"], 429, r)  # a conta do presidente é uma só, com qualquer pid
        finally:
            s.parar()

    def test_tentativas_em_paralelo_contam(self):
        from concurrent.futures import ThreadPoolExecutor
        s = Servidor()
        try:
            with ThreadPoolExecutor(20) as ex:
                codigos = list(ex.map(lambda k: s.req("/api/entrar", {"papel": "presidente", "pin": "88%04d" % k})["HTTP"],
                                      range(20)))
            self.assertLessEqual(codigos.count(403), 5, codigos)  # no máximo 5 palpites conferidos
            self.assertGreaterEqual(codigos.count(429), 15, codigos)
        finally:
            s.parar()


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
