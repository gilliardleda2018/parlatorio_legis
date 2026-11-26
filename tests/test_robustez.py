"""Robustez das regras: ação que falha não deixa nada pela metade, a thread das regras automáticas
não morre num erro e booleanos vindos como texto são lidos certo.

Diferente dos outros testes, este importa o servidor no próprio processo (banco temporário), para
poder injetar falhas no meio de uma ação ou de uma volta do tique.
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

_PASTA = tempfile.TemporaryDirectory(prefix="parlatorio-robustez-")
_AMBIENTE = dict(os.environ)  # o servidor lê o ambiente ao ser importado; depois ele volta ao que era,
os.environ.update(PARLATORIO_BANCO=f"{_PASTA.name}/teste.db", PIN_PRESIDENTE="1234", TROCA_PIN="0", HTTPS="0",
                  TRANSCRICAO="0", BACKUP_HORA="", BACKUP_DIR=f"{_PASTA.name}/backups")
for _k in ("ESTACAO_SENHA", "NEXTCLOUD_URL"):
    os.environ.pop(_k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import servidor as sv  # noqa: E402  (depende das variáveis acima)

os.environ.clear()
os.environ.update(_AMBIENTE)  # para não vazar TROCA_PIN=0 etc. aos servidores que os outros testes sobem

sv.seguranca.ITERACOES = 1000  # estado_inicial() refaz o hash dos 15 PINs a cada teste; aqui não precisa ser lento


def setUpModule():
    global HTTP, BASE
    HTTP = ThreadingHTTPServer(("127.0.0.1", 0), sv.Manipulador)
    HTTP.daemon_threads = True
    BASE = f"http://127.0.0.1:{HTTP.server_address[1]}"
    threading.Thread(target=HTTP.serve_forever, daemon=True).start()


def tearDownModule():
    HTTP.shutdown()
    HTTP.server_close()
    _PASTA.cleanup()


def post(caminho, corpo, token=None):
    cab = {"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})}
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + caminho, json.dumps(corpo).encode(), cab), timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class Base(unittest.TestCase):
    def setUp(self):
        with sv.trava:
            sv.S.clear()
            sv.S.update(sv.estado_inicial())
            sv.db.execute("delete from projetos")
            sv.db.execute("delete from sessoes")
            sv.db.commit()
        codigo, j = post("/api/entrar", {"papel": "presidente", "pin": "1234"})
        self.assertEqual(codigo, 200, j)
        self.P = j["token"]

    def acao(self, nome, **args):
        return post("/api/acao", {"nome": nome, "args": args}, self.P)


class AcaoTudoOuNada(Base):
    def test_erro_de_tipo_no_meio_responde_e_nao_altera_nada(self):
        antes = sv.S["casa"]["nome"]
        codigo, j = self.acao("configSalvar", casa={"nome": "ALTERADA"}, cfg={}, fases=[{"nome": "X", "min": "abc"}], partidos={})
        self.assertEqual((codigo, j.get("erro")), (400, "Parâmetros inválidos."))
        self.assertEqual(sv.S["casa"]["nome"], antes)

    def test_banco_desfeito_quando_a_acao_falha(self):
        def grava_e_falha(ator):
            sv.proj_salvar(sv.proj_novo(id="fantasma", numero="PL 1/2026", ementa="Não deveria ficar no banco."))
            raise sv.Erro("falhou depois de gravar")
        with mock.patch.dict(sv.ACOES, {"testeGravaEFalha": ({"presidente"}, grava_e_falha)}):
            codigo, j = self.acao("testeGravaEFalha")
        self.assertEqual((codigo, j["erro"]), (400, "falhou depois de gravar"))
        self.assertIsNone(sv.proj_obter("fantasma"))

    def test_banco_gravado_quando_a_acao_da_certo(self):
        self.assertEqual(self.acao("projetoSalvar", numero="PL 7/2026", ementa="Cria hortas nas escolas.")[0], 200)
        self.assertEqual([p["numero"] for p in sv.proj_todos()], ["PL 7/2026"])

    def test_erro_inesperado_vira_500_sem_alterar(self):
        def quebra(ator):
            sv.S["casa"]["nome"] = "pela metade"
            raise RuntimeError("bug")
        with mock.patch.dict(sv.ACOES, {"testeQuebra": ({"presidente"}, quebra)}), mock.patch("sys.stderr"):
            codigo, _ = self.acao("testeQuebra")
        self.assertEqual(codigo, 500)
        self.assertNotEqual(sv.S["casa"]["nome"], "pela metade")

    def test_args_que_nao_sao_objeto(self):
        self.assertEqual(post("/api/acao", {"nome": "proximo", "args": [1, 2]}, self.P)[0], 400)


class Booleanos(Base):
    FASES = [{"id": "pe", "nome": "Pequeno Expediente", "min": 5}]

    def test_texto_false_vira_falso(self):
        self.assertEqual(self.acao("configSalvar", casa={}, cfg={"som": "false", "corte": "true"}, fases=self.FASES, partidos={})[0], 200)
        self.assertIs(sv.S["cfg"]["som"], False)
        self.assertIs(sv.S["cfg"]["corte"], True)

    def test_texto_invalido_recusa_sem_alterar(self):
        self.assertEqual(self.acao("configSalvar", casa={}, cfg={"som": "talvez"}, fases=self.FASES, partidos={})[0], 400)
        self.assertIs(sv.S["cfg"]["som"], True)

    def test_mesa_e_sessao_remota(self):
        cfg = dict(sv.S["mesa"], reenviar="false")
        self.assertEqual(self.acao("mesaSalvar", cfg=cfg)[0], 200)
        self.assertIs(sv.S["mesa"]["reenviar"], False)
        self.assertEqual(self.acao("remotoConfigurar", ativo="false")[0], 200)
        self.assertIs(sv.S["remoto"]["ativo"], False)


class Tique(Base):
    def volta(self):
        sv._tique({"gravacoes": time.time(), "erro": None})

    def _virtual_vencida(self):
        self.assertEqual(self.acao("projetoSalvar", numero="PL 9/2026", ementa="Cria a tarifa social de transporte.")[0], 200)
        pid_proj = sv.proj_todos()[0]["id"]
        inicio = sv.agora() + 60_000
        self.assertEqual(self.acao("virtualCriar", titulo="Virtual 1", inicio=inicio, fim=inicio + 3600_000)[0], 200)
        vid = sv.S["virtuais"][0]["id"]
        self.assertEqual(self.acao("virtualItem", vid=vid, projetoId=pid_proj)[0], 200)
        with sv.trava:
            v = sv.S["virtuais"][0]
            v["inicio"], v["fim"] = sv.agora() - 7200_000, sv.agora() - 1000
        return pid_proj

    def test_apuracao_que_falha_nao_deixa_nada_pela_metade(self):
        pid_proj = self._virtual_vencida()
        historico = sv.proj_obter(pid_proj)["historico"]
        with mock.patch.object(sv, "resumo", side_effect=RuntimeError("falha no fim da apuração")):
            with self.assertRaises(RuntimeError):
                self.volta()
        self.assertEqual(sv.proj_obter(pid_proj)["historico"], historico)
        self.assertIsNone(sv.S["virtuais"][0]["encerrada"])
        self.volta()  # na volta seguinte apura, uma vez só
        self.assertTrue(sv.S["virtuais"][0]["encerrada"])
        self.assertEqual(len([h for h in sv.proj_obter(pid_proj)["historico"] if h["evento"] == "votacao"]), 1)

    def test_thread_continua_depois_de_um_erro(self):
        voltou, chamadas = threading.Event(), []

        def falso(estado):
            chamadas.append(1)
            if len(chamadas) == 1:
                raise RuntimeError("erro numa volta")
            voltou.set()
            raise SystemExit  # encerra a thread do teste

        with mock.patch.object(sv, "_tique", side_effect=falso), mock.patch("sys.stderr"):
            threading.Thread(target=sv.tique, daemon=True).start()
            self.assertTrue(voltou.wait(3), "a thread do tique morreu no primeiro erro")

    def test_gravacoes_conferidas_uma_vez_a_cada_5_s(self):
        estado = {"gravacoes": 0.0, "erro": None}
        with mock.patch.object(sv, "grav_da_sessao", return_value=[]) as conferir:
            for _ in range(8):  # 8 voltas no mesmo segundo
                sv._tique(estado)
        self.assertEqual(conferir.call_count, 1)

    def test_tempo_esgotado_corta_o_microfone(self):
        pid = sv.S["parls"][0]["id"]
        self.acao("chamar", pid=pid)
        self.acao("iniciar")
        with sv.trava:
            sv.S["orador"]["start"] -= sv.S["orador"]["total"] + 1000
        self.volta()
        self.assertTrue(sv.S["orador"]["esgotou"])
        self.assertFalse(sv.S["orador"]["mic"])
        self.assertEqual(sv.S["ocorrencias"][0]["tipo"], "tempo_esgotado")


if __name__ == "__main__":
    unittest.main()
