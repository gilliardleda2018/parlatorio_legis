"""Primeira configuração pelo painel: de qual cidade é esta Câmara (TSE e IBGE simulados, nomes fictícios)."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from apoio import RAIZ, Servidor

FOTO = b"\xff\xd8\xff\xe0" + b"j" * 200   # começo de um JPEG
CONFIG = {"abr": [{"cd": "ma", "mu": [{"cd": "00001", "cdi": "9900001", "nm": "VILA ESPERANÇA"}]}]}
RESULTADO = {"dg": "06/10/2024", "carg": [{"nv": 3, "agr": [{"par": [
    {"sg": "AAA", "cand": [{"nmu": "MARIA DA SILVA", "n": 11111, "vap": "900", "sqcand": "1", "st": "Eleito por QP"},
                           {"nmu": "JOSE SUPLENTE", "n": 11112, "vap": "10", "sqcand": "2", "st": "Suplente"}]},
    {"sg": "BBB", "cand": [{"nmu": "JOAO DOS SANTOS", "n": 22222, "vap": "700", "sqcand": "3", "st": "Eleito por média"},
                           {"nmu": "ANA PEREIRA", "n": 22223, "vap": "650", "sqcand": "4", "st": "Eleito por QP"}]}]}]}]}
IBGE = [{"resultados": [{"series": [{"serie": {"2022": "12345"}}]}]}]
ROTAS = {"/tse/619/config/mun-e000619-cm.json": json.dumps(CONFIG).encode(),
         "/tse/619/dados/ma/ma00001-c0013-e000619-u.json": json.dumps(RESULTADO).encode(),
         "/tse/619/fotos/ma/1.jpeg": FOTO, "/tse/619/fotos/ma/3.jpeg": FOTO}


class TseFalso(BaseHTTPRequestHandler):
    def do_GET(self):
        caminho = self.path.split("?")[0]
        corpo = json.dumps(IBGE).encode() if caminho.startswith("/ibge/v3/agregados/4709/") else ROTAS.get(caminho)
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
        cls.s = Servidor(LEGIS_TSE_URL=base + "/tse", LEGIS_IBGE_URL=base + "/ibge")
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

        # o login do vereador de exemplo deixa de valer; os novos entram com 0000 (troca obrigatória)
        self.assertEqual(s.acao(vereador, "presenca")["HTTP"], 401)
        novo = s.req("/api/entrar", {"papel": "parlamentar", "pid": e["parls"][0]["id"], "pin": "0000"})
        self.assertTrue(novo["ator"]["deveTrocar"])

        # depois de configurada, não troca mais os vereadores por aqui
        r = s.req("/api/cidade", {"uf": "MA", "municipio": "Vila Esperança"}, P)
        self.assertEqual(r["HTTP"], 409)


    def test_importacao_do_instalador_preserva_o_pin_e_os_dados_apos_a_configuracao(self):
        base = f"http://127.0.0.1:{self.fake.server_address[1]}"
        env = dict(os.environ, LEGIS_TSE_URL=base + "/tse", LEGIS_IBGE_URL=base + "/ibge", TRANSCRICAO="0")
        servidor = Servidor(PIN_PRESIDENTE="735915")
        try:
            token = servidor.req("/api/entrar", {"papel": "presidente", "pin": "735915"})["token"]
            resposta = servidor.req("/api/trocar-pin", {"atual": "735915", "novo": "563279"}, token)
            self.assertTrue(resposta["ok"])
            servidor.proc.terminate()
            servidor.proc.wait(5)
            banco = Path(servidor.dir.name) / "teste.db"
            marcador = banco.parent / ".sem-municipio"
            marcador.touch()
            comando = [sys.executable, str(RAIZ / "scripts/tse_eleitos.py"), "--configurar", "MA", "Vila Esperança", str(banco)]
            resultado = subprocess.run(comando, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
            self.assertFalse(marcador.exists())
            with sqlite3.connect(banco) as conexao:
                figura = json.loads(conexao.execute("select json from estado where id = 1").fetchone()[0])
                self.assertEqual(figura["casa"]["municipio"], "Vila Esperança")
                self.assertEqual(len(figura["parls"]), 3)
                self.assertTrue(figura["_pinPresidente"].startswith("pbkdf2$"))
                self.assertIn("Vila Esperança", resultado.stdout)

            marcador.touch()  # marcador antigo, deixado por uma versão anterior
            repeticao = subprocess.run(comando, env=env, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(repeticao.returncode, 0)
            with sqlite3.connect(banco) as conexao:
                atual = json.loads(conexao.execute("select json from estado where id = 1").fetchone()[0])
                self.assertEqual(atual["parls"], figura["parls"])
                self.assertEqual(atual["_pinPresidente"], figura["_pinPresidente"])
        finally:
            servidor.parar()


if __name__ == "__main__":
    unittest.main()
