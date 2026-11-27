"""Participação do cidadão: enquete por projeto, Ideia Legislativa e perguntas ao vivo."""
import json
import unittest

from apoio import TesteComServidor


class Cidadania(TesteComServidor):
    def _projeto(self):
        r = self.s.acao(self.P, "projetoSalvar", numero="PL 77/2026", ementa="Cria o programa municipal de leitura nas praças.",
                        autor=self.s.estado()["parls"][0]["nome"], situacao="em_tramitacao")
        self.ok(r)
        return next(p for p in self.s.req("/api/enquetes")["projetos"] if p["numero"] == "PL 77/2026")["id"]

    def test_enquete(self):
        s = self.s
        pid = self._projeto()
        self.assertEqual(s.req(f"/api/enquete/{pid}")["total"], 0)
        self.assertEqual(s.req(f"/api/enquete/{pid}", {"opcao": "talvez"})["HTTP"], 400)
        r = s.req(f"/api/enquete/{pid}", {"opcao": "concordo_total"})
        self.assertEqual((r["total"], r["contagem"]["concordo_total"], r["meuVoto"]), (1, 1, "concordo_total"))
        r = s.req(f"/api/enquete/{pid}", {"opcao": "discordo_parte"})     # muda a opinião, não soma outro voto
        self.assertEqual((r["total"], r["contagem"]["concordo_total"], r["contagem"]["discordo_parte"]), (1, 0, 1))
        self.assertEqual(s.req(f"/api/enquete/{pid}", {"opcao": "concordo_total", "site": "robô"}), {"ok": True})
        self.assertEqual(s.req(f"/api/enquete/{pid}")["total"], 1)
        self.assertIn(pid, [p["id"] for p in s.req("/api/enquetes")["projetos"]])

    def test_ideia_legislativa(self):
        s, P = self.s, self.P
        self.ok(s.acao(P, "ideiaMeta", meta=5))
        self.assertEqual(s.req("/api/ideias", {"titulo": "curto", "texto": "x", "nome": "Ana"})["HTTP"], 400)
        r = s.req("/api/ideias", {"titulo": "Iluminação na praça do bairro Centro", "nome": "Ana Souza", "bairro": "Centro",
                                  "texto": "Instalar postes de LED na praça principal para mais segurança à noite.", "email": "ana@exemplo.com"})
        i = r["ideia"]
        self.assertEqual((i["apoios"], i["meta"], i["situacao"]), (1, 5, "aberta"))   # quem propõe já apoia
        self.assertNotIn("email", i)
        self.assertNotIn("ana@exemplo.com", json.dumps(s.req("/api/ideias")))
        self.assertEqual(s.req(f"/api/ideias/{i['id']}/apoiar", {})["ideia"]["apoios"], 1)   # mesmo endereço não apoia duas vezes
        self.ok(s.acao(P, "ideiaResponder", id=i["id"], situacao="virou_projeto", texto="Virou o PL 80/2026, em tramitação."))
        publica = s.req("/api/ideias")["ideias"][0]
        self.assertEqual((publica["situacao"], publica["resposta"]["texto"]), ("virou_projeto", "Virou o PL 80/2026, em tramitação."))
        self.ok(s.acao(P, "ideiaOcultar", id=i["id"], oculta=True))
        self.assertEqual(s.req("/api/ideias")["ideias"], [])
        self.assertEqual(len(s.req("/api/ideias", token=P)["ideias"]), 1)   # a Mesa ainda vê

    def test_perguntas_ao_vivo(self):
        s, P = self.s, self.P
        envio = {"nome": "Carlos Lima", "bairro": "Vila Nova", "texto": "Quando começa a reforma do posto de saúde?"}
        self.assertEqual(s.req("/api/perguntas", envio)["HTTP"], 400)            # fechado
        self.ok(s.acao(P, "perguntasAbrir", abertas=True))
        pid = s.req("/api/perguntas", envio)["pergunta"]["id"]
        self.assertEqual(s.req("/api/perguntas")["perguntas"], [])               # pendente não aparece ao público
        self.assertEqual(s.estado()["perguntasPendentes"], 1)
        self.erro(s.acao(P, "perguntaTelao", id=pid), 400, "Aprove")
        self.ok(s.acao(P, "perguntaModerar", id=pid, situacao="aprovada"))
        self.ok(s.acao(P, "perguntaTelao", id=pid))
        self.assertEqual(s.estado()["telaoPergunta"]["texto"], envio["texto"])
        self.assertEqual(len(s.req("/api/perguntas")["perguntas"]), 1)
        self.ok(s.acao(P, "perguntaModerar", id=pid, situacao="recusada"))      # recusar tira do telão
        self.assertIsNone(s.estado()["telaoPergunta"])
        self.ok(s.acao(P, "perguntasAbrir", abertas=False))


if __name__ == "__main__":
    unittest.main()
