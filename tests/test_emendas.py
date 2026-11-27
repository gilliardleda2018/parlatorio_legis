"""Emendas estruturadas: texto articulado, tipos de emenda, comparativo, redação final e permissões."""
import sys
import unittest
import xml.dom.minidom

from apoio import RAIZ, TesteComServidor

sys.path.insert(0, str(RAIZ / "server"))
import emendas as E  # noqa: E402

TEXTO = """PROJETO DE LEI Nº 12/2026
Institui o programa Horta na Escola.
Art. 1º Fica instituído o programa Horta na Escola.
Parágrafo único. O programa será coordenado pela Secretaria de Educação.
Art. 2º São objetivos do programa:
I – promover a alimentação saudável;
II – incentivar a educação ambiental:
a) nas escolas municipais;
b) nas creches;
III – gerar renda.
§ 1º Os alimentos serão usados na merenda.
§ 2º O excedente poderá ser doado.
Art. 3º Esta Lei entra em vigor na data de sua publicação."""


def emenda(numero, tipo, alvo, texto="", situacao="aprovada", **extra):
    return {"id": f"e{numero}", "numero": numero, "tipo": tipo, "alvo": alvo, "texto": texto, "situacao": situacao, **extra}


class TextoArticulado(unittest.TestCase):
    def test_dispositivos_com_ids_estaveis(self):
        lista = E.com_referencias(E.dispositivos(TEXTO))
        ids = [d["id"] for d in lista]
        self.assertEqual(ids, ["preambulo", "art1", "art1_parunico", "art2", "art2_inc1", "art2_inc2", "art2_inc2_alia",
                               "art2_inc2_alib", "art2_inc3", "art2_par1", "art2_par2", "art3"])
        por_id = {d["id"]: d for d in lista}
        self.assertEqual(por_id["art2_inc2_alib"]["ref"], "art. 2º, inciso II, alínea b")
        self.assertEqual(por_id["art1_parunico"]["rotulo"], "Parágrafo único")
        self.assertEqual(por_id["art2_par2"]["texto"], "O excedente poderá ser doado.")
        self.assertEqual(por_id["art2_inc3"]["pai"], "art2")
        self.assertEqual(E.montar(E.dispositivos(TEXTO)), TEXTO)  # ida e volta sem perda

    def test_variacoes_de_escrita(self):
        lista = E.dispositivos("Art. 10. Texto dez.\n§ 1o - Primeiro.\nArt 11 - Onze.\nArt. 11-A. Onze A.\nIV. quarto")
        self.assertEqual([d["id"] for d in lista], ["art10", "art10_par1", "art11", "art11a", "art11a_inc4"])
        self.assertEqual(E.linha(lista[0]), "Art. 10. Texto dez.")
        self.assertEqual(lista[3]["suf"], "A")


class TiposDeEmenda(unittest.TestCase):
    def setUp(self):
        self.base = E.dispositivos(TEXTO)

    def ids(self, lista):
        return [d["id"] for d in lista]

    def test_supressiva_remove_com_filhos(self):
        r = E.aplicar(self.base, emenda(1, "supressiva", "art2_inc2"))
        self.assertNotIn("art2_inc2_alia", self.ids(r))
        self.assertNotIn("art2_inc2", self.ids(r))
        self.assertEqual(len(r), len(self.base) - 3)

    def test_modificativa_troca_so_o_texto(self):
        r = E.aplicar(self.base, emenda(1, "modificativa", "art3", "Art. 3º Esta Lei entra em vigor em 90 dias."))
        d = next(x for x in r if x["id"] == "art3")
        self.assertEqual(d["texto"], "Esta Lei entra em vigor em 90 dias.")
        with self.assertRaises(ValueError):
            E.aplicar(self.base, emenda(1, "modificativa", "art3", "Novo texto.\n§ 1º Outro dispositivo."))

    def test_substitutiva_troca_dispositivo_e_filhos(self):
        r = E.aplicar(self.base, emenda(1, "substitutiva", "art1", "Art. 1º Fica criado o programa.\n§ 1º Primeiro.\n§ 2º Segundo."))
        texto = E.montar(r)
        self.assertIn("Art. 1º Fica criado o programa.\n§ 1º Primeiro.\n§ 2º Segundo.", texto)
        self.assertNotIn("Secretaria de Educação", texto)
        self.assertEqual(r[1]["id"], "art1")  # o par mantém o id (outras emendas continuam achando o alvo)

    def test_aditiva_insere_apos(self):
        r = E.aplicar(self.base, emenda(1, "aditiva", "art2_inc2", "IV – combater o desperdício;"))
        i = self.ids(r).index("art2_inc2_alib")
        self.assertEqual(r[i + 1]["texto"], "combater o desperdício;")
        self.assertEqual(r[i + 1]["pai"], "art2")
        # sem rótulo: vira dispositivo do mesmo tipo do alvo
        r = E.aplicar(self.base, emenda(2, "aditiva", "art2_par2", "Os resultados serão publicados."))
        self.assertEqual((r[-2]["tipo"], r[-2]["pai"]), ("paragrafo", "art2"))
        # artigo novo depois do art. 1º (com o parágrafo dele)
        r = E.aplicar(self.base, emenda(3, "aditiva", "art1", "Art. 1º-A Haverá um comitê gestor."))
        self.assertEqual(r[3]["tipo"], "artigo")
        self.assertEqual(r[3]["texto"], "Haverá um comitê gestor.")

    def test_aglutinativa_e_substitutivo_integral(self):
        r = E.aplicar(self.base, emenda(5, "aglutinativa", "art3", "Art. 3º Vigora em 60 dias.", aglutina=[1, 2]))
        self.assertEqual(r[-1]["texto"], "Vigora em 60 dias.")
        r = E.aplicar(self.base, emenda(6, "substitutiva", "", "Art. 1º Texto novo inteiro.\nArt. 2º Vigência imediata."))
        self.assertEqual(E.montar(r), "Art. 1º Texto novo inteiro.\nArt. 2º Vigência imediata.")

    def test_alvo_inexistente(self):
        with self.assertRaises(ValueError):
            E.aplicar(self.base, emenda(1, "supressiva", "art9"))


class ComparativoEConsolidado(unittest.TestCase):
    def test_comparativo_ins_del_e_escape(self):
        h = E.diff_html("Esta Lei entra em vigor na data de sua publicação.", "Esta Lei entra em vigor em <90> dias & já.")
        self.assertIn("<del>na data de sua publicação</del>", h)
        self.assertIn("<ins>em &lt;90&gt; dias &amp; já</ins>", h)
        self.assertNotIn("<90>", h)
        self.assertEqual(E.diff_html("a <b>", "a <b>"), "a &lt;b&gt;")
        self.assertEqual(E.diff_html("x y", "x z", "antes"), "x <del>y</del>")
        self.assertEqual(E.diff_html("x y", "x z", "depois"), "x <ins>z</ins>")

    def test_consolidado_duas_aprovadas_uma_rejeitada(self):
        ems = [emenda(1, "modificativa", "art3", "Esta Lei entra em vigor em 90 dias."),
               emenda(2, "supressiva", "art2_par2"),
               emenda(3, "aditiva", "art1", "Art. 1º-A Rejeitado.", situacao="rejeitada"),
               emenda(4, "supressiva", "art1", situacao="retirada")]
        c = E.consolidar(TEXTO, ems)
        self.assertEqual(c["aplicadas"], [1, 2])
        self.assertIn("Art. 3º Esta Lei entra em vigor em 90 dias.", c["texto"])
        self.assertNotIn("excedente", c["texto"])
        self.assertNotIn("Rejeitado", c["texto"])
        self.assertIn("Parágrafo único. Os alimentos serão usados na merenda.", c["texto"])  # renumerado: sobrou um só
        comp = E.comparativo(c["original"], c["dispositivos"])
        estados = {l["estado"] for l in comp["linhas"]}
        self.assertTrue({"alterado", "suprimido", "renumerado", "igual"} <= estados)
        self.assertEqual(comp["resumo"]["suprimido"], 1)
        # uma emenda só, contra o original
        so = E.consolidar(TEXTO, ems, so="e3")
        self.assertIn("Art. 2º Rejeitado.", so["texto"])
        self.assertIn("Art. 4º Esta Lei", so["texto"])

    def test_pdf_html_e_akn(self):
        import akn
        p = {"id": "abc", "numero": "PL 12/2026", "ementa": "Horta <na> Escola", "autor": "Ana", "situacao": "aprovada", "criado": 0}
        ems = [emenda(1, "modificativa", "art3", "Vigora em <90> dias.", alvoRef="art. 3º", autor="Ana & Bia", justificativa="x" * 12)]
        c = E.consolidar(TEXTO, ems)
        pdf = E.documento_pdf(p, c, ems, "Câmara Municipal de Teste")
        self.assertTrue(pdf.startswith(b"%PDF"))
        h = E.documento_html(p, c, ems, "Câmara")
        self.assertIn("Autógrafo", h)
        self.assertIn("Vigora em &lt;90&gt; dias.", h)
        self.assertNotIn("<90>", h)
        x = akn.projeto_final(p, c["dispositivos"], ems, "Câmara")
        doc = xml.dom.minidom.parseString(x.encode())  # XML bem formado
        eids = {n.getAttribute("eId") for n in doc.getElementsByTagName("point")}
        self.assertIn("art_2__inc_2__ali_b", eids)
        self.assertEqual(doc.getElementsByTagName("legis:emenda")[0].getAttribute("autor"), "Ana & Bia")


class EmendasNoServidor(TesteComServidor):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        s, P = cls.s, cls.P
        assert s.acao(P, "projetoSalvar", numero="PL 12/2026", ementa="Horta na Escola", autor="Fulano",
                      situacao="em_tramitacao") == {"ok": True}
        cls.pid = next(p["id"] for p in s.req("/api/transparencia?interno=1", token=P)["projetos"] if p["numero"] == "PL 12/2026")
        assert s.acao(P, "projetoTexto", id=cls.pid, texto=TEXTO) == {"ok": True}
        cls.v1 = s.entrar("parlamentar", cls.ids[0])
        cls.v2 = s.entrar("parlamentar", cls.ids[1])

    def emendas(self):
        return self.s.req(f"/api/emendas/{self.pid}")["emendas"]

    def test_fluxo_permissoes_e_votacao(self):
        s, P, pid = self.s, self.P, self.pid
        just = "Melhora a redação do dispositivo."
        self.ok(s.acao(self.v1, "emendaPropor", projeto=pid, tipo="modificativa", alvo="art3",
                       texto="Esta Lei entra em vigor em 90 dias.", justificativa=just, autorPid=self.ids[5]))
        e1 = self.emendas()[-1]
        self.assertEqual(e1["autorPid"], self.ids[0])  # em nome próprio, mesmo pedindo outro autor
        self.assertTrue(e1["titulo"].startswith("Emenda nº"))
        self.assertIn("PL 12/2026", e1["titulo"])
        # validações
        self.erro(s.acao(self.v1, "emendaPropor", projeto=pid, tipo="modificativa", alvo="art99", texto="x", justificativa=just), 400, "alvo")
        self.erro(s.acao(self.v1, "emendaPropor", projeto=pid, tipo="supressiva", alvo="art1", justificativa="curta"), 400, "justificativa")
        self.erro(s.acao(self.v1, "emendaPropor", projeto=pid, tipo="aditiva", alvo="art1", texto="x" * 20001, justificativa=just), 400, "grande")
        self.erro(s.acao(self.v1, "emendaPropor", projeto=pid, tipo="xyz", alvo="art1", texto="x", justificativa=just), 400, "Tipo")
        # outro parlamentar não retira; o autor retira antes do resultado
        self.erro(s.acao(self.v2, "emendaRetirar", id=e1["id"]), 400, "autor")
        self.ok(s.acao(self.v2, "emendaPropor", projeto=pid, tipo="supressiva", alvo="art2_par2", justificativa=just))
        self.ok(s.acao(P, "emendaPropor", projeto=pid, tipo="aditiva", alvo="art1", texto="Art. 1º-A <script>x</script>",
                       justificativa=just, autorPid=self.ids[3]))
        ems = self.emendas()
        e2, e3 = ems[-2], ems[-1]
        self.assertEqual(e3["autorPid"], self.ids[3])
        self.assertEqual(e3["numero"], e1["numero"] + 2)
        # parlamentar não registra resultado
        self.erro(s.acao(self.v1, "emendaResultado", id=e2["id"], situacao="aprovada"), 403)
        self.ok(s.acao(P, "emendaResultado", id=e1["id"], situacao="aprovada"))
        self.erro(s.acao(self.v1, "emendaRetirar", id=e1["id"]), 400, "não votada")
        # e2 vai à votação como matéria da pauta
        self.ok(s.acao(P, "emendaPautar", id=e2["id"]))
        self.assertTrue(any(m.get("emenda") == e2["id"] for m in s.estado()["pauta"]))
        self.ok(s.acao(P, "votacaoAbrir", materia_id=e2["id"], tipo="simbolica", quorum="simples"))
        self.ok(s.acao(P, "votacaoEncerrar", resultado="aprovada"))
        self.ok(s.acao(P, "votacaoLimpar"))
        self.ok(s.acao(P, "emendaResultado", id=e3["id"], situacao="rejeitada"))
        ems = {e["id"]: e for e in self.emendas()}
        self.assertEqual(ems[e2["id"]]["situacao"], "aprovada")
        self.assertEqual(ems[e2["id"]]["resultado"]["votacao"], "simbolica")
        # comparativo e texto final
        d = s.req(f"/api/emendas/{pid}")
        self.assertIn("Art. 3º Esta Lei entra em vigor em 90 dias.", d["final"]["texto"])
        self.assertNotIn("excedente", d["final"]["texto"])
        self.assertNotIn("<script>", d["final"]["texto"])
        comp = s.req(f"/api/emendas/{pid}/comparativo")
        self.assertIn(["+", "em 90 dias"], [op for l in comp["linhas"] for op in l["ops"]])
        so = s.req(f"/api/emendas/{pid}/comparativo?emenda={e3['id']}")
        self.assertEqual(so["resumo"]["acrescido"], 1)
        pdf = s.req(f"/redacao-final/{pid}.pdf", bruto=True)
        self.assertTrue(pdf.startswith(b"%PDF"))
        pagina = s.req(f"/redacao-final/{pid}", bruto=True).decode()
        self.assertIn("90 dias", pagina)
        xml.dom.minidom.parseString(s.req(f"/dados/akn/projeto/{pid}/redacao-final.xml", bruto=True))
        lista = s.req("/api/emendas")["projetos"]
        self.assertEqual(next(p for p in lista if p["id"] == pid)["emendas"]["aprovada"], 2)

    def test_retirada_pelo_autor(self):
        s = self.s
        self.ok(s.acao(self.v2, "emendaPropor", projeto=self.pid, tipo="supressiva", alvo="art2_inc1",
                       justificativa="Dispositivo desnecessário."))
        e = self.emendas()[-1]
        self.ok(s.acao(self.v2, "emendaRetirar", id=e["id"]))
        self.assertEqual(self.emendas()[-1]["situacao"], "retirada")
        self.erro(s.acao(self.P, "emendaResultado", id=e["id"], situacao="aprovada"), 400, "retirada")

    def test_publico_e_projeto_inexistente(self):
        self.assertEqual(self.s.req("/api/emendas/naoexiste")["HTTP"], 404)
        self.assertEqual(self.s.req("/api/emendas/../x")["HTTP"], 404)
        self.erro(self.s.acao(self.v1, "emendaPropor", projeto="naoexiste", tipo="supressiva", alvo="art1",
                              justificativa="Justificativa longa."), 400, "não encontrado")
        d = self.s.req(f"/api/emendas/{self.pid}")
        self.assertFalse(d["pode"]["emendar"])  # sem login: só leitura
        self.assertEqual(d["dispositivos"][1]["ref"], "art. 1º")


if __name__ == "__main__":
    unittest.main()
