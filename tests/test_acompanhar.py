"""Acompanhar (Atom, iCalendar, e-mail com double opt-in), opinião na pauta, Tribuna Livre, citações LexML e OAI-PMH."""
import email
import email.policy
import json
import re
import socket
import socketserver
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from apoio import RAIZ, Servidor, TesteComServidor

sys.path.insert(0, str(RAIZ / "server"))
import citacoes  # noqa: E402
import feeds  # noqa: E402

ATOM = "{http://www.w3.org/2005/Atom}"
OAI = "{http://www.openarchives.org/OAI/2.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"
SP = citacoes.localidade("SP", "São Paulo")


def urn_de(texto: str, local=SP) -> list[str]:
    return [citacoes.resolver(c, local)["urn"] for c in citacoes.reconhecer(texto)]


class Citacoes(unittest.TestCase):
    def test_reconhece_formatos(self):
        casos = {
            "Lei nº 1.234/2020": "urn:lex:br;sao.paulo;sao.paulo:municipal:lei:2020;1234",
            "Lei Municipal nº 123, de 5 de maio de 2019": "urn:lex:br;sao.paulo;sao.paulo:municipal:lei:2019-05-05;123",
            "Lei Complementar nº 10/2015": "urn:lex:br;sao.paulo;sao.paulo:municipal:lei.complementar:2015;10",
            "Decreto nº 45/2021": "urn:lex:br;sao.paulo;sao.paulo:municipal:decreto:2021;45",
            "nos termos do art. 5º da Lei nº 8.080/1990": "urn:lex:br:federal:lei:1990;8080!art5",
            "Constituição Federal, art. 30": "urn:lex:br:federal:constituicao:1988-10-05;1988!art30",
            "Lei federal nº 8.666/1993": "urn:lex:br:federal:lei:1993;8666",
            "Lei nº 14.133, de 1º de abril de 2021": "urn:lex:br:federal:lei:2021-04-01;14133",
            "Decreto-Lei nº 3.689/1941": "urn:lex:br:federal:decreto.lei:1941;3689",
            "art. 37 da Constituição Federal": "urn:lex:br:federal:constituicao:1988-10-05;1988!art37",
            "Lei 13.019/14": "urn:lex:br:federal:lei:2014;13019",
            "CF/88": "urn:lex:br:federal:constituicao:1988-10-05;1988",
            "Emenda Constitucional nº 95/2016": "urn:lex:br:federal:emenda.constitucional:2016;95",
            "Lei Complementar Federal nº 101, de 4 de maio de 2000": "urn:lex:br:federal:lei.complementar:2000-05-04;101",
            "Lei Estadual nº 10.000/1998": "urn:lex:br;sao.paulo:estadual:lei:1998;10000",
            "Resolução nº 3/2010": "urn:lex:br;sao.paulo;sao.paulo:municipal:resolucao:2010;3",
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(urn_de(texto), [esperado])
        # Lei Orgânica: reconhecida (sem URN até ser cadastrada); sem número não é citação
        r = citacoes.reconhecer("conforme a Lei Orgânica do Município")
        self.assertEqual((len(r), r[0]["tipo"], r[0]["texto"]), (1, "lei_organica", "Lei Orgânica do Município"))
        self.assertEqual(citacoes.reconhecer("a lei municipal prevê e cf. o decreto"), [])

    def test_varias_citacoes_e_offsets(self):
        t = "Altera a Lei nº 1.234/2020 e observa a Lei federal nº 8.666/1993."
        r = citacoes.reconhecer(t)
        self.assertEqual([t[c["inicio"]:c["fim"]] for c in r], ["Lei nº 1.234/2020", "Lei federal nº 8.666/1993"])

    def test_urn_e_localidade(self):
        self.assertEqual(citacoes.localidade("MG", "Belo Horizonte"), "br;minas.gerais;belo.horizonte")
        self.assertEqual(citacoes.localidade("RJ", "São João de Meriti"), "br;rio.de.janeiro;sao.joao.de.meriti")
        self.assertIsNone(citacoes.localidade("", "Cidade"))
        self.assertEqual(citacoes.urn("federal", "lei", "8666", 1993, "1993-06-21"), "urn:lex:br:federal:lei:1993-06-21;8666")
        self.assertIsNone(citacoes.urn("municipal", "lei", "10", 2020, None, None))   # sem município: sem URN
        # sem município configurado a municipal fica sem URN (e sem link), a federal continua com link do LexML
        html, cits = citacoes.linkar("Lei nº 1.234/2020 e Lei nº 8.666/1993", None)
        self.assertEqual([c["urn"] for c in cits], [None, "urn:lex:br:federal:lei:1993;8666"])
        self.assertIn('<span class="citacao"', html)
        self.assertIn('href="https://www.lexml.gov.br/urn/urn:lex:br:federal:lei:1993;8666"', html)

    def test_link_sem_xss(self):
        texto = '<script>alert("x")</script> Lei nº 8.666/1993 <img src=x onerror=alert(1)> & "Lei <b>nº</b> 9/2020"'
        html, cits = citacoes.linkar(texto, SP)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("<b>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertEqual(len(cits), 1)
        # tirando as marcas inseridas, sobra exatamente o texto escapado
        sem_marcas = re.sub(r"</?(a|span)\b[^>]*>", "", html)
        import html as h
        self.assertEqual(sem_marcas, h.escape(texto, quote=True))
        self.assertEqual(len(re.findall(r"<a ", html)), 1)

    def test_link_interno_para_norma_cadastrada(self):
        norma = {"id": "abc123", "tipo": "lei", "numero": "1234", "ano": 2020, "data": "2020-03-10", "ementa": "Programa X"}
        busca = lambda t, n, a: norma if (t, n, a) == ("lei", "1234", 2020) else None
        html, cits = citacoes.linkar("Lei nº 1.234/2020", SP, busca)
        self.assertEqual(cits[0]["href"], "/normas#n-abc123")
        self.assertEqual(cits[0]["urn"], "urn:lex:br;sao.paulo;sao.paulo:municipal:lei:2020-03-10;1234")
        self.assertIn('href="/normas#n-abc123"', html)
        self.assertNotIn("target=", html)


class Formatos(unittest.TestCase):
    def test_atom_bem_formado(self):
        corpo = feeds.atom("Projetos <&>", "urn:legis:x:feed", "http://h/feeds/projetos.atom", "http://h/", "Câmara",
                           [{"id": "urn:legis:x:1", "titulo": "Novo: PL 1 <b>", "link": "http://h/projeto/1", "atualizado": 1_700_000_000_000,
                             "resumo": "Texto com & e \x01 controle"}])
        raiz = ET.fromstring(corpo)
        self.assertEqual(raiz.tag, ATOM + "feed")
        for tag in ("id", "title", "updated", "author"):
            self.assertIsNotNone(raiz.find(ATOM + tag), tag)
        e = raiz.find(ATOM + "entry")
        self.assertEqual(e.find(ATOM + "title").text, "Novo: PL 1 <b>")
        self.assertRegex(e.find(ATOM + "updated").text, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_ics_escape_e_dobra(self):
        titulo = "Sessão; com, vírgula\\ e quebra\n" + "ç" * 80
        corpo = feeds.ics("Agenda", [{"uid": "x1", "inicio": 1_700_000_000_000, "fim": 1_700_003_600_000, "titulo": titulo,
                                       "descricao": "a" * 300, "url": "http://h/sessao/1"}], "h")
        texto = corpo.decode()
        self.assertTrue(texto.endswith("\r\n"))
        self.assertNotIn("\n", texto.replace("\r\n", ""))   # só CRLF
        for linha in corpo.split(b"\r\n"):
            self.assertLessEqual(len(linha), 75, linha)
        desdobrado = texto.replace("\r\n ", "")
        self.assertIn("SUMMARY:Sessão\\; com\\, vírgula\\\\ e quebra\\n" + "ç" * 80, desdobrado)
        self.assertIn("BEGIN:VCALENDAR\r\nVERSION:2.0", texto)
        self.assertIn("DTSTART:20231114T221320Z", texto)
        self.assertEqual(texto.count("BEGIN:VEVENT"), texto.count("END:VEVENT"))

    def test_oai_puro(self):
        regs = [{"id": f"projeto/{k}", "datestamp": 1_700_000_000_000 + k * 1000, "set": "projetos",
                 "dc": {"title": f"PL {k}", "identifier": [f"http://h/projeto/{k}"]}} for k in range(120)]
        xml = citacoes.oai({"verb": ["ListRecords"], "metadataPrefix": ["oai_dc"]}, "http://h/oai", "Câmara", "legis.camara", "a@b.c", regs)
        raiz = ET.fromstring(xml)
        lista = raiz.find(OAI + "ListRecords")
        self.assertEqual(len(lista.findall(OAI + "record")), citacoes.POR_PAGINA)
        tk = lista.find(OAI + "resumptionToken")
        self.assertEqual(tk.get("completeListSize"), "120")
        xml2 = ET.fromstring(citacoes.oai({"verb": ["ListRecords"], "resumptionToken": [tk.text]}, "http://h/oai", "C", "legis.camara", "a@b.c", regs))
        self.assertEqual(len(xml2.find(OAI + "ListRecords").findall(OAI + "record")), citacoes.POR_PAGINA)
        erro = lambda p: ET.fromstring(citacoes.oai(p, "http://h/oai", "C", "legis.camara", "a@b.c", regs)).find(OAI + "error").get("code")
        self.assertEqual(erro({"verb": ["Nada"]}), "badVerb")
        self.assertEqual(erro({"verb": ["ListRecords"]}), "badArgument")
        self.assertEqual(erro({"verb": ["ListRecords"], "metadataPrefix": ["marc"]}), "cannotDisseminateFormat")
        self.assertEqual(erro({"verb": ["ListRecords"], "resumptionToken": ["lixo!"]}), "badResumptionToken")
        self.assertEqual(erro({"verb": ["GetRecord"], "metadataPrefix": ["oai_dc"], "identifier": ["oai:legis.camara:nao"]}), "idDoesNotExist")
        self.assertEqual(erro({"verb": ["ListRecords"], "metadataPrefix": ["oai_dc"], "from": ["2030-01-01"]}), "noRecordsMatch")


def _get(base, caminho, cabecalhos=None):
    r = urllib.request.Request(base + caminho, headers=cabecalhos or {})
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, dict(x.headers), x.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _post(base, caminho, corpo, ip=None, bruto=None):
    dados = bruto if bruto is not None else json.dumps(corpo).encode()
    cab = {"Content-Type": "application/x-www-form-urlencoded" if bruto is not None else "application/json"}
    if ip:
        cab["X-Forwarded-For"] = ip
    r = urllib.request.Request(base + caminho, dados, cab)
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            c = x.read()
            return x.status, (json.loads(c) if bruto is None else c)
    except urllib.error.HTTPError as e:
        c = e.read()
        return e.code, (json.loads(c or b"{}") if bruto is None else c)


class Servico(TesteComServidor):
    """Servidor sem SMTP: feeds, agenda, OAI, citações no projeto público, opinião na pauta e Tribuna Livre."""

    @classmethod
    def setUpClass(cls):
        cls.servidor = Servidor(PROXY_CONFIAVEL="1")   # o X-Forwarded-For do 127.0.0.1 vale: simula cidadãos diferentes
        cls.s = cls.servidor
        cls.P = cls.s.entrar("presidente")
        cls.ids = [p["id"] for p in cls.s.estado()["parls"]]

    def _projeto(self, numero, ementa, texto=None):
        autor = self.s.estado()["parls"][0]["nome"]
        self.ok(self.s.acao(self.P, "projetoSalvar", numero=numero, ementa=ementa, autor=autor, situacao="em_tramitacao"))
        p = next(x for x in self.s.req("/api/dados/projetos.json")["projetos"] if x["numero"] == numero)
        if texto:
            self.ok(self.s.acao(self.P, "projetoTexto", id=p["id"], texto=texto))
        return p["id"]

    def test_1_feeds_e_agenda(self):
        s, P = self.s, self.P
        pid = self._projeto("PL 501/2026", "Dispõe sobre hortas & pomares <urbanos>.")
        self.ok(s.acao(P, "pautaIncluir", id=pid))
        tokens = [s.entrar("parlamentar", x) for x in self.ids[:9]]
        for t in tokens:
            self.ok(s.acao(t, "presenca"))
        self.ok(s.acao(P, "votacaoAbrir", materia_id=pid, tipo="nominal", quorum="simples"))
        for t in tokens:
            self.ok(s.acao(t, "votar", valor="sim"))
        self.ok(s.acao(P, "votacaoEncerrar"))
        self.ok(s.acao(P, "sessaoEncerrar", proximoTitulo="Próxima Ordinária", manterPauta=True))
        agora = int(time.time() * 1000)
        self.ok(s.acao(P, "virtualCriar", titulo="Virtual; com, vírgulas e um título bem comprido para forçar a dobra da linha no iCalendar",
                       inicio=agora + 3_600_000, fim=agora + 90_000_000))

        for caminho in ("/feeds/projetos.atom", f"/feeds/projeto/{pid}.atom", f"/feeds/vereador/{self.ids[0]}.atom", "/feeds/sessoes.atom"):
            with self.subTest(caminho=caminho):
                st, cab, corpo = _get(s.base, caminho)
                self.assertEqual(st, 200)
                self.assertTrue(cab["Content-Type"].startswith("application/atom+xml"))
                raiz = ET.fromstring(corpo)
                self.assertEqual(raiz.tag, ATOM + "feed")
                self.assertTrue(raiz.find(ATOM + "link[@rel='self']").get("href").endswith(caminho))
                self.assertTrue(raiz.findall(ATOM + "entry"), caminho)
        titulos = [e.find(ATOM + "title").text for e in ET.fromstring(_get(s.base, f"/feeds/projeto/{pid}.atom")[2]).findall(ATOM + "entry")]
        self.assertTrue(any("Votação" in t and "aprovada" in t for t in titulos), titulos)
        self.assertTrue(any("Novo projeto" in t for t in titulos))
        vereador = [e.find(ATOM + "title").text for e in ET.fromstring(_get(s.base, f"/feeds/vereador/{self.ids[0]}.atom")[2]).findall(ATOM + "entry")]
        self.assertTrue(any("votou SIM" in t for t in vereador), vereador)
        sessoes = ET.fromstring(_get(s.base, "/feeds/sessoes.atom")[2]).findall(ATOM + "entry")
        self.assertTrue(sessoes[0].find(ATOM + "link[@rel='alternate']").get("href").split("/sessao/")[1])
        self.assertEqual(_get(s.base, "/feeds/projeto/naoexiste.atom")[0], 404)

        st, cab, corpo = _get(s.base, "/agenda.ics")
        self.assertEqual(st, 200)
        self.assertTrue(cab["Content-Type"].startswith("text/calendar"))
        texto = corpo.decode()
        self.assertNotIn("\n", texto.replace("\r\n", ""))
        self.assertTrue(all(len(l) <= 75 for l in corpo.split(b"\r\n")))
        self.assertIn("\r\n ", texto)   # título longo foi dobrado
        desdobrado = texto.replace("\r\n ", "")
        self.assertIn("SUMMARY:Sessão virtual: Virtual\\; com\\, vírgulas", desdobrado)
        self.assertEqual(desdobrado.count("BEGIN:VEVENT"), 2)   # a sessão encerrada e a virtual
        self.assertIn("SUMMARY:45ª Sessão Ordinária", desdobrado)
        self.assertEqual(_get(s.base, f"/agenda.ics?projeto={pid}")[2].decode().count("BEGIN:VEVENT"), 1)

    def test_2_projeto_publico_com_citacoes_e_normas(self):
        s, P = self.s, self.P
        texto = ('Art. 1º Fica alterada a Lei nº 1.234/2020, nos termos da Lei federal nº 8.666/1993.\n'
                 'Art. 2º <script>alert("xss")</script> Esta lei entra em vigor na data da publicação.')
        pid = self._projeto("PL 502/2026", "Altera a Lei nº 1.234/2020.", texto)
        # norma municipal cadastrada à mão e por CSV
        self.ok(s.acao(P, "normaMunicipalSalvar", tipo="lei", numero="1.234", data="10/03/2020", ementa="Programa municipal X",
                       link="https://camara.exemplo/lei1234.pdf"))
        self.erro(s.acao(P, "normaMunicipalSalvar", tipo="lei", numero="1", data="2020", link="javascript:alert(1)"), 400)
        self.assertEqual(s.req("/api/normas-municipais/importar", {"csv": "tipo;numero"})["HTTP"], 401)
        r = s.req("/api/normas-municipais/importar", {"csv": "tipo;numero;data;ementa;link\nlei complementar;10;2015;Plano diretor;\n"
                                                      "decreto;45;2021-02-01;Regulamenta;\nlixo;x;y;;\n"}, token=P)
        self.assertEqual(r["importadas"], 2)
        self.assertEqual(len(r["erros"]), 1)
        normas = s.req("/api/normas-municipais")["normas"]
        self.assertEqual(sorted(n["numero"] for n in normas), ["10", "1234", "45"])
        d = s.req(f"/api/projeto-publico/{pid}")
        self.assertNotIn("<script>", d["textoHtml"])
        self.assertIn("&lt;script&gt;", d["textoHtml"])
        lei = next(n for n in normas if n["numero"] == "1234")
        self.assertIn(f'href="/normas#n-{lei["id"]}"', d["textoHtml"])
        self.assertIn(f'href="/normas#n-{lei["id"]}"', d["ementaHtml"])
        self.assertIn('href="https://www.lexml.gov.br/urn/urn:lex:br:federal:lei:1993;8666"', d["textoHtml"])
        self.assertFalse(d["email"])   # sem SMTP no ambiente, a opção de e-mail não aparece
        self.assertEqual(s.req("/api/acompanhar"), {"email": False})
        self.assertEqual(s.req("/api/acompanhar", {"email": "a@b.com.br", "projeto": pid})["HTTP"], 404)
        st, cab, pagina = _get(s.base, f"/projeto/{pid}")
        self.assertEqual(st, 200)
        self.assertIn(b"projeto-publico", pagina)
        r = s.req("/api/citacoes", {"texto": "<b>Lei nº 8.666/1993</b>"})
        self.assertTrue(r["html"].startswith("&lt;b&gt;<a "))

    def test_3_oai(self):
        s = self.s
        st, cab, corpo = _get(s.base, "/oai?verb=Identify")
        self.assertEqual(st, 200)
        raiz = ET.fromstring(corpo)
        ident = raiz.find(OAI + "Identify")
        self.assertEqual(ident.find(OAI + "protocolVersion").text, "2.0")
        self.assertTrue(ident.find(OAI + "baseURL").text.endswith("/oai"))
        self.assertRegex(ident.find(OAI + "earliestDatestamp").text, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        formatos = ET.fromstring(_get(s.base, "/oai?verb=ListMetadataFormats")[2])
        self.assertEqual(formatos.find(f"{OAI}ListMetadataFormats/{OAI}metadataFormat/{OAI}metadataPrefix").text, "oai_dc")
        lista = ET.fromstring(_get(s.base, "/oai?verb=ListRecords&metadataPrefix=oai_dc")[2])
        recs = lista.findall(f"{OAI}ListRecords/{OAI}record")
        self.assertTrue(recs)
        sets = {r.find(f"{OAI}header/{OAI}setSpec").text for r in recs}
        self.assertEqual(sets, {"normas", "projetos"})
        titulos = [t.text for t in lista.iter(DC + "title")]
        self.assertIn("Lei nº 1234/2020", titulos)
        ident = recs[0].find(f"{OAI}header/{OAI}identifier").text
        um = ET.fromstring(_get(s.base, "/oai?verb=GetRecord&metadataPrefix=oai_dc&identifier=" + urllib.parse.quote(ident))[2])
        self.assertIsNotNone(um.find(f"{OAI}GetRecord/{OAI}record"))
        so_normas = ET.fromstring(_get(s.base, "/oai?verb=ListIdentifiers&metadataPrefix=oai_dc&set=normas")[2])
        self.assertTrue(all(h.find(OAI + "setSpec").text == "normas" for h in so_normas.iter(OAI + "header")))
        self.assertEqual(ET.fromstring(_get(s.base, "/oai?verb=Errado")[2]).find(OAI + "error").get("code"), "badVerb")

    def test_4_opiniao_na_pauta_moderada(self):
        s, P = self.s, self.P
        item = s.estado()["pauta"][0]["id"]
        corpo = {"nome": "Maria das Dores", "email": "maria@exemplo.com.br", "bairro": "Centro", "posicao": "favor",
                 "texto": "Apoio porque o bairro precisa."}
        self.assertEqual(_post(s.base, f"/api/pauta/{item}/opinar", {**corpo, "email": ""}, "10.0.0.1")[0], 400)   # e-mail obrigatório
        self.assertEqual(_post(s.base, f"/api/pauta/{item}/opinar", {**corpo, "posicao": "talvez"}, "10.0.0.1")[0], 400)
        self.assertEqual(_post(s.base, f"/api/pauta/{item}/opinar", corpo, "10.0.0.1"), (200, {"ok": True, "situacao": "pendente"}))
        self.assertEqual(_post(s.base, f"/api/pauta/{item}/opinar", {**corpo, "posicao": "contra", "nome": "José"}, "10.0.0.2")[0], 200)
        self.assertEqual(_post(s.base, "/api/pauta/naoexiste/opinar", corpo, "10.0.0.1")[0], 400)
        publico = s.req("/api/pauta-cidada")
        it = next(m for m in publico["itens"] if m["id"] == item)
        self.assertEqual(it["comentarios"], [])                  # nada aparece antes da moderação
        self.assertEqual(sum(it["placar"].values()), 0)
        interno = next(m for m in s.req("/api/pauta-cidada", token=P)["itens"] if m["id"] == item)
        self.assertEqual(interno["placarTotal"], {"favor": 1, "contra": 1, "neutro": 0})
        maria = next(o for o in interno["moderacao"] if o["nome"] == "Maria das Dores")
        jose = next(o for o in interno["moderacao"] if o["nome"] == "José")
        self.ok(s.acao(P, "opiniaoModerar", id=maria["id"], situacao="aprovado"))
        self.ok(s.acao(P, "opiniaoModerar", id=jose["id"], situacao="recusado"))
        self.erro(s.acao(P, "opiniaoModerar", id=maria["id"], situacao="talvez"), 400)
        it = next(m for m in s.req("/api/pauta-cidada")["itens"] if m["id"] == item)
        self.assertEqual([c["nome"] for c in it["comentarios"]], ["Maria das Dores"])
        self.assertEqual(it["placar"], {"favor": 1, "contra": 0, "neutro": 0})
        est = s.estado()
        self.assertEqual(est["placarCidadao"]["itens"][item], {"favor": 1, "contra": 0, "neutro": 0})
        self.ok(s.acao(P, "placarTelao", mostrar=True))
        self.assertTrue(s.estado()["placarCidadao"]["mostrar"])
        # e-mail nunca sai nas APIs públicas nem no estado das telas
        for publica in (json.dumps(s.req("/api/pauta-cidada")), json.dumps(s.estado()), json.dumps(s.req("/api/tribuna"))):
            self.assertNotIn("maria@exemplo.com.br", publica)
        # limite por endereço
        codigos = [_post(s.base, f"/api/pauta/{item}/opinar", corpo, "10.0.0.9")[0] for _ in range(8)]
        self.assertEqual(codigos.count(200), 6)
        self.assertEqual(codigos[-1], 429)

    def test_5_tribuna_livre(self):
        s, P = self.s, self.P
        base = {"entidade": "Associação do Bairro Alto", "tema": "Iluminação da praça", "contato": "(11) 99999-0000"}
        self.assertEqual(_post(s.base, "/api/tribuna", {**base, "nome": "Ana", "contato": "x"}, "10.1.0.1")[0], 400)
        for k, nome in enumerate(["Ana Souza", "Bruno Lima", "Carla Dias"]):
            self.assertEqual(_post(s.base, "/api/tribuna", {**base, "nome": nome, "contato": f"pessoa{k}@exemplo.org"}, f"10.1.0.{k + 1}")[0], 200)
        self.assertEqual(s.req("/api/tribuna")["inscritos"], [])   # nada público antes da aprovação
        todas = {i["nome"]: i for i in s.req("/api/tribuna", token=P)["todas"]}
        self.assertEqual(todas["Ana Souza"]["contato"], "pessoa0@exemplo.org")
        for nome in ("Carla Dias", "Ana Souza", "Bruno Lima"):
            self.ok(s.acao(P, "tribunaModerar", id=todas[nome]["id"], situacao="aprovado"))
        ordem = lambda: [(i["nome"], i["ordem"]) for i in s.req("/api/tribuna")["inscritos"]]
        self.assertEqual(ordem(), [("Carla Dias", 1), ("Ana Souza", 2), ("Bruno Lima", 3)])
        self.ok(s.acao(P, "tribunaMover", id=todas["Bruno Lima"]["id"], d=-1))
        self.assertEqual(ordem(), [("Carla Dias", 1), ("Bruno Lima", 2), ("Ana Souza", 3)])
        self.ok(s.acao(P, "tribunaModerar", id=todas["Carla Dias"]["id"], situacao="recusado"))
        self.assertEqual(ordem(), [("Bruno Lima", 1), ("Ana Souza", 2)])
        publico = json.dumps(s.req("/api/tribuna"))
        self.assertNotIn("@exemplo.org", publico)
        self.assertNotIn("contato", publico)
        # limite diário por endereço e inscrições fechadas
        self.assertEqual([_post(s.base, "/api/tribuna", {**base, "nome": "Repetido"}, "10.1.0.50")[0] for _ in range(3)], [200, 200, 429])
        self.ok(s.acao(P, "tribunaAbrir", abertas=False))
        self.assertEqual(_post(s.base, "/api/tribuna", {**base, "nome": "Depois"}, "10.1.0.60")[0], 400)
        self.assertFalse(s.req("/api/tribuna")["abertas"])


# ---------------------------------------------------------------- SMTP falso (sem TLS), só para o teste do double opt-in
class _SMTPFalso(socketserver.StreamRequestHandler):
    caixa: list = []

    def handle(self):
        w = lambda t: self.wfile.write((t + "\r\n").encode())
        w("220 falso ESMTP")
        while True:
            linha = self.rfile.readline().decode(errors="replace").strip()
            if not linha:
                return
            cmd = linha.split(" ", 1)[0].upper()
            if cmd in ("EHLO", "HELO"):
                w("250-falso")
                w("250 AUTH PLAIN LOGIN")
            elif cmd == "AUTH":
                w("235 ok")
            elif cmd in ("MAIL", "RCPT", "RSET", "NOOP"):
                w("250 ok")
            elif cmd == "DATA":
                w("354 manda")
                dados = []
                while (l := self.rfile.readline()) not in (b".\r\n", b""):
                    dados.append(l)
                m = email.message_from_bytes(b"".join(dados), policy=email.policy.default)
                self.caixa.append(f"List-Unsubscribe: {m['List-Unsubscribe']}\nSubject: {m['Subject']}\n\n{m.get_body().get_content()}")
                w("250 guardado")
            elif cmd == "QUIT":
                w("221 tchau")
                return
            else:
                w("502 ?")


class Email(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.smtp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SMTPFalso)
        cls.smtp.daemon_threads = True
        threading.Thread(target=cls.smtp.serve_forever, daemon=True).start()
        porta = cls.smtp.server_address[1]
        cls.s = Servidor(SMTP_HOST="127.0.0.1", SMTP_PORTA=str(porta), SMTP_USUARIO="u", SMTP_SENHA="p",
                         SMTP_REMETENTE="camara@exemplo.gov.br", SMTP_STARTTLS="0", URL_PUBLICA="http://camara.exemplo",
                         ACOMPANHAR_INTERVALO="5")
        cls.P = cls.s.entrar("presidente")

    @classmethod
    def tearDownClass(cls):
        cls.s.parar()
        cls.smtp.shutdown()
        cls.smtp.server_close()

    def _esperar_email(self, n, limite=20):
        fim = time.time() + limite
        while time.time() < fim and len(_SMTPFalso.caixa) < n:
            time.sleep(0.2)
        self.assertGreaterEqual(len(_SMTPFalso.caixa), n, "e-mail não chegou")
        return _SMTPFalso.caixa[n - 1]

    def test_double_opt_in(self):
        s, P = self.s, self.P
        _SMTPFalso.caixa.clear()
        self.assertEqual(s.req("/api/acompanhar"), {"email": True})
        self.assertEqual(s.acao(P, "projetoSalvar", numero="PL 700/2026", ementa="Cria o festival de inverno.", situacao="em_tramitacao"), {"ok": True})
        pid = next(x for x in s.req("/api/dados/projetos.json")["projetos"] if x["numero"] == "PL 700/2026")["id"]
        self.assertTrue(s.req(f"/api/projeto-publico/{pid}")["email"])
        self.assertEqual(s.req("/api/acompanhar", {"email": "nao-e-email", "projeto": pid})["HTTP"], 400)
        self.assertTrue(s.req("/api/acompanhar", {"email": "Cidada@Exemplo.com.br", "projeto": pid})["ok"])
        msg = self._esperar_email(1)
        self.assertIn("List-Unsubscribe: <http://camara.exemplo/acompanhar/sair?t=", msg)
        token = re.search(r"/acompanhar/confirmar\?t=([\w-]+)", msg).group(1)
        sair = re.search(r"/acompanhar/sair\?t=([\w-]+)", msg).group(1)
        # o e-mail nunca aparece nas APIs públicas
        for caminho in ("/api/dados/projetos.json", f"/api/projeto-publico/{pid}", "/api/transparencia", f"/feeds/projeto/{pid}.atom"):
            self.assertNotIn(b"cidada@exemplo", s.req(caminho, bruto=True).lower())
        # GET só mostra o botão (robôs que abrem links não confirmam); o POST confirma, uma única vez
        st, _, pagina = _get(s.base, f"/acompanhar/confirmar?t={token}")
        self.assertEqual(st, 200)
        self.assertIn(b'method="post"', pagina)
        self.assertEqual(_post(s.base, "/acompanhar/confirmar", None, bruto=f"t={token}".encode())[0], 200)
        self.assertEqual(_post(s.base, "/acompanhar/confirmar", None, bruto=f"t={token}".encode())[0], 400)   # uso único
        self.assertEqual(_post(s.base, "/acompanhar/confirmar", None, bruto=b"t=inventado")[0], 400)
        # novidade no projeto: aviso por e-mail com link de descadastro
        self.assertEqual(s.acao(P, "pautaIncluir", id=pid), {"ok": True})
        self.assertEqual(s.acao(P, "sessaoEncerrar", proximoTitulo="Outra", manterPauta=False), {"ok": True})
        aviso = self._esperar_email(2, limite=30)
        self.assertIn("PL 700/2026", aviso)
        self.assertIn(f"/acompanhar/sair?t={sair}", aviso)
        # descadastro one-click (RFC 8058)
        st, pagina = _post(s.base, f"/acompanhar/sair?t={sair}", None, bruto=b"List-Unsubscribe=One-Click")
        self.assertEqual(st, 200)
        self.assertIn("cancelados".encode(), pagina)
        st, pagina = _post(s.base, f"/acompanhar/sair?t={sair}", None, bruto=b"")
        self.assertIn("Nada a cancelar".encode(), pagina)


if __name__ == "__main__":
    unittest.main()
