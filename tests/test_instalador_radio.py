import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from apoio import RAIZ

spec = importlib.util.spec_from_file_location("provisionar_radio", RAIZ / "deploy/azuracast/provisionar.py")
radio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(radio)


class InstaladorRadio(unittest.TestCase):
    def test_estacao_e_operador_sao_criados_no_primeiro_acesso(self):
        azura = radio.AzuraCast(19080)
        azura.esperar = Mock(return_value=(
            "/setup/register", """window.vueComponent('#setup-register', {"componentProps": {"csrf": "cadastro"}});"""
        ))
        azura.requisitar = Mock(side_effect=[
            ("/setup/station", """window.vueComponent('#setup-station', {"apiCsrf": "sessao"});"""),
            ("/api/admin/stations", {"rows": []}),
            ("/api/admin/stations", {"id": 1, "short_name": "radio_da_camara"}),
            ("/api/admin/settings/general", {}),
            ("/api/station/1/streamers", []),
            ("/api/station/1/streamers", {"id": 1}),
            ("/api/station/1/restart", {}),
        ])
        credenciais = {"email": "test@example.org", "senha_admin": "admin-teste", "senha_dj": "radio-teste"}
        self.assertEqual(azura.configurar(credenciais), "radio_da_camara")
        chamadas = azura.requisitar.call_args_list
        self.assertEqual(chamadas[0].args[1]["csrf"], "cadastro")
        self.assertTrue(chamadas[2].args[1]["enable_streamers"])
        self.assertTrue(chamadas[3].args[1]["use_radio_proxy"])
        self.assertEqual(chamadas[5].args[1]["streamer_username"], "legis")
        self.assertEqual(azura.csrf, "sessao")

    def test_credenciais_e_portas_sao_reutilizadas_e_expostas_so_no_loopback(self):
        with tempfile.TemporaryDirectory() as pasta:
            dados = radio.configuracao(Path(pasta))
            self.assertEqual(radio.configuracao(Path(pasta)), dados)
        self.assertNotEqual(dados["web"], dados["dj"])
        self.assertNotEqual(dados["senha_admin"], dados["senha_dj"])
        compose = radio.especificacao_compose(dados)
        self.assertEqual(compose["services"]["web"]["ports"],
                         [f"127.0.0.1:{dados['web']}:80", f"127.0.0.1:{dados['dj']}:8005"])
