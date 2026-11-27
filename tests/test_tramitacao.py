"""Protocolo eletrônico, tramitação com comissões e pedidos de informação ao Executivo."""
import base64
import hashlib
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import tramitacao  # noqa: E402
from apoio import TesteComServidor  # noqa: E402

DIA = tramitacao.DIA


class Regras(unittest.TestCase):
    """Regras puras (sem servidor): prazos, placar e alertas com relógio controlado."""

    def setUp(self):
        self.S = {}
        tramitacao.completar(self.S)
        self.cfg = self.S["tramitacao"]

    def materia(self, sigla="PI"):
        p = {"id": "abc123", "numero": "PROP 1/2026", "ementa": "Pede informações sobre a merenda escolar.",
             "autor": "Ana", "situacao": "proposta", "historico": [], "texto": "Requer à Prefeitura..."}
        p.update(tramitacao.dados_proposta(self.cfg, sigla, p["ementa"], p["texto"], ""))
        return p

    def test_numeracao_por_tipo_e_ano_nao_repete(self):
        numeros = ["PL 7/2026", "PL 3/2025", "REQ 9/2026"]
        self.assertEqual(tramitacao.proximo_numero(self.cfg, "PL", 2026, numeros), "PL 8/2026")
        self.assertEqual(tramitacao.proximo_numero(self.cfg, "PL", 2026, []), "PL 9/2026")  # contador guardado
        self.assertEqual(tramitacao.proximo_numero(self.cfg, "PL", 2025, numeros), "PL 4/2025")
        self.assertEqual(tramitacao.proximo_numero(self.cfg, "IND", 2026, numeros), "IND 1/2026")

    def test_executivo_placar_e_alertas(self):
        t0 = 1_780_000_000_000
        a, b, c = self.materia(), self.materia(), self.materia()
        for k, p in enumerate((a, b, c)):
            p["id"] = f"m{k}"
            tramitacao.protocolar(p, self.cfg, None, t0, "Presidência", [])
            tramitacao.enviar_executivo(p, self.cfg, 30, "Ofício 1", t0, "Presidência")
        self.assertEqual({a["numero"], b["numero"], c["numero"]}, {"PI 1/2026", "PI 2/2026", "PI 3/2026"})
        tramitacao.responder_executivo(a, "Respondeu com a lista de contratos.", t0 + 10 * DIA, t0 + 10 * DIA, "Presidência", None)
        tramitacao.responder_executivo(b, "Respondeu tarde.", t0 + 40 * DIA, t0 + 40 * DIA, "Presidência", None)
        agora = t0 + 27 * DIA
        self.assertEqual(tramitacao.situacao_executivo(c, agora), "aguardando")
        al = tramitacao.alertas([a, b, c], self.cfg, agora, {})
        self.assertEqual([(x["materia"], x["atrasado"], x["dias"]) for x in al], [("m2", False, 3)])
        depois = t0 + 31 * DIA
        self.assertEqual(tramitacao.situacao_executivo(c, depois), "em_atraso")
        self.assertTrue(tramitacao.alertas([c], self.cfg, depois, {})[0]["atrasado"])
        n = tramitacao.placar([a, b, c], depois)
        self.assertEqual((n["enviados"], n["noPrazo"], n["foraDoPrazo"], n["emAtraso"], n["pctNoPrazo"]), (3, 1, 1, 1, 33))
        with self.assertRaises(tramitacao.ErroTramitacao):
            tramitacao.responder_executivo(a, "de novo", t0 + 11 * DIA, t0 + 11 * DIA, "Presidência", None)

    def test_maquina_de_estados(self):
        p = self.materia("PL")
        with self.assertRaises(tramitacao.ErroTramitacao):
            tramitacao.avancar(p, "lida", 1, "x")  # sem protocolo
        tramitacao.protocolar(p, self.cfg, None, 1, "x", [])
        with self.assertRaises(tramitacao.ErroTramitacao):
            tramitacao.avancar(p, "sancao", 2, "x")  # não pula etapas
        with self.assertRaises(tramitacao.ErroTramitacao):
            tramitacao.enviar_executivo(p, self.cfg, None, "", 2, "x")  # PL não vai ao Executivo
        tramitacao.avancar(p, "lida", 2, "x")
        p["situacao"] = "aprovada"   # votada no plenário (presencial ou virtual)
        self.assertEqual(tramitacao.etapa(p), "votada")
        tramitacao.avancar(p, "sancao", 3, "x")
        self.assertEqual([h["etapa"] for h in p["historico"]], ["protocolada", "lida", "sancao"])

    def test_config_valida(self):
        with self.assertRaises(tramitacao.ErroTramitacao):
            tramitacao.validar_config([{"sigla": "PROP", "nome": "Reservada"}], {})
        with self.assertRaises(tramitacao.ErroTramitacao):
            tramitacao.validar_config([{"sigla": "PL", "nome": "Projeto"}], {"executivo": 0})
        tipos, prazos = tramitacao.validar_config([{"sigla": "pl", "nome": "Projeto de Lei"}], {"executivo": 45})
        self.assertEqual((tipos[0]["sigla"], prazos["executivo"], prazos["parecer"]), ("PL", 45, 15))


class Tramitacao(TesteComServidor):
    def painel(self):
        return self.s.req("/api/tramitacao/painel", token=self.P)

    def test_protocolo_eletronico_do_vereador(self):
        s, P, ids = self.s, self.P, self.ids
        V = s.entrar("parlamentar", ids[0])
        nome = s.estado()["parls"][0]["nome"]
        texto = "Art. 1º Fica instituída a Semana Municipal da Leitura.\nArt. 2º Esta lei entra em vigor na data da publicação."
        self.ok(s.acao(V, "protocoloEnviar", tipo="PL", ementa="Institui a Semana Municipal da Leitura nas escolas.",
                       texto=texto, justificativa="Incentivo à leitura."))
        # só em nome próprio: não há como informar outro autor
        self.erro(s.acao(V, "protocoloEnviar", tipo="PL", ementa="Outra ementa qualquer bem longa.", autor="Fulano"), 400)
        self.erro(s.acao(V, "protocoloEnviar", tipo="XYZ", ementa="Tipo que não existe na casa."), 400, "Tipo")
        self.erro(s.acao(V, "protocoloEnviar", tipo="PL", ementa="Texto grande demais.", texto="x" * 100_001), 400, "grande")
        self.erro(s.acao(V, "tramProtocolar", id="qualquer"), 403)
        prop = next(p for p in self.painel()["propostas"] if p["autor"] == nome and p["tipo"] == "PL")
        self.assertEqual(prop["etapa"], "recebida")
        self.assertEqual(s.req(f"/api/tramitacao/materia/{prop['id']}")["HTTP"], 404)  # proposta não é pública
        # devolvida, corrigida e reenviada
        self.ok(s.acao(P, "tramDevolver", id=prop["id"], motivo="Falta a cláusula de vigência."))
        minhas = s.req("/api/tramitacao/minhas", token=V)["minhas"]
        self.assertEqual(next(m for m in minhas if m["id"] == prop["id"])["motivo"], "Falta a cláusula de vigência.")
        self.ok(s.acao(V, "protocoloEnviar", id=prop["id"], tipo="PL", ementa="Institui a Semana Municipal da Leitura nas escolas.",
                       texto=texto, justificativa="Incentivo à leitura."))
        self.ok(s.acao(P, "tramProtocolar", id=prop["id"]))
        m = s.req(f"/api/tramitacao/materia/{prop['id']}")["materia"]
        self.assertRegex(m["numero"], r"^PL \d+/\d{4}$")
        self.assertEqual(m["protocolo"]["hash"], hashlib.sha256(texto.strip().encode()).hexdigest())
        self.assertEqual(m["etapa"], "protocolada")
        n1 = int(re.match(r"PL (\d+)/", m["numero"]).group(1))
        self.assertGreater(n1, 118)  # o banco de exemplo já tem o PL 118: o número não repete
        pdf = s.req(f"/api/tramitacao/comprovante/{prop['id']}.pdf", bruto=True)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertIn(m["protocolo"]["hash"].encode(), pdf)
        self.assertIn(m["numero"].encode(), pdf)
        # protocolo direto da secretaria: número seguinte do mesmo tipo e ano
        self.ok(s.acao(P, "tramNova", tipo="PL", ementa="Dispõe sobre a poda de árvores em vias públicas.", autor="Executivo Municipal"))
        segundo = next(x for x in self.painel()["materias"] if x["autor"] == "Executivo Municipal")
        self.assertEqual(segundo["numero"], f"PL {n1 + 1}/{m['numero'][-4:]}")
        # vai para a pauta como qualquer projeto do banco
        self.ok(s.acao(P, "pautaIncluir", id=prop["id"]))
        self.assertIn(m["numero"], [x["numero"] for x in s.estado()["pauta"]])

    def test_comissao_relator_vista_reuniao(self):
        s, P, ids = self.s, self.P, self.ids
        relator, outro = s.entrar("parlamentar", ids[3]), s.entrar("parlamentar", ids[5])
        self.ok(s.acao(P, "tramComissaoSalvar", nome="Comissão de Constituição e Justiça", membros=ids[3:6], presidente=ids[4]))
        self.erro(s.acao(P, "tramComissaoSalvar", nome="Outra", membros=["naoexiste"]), 400)
        self.erro(s.acao(P, "tramComissaoSalvar", nome="Comissão X", membros=ids[3:5], presidente=ids[9]), 400, "membro")
        cid = s.estado()["tramitacao"]["comissoes"][0]["id"]
        self.ok(s.acao(P, "tramNova", tipo="PLC", ementa="Altera o Código Tributário Municipal para isentar hortas.",
                       texto="Art. 1º ...", autorPid=ids[3]))
        mid = next(x for x in self.painel()["materias"] if x["tipo"] == "PLC")["id"]
        self.ok(s.acao(P, "tramAvancar", id=mid, etapa="lida"))
        self.ok(s.acao(P, "tramDistribuir", id=mid, comissoes=[cid]))
        self.erro(s.acao(P, "tramRelator", id=mid, comissao=cid, relator=ids[0]), 400, "membro")
        self.ok(s.acao(P, "tramRelator", id=mid, comissao=cid, relator=ids[3]))
        self.assertEqual([r["materia"] for r in s.req("/api/tramitacao/minhas", token=relator)["relatorias"]], [mid])
        # só o relator envia o parecer
        self.erro(s.acao(outro, "tramParecer", id=mid, comissao=cid, voto="favoravel", texto="Parecer de quem não é relator."), 400, "relator")
        self.erro(s.acao(relator, "tramParecer", id=mid, comissao=cid, voto="talvez", texto="Voto que não existe aqui."), 400)
        self.ok(s.acao(relator, "tramParecer", id=mid, comissao=cid, voto="com_emendas", texto="Pela aprovação, com a emenda nº 1."))
        self.ok(s.acao(P, "tramVista", id=mid, comissao=cid, pid=ids[5]))
        self.erro(s.acao(P, "tramAvancar", id=mid, etapa="pronta_pauta"), 400, "comissão")
        al = self.painel()["alertas"]
        self.assertTrue(any(a["materia"] == mid and a["tipo"] == "vista" for a in al))  # vista de 3 dias: já no aviso
        self.ok(s.acao(P, "tramReuniaoAbrir", comissao=cid))
        rid = s.estado()["tramitacao"]["reunioes"][0]["id"]
        self.erro(s.acao(P, "tramReuniaoAbrir", comissao=cid), 400, "aberta")
        for pid in ids[3:5]:
            self.ok(s.acao(P, "tramReuniaoPresenca", id=rid, pid=pid))
        self.erro(s.acao(P, "tramReuniaoPresenca", id=rid, pid=ids[0]), 400, "membro")
        self.erro(s.acao(P, "tramReuniaoVotar", id=rid, materia=mid, votos={ids[3]: "sim"}), 400, "vista")
        self.ok(s.acao(P, "tramVistaDevolver", id=mid, comissao=cid))
        self.erro(s.acao(P, "tramReuniaoVotar", id=rid, materia=mid, votos={ids[5]: "sim"}), 400, "presentes")
        self.ok(s.acao(P, "tramReuniaoVotar", id=rid, materia=mid, votos={ids[3]: "sim", ids[4]: "sim"}))
        self.ok(s.acao(P, "tramReuniaoEncerrar", id=rid))
        self.erro(s.acao(P, "tramComissaoRemover", id="naoexiste"), 400)
        self.ok(s.acao(P, "tramAvancar", id=mid, etapa="pronta_pauta"))
        self.ok(s.acao(P, "tramAvancar", id=mid, etapa="votada", resultado="aprovada"))
        self.ok(s.acao(P, "tramAvancar", id=mid, etapa="sancao"))
        publico = s.req(f"/api/tramitacao/materia/{mid}")
        self.assertEqual(publico["materia"]["etapa"], "sancao")
        tipos = [x["tipo"] for x in publico["linhaDoTempo"]]
        for t in ("protocolada", "lida", "em_comissao", "relator", "parecer", "vista", "vista_devolvida", "parecer_votado", "pronta_pauta", "votada", "sancao"):
            self.assertIn(t, tipos)
        c = publico["materia"]["comissoes"][0]
        self.assertEqual((c["situacao"], len(c["votacao"]["sim"]), c["parecer"]["voto"]), ("concluida", 2, "com_emendas"))

    def test_pedido_de_informacao_ao_executivo(self):
        s, P = self.s, self.P
        self.ok(s.acao(P, "tramNova", tipo="PI", ementa="Solicita informações sobre os contratos de merenda escolar.",
                       autor="Vera Lúcia Campos"))
        mid = next(x for x in self.painel()["materias"] if x["tipo"] == "PI")["id"]
        self.erro(s.acao(P, "tramRespostaExecutivo", id=mid, texto="Antes do envio."), 400, "não foi enviada")
        self.ok(s.acao(P, "tramEnviarExecutivo", id=mid, prazoDias=4, oficio="Ofício 12/2026"))
        self.erro(s.acao(P, "tramEnviarExecutivo", id=mid), 400, "Já enviada")
        al = self.painel()["alertas"]
        self.assertTrue(any(a["materia"] == mid and a["tipo"] == "executivo" and not a["atrasado"] for a in al))
        falso = "data:application/pdf;base64," + base64.b64encode(b"<html>nao e pdf</html>").decode()
        self.erro(s.acao(P, "tramRespostaExecutivo", id=mid, texto="Resposta com anexo falso.", dataUrl=falso), 400, "PDF")
        pdf = b"%PDF-1.4\n% resposta da prefeitura\n%%EOF\n"
        self.ok(s.acao(P, "tramRespostaExecutivo", id=mid, texto="A Secretaria de Educação enviou os 4 contratos.",
                       nome="oficio.pdf", dataUrl="data:application/pdf;base64," + base64.b64encode(pdf).decode()))
        pub = s.req("/api/tramitacao/publico")
        item = next(x for x in pub["pedidos"] if x["id"] == mid)
        self.assertEqual((item["executivo"]["situacao"], item["executivo"]["resposta"]["noPrazo"]), ("respondido", True))
        self.assertGreaterEqual(pub["placar"]["noPrazo"], 1)
        self.assertEqual(s.req(f"/api/tramitacao/resposta/{mid}.pdf", bruto=True), pdf)
        self.assertEqual(s.req("/api/tramitacao/painel")["HTTP"], 401)  # painel só do presidente

    def test_materia_antiga_ou_do_sapl_continua_funcionando(self):
        s, P = self.s, self.P
        sem = self.painel()["semTramitacao"]
        antiga = next(x for x in sem if x["numero"] == "PL 112/2026")
        self.ok(s.acao(P, "tramIniciar", id=antiga["id"]))
        self.erro(s.acao(P, "tramIniciar", id=antiga["id"]), 400, "já tramita")
        m = s.req(f"/api/tramitacao/materia/{antiga['id']}")["materia"]
        self.assertEqual((m["numero"], m["tipo"], m["etapa"]), ("PL 112/2026", "PL", "protocolada"))
        self.ok(s.acao(P, "tramConfigurar", tipos=[{"sigla": "PL", "nome": "Projeto de Lei"}, {"sigla": "REQI", "nome": "Requerimento de Informação", "executivo": True}],
                       prazos={"parecer": 10, "vista": 2, "executivo": 45, "alerta": 7}))
        self.assertEqual(s.estado()["tramitacao"]["prazos"]["executivo"], 45)
        self.erro(s.acao(P, "tramConfigurar", tipos=[], prazos={}), 400)
        self.ok(s.acao(P, "tramConfigurar", tipos=tramitacao.TIPOS_PADRAO, prazos=tramitacao.PRAZOS_PADRAO))  # os outros testes usam o padrão


if __name__ == "__main__":
    unittest.main()
