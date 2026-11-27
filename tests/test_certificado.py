"""Primeiro acesso de cada aparelho sem aviso de certificado: a página /certificado (Windows, Apple, Android, Linux),
o instalador .cmd, o perfil da Apple, o certificado renovado sozinho quando muda o IP (mesma autoridade) e o
https:// conferido de verdade contra a autoridade da Câmara."""
import os
import plistlib
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import certificado  # noqa: E402
import tls  # noqa: E402
from apoio import Servidor, porta_livre  # noqa: E402

TEM_OPENSSL = bool(shutil.which("openssl"))


def ca_teste(pasta: Path) -> bytes:
    tls.gerar(pasta)
    return (pasta / "ca.crt").read_bytes()


class Pagina(unittest.TestCase):
    def test_host_seguro(self):
        self.assertEqual(certificado.host_seguro("192.168.0.50:8080"), "192.168.0.50")
        self.assertEqual(certificado.host_seguro("arame.local"), "arame.local")
        self.assertEqual(certificado.host_seguro("[fe80::1]:80"), "[fe80::1]")
        for ruim in ("<script>", "a b", "x..y", "evil.com/x", ""):
            self.assertEqual(certificado.host_seguro(ruim), "", ruim)

    def test_endereco_https(self):
        self.assertEqual(certificado.endereco_https("192.168.0.50", 443), "https://192.168.0.50/")
        self.assertEqual(certificado.endereco_https("arame.local", 8443), "https://arame.local:8443/")

    def test_pagina_reconhece_aparelhos_e_testa_o_https(self):
        html = certificado.pagina("https://192.168.0.50/", "Câmara Municipal de Arame").decode()
        for trecho in ('"https://192.168.0.50/"', "/certificado/windows.cmd", "/certificado/legis.mobileconfig",
                       "/certificado.crt", 'data-so="android"', "certificado/ok", "Abrir o LEGIS", "Câmara Municipal de Arame"):
            self.assertIn(trecho, html)


@unittest.skipUnless(TEM_OPENSSL, "openssl ausente")
class Arquivos(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.pem = ca_teste(Path(self.dir.name))

    def test_windows_cmd_instala_sem_admin_e_abre(self):
        cmd = certificado.windows_cmd(self.pem, "https://192.168.0.50/").decode("ascii")
        self.assertIn("\r\n", cmd)
        self.assertIn('certutil -user -addstore Root "%CRT%"', cmd)
        self.assertIn('start "" "https://192.168.0.50/"', cmd)
        linhas_pem = [l for l in self.pem.decode().splitlines() if l.strip()]
        for l in linhas_pem:
            self.assertIn(f'echo {l}', cmd)
        # o PEM que o .cmd escreve é o mesmo da autoridade
        escrito = [l.split(" echo ", 1)[1] for l in cmd.split("\r\n") if '"%CRT%" echo ' in l]
        self.assertEqual(escrito, linhas_pem)

    def test_perfil_da_apple(self):
        perfil = plistlib.loads(certificado.mobileconfig(self.pem, "Câmara Municipal de Arame"))
        conteudo = perfil["PayloadContent"][0]
        self.assertEqual(conteudo["PayloadType"], "com.apple.security.root")
        self.assertEqual(conteudo["PayloadContent"], certificado.pem_para_der(self.pem))
        self.assertIn("Arame", perfil["PayloadDisplayName"])


@unittest.skipUnless(TEM_OPENSSL, "openssl ausente")
class Renovacao(unittest.TestCase):
    def test_ip_novo_renova_com_a_mesma_autoridade(self):
        with tempfile.TemporaryDirectory() as d:
            pasta = Path(d)
            tls.gerar(pasta)
            ca = (pasta / "ca.crt").read_bytes()
            crt = pasta / "servidor.crt"
            self.assertFalse(tls.precisa_renovar(crt))
            with mock.patch.dict(os.environ, {"TLS_NOMES": "10.99.88.77,camara-teste.local"}):
                self.assertTrue(tls.precisa_renovar(crt))
                tls.gerar(pasta)  # sozinho, sem renovar=True
                self.assertFalse(tls.precisa_renovar(crt))
                nomes = tls.nomes_atuais(crt)
            self.assertIn("IP:10.99.88.77", nomes)
            self.assertIn("DNS:camara-teste.local", nomes)
            self.assertEqual((pasta / "ca.crt").read_bytes(), ca)
            r = subprocess.run(["openssl", "verify", "-CAfile", str(pasta / "ca.crt"), str(crt)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_vence_logo_renova(self):
        with tempfile.TemporaryDirectory() as d:
            crt, _ = tls.gerar(Path(d))
            self.assertFalse(tls.precisa_renovar(crt, dias=30))
            self.assertTrue(tls.precisa_renovar(crt, dias=900))  # o do servidor vale 825 dias


@unittest.skipUnless(TEM_OPENSSL, "openssl ausente")
class PeloServidor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.https = porta_livre()
        cls.srv = Servidor(HTTPS="auto", PORTA_HTTPS=str(cls.https))
        cls.tls = Path(cls.srv.dir.name) / "tls"

    @classmethod
    def tearDownClass(cls):
        cls.srv.parar()

    def get(self, caminho, accept=None):
        r = urllib.request.Request(self.srv.base + caminho, headers={"Accept": accept} if accept else {})
        with urllib.request.urlopen(r, timeout=10) as x:
            return x.status, x.headers, x.read()

    def test_pagina_para_navegador_e_arquivo_para_curl(self):
        status, headers, corpo = self.get("/certificado", "text/html,application/xhtml+xml")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(f'"https://127.0.0.1:{self.https}/"'.encode(), corpo)
        _, headers, corpo = self.get("/certificado")
        self.assertEqual(corpo, (self.tls / "ca.crt").read_bytes())
        self.assertIn("x509", headers["Content-Type"])
        self.assertEqual(self.get("/certificado.crt")[2], (self.tls / "ca.crt").read_bytes())

    def test_instaladores(self):
        _, headers, cmd = self.get("/certificado/windows.cmd")
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn(f'start "" "https://127.0.0.1:{self.https}/"'.encode(), cmd)
        _, _, perfil = self.get("/certificado/legis.mobileconfig")
        self.assertEqual(plistlib.loads(perfil)["PayloadContent"][0]["PayloadType"], "com.apple.security.root")

    def test_https_confiavel_com_a_autoridade_da_camara(self):
        """O que o aparelho faz depois de instalar: confia só na autoridade da Câmara e abre o https:// sem aviso."""
        ctx = ssl.create_default_context(cafile=str(self.tls / "ca.crt"))
        with urllib.request.urlopen(f"https://127.0.0.1:{self.https}/certificado/ok", context=ctx, timeout=10) as x:
            self.assertEqual(x.status, 204)
        nome = socket.gethostname()
        self.assertIn(f"DNS:{nome}.local", tls.nomes_atuais(self.tls / "servidor.crt"))
        # sem a autoridade instalada, o navegador recusa (o aviso de certificado)
        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen(f"https://127.0.0.1:{self.https}/certificado/ok", context=ssl.create_default_context(),
                                   timeout=10)


if __name__ == "__main__":
    unittest.main()
