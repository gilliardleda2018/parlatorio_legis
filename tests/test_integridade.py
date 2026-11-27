"""Auditoria encadeada por hash, selo da ata, recibo do voto nominal e governança do uso de IA."""
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

from apoio import Servidor, TesteComServidor


class Integridade(TesteComServidor):
    @classmethod
    def setUpClass(cls):
        cls.servidor = Servidor(IA="falso")
        cls.s = cls.servidor
        cls.P = cls.s.entrar("presidente")
        e = cls.s.estado()
        cls.ids = [p["id"] for p in e["parls"]]
        cls.pauta = e["pauta"]

    def _banco(self, sql, args=()):
        banco = sqlite3.connect(self.s.dir.name + "/teste.db")
        try:
            return banco.execute(sql, args).fetchall()
        finally:
            banco.close()

    def _executar(self, sql, args=()):
        banco = sqlite3.connect(self.s.dir.name + "/teste.db")
        banco.execute(sql, args)
        banco.commit()
        banco.close()

    def esperar(self, cond, n=100):
        for _ in range(n):
            v = cond()
            if v:
                return v
            time.sleep(0.1)
        self.fail("condição não atingida")

    def test_1_cadeia_integra_e_adulteracao(self):
        s, P = self.s, self.P
        self.ok(s.acao(P, "presenca", pid=self.ids[0], presente=True))
        self.ok(s.acao(P, "presenca", pid=self.ids[1], presente=True))
        c = s.req("/api/integridade")
        self.assertTrue(c["ok"], c)
        self.assertGreaterEqual(c["encadeados"], 3)
        self.assertEqual(c["inicio"], 0)
        self.assertEqual(len(c["ultimo_hash"]), 64)
        self.assertEqual(c["total"], c["encadeados"])
        self.assertFalse(self._banco("select count(*) from eventos where hash is null")[0][0])
        # adulterar uma linha do meio quebra a cadeia exatamente nela
        eid, args = self._banco("select id, args from eventos where acao = 'presenca' order by id limit 1")[0]
        self._executar("update eventos set args = ? where id = ?", (json.dumps({"pid": self.ids[5]}), eid))
        c = s.req("/api/integridade")
        self.assertFalse(c["ok"])
        self.assertEqual(c["primeira_quebra"]["evento"], eid)
        self._executar("update eventos set args = ? where id = ?", (args, eid))
        self.assertTrue(s.req("/api/integridade")["ok"])
        # apagar uma linha do meio também
        linha = self._banco("select * from eventos where id = ?", (eid,))[0]
        self._executar("delete from eventos where id = ?", (eid,))
        self.assertFalse(s.req("/api/integridade")["ok"])
        self._executar("insert into eventos (id, ts, autor, acao, args, hash) values (?, ?, ?, ?, ?, ?)", linha)
        self.assertTrue(s.req("/api/integridade")["ok"])

    def test_2_recibo_do_voto_nominal(self):
        s, P = self.s, self.P
        t = s.entrar("parlamentar", self.ids[0])
        self.ok(s.acao(P, "votacaoAbrir", materia_id=self.pauta[0]["id"], tipo="nominal", quorum="simples"))
        vid = s.estado()["votacao"]["id"]
        self.assertFalse(s.req(f"/api/recibo?votacao={vid}", token=t)["votou"])
        self.ok(s.acao(t, "votar", valor="sim"))
        self.ok(s.acao(P, "votarPor", pid=self.ids[1], valor="nao"))
        r = s.req(f"/api/recibo?votacao={vid}", token=t)
        self.assertEqual(r["valor"], "sim")
        self.assertRegex(r["recibo"], r"^[A-Z2-7]{5}-[A-Z2-7]{5}$")
        self.assertEqual(s.req("/api/recibo", token=t)["recibo"], r["recibo"])   # sem parâmetro: a votação atual
        self.assertEqual(s.req(f"/api/recibo?votacao={vid}")["HTTP"], 401)        # só o próprio parlamentar
        self.assertEqual(s.req(f"/api/recibo?votacao={vid}", token=P)["HTTP"], 401)
        confere = lambda codigo, v=vid: s.req("/api/verificar/recibo", {"votacao": v, "recibo": codigo})
        self.assertEqual(confere(r["recibo"]), {"confere": True})
        self.assertEqual(confere(r["recibo"].lower().replace("-", "")), {"confere": True})
        self.assertEqual(confere("AAAAA-AAAAA"), {"confere": False})
        self.assertEqual(confere(r["recibo"], "outra"), {"confere": False})
        self.assertNotIn("_chaveRecibo", s.estado())   # a chave nunca vai para as telas
        # mudou o voto: vale o recibo do último
        self.ok(s.acao(t, "votar", valor="abst"))
        novo = s.req(f"/api/recibo?votacao={vid}", token=t)["recibo"]
        self.assertNotEqual(novo, r["recibo"])
        self.assertEqual(confere(r["recibo"]), {"confere": False})
        self.ok(s.acao(P, "votacaoEncerrar"))
        self.ok(s.acao(P, "votacaoLimpar"))
        self.assertEqual(confere(novo), {"confere": True})   # continua conferível depois de encerrada
        self.__class__.vid_nominal, self.__class__.recibo_nominal = vid, novo

    def test_3_voto_secreto_sem_recibo(self):
        s, P = self.s, self.P
        t = s.entrar("parlamentar", self.ids[0])
        self.ok(s.acao(P, "votacaoAbrir", materia_id=self.pauta[1]["id"], tipo="secreta", quorum="simples"))
        vid = s.estado()["votacao"]["id"]
        self.ok(s.acao(t, "votar", valor="nao"))
        r = s.req(f"/api/recibo?votacao={vid}", token=t)
        self.assertTrue(r["votou"])
        self.assertNotIn("recibo", r)
        self.assertNotIn("valor", r)
        self.assertNotIn('"nao"', json.dumps(r))
        self.ok(s.acao(P, "votacaoEncerrar"))
        self.ok(s.acao(P, "votacaoLimpar"))
        for (args,) in self._banco("select args from eventos where acao = 'votar'"):
            if '"votou"' in args:
                self.assertNotIn("valor", json.loads(args))
        self.assertTrue(s.req("/api/integridade")["ok"])

    def test_4_rascunho_da_ata_por_ia_e_revisao(self):
        s, P = self.s, self.P
        self.ok(s.acao(P, "ataResumoGerar", sessao="atual"))
        self.esperar(lambda: (s.estado().get("resumoJob") or {}).get("fim"))
        r = s.req("/api/resumo/atual", token=P)
        self.assertIn("não revisado", r["seloIA"])
        self.assertIn("simulada", r["seloIA"])
        ia = s.req("/api/ia")
        ata = next(e for e in ia["recursos"] if e["recurso"] == "rascunho_ata")
        self.assertGreaterEqual(ata["usos"], 1)
        self.assertEqual(ata["pctRevisado"], 0)
        self.assertTrue(ia["padrao"])
        self.assertIn("revisão humana", ia["politica"].lower())
        pdf = s.req("/api/ata/atual.pdf", token=P, bruto=True)
        self.assertIn(b"Rascunho gerado por IA", pdf)
        self.assertIn(b"o revisado.", pdf)
        self.assertEqual(s.acao(P, "iaRevisarAta", sessao="inexistente")["HTTP"], 400)
        self.ok(s.acao(P, "iaRevisarAta", sessao="atual", revisor="Maria Secretária"))
        r = s.req("/api/resumo/atual", token=P)
        self.assertIn("revisado por Maria Secret", r["seloIA"])
        self.assertTrue(all(i["revisado"] for i in r["resumo"]["itens"]))
        self.assertEqual(next(e for e in s.req("/api/ia")["recursos"] if e["recurso"] == "rascunho_ata")["pctRevisado"], 100)
        pdf = s.req("/api/ata/atual.pdf", token=P, bruto=True)
        self.assertIn(b"revisado por Maria Secret", pdf)
        self.assertEqual(s.acao(self.s.entrar("parlamentar", self.ids[2]), "iaRevisarAta")["HTTP"], 403)

    def test_5_assistente_registra_sem_expor_pergunta(self):
        s = self.s
        pergunta = "Quais projetos tratam de hortas comunitárias zebra-quindim?"
        r = s.req("/api/perguntar", {"pergunta": pergunta, "escopo": "tudo"})
        if r.get("modo") == "ia":
            self.assertIn("gerada por IA", r["seloIA"])
        bruto = s.req("/api/ia", bruto=True)
        self.assertNotIn(b"zebra", bruto)
        self.assertNotIn(b"127.0.0.1", bruto)
        for (entrada,) in self._banco("select entrada_resumo from uso_ia"):
            self.assertNotIn("zebra", entrada)
        html = s.req("/ia", bruto=True)
        self.assertIn(b"api/ia", html)

    def test_6_politica_editavel(self):
        s, P = self.s, self.P
        self.ok(s.acao(P, "iaPoliticaSalvar", texto="Nossa política: IA só como rascunho."))
        d = s.req("/api/ia")
        self.assertEqual(d["politica"], "Nossa política: IA só como rascunho.")
        self.assertFalse(d["padrao"])
        self.ok(s.acao(P, "iaPoliticaSalvar", texto=""))
        self.assertTrue(s.req("/api/ia")["padrao"])

    def test_7_selo_da_ata_arquivada(self):
        s, P = self.s, self.P
        antes = s.req("/api/integridade")["ultimo_hash"]
        self.ok(s.acao(P, "sessaoEncerrar", proximoTitulo="Próxima", manterPauta=True))
        sid = s.req("/api/sessoes", token=P)["sessoes"][0]["id"]
        arq = s.req(f"/api/sessoes/{sid}", token=P)
        selo = arq["selo"]["hash"]
        self.assertEqual(selo, antes)   # último elo antes do encerramento
        self.assertIn("revisao", arq["resumoIA"])   # a revisão vai junto para o arquivo
        pdf = s.req(f"/api/ata/{sid}.pdf", token=P, bruto=True)
        self.assertIn(b"Selo de integridade: " + selo.encode(), pdf)
        self.assertIn(b"/verificar", pdf)
        v = s.req("/api/verificar/selo", {"selo": selo.upper()})
        self.assertTrue(v["confere"])
        self.assertTrue(v["naCadeia"])
        self.assertEqual(v["sessao"]["titulo"], arq["titulo"])
        self.assertEqual(s.req("/api/verificar/selo", {"selo": "0" * 64}), {"confere": False})
        self.assertEqual(s.req("/api/verificar/selo", {"selo": "x' or 1=1"}), {"confere": False})
        c = s.req("/api/integridade")
        self.assertTrue(c["ok"])
        self.assertEqual(c["selos"], 1)
        # o recibo de uma votação de sessão arquivada continua conferível
        self.assertEqual(s.req("/api/verificar/recibo", {"votacao": self.vid_nominal, "recibo": self.recibo_nominal}),
                         {"confere": True})
        self.assertIn(b"api/integridade", s.req("/verificar", bruto=True))

    def test_8_apagar_o_fim_do_registro_e_percebido(self):
        s, P = self.s, self.P
        sid = s.req("/api/sessoes", token=P)["sessoes"][0]["id"]
        selo = s.req(f"/api/sessoes/{sid}", token=P)["selo"]
        # quem apaga tudo a partir do selo e refaz a corrente não engana: o selo some da cadeia
        self._executar("delete from eventos where id >= ?", (selo["evento"],))
        c = s.req("/api/integridade")
        self.assertFalse(c["ok"])
        self.assertTrue(c["selos_ausentes"])
        self.assertFalse(s.req("/api/verificar/selo", {"selo": selo["hash"]})["naCadeia"])


class Migracao(unittest.TestCase):
    def test_banco_antigo_sem_hash_vira_inicio_da_cadeia(self):
        pasta = tempfile.TemporaryDirectory(prefix="parlatorio-migracao-")
        caminho = pasta.name + "/antigo.db"
        banco = sqlite3.connect(caminho)
        banco.execute("create table eventos (id integer primary key, ts integer, autor text, acao text, args text)")
        for k in range(5):
            banco.execute("insert into eventos (ts, autor, acao, args) values (?, 'presidente', 'setFase', '{}')", (k,))
        banco.commit()
        banco.close()
        s = Servidor(PARLATORIO_BANCO=caminho)
        try:
            P = s.entrar("presidente")
            s.acao(P, "presenca", pid=s.estado()["parls"][0]["id"], presente=True)
            c = s.req("/api/integridade")
            self.assertTrue(c["ok"], c)
            self.assertEqual(c["inicio"], 5)
            self.assertGreaterEqual(c["encadeados"], 2)
        finally:
            s.parar()
            pasta.cleanup()
        # rodar de novo a migração (servidor reiniciado) não muda nada
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
        import integridade
        mem = sqlite3.connect(":memory:")
        mem.execute("create table eventos (id integer primary key, ts integer, autor text, acao text, args text)")
        integridade.preparar_banco(mem)
        integridade.preparar_banco(mem)
        self.assertIn("hash", {c[1] for c in mem.execute("pragma table_info(eventos)")})


if __name__ == "__main__":
    unittest.main()
