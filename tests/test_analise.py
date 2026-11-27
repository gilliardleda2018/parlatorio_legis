"""Análise prévia da proposição: regras jurídicas sempre; resumo pela IA; vale só depois de revisada."""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import analise  # noqa: E402
from apoio import Servidor  # noqa: E402


class Regras(unittest.TestCase):
    def test_vicio_de_iniciativa_e_comissoes(self):
        r = analise.regras({"ementa": "Cria a Secretaria Municipal da Juventude e dá outras providências.",
                            "texto": "Art. 1º Fica criada a Secretaria Municipal da Juventude.\nArt. 2º Ficam criados 3 cargos em comissão.\n"
                                     "Art. 3º As despesas correrão por conta de dotações orçamentárias próprias.", "areas": ["direitos"]})
        titulos = [a["titulo"] for a in r["alertas"]]
        self.assertEqual(titulos[0], "Possível vício de iniciativa: estrutura da administração")
        self.assertIn("Aumento de despesa", titulos)
        self.assertEqual(r["comissoes"][:2], ["Constituição, Justiça e Redação", "Finanças, Orçamento e Tributação"])
        self.assertIn("Direitos Humanos, Cidadania e Defesa do Consumidor", r["comissoes"])

    def test_renuncia_uniao_e_datas(self):
        self.assertEqual([a["titulo"] for a in analise.regras({"ementa": "Concede isenção de IPTU a aposentados."})["alertas"]],
                         ["Renúncia de receita"])
        self.assertIn("Competência da União", [a["titulo"] for a in analise.regras({"ementa": "Altera regras do Código de Trânsito no município."})["alertas"]])
        datas = analise.regras({"ementa": "Institui o Dia Municipal do Agricultor no calendário oficial."})
        self.assertEqual([a["gravidade"] for a in datas["alertas"]], ["info"])
        self.assertIn("Desenvolvimento Econômico, Agricultura e Turismo", datas["comissoes"])
        self.assertEqual(analise.regras({"ementa": "Dispõe sobre a limpeza das praças."})["alertas"], [])


class AnaliseNoServidor(unittest.TestCase):
    def test_gera_revisa_e_publica_resumo(self):
        s = Servidor(IA="falso")
        try:
            P = s.entrar("presidente")
            self.assertEqual(s.acao(P, "projetoSalvar", numero="PL 90/2026", ementa="Concede isenção de IPTU a idosos de baixa renda.",
                                    situacao="em_tramitacao"), {"ok": True})
            pid = next(p for p in s.req("/api/enquetes")["projetos"] if p["numero"] == "PL 90/2026")["id"]
            self.assertEqual(s.req(f"/api/analise/{pid}")["HTTP"], 401)              # só o presidente
            self.assertIsNone(s.req(f"/api/analise/{pid}", token=P)["analise"])
            self.assertEqual(s.acao(P, "projetoAnaliseRevisar", id=pid)["HTTP"], 400)       # nada para revisar ainda
            self.assertEqual(s.acao(P, "projetoAnalisar", id=pid), {"ok": True})
            for _ in range(50):
                a = s.req(f"/api/analise/{pid}", token=P)["analise"]
                if not a.get("emAndamento"):
                    break
                time.sleep(0.1)
            self.assertEqual(a["alertas"][0]["titulo"], "Renúncia de receita")
            self.assertTrue(a["resumo"].startswith("Resumo automático"))                   # IA simulada
            self.assertFalse(a["revisada"])
            self.assertIsNone(next(p for p in s.req("/api/enquetes")["projetos"] if p["id"] == pid)["resumo"])   # rascunho não é público
            self.assertEqual(s.acao(P, "projetoAnaliseRevisar", id=pid, resumo="Idosos de baixa renda deixam de pagar IPTU."), {"ok": True})
            publico = next(p for p in s.req("/api/enquetes")["projetos"] if p["id"] == pid)
            self.assertEqual(publico["resumo"], "Idosos de baixa renda deixam de pagar IPTU.")
        finally:
            s.parar()


if __name__ == "__main__":
    unittest.main()
