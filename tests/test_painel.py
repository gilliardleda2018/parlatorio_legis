"""Painel dos pacotes: cria uma Câmara, gera o pacote dela (TSE simulado) e protege as ações."""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from apoio import RAIZ, porta_livre
from test_cidade import TseFalso

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


@unittest.skipUnless(shutil.which("dpkg-deb"), "precisa do dpkg-deb")
class PainelDosPacotes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), TseFalso)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.fake.server_address[1]}"
        cls.pasta = tempfile.TemporaryDirectory()
        cls.porta = porta_livre()
        env = {**os.environ, "PAINEL_CAMARAS": f"{cls.pasta.name}/camaras", "PAINEL_DIST": f"{cls.pasta.name}/dist",
               "PAINEL_PORTA": str(cls.porta), "LEGIS_TSE_URL": base + "/tse", "LEGIS_IBGE_URL": base + "/ibge"}
        cls.proc = subprocess.Popen([sys.executable, str(RAIZ / "packaging" / "painel.py"), "--sem-navegador"], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = f"http://127.0.0.1:{cls.porta}"
        for _ in range(50):
            try:
                pagina = urllib.request.urlopen(cls.base + "/", timeout=1).read().decode()
                break
            except OSError:
                time.sleep(0.1)
        cls.token = re.search(r"const TOKEN = '([^']+)'", pagina).group(1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.fake.shutdown()
        cls.fake.server_close()
        cls.pasta.cleanup()

    def req(self, caminho, corpo=None, token=True, host=None):
        cab = {"Content-Type": "application/json", **({"X-Painel": self.token} if token else {}), **({"Host": host} if host else {})}
        r = urllib.request.Request(self.base + caminho, json.dumps(corpo).encode() if corpo is not None else None, cab)
        try:
            with urllib.request.urlopen(r, timeout=20) as x:
                return json.loads(x.read())
        except urllib.error.HTTPError as e:
            return {"HTTP": e.code, **json.loads(e.read() or b"{}")}

    def test_cria_camara_e_gera_pacote(self):
        self.assertEqual(self.req("/api/estado", token=False)["HTTP"], 403)                      # sem o código do painel
        self.assertEqual(self.req("/api/estado", host="exemplo.com:80")["HTTP"], 403)            # outro site via DNS
        self.assertEqual(self.req("/api/camaras", {"uf": "XX", "municipio": "Vila"})["HTTP"], 400)
        r = self.req("/api/camaras", {"uf": "MA", "municipio": "Vila Esperança", "youtube": "https://www.youtube.com/@vila/live",
                                      "brasao": "data:image/png;base64," + base64.b64encode(PNG).decode()})
        self.assertEqual(r["camara"]["nome"], "vila-esperanca")
        tid = r["tarefa"]["id"]
        for _ in range(120):
            t = self.req(f"/api/tarefas/{tid}")
            if t["estado"] in ("concluída", "erro"):
                break
            time.sleep(0.5)
        self.assertEqual(t["estado"], "concluída", "\n".join(t["log"][-20:]))
        e = self.req("/api/estado")
        c = e["camaras"][0]
        self.assertTrue(c["temBrasao"])
        self.assertTrue(c["atualizado"])
        nomes = [a["nome"] for a in c["arquivos"]]
        deb = next(n for n in nomes if n.endswith("_all.deb"))
        self.assertTrue(deb.startswith("legis-vila-esperanca_"))
        self.assertTrue(any(n.endswith(".tar.gz") for n in nomes))
        info = subprocess.run(["dpkg-deb", "-f", f"{self.pasta.name}/dist/cidades/vila-esperanca/{deb}", "X-Legis-Cidade"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(info, "Câmara Municipal de Vila Esperança (Vila Esperança/MA)")
        with urllib.request.urlopen(self.base + c["arquivos"][0]["url"], timeout=10) as f:      # download
            self.assertGreater(len(f.read()), 1000)
        self.assertEqual(self.req("/api/camaras", {"uf": "MA", "municipio": "Vila Esperança"})["HTTP"], 400)   # já existe
        self.assertEqual(self.req("/api/camaras/vila-esperanca/remover", {}), {"ok": True})
        self.assertEqual(self.req("/api/estado")["camaras"], [])
        self.assertFalse(Path(f"{self.pasta.name}/dist/cidades/vila-esperanca").exists())


if __name__ == "__main__":
    unittest.main()
