"""Primeira configuração pelo painel: de qual cidade é esta Câmara (TSE e IBGE simulados, nomes fictícios)."""
import json
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from apoio import Servidor

FOTO = b"\xff\xd8\xff\xe0" + b"j" * 200   # começo de um JPEG
CONFIG = {"abr": [{"cd": "ma", "mu": [{"cd": "00001", "cdi": "9900001", "nm": "VILA ESPERANÇA"}]}]}
RESULTADO = {"dg": "06/10/2024", "carg": [{"nv": 3, "agr": [{"par": [
    {"sg": "AAA", "cand": [{"nmu": "MARIA DA SILVA", "n": 11111, "vap": "900", "sqcand": "1", "st": "Eleito por QP"},
                           {"nmu": "JOSE SUPLENTE", "n": 11112, "vap": "10", "sqcand": "2", "st": "Suplente"}]},
    {"sg": "BBB", "cand": [{"nmu": "JOAO DOS SANTOS", "n": 22222, "vap": "700", "sqcand": "3", "st": "Eleito por média"},
                           {"nmu": "ANA PEREIRA", "n": 22223, "vap": "650", "sqcand": "4", "st": "Eleito por QP"}]}]}]}]}
IBGE = [{"resultados": [{"series": [{"serie": {"2022": "12345"}}]}]}]
BRASAO = b"\x89PNG\r\n\x1a\n" + b"b" * 300   # começo de um PNG
WIKIDATA = {"results": {"bindings": [{"b": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/Bras%C3%A3o%20Vila.png"}}]}}
LICENCA = {"valor": "Public domain"}   # trocável nos testes


def commons(base):
    return {"query": {"pages": {"1": {"imageinfo": [{"url": base + "/img/brasao.png", "thumburl": base + "/img/brasao.png",
                                                     "mime": "image/png",
                                                     "extmetadata": {"LicenseShortName": {"value": LICENCA["valor"]}}}]}}}}
ROTAS = {"/tse/619/config/mun-e000619-cm.json": json.dumps(CONFIG).encode(),
         "/tse/619/dados/ma/ma00001-c0013-e000619-u.json": json.dumps(RESULTADO).encode(),
         "/tse/619/fotos/ma/1.jpeg": FOTO, "/tse/619/fotos/ma/3.jpeg": FOTO}


class TseFalso(BaseHTTPRequestHandler):
    def do_GET(self):
        caminho = self.path.split("?")[0]
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        corpo = json.dumps(IBGE).encode() if caminho.startswith("/ibge/v3/agregados/4709/") else ROTAS.get(caminho)
        if caminho == "/wikidata":
            corpo = json.dumps(WIKIDATA if "9900001" in self.path else {"results": {"bindings": []}}).encode()
        elif caminho == "/commons":
            corpo = json.dumps(commons(base)).encode()
        elif caminho == "/img/brasao.png":
            corpo = BRASAO
        elif caminho == "/portal-vilaesperanca/vereadores":   # portal legislativomunicipal.com simulado
            corpo = b'<a href="/vereadores/1">Maria</a> <a href="/vereadores/2">Joao</a>'

        if corpo is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *a):
        pass


class PrimeiraConfiguracao(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), TseFalso)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.fake.server_address[1]}"
        cls.s = Servidor(LEGIS_TSE_URL=base + "/tse", LEGIS_IBGE_URL=base + "/ibge",
                         LEGIS_WIKIDATA_URL=base + "/wikidata", LEGIS_COMMONS_URL=base + "/commons",
                         LEGIS_PORTAL_URL=base + "/portal-{nome}")
        cls.P = cls.s.entrar("presidente")

    @classmethod
    def tearDownClass(cls):
        cls.s.parar()
        cls.fake.shutdown()
        cls.fake.server_close()

    def test_cidade_pelo_painel(self):
        s, P = self.s, self.P
        antes = s.estado()
        self.assertFalse(antes["camaraConfigurada"])           # banco de exemplo: o painel pergunta a cidade
        vereador = s.entrar("parlamentar", antes["parls"][0]["id"])

        self.assertEqual(s.req("/api/cidade", {"uf": "MA", "municipio": "x"})["HTTP"], 401)          # só o presidente
        self.assertEqual(s.req("/api/cidade", {"uf": "XX", "municipio": "Vila"}, P)["HTTP"], 400)     # estado inválido
        r = s.req("/api/cidade", {"uf": "MA", "municipio": "Lugar Nenhum"}, P)
        self.assertEqual(r["HTTP"], 400)
        self.assertIn("não encontrado", r["erro"])

        r = s.req("/api/cidade", {"uf": "MA", "municipio": "vila esperanca"}, P)                    # sem acento e minúsculas
        self.assertEqual(r, {"ok": True, "nome": "Câmara Municipal de Vila Esperança", "vereadores": 3, "fotos": 2,
                             "populacao": 12345})
        e = s.estado()
        self.assertTrue(e["camaraConfigurada"])
        # brasão automático: Wikidata pelo código IBGE (9900001) e o arquivo no Commons, de domínio público
        self.assertGreater(e["casa"]["logoV"], 0)
        self.assertEqual(e["casa"]["brasaoFonte"], "https://commons.wikimedia.org/wiki/File:Bras%C3%A3o%20Vila.png")
        with urllib.request.urlopen(f"{s.base}/imagem/logo?v={e['casa']['logoV']}", timeout=5) as f:
            self.assertEqual(f.read(), BRASAO)
        # portal legislativomunicipal.com descoberto pelo nome do município (a instalação importa de lá)
        base = s.base.rsplit(":", 1)[0]
        self.assertTrue(e["casa"]["portal"].endswith("/portal-vilaesperanca"), e["casa"].get("portal"))
        self.assertEqual(e["rodape"]["camara"]["site"], e["casa"]["portal"])
        self.assertEqual(e["casa"]["municipio"], "Vila Esperança")
        self.assertEqual(e["casa"]["uf"], "MA")
        self.assertEqual([(p["nome"], p["partido"]) for p in e["parls"]],
                         [("Ana Pereira", "BBB"), ("Joao dos Santos", "BBB"), ("Maria da Silva", "AAA")])   # suplente fora
        self.assertEqual(e["pauta"], [])
        self.assertEqual(e["relevancia"]["populacao"], 12345)
        com_foto = [p for p in e["parls"] if p["fotoV"]]
        self.assertEqual(len(com_foto), 2)
        with urllib.request.urlopen(f"{s.base}/imagem/{com_foto[0]['id']}?v={com_foto[0]['fotoV']}", timeout=5) as f:
            self.assertEqual(f.read(), FOTO)

        # o login do vereador de exemplo deixa de valer; os novos entram com PIN inicial próprio (troca obrigatória)
        self.assertEqual(s.acao(vereador, "presenca")["HTTP"], 401)
        novo = s.req("/api/entrar", {"papel": "parlamentar", "pid": e["parls"][0]["id"],
                                     "pin": s.pin_inicial(e["parls"][0]["id"])})
        self.assertTrue(novo["ator"]["deveTrocar"])

        # depois de configurada, não troca mais os vereadores por aqui
        r = s.req("/api/cidade", {"uf": "MA", "municipio": "Vila Esperança"}, P)
        self.assertEqual(r["HTTP"], 409)


class BrasaoAutomatico(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib
        import os
        import sys
        from pathlib import Path
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), TseFalso)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.fake.server_address[1]}"
        os.environ.update(LEGIS_WIKIDATA_URL=base + "/wikidata", LEGIS_COMMONS_URL=base + "/commons")
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
        import cidade
        cls.cidade = importlib.reload(cidade)

    @classmethod
    def tearDownClass(cls):
        import os
        for k in ("LEGIS_WIKIDATA_URL", "LEGIS_COMMONS_URL"):
            os.environ.pop(k, None)
        cls.fake.shutdown()
        cls.fake.server_close()

    def setUp(self):
        self.addCleanup(LICENCA.update, valor="Public domain")
        self.log = []

    def test_dominio_publico_entra(self):
        b = self.cidade.brasao("9900001", self.log.append)
        self.assertEqual((b["mime"], b["dados"], b["licenca"]), ("image/png", BRASAO, "Public domain"))

    def test_licenca_que_pede_credito_nao_entra(self):
        LICENCA["valor"] = "CC BY-SA 4.0"
        self.assertIsNone(self.cidade.brasao("9900001", self.log.append))
        self.assertIn("pede crédito", " ".join(self.log))

    def test_sem_brasao_no_wikidata_e_codigo_invalido(self):
        self.assertIsNone(self.cidade.brasao("9900002", self.log.append))
        self.assertIsNone(self.cidade.brasao("abc", self.log.append))
        self.assertIsNone(self.cidade.brasao('1" } DROP', self.log.append))   # nada de injeção na consulta

    def test_fora_do_ar_nao_derruba(self):
        import os
        os.environ["LEGIS_WIKIDATA_URL"] = "http://127.0.0.1:9/sparql"
        try:
            c = __import__("importlib").reload(self.cidade)
            self.assertIsNone(c.brasao("9900001", self.log.append))
        finally:
            os.environ["LEGIS_WIKIDATA_URL"] = f"http://127.0.0.1:{self.fake.server_address[1]}/wikidata"
            type(self).cidade = __import__("importlib").reload(self.cidade)


if __name__ == "__main__":
    unittest.main()
