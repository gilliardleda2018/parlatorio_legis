"""Ata por IA, consulta pública, perguntas/assistente, vídeo com marcação, anexos, dados abertos e legendas."""
import base64
import sys
import time
import unittest
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from apoio import Servidor

FALSO = f"{sys.executable} {Path(__file__).resolve().parent / 'transcritor_falso.py'}"
TEXTO = """Dispõe sobre hortas comunitárias nas escolas municipais.
Art. 1º Fica instituído o programa de hortas comunitárias nas escolas.
Art. 2º As hortas serão cuidadas por alunos e pela comunidade.
Parágrafo único. A Secretaria de Educação fornecerá sementes.
Art. 3º Esta lei entra em vigor na data de sua publicação."""


class Pacote2026(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Servidor(IA="falso", TRANSCRICAO_CMD=FALSO)
        cls.P = cls.s.entrar("presidente")
        e = cls.s.estado()
        cls.ids = [p["id"] for p in e["parls"]]
        cls.parls = e["parls"]
        cls.pauta = e["pauta"]

    @classmethod
    def tearDownClass(cls):
        cls.s.parar()

    def ok(self, r):
        self.assertEqual(r, {"ok": True}, r)

    def esperar(self, cond, n=100):
        for _ in range(n):
            v = cond()
            if v:
                return v
            time.sleep(0.1)
        self.fail("condição não atingida")

    def _fala_gravada(self, pid, materia_id=None):
        s, P = self.s, self.P
        if materia_id:
            self.ok(s.acao(P, "materiaAtual", id=materia_id))
        self.ok(s.acao(P, "inscrever", pid=pid))
        self.ok(s.acao(P, "proximo"))
        self.ok(s.acao(P, "iniciar"))
        o = s.estado()["orador"]
        g = s.req("/api/gravacao/iniciar", {"pid": pid, "inicio": o["inicio"], "mime": "audio/webm"}, P)
        s.enviar_bytes(f"/api/gravacao/{g['id']}/parte", b"\x1aE\xdf\xa3" + b"x" * 1000, P)
        s.req(f"/api/gravacao/{g['id']}/fim", {}, P)
        self.ok(s.acao(P, "encerrar"))
        self.esperar(lambda: (next(x for x in s.estado()["gravacoes"] if x["id"] == g["id"]).get("transcricao") or {}).get("status") == "pronta")
        return g["id"]

    def test_1_ata_por_ia_e_video(self):
        s, P = self.s, self.P
        mid = self.pauta[0]["id"]
        gid = self._fala_gravada(self.ids[0], mid)
        self.ok(s.acao(P, "transcricaoSalvar", id=gid, publicada=True))
        self.ok(s.acao(P, "ataResumoGerar", sessao="atual"))
        self.esperar(lambda: (s.estado().get("resumoJob") or {}).get("fim"))
        r = s.req("/api/resumo/atual", token=P)
        self.assertEqual(r["HTTP"] if "HTTP" in r else 200, 200)
        itens = r["resumo"]["itens"]
        self.assertEqual(itens[0]["chave"], mid)
        self.assertTrue(itens[0]["texto"].startswith("Resumo automático"))
        self.assertNotIn("_resumoAtual", s.estado())  # rascunho não vai para as telas públicas
        self.assertEqual(s.req("/api/resumo/atual")["HTTP"], 401)
        self.ok(s.acao(P, "ataResumoEditar", sessao="atual", chave=mid, texto="Texto revisado pela secretaria.", revisado=True))
        pdf = s.req("/api/ata/atual.pdf", token=P, bruto=True)
        self.assertIn(b"RESUMO DOS TRABALHOS", pdf)
        self.assertIn(b"Texto revisado pela secretaria", pdf)
        inicio = s.estado()["hist"][0]["inicio"] - 60000
        self.erro = None
        self.assertEqual(s.acao(P, "sessaoVideo", url="javascript:alert(1)", inicio=inicio)["HTTP"], 400)
        self.ok(s.acao(P, "sessaoVideo", url="https://www.youtube.com/watch?v=abcdefghijk", inicio=inicio))
        pub = s.req("/api/sessao-publica/atual")
        self.assertEqual(pub["video"]["url"], "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(pub["capitulos"][0]["tempo"], 60)
        self.assertTrue(pub["falas"][0]["audio"].startswith("/gravacao/"))
        # ao encerrar, resumo e vídeo vão para o arquivo da sessão
        self.ok(s.acao(P, "presenca", pid=self.ids[0], presente=True))
        self.ok(s.acao(P, "sessaoEncerrar", proximoTitulo="Próxima", manterPauta=True))
        sid = s.req("/api/sessoes", token=P)["sessoes"][0]["id"]
        arq = s.req(f"/api/sessoes/{sid}", token=P)
        self.assertTrue(arq["resumoIA"]["itens"][0]["revisado"])
        self.assertTrue(arq["video"])
        self.assertIsNone(s.req("/api/resumo/atual", token=P)["resumo"])
        html = s.req(f"/sessao/{sid}", bruto=True)
        self.assertIn(b"Sess", html)
        # dados abertos e Akoma Ntoso do debate
        sessoes = s.req("/api/dados/sessoes.json")["sessoes"]
        self.assertEqual(sessoes[-1]["id"], sid)
        xml = s.req(f"/dados/akn/sessao/{sid}.xml", bruto=True)
        raiz = ET.fromstring(xml)
        self.assertTrue(raiz.tag.endswith("akomaNtoso"))
        self.assertIn(b"<speech", xml)
        csv = s.req("/api/dados/votacoes.csv", bruto=True).decode("utf-8-sig")
        self.assertTrue(csv.startswith("sessao;data;materia"))

    def test_2_consulta_publica(self):
        s, P = self.s, self.P
        proj = s.req("/api/transparencia?interno=1", token=P)["projetos"]
        p = next(x for x in proj if x["situacao"] != "proposta")
        self.assertIn("Cadastre o texto", s.acao(P, "consultaAbrir", id=p["id"], aberta=True)["erro"])
        self.ok(s.acao(P, "projetoTexto", id=p["id"], texto=TEXTO))
        self.ok(s.acao(P, "consultaAbrir", id=p["id"], aberta=True, prazo=int(time.time() * 1000) + 86400000))
        d = s.req(f"/api/consulta/{p['id']}")
        self.assertEqual([a["chave"] for a in d["artigos"]], ["inicio", "art1", "art2", "art3"])
        self.assertIn("Parágrafo único", d["artigos"][2]["texto"])
        com = s.req(f"/api/consulta/{p['id']}/comentar", {"artigo": "art2", "nome": "Maria do Povoado", "bairro": "Centro",
                                                           "texto": "Sugiro incluir os pais dos alunos no cuidado das hortas.",
                                                           "email": "maria@exemplo.com"})
        self.assertTrue(com["ok"])
        cid = com["comentario"]["id"]
        self.assertNotIn("email", com["comentario"])
        self.assertEqual(s.req(f"/api/consulta/{p['id']}/comentar", {"artigo": "art99", "nome": "Zé", "texto": "x" * 20})["HTTP"], 400)
        self.assertTrue(s.req(f"/api/consulta/{p['id']}/comentar", {"site": "spam", "nome": "Robô"})["ok"])  # isca: ignorado
        self.assertEqual(s.req("/api/consulta/apoiar", {"id": cid})["apoios"], 1)
        self.assertEqual(s.req("/api/consulta/apoiar", {"id": cid})["apoios"], 1)  # mesmo endereço não apoia duas vezes
        # parlamentar que não é autor não responde; presidência responde
        outro = next(x for x in self.parls if x["nome"] != p.get("autor"))
        t = s.entrar("parlamentar", outro["id"])
        self.assertEqual(s.acao(t, "consultaResponder", comentario=cid, texto="ok", situacao="acatada")["HTTP"], 400)
        self.ok(s.acao(P, "consultaResponder", comentario=cid, texto="Sugestão incorporada na emenda 1.", situacao="acatada"))
        d = s.req(f"/api/consulta/{p['id']}")
        c = next(x for x in d["comentarios"] if x["id"] == cid)
        self.assertEqual(c["resposta"]["situacao"], "acatada")
        self.ok(s.acao(P, "consultaOcultar", comentario=cid, oculto=True))
        self.assertFalse(any(x["id"] == cid for x in s.req(f"/api/consulta/{p['id']}")["comentarios"]))
        self.assertTrue(any(x["id"] == cid for x in s.req(f"/api/consulta/{p['id']}", token=P)["comentarios"]))
        xml = s.req(f"/dados/akn/projeto/{p['id']}.xml", bruto=True)
        self.assertEqual(len(ET.fromstring(xml).findall(".//{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}article")), 3)
        lista = s.req("/api/consultas")["consultas"]
        self.assertTrue(any(x["id"] == p["id"] and x["aberta"] for x in lista))
        # limite de comentários por endereço
        for k in range(10):
            r = s.req(f"/api/consulta/{p['id']}/comentar", {"artigo": "geral", "nome": "Pessoa Teste", "texto": f"Comentário número {k} sobre o projeto."})
        self.assertEqual(r["HTTP"], 429)

    def test_3_anexo_e_pauta(self):
        s, P = self.s, self.P
        mid = s.estado()["pauta"][0]["id"]
        pdf = b"%PDF-1.4\n%teste\n" + b"0" * 500
        self.assertEqual(s.acao(P, "materiaAnexo", id=mid, nome="x.pdf", dataUrl="data:image/png;base64,AAAA")["HTTP"], 400)
        self.ok(s.acao(P, "materiaAnexo", id=mid, nome="parecer.pdf", dataUrl="data:application/pdf;base64," + base64.b64encode(pdf).decode()))
        m = next(x for x in s.estado()["pauta"] if x["id"] == mid)
        self.assertEqual(m["anexo"]["nome"], "parecer.pdf")
        with urllib.request.urlopen(f"{s.base}/anexo/{mid}.pdf") as r:
            self.assertEqual(r.read(), pdf)

    def test_4_perguntas_e_assistente(self):
        s = self.s
        r = s.req("/api/perguntar", {"pergunta": "o que foi dito sobre saúde no povoado?", "escopo": "discursos"})
        self.assertTrue(r["fontes"], r)
        self.assertEqual(r["fontes"][0]["tipo"], "discurso")
        self.assertEqual(r["modo"], "ia")
        self.assertTrue(r["resposta"])
        r = s.req("/api/perguntar", {"pergunta": "quais projetos tratam de hortas nas escolas?", "escopo": "tudo"})
        self.assertTrue(any(f["tipo"] == "projeto" for f in r["fontes"]), r)
        self.assertEqual(s.req("/api/perguntar", {"pergunta": "x"})["HTTP"], 400)

    def test_5_legendas_e_estado(self):
        s = self.s
        r = s.req("/api/legendas?desde=0")
        self.assertEqual(r, {"ativa": False, "ligadas": False, "itens": []})
        e = s.estado()
        self.assertEqual(e["iaMotor"], "simulada (testes)")
        for pagina in ("/participe", "/dados", "/assistente.js", "/presidente2.js"):
            self.assertGreater(len(s.req(pagina, bruto=True)), 500, pagina)


if __name__ == "__main__":
    unittest.main()
