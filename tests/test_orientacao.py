"""Orientação de bancada na votação nominal (como na Câmara dos Deputados)."""
import unittest

from apoio import TesteComServidor


class OrientacaoDeBancada(TesteComServidor):
    def test_lider_orienta_e_divergencias(self):
        s, P = self.s, self.P
        parls = s.estado()["parls"]
        lider, colega, outro = parls[0], parls[1], parls[2]
        # dois vereadores do mesmo partido; o primeiro é líder
        self.ok(s.acao(P, "parlSalvar", id=lider["id"], nome=lider["nome"], partido="ABC", lider=True))
        self.ok(s.acao(P, "parlSalvar", id=colega["id"], nome=colega["nome"], partido="ABC"))
        tl, tc, to = (s.entrar("parlamentar", p["id"]) for p in (lider, colega, outro))
        for t in (tl, tc, to):
            self.ok(s.acao(t, "presenca"))
        self.erro(s.acao(tl, "orientar", orientacao="sim"), 400, "nominal")   # sem votação aberta
        pauta = s.estado()["pauta"]
        self.ok(s.acao(P, "votacaoAbrir", materia_id=pauta[0]["id"], tipo="nominal", quorum="simples"))
        self.erro(s.acao(tc, "orientar", orientacao="nao"), 400, "líder")      # não é líder
        self.erro(s.acao(tl, "orientar", orientacao="talvez"), 400, "inválida")
        self.ok(s.acao(tl, "orientar", orientacao="sim"))
        self.ok(s.acao(P, "orientar", orientacao="obstrucao", bancada_nome=outro["partido"]))  # a Mesa registra
        self.erro(s.acao(P, "orientar", orientacao="sim", bancada_nome="NAOEXISTE"), 400, "Bancada")
        v = s.estado()["votacao"]
        self.assertEqual({k: x["o"] for k, x in v["orientacoes"].items()}, {"ABC": "sim", outro["partido"]: "obstrucao"})
        self.ok(s.acao(tl, "votar", valor="sim"))
        self.ok(s.acao(tc, "votar", valor="nao"))        # diverge da orientação da bancada
        self.ok(s.acao(to, "votar", valor="sim"))        # obstrução não conta como divergência Sim/Não
        self.ok(s.acao(P, "votacaoEncerrar"))
        v = s.estado()["votacao"]
        self.assertEqual(v["divergencias"], [colega["id"]])
        self.assertEqual(s.estado()["votacoes"][0]["divergencias"], [colega["id"]])
        self.ok(s.acao(P, "votacaoLimpar"))
        # votação secreta não tem orientação
        self.ok(s.acao(P, "votacaoAbrir", materia_id=pauta[1]["id"], tipo="secreta", quorum="simples"))
        self.erro(s.acao(tl, "orientar", orientacao="sim"), 400, "nominal")


if __name__ == "__main__":
    unittest.main()
