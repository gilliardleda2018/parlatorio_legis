import json
import socket
import sqlite3
import struct
import threading
import time
import unittest

from apoio import TesteComServidor


class Votacao(TesteComServidor):
    def test_votacao_nominal_e_secreta(self):
        s, P, ids = self.s, self.P, self.ids
        tokens = [s.entrar("parlamentar", pid) for pid in ids[:9]]
        for t in tokens:
            self.ok(s.acao(t, "presenca"))
        pauta = s.estado()["pauta"]
        self.ok(s.acao(P, "votacaoAbrir", materia_id=pauta[0]["id"], tipo="nominal", quorum="simples"))
        for k, t in enumerate(tokens):
            self.ok(s.acao(t, "votar", valor="sim" if k < 6 else "nao"))
        self.erro(s.acao(P, "votarPor", pid=ids[12], valor="sim"), 400, "presentes")  # ausente não vota
        self.ok(s.acao(P, "votacaoEncerrar"))
        v = s.estado()["votacao"]
        self.assertEqual((v["resultado"], v["contagem"]["sim"], v["contagem"]["nao"]), ("aprovada", 6, 3))
        self.ok(s.acao(P, "votacaoLimpar"))
        # secreta: voto único e escondido enquanto aberta
        self.ok(s.acao(P, "votacaoAbrir", materia_id=pauta[1]["id"], tipo="secreta", quorum="absoluta"))
        self.ok(s.acao(tokens[0], "votar", valor="nao"))
        self.erro(s.acao(tokens[0], "votar", valor="sim"), 400, "já registrado")
        publico = s.estado()["votacao"]
        self.assertEqual(publico["votos"], {})
        self.assertIsNone(publico["contagem"])
        self.ok(s.acao(P, "votacaoEncerrar"))
        self.assertEqual(s.estado()["votacao"]["resultado"], "rejeitada")

        with sqlite3.connect(f"{s.dir.name}/teste.db") as db:
            registros = [json.loads(args) for args, in db.execute("select args from eventos where acao = 'votar' order by rowid")]
        self.assertEqual(registros[0]["valor"], "sim")
        self.assertNotIn("valor", registros[-1])


class OrdemDoDia(TesteComServidor):
    def test_fluxo_da_pauta_ao_arquivo(self):
        s, P = self.s, self.P
        pauta = s.estado()["pauta"]
        expediente, deliberacao, ultima = [m["id"] for m in pauta]
        atual = s.req("/api/sessao-publica/atual")
        self.assertEqual([m["secao"] for m in atual["pauta"]], ["ordem_do_dia"] * 3)
        self.erro(s.acao(P, "materiaSalvar", numero="PL inválido", ementa="Teste", secao=[]), 400, "Seção")
        self.ok(s.acao(P, "materiaSalvar", id=expediente, numero=pauta[0]["numero"],
                       ementa=pauta[0]["ementa"], secao="expediente"))
        self.ok(s.acao(P, "materiaMover", id=expediente, d=1))
        self.assertEqual([m["id"] for m in s.estado()["pauta"]], [expediente, deliberacao, ultima])
        self.ok(s.acao(P, "materiaMover", id=deliberacao, d=1))
        self.assertEqual([m["id"] for m in s.estado()["pauta"]], [expediente, ultima, deliberacao])

        self.ok(s.acao(P, "setFase", id="od"))
        self.ok(s.acao(P, "setFase", id="od"))
        self.ok(s.acao(P, "materiaAtual", id=deliberacao))
        self.ok(s.acao(P, "votacaoAbrir", materia_id=deliberacao, tipo="simbolica", quorum="simples"))
        self.erro(s.acao(P, "materiaAtual", id=expediente), 400, "votação aberta")
        self.erro(s.acao(P, "materiaSituacao", id=deliberacao, situacao="adiada"), 400, "votação aberta")
        self.ok(s.acao(P, "votacaoEncerrar", resultado="aprovada"))
        self.ok(s.acao(P, "materiaAtual", id=expediente))
        self.assertIsNone(s.estado()["votacao"])
        t = s.entrar("parlamentar", self.ids[0])
        self.erro(s.acao(t, "materiaSituacao", id=expediente, situacao="adiada"), 403)
        self.ok(s.acao(P, "materiaSituacao", id=expediente, situacao="adiada", motivo=" Análise da comissão "))
        self.ok(s.acao(P, "sessaoEncerrar", proximoTitulo="Próxima", manterPauta=True))

        arquivada = s.req(f"/api/sessao-publica/{atual['id']}")
        self.assertEqual([e["tipo"] for e in arquivada["andamento"]],
                         ["fase", "discussao", "votacao_aberta", "votacao_encerrada", "discussao", "materia", "encerramento"])
        self.assertIn("Análise da comissão", arquivada["andamento"][-2]["rotulo"])
        self.assertEqual(arquivada["pauta"][0]["secao"], "expediente")
        self.assertEqual(arquivada["votacoes"][0]["resultado"], "aprovada")
        proxima = s.req("/api/sessao-publica/atual")
        self.assertEqual(proxima["andamento"], [])
        self.assertEqual([(m["id"], m["secao"], m["situacao"]) for m in proxima["pauta"]],
                         [(expediente, "expediente", "pendente"), (ultima, "ordem_do_dia", "pendente")])
        pdf = s.req(f"/api/ata/{atual['id']}.pdf", token=P, bruto=True)
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))


class Tribuna(TesteComServidor):
    def test_orador_aparte_congelar_e_tempo(self):
        s, P, ids = self.s, self.P, self.ids
        self.ok(s.acao(P, "inscrever", pid=ids[2]))
        self.ok(s.acao(P, "proximo"))
        self.ok(s.acao(P, "iniciar"))
        self.assertEqual(s.estado()["microfones"][ids[2]], "aberto")
        self.ok(s.acao(P, "interromper", tipo="aparte", pid=ids[4]))
        self.assertEqual(s.estado()["microfones"][ids[4]], "aberto")
        self.ok(s.acao(P, "encerrarInterr"))
        self.ok(s.acao(P, "congelar", motivo="tumulto"))
        e = s.estado()
        self.assertIsNone(e["orador"]["start"])
        self.assertEqual(e["microfones"][ids[2]], "fechado")
        self.erro(s.acao(P, "iniciar"), 400, "congelado")
        self.ok(s.acao(P, "descongelar"))
        self.ok(s.acao(P, "addTempo", seg=120))
        self.ok(s.acao(P, "cortarMic", pid=ids[6], cortar=True, motivo="ofensa"))
        self.erro(s.acao(P, "micManual", pid=ids[6], aberto=True), 400, "cortado")
        self.ok(s.acao(P, "encerrar"))
        hist = s.estado()["hist"]
        self.assertEqual(hist[0]["pid"], ids[2])
        self.assertEqual(hist[0]["apartes"][0]["tipo"], "aparte")
        tipos = [o["tipo"] for o in s.estado()["ocorrencias"]]
        self.assertIn("congelar", tipos)
        self.assertIn("corte", tipos)


class MesaDeSom(TesteComServidor):
    def test_osc_abre_e_fecha_canal(self):
        s, P, ids = self.s, self.P, self.ids
        recebidos = []
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(0.2)
        porta = sock.getsockname()[1]
        parar = threading.Event()

        def ouvir():
            while not parar.is_set():
                try:
                    d, _ = sock.recvfrom(1024)
                except socket.timeout:
                    continue
                recebidos.append((d[:d.index(b"\0")].decode(), struct.unpack(">i", d[-4:])[0]))
        threading.Thread(target=ouvir, daemon=True).start()
        try:
            self.ok(s.acao(P, "mesaSalvar", cfg={"driver": "osc", "host": "127.0.0.1", "porta": porta, "reenviar": False}))
            time.sleep(0.5)
            recebidos.clear()
            self.ok(s.acao(P, "inscrever", pid=ids[2]))
            self.ok(s.acao(P, "proximo"))
            self.ok(s.acao(P, "iniciar"))
            time.sleep(0.5)
            self.assertIn(("/ch/03/mix/on", 1), recebidos)
            self.ok(s.acao(P, "congelar"))
            time.sleep(0.5)
            self.assertEqual(recebidos[-1], ("/ch/03/mix/on", 0))
        finally:
            parar.set()
            sock.close()
            s.acao(P, "mesaSalvar", cfg={"driver": "simulado"})


class SessoesEAta(TesteComServidor):
    def test_gravacao_arquivo_e_ata_pdf(self):
        s, P, ids = self.s, self.P, self.ids
        g = s.req("/api/gravacao/iniciar", {"pid": ids[0], "inicio": 1, "mime": "audio/webm"}, P)
        self.ok(s.enviar_bytes(f"/api/gravacao/{g['id']}/parte", b"\x1aE\xdf\xa3" + b"x" * 4000, P))
        self.ok(s.req(f"/api/gravacao/{g['id']}/fim", {}, P))
        with __import__("urllib.request").request.urlopen(f"{s.base}/gravacao/{g['id']}.webm") as audio:
            self.assertEqual(len(audio.read()), 4004)
        self.erro(s.req("/api/gravacao/iniciar", {"pid": ids[0], "mime": "audio/webm"}), 401)
        self.ok(s.acao(P, "presenca", pid=ids[0], presente=True))
        self.ok(s.acao(P, "sessaoEncerrar", proximoTitulo="46ª Sessão Ordinária", manterPauta=True))
        self.assertEqual(s.estado()["casa"]["titulo"], "46ª Sessão Ordinária")
        lista = s.req("/api/sessoes", token=P)["sessoes"]
        self.assertEqual(len(lista), 1)
        pdf = s.req(f"/api/ata/{lista[0]['id']}.pdf", token=P, bruto=True)
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.erro(s.req("/api/sessoes"), 401)


class PlenarioVirtual(TesteComServidor):
    def test_sessao_virtual_com_pin_destaque_e_apuracao(self):
        s, P, ids = self.s, self.P, self.ids
        tokens = [s.entrar("parlamentar", pid) for pid in ids[:10]]
        agora = int(time.time() * 1000)
        self.erro(s.acao(P, "virtualCriar", titulo="X", inicio=agora, fim=agora + 60000), 400, "1 hora")
        self.ok(s.acao(P, "virtualCriar", titulo="1ª Virtual", inicio=agora + 2500, fim=agora + 86400000))
        v = s.estado()["virtuais"][0]
        for m in s.estado()["pauta"][:3]:
            self.ok(s.acao(P, "virtualItem", vid=v["id"], projetoId=m["id"]))
        time.sleep(3)
        v = s.estado()["virtuais"][0]
        self.assertEqual(v["status"], "aberta")
        itens = [i["id"] for i in v["itens"]]
        pin = lambda k: s.pins[("parlamentar", ids[k])]
        self.erro(s.acao(tokens[0], "virtualVotar", vid=v["id"], itemId=itens[0], valor="sim", pin="0001"), 403, "PIN")
        for k, t in enumerate(tokens):
            self.ok(s.acao(t, "virtualVotar", vid=v["id"], itemId=itens[0], valor="sim" if k < 7 else "nao", pin=pin(k)))
        self.ok(s.acao(tokens[1], "virtualManifestar", vid=v["id"], itemId=itens[0], texto="Voto a favor pelo interesse público."))
        self.ok(s.acao(tokens[2], "virtualDestaque", vid=v["id"], itemId=itens[2], pin=pin(2)))
        self.erro(s.acao(P, "virtualVotar", vid=v["id"], itemId=itens[0], valor="sim"), 403)
        self.ok(s.acao(P, "virtualEncerrarAgora", vid=v["id"]))
        v = s.estado()["virtuais"][0]
        self.assertEqual([i["resultado"] for i in v["itens"]], ["aprovada", "sem_quorum", "destacado"])
        arquivada = s.req(f"/api/sessoes/{v['arquivo']}", token=P)
        self.assertEqual(len(arquivada["votacoes"]), 2)
        self.assertEqual(len(arquivada["presenca"]), 10)

    def test_sessao_remota_esconde_a_sala(self):
        s, P, ids = self.s, self.P, self.ids
        t = s.entrar("parlamentar", ids[3])
        self.erro(s.req("/api/remoto", token=t), 403)
        self.erro(s.acao(P, "remotoConfigurar", ativo=True, servidor="http://inseguro"), 400)
        self.ok(s.acao(P, "remotoConfigurar", ativo=True, servidor="https://meet.jit.si"))
        sala = s.req("/api/remoto", token=t)["sala"]
        self.assertTrue(sala.startswith("parlatorio-"))
        self.assertNotIn(sala, json.dumps(s.estado()))
        self.erro(s.req("/api/remoto"), 403)
        self.ok(s.acao(t, "presencaRemota"))
        self.assertEqual(s.estado()["presencaModo"][ids[3]], "remoto")


class TransparenciaERecomendacoes(TesteComServidor):
    def test_publico_sem_dados_internos(self):
        s, P = self.s, self.P
        d = s.req("/api/transparencia")
        self.assertFalse(d["interno"])
        self.assertTrue(all("blocoSugerido" not in p and "ocorrencias" not in p for p in d["parlamentares"]))
        self.erro(s.req("/api/transparencia?interno=1"), 401)
        self.assertTrue(s.req("/api/transparencia?interno=1", token=P)["interno"])
        perfil = s.req(f"/api/perfil/{self.ids[0]}-qualquer")
        self.assertEqual(perfil["parlamentar"]["id"], self.ids[0])
        self.erro(s.req("/api/perfil/inexistente"), 404)

    def test_recomendacoes_e_proposta(self):
        s, ids = self.s, self.ids
        t = s.entrar("parlamentar", ids[10])
        self.ok(s.acao(t, "perfilInteresses", areas=["assistencia"], temas="combate à fome e hortas nas escolas"))
        r = s.req("/api/recomendacoes", token=t)
        self.assertTrue(r["itens"][0]["motivos"])
        alvo = r["itens"][0]
        self.ok(s.acao(t, "propor", praticaId=alvo["id"], ementa=alvo["ementaSugerida"], beneficiados=100))
        self.erro(s.acao(t, "propor", praticaId=alvo["id"], ementa=alvo["ementaSugerida"]), 400, "já tem")
        publico = s.req("/api/transparencia")["projetos"]
        self.assertFalse(any(p["situacao"] == "proposta" for p in publico))

    def test_rodape_valida_links(self):
        s, P = self.s, self.P
        ag = {"site": "www.aggillintelligence.com.br", "instagram": "https://instagram.com/aggillisoftware"}
        self.erro(s.acao(P, "rodapeSalvar", camara={"site": "javascript:alert(1)"}, aggilli=ag), 400)
        self.erro(s.acao(P, "rodapeSalvar", camara={"email": "invalido"}, aggilli=ag), 400)
        self.ok(s.acao(P, "rodapeSalvar", camara={"site": "camara.leg.br"}, aggilli=ag))
        self.assertEqual(s.req("/api/transparencia")["rodape"]["camara"]["site"], "https://camara.leg.br")


class Backup(TesteComServidor):
    def test_backup_manual(self):
        s, P = self.s, self.P
        self.ok(s.acao(P, "backupAgora"))
        for _ in range(50):
            b = s.estado()["backup"]
            if b.get("ultimo") and not b.get("emAndamento"):
                break
            time.sleep(0.1)
        self.assertIsNone(b["erro"])
        self.assertTrue(b["arquivo"].endswith(".db.gz"))
        self.assertEqual(b["nextcloud"], "desligado")


if __name__ == "__main__":
    unittest.main()
