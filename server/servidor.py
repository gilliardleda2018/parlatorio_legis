#!/usr/bin/env python3
"""Parlatório Legis — servidor do plenário.

Só usa a biblioteca padrão do Python (3.10+):
  * HTTP estático para as telas em ../public
  * Server-Sent Events (/eventos) para empurrar o estado a todas as telas
  * POST /api/entrar e /api/acao para login e comandos
  * SQLite para guardar o estado, as imagens e o registro de auditoria

O servidor é a única fonte da verdade: as telas só enviam comandos e
recebem o estado. Os cronômetros guardam instantes (ms) do relógio do
servidor; cada tela corrige a diferença do próprio relógio com o campo
"agora" de cada mensagem.
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import math
import os
import queue
import re
import secrets
import socket
import ssl
import shutil
import subprocess
import sys
import sqlite3
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.parse
from urllib.parse import urlparse

import tls
import certificado as certificado_mod
from ata import gerar_ata
import estatisticas
import recomendacao
import sapl as sapl_cli
import cidade
import seguranca
import passkey
import backup as backup_mod
import transcricao
import transmissao
import youtube
import akn
import conhecimento
import ia
import legendas as legendas_mod
import participacao
import emendas as emendas_mod
import cidadania
import analise
import regimento
import resumo as resumo_mod
import integridade
import modelo_cidade
import tramitacao
from texto import _sem_acento
from mesa import CFG_PADRAO, DRIVERS as DRIVERS_MESA, PRESETS, Mesa

RAIZ = Path(__file__).resolve().parent.parent
PUBLICO = RAIZ / "public"
BANCO = Path(os.environ.get("PARLATORIO_BANCO", RAIZ / "dados" / "parlatorio.db"))
GRAVACOES = BANCO.parent / "gravacoes"
TLS_DIR = BANCO.parent / "tls"
PORTA_HTTPS = int(os.environ.get("PORTA_HTTPS", "8443"))
HTTPS = os.environ.get("HTTPS", "auto")  # auto: gera certificado local se faltar; 0: só HTTP
HTTPS_ATIVO = False
LIMITE_PARTE = 32 * 1024 * 1024
TIPOS_AUDIO = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a"}
PORTA = int(os.environ.get("PORTA", "8090"))
HOST = os.environ.get("HOST", "0.0.0.0")  # 127.0.0.1 quando há um proxy (Caddy, nginx) na frente
# Atrás de proxy, o IP real do visitante vem no X-Forwarded-For (usado no bloqueio por tentativas)
PROXY_CONFIAVEL = os.environ.get("PROXY_CONFIAVEL", "0") == "1"
# de onde o proxy conecta: em Docker, o túnel/proxy do host chega pelo gateway da rede (ex.: 172.26.0.1)
PROXY_IPS = [ipaddress.ip_network(x.strip(), strict=False)  # IPs ou faixas (ex.: rede Docker 172.26.0.0/16)
             for x in os.environ.get("PROXY_IPS", "127.0.0.1,::1").split(",") if x.strip()]


def _de_proxy(ip: str) -> bool:
    try:
        endereco = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(endereco in rede for rede in PROXY_IPS if rede.version == endereco.version)
# PIN inicial do painel: o de PIN_PRESIDENTE ou, sem ele, um aleatório criado na primeira partida (mostrado no log
# até a primeira troca). Depois da troca vale o salvo (com hash).
PIN_PRESIDENTE = os.environ.get("PIN_PRESIDENTE", "").strip()
# Período de testes: TROCA_PIN=0 deixa entrar com PIN padrão (0000) sem obrigar a troca. Em produção, deixe ligado.
TROCA_PIN_OBRIGATORIA = os.environ.get("TROCA_PIN", "1") != "0"


def url_segura(u) -> str:
    """Só endereços http(s) vindos de fora (SAPL etc.) vão para links das páginas: nunca javascript: e afins."""
    u = str(u or "").strip()
    return u if re.match(r"(?i)^https?://[^\s<>\"']+$", u) else ""


def novo_pin_inicial() -> str:
    """PIN inicial de cada conta: aleatório (6 dígitos, nunca óbvio). Em período de testes (TROCA_PIN=0), 0000."""
    if not TROCA_PIN_OBRIGATORIA:
        return "0000"
    while True:
        pin = f"{secrets.randbelow(10 ** 6):06d}"
        if not seguranca.pin_fraco(pin):
            return pin


def chave_token(token: str) -> str:
    """Os tokens de login ficam guardados só como hash: um backup do banco não abre sessões."""
    return hashlib.sha256(str(token).encode()).hexdigest()
OCIOSO_PRESIDENTE = 12 * 3600_000      # login do painel expira após 12 h sem uso
OCIOSO_PARLAMENTAR = 30 * 86400_000    # no celular, após 30 dias sem uso
VALIDADE_MAXIMA = 90 * 86400_000       # nenhum login dura mais que 90 dias
ACOES_COM_PIN = {"virtualVotar", "virtualDestaque"}  # confirmação do próprio PIN
# Rádio da Câmara no próprio servidor (pacote .deb: Liquidsoap toca a programação e entra com a sessão)
RADIO_LOCAL = os.environ.get("RADIO_LOCAL", "0") == "1"
RADIO_PORTA = int(os.environ.get("RADIO_PORTA", "8001"))
RADIO_DIR = Path(os.environ.get("RADIO_DIR", str(BANCO.parent / "radio" / "programacao")))
RADIO_MAX_OUVINTES = int(os.environ.get("RADIO_MAX_OUVINTES", "200"))
RADIO_FORMATOS = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".wav": "audio/wav", ".flac": "audio/flac"}
radio_ouvintes = 0
# Jitsi próprio da Câmara (deploy/local/jitsi.sh): só entra quem tem o token emitido pelo Parlatório
JITSI_URL = os.environ.get("JITSI_URL", "").strip().rstrip("/")
JITSI_APP_ID = os.environ.get("JITSI_APP_ID", "parlatorio")
JITSI_SEGREDO = os.environ.get("JITSI_SEGREDO", "")
bloqueio = seguranca.Bloqueio()
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", BANCO.parent / "backups"))
BACKUP_HORA = os.environ.get("BACKUP_HORA", "03:00")   # backup diário automático (vazio desliga)
BACKUP_MANTER = int(os.environ.get("BACKUP_MANTER", "14"))
LIMITE_CORPO = 4 * 1024 * 1024
TEMPO_LIMITE_CONEXAO = int(os.environ.get("TEMPO_LIMITE_CONEXAO", "60"))   # segundos sem receber nem enviar nada
MAX_CONEXOES = int(os.environ.get("MAX_CONEXOES", "800"))   # conexões simultâneas (telas, celulares, ouvintes da rádio)
RADIO_MAX_GB = float(os.environ.get("RADIO_MAX_GB", "20"))  # espaço máximo da programação da rádio
TIPOS_IMAGEM = {"image/png", "image/jpeg", "image/webp"}

trava = threading.RLock()
clientes: set[queue.Queue] = set()


def agora() -> int:
    return int(time.time() * 1000)


def uid() -> str:
    return secrets.token_hex(4)


class Erro(Exception):
    """Erro de regra: volta para a tela como mensagem."""


class TrocaPin(Exception):
    """Login com PIN inicial ou fraco: antes de qualquer outra coisa, é preciso trocar o PIN."""


def pin_exige_troca(papel: str, pin: str, pid) -> bool:
    if seguranca.pin_fraco(pin):
        return True
    if papel == "presidente":
        # PIN curto (menos de 6) ou o inicial aleatório mostrado no log: troca obrigatória.
        # Um PIN_PRESIDENTE forte do ambiente (ex.: o sorteado pelo instalador da AWS) pode seguir valendo.
        return len(str(pin)) < 6 or (not S.get("_pinPresidente") and pin == S.get("_pinInicialPresidente"))
    return pid in S["_pinsIniciais"]


# ---------------------------------------------------------------- banco
def abrir_banco() -> sqlite3.Connection:
    BANCO.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(BANCO, check_same_thread=False)
    db.executescript(
        """
        create table if not exists estado (id integer primary key check (id = 1), json text not null);
        create table if not exists imagens (chave text primary key, mime text not null, dados blob not null);
        create table if not exists eventos (id integer primary key, ts integer, autor text, acao text, args text);
        create table if not exists sessoes (id text primary key, titulo text, inicio integer, fim integer, resumo text, json text);
        create table if not exists gravacoes (id text primary key, sessao text, criado integer, json text);
        create table if not exists projetos (id text primary key, atualizado integer, json text);
        """
    )
    return db


db = abrir_banco()
integridade.preparar_banco(db)  # cadeia de hashes da auditoria e registro do uso de IA


# gravações ficam numa tabela própria: sobrevivem ao arquivamento da sessão
def grav_salvar(g: dict):
    db.execute("insert or replace into gravacoes (id, sessao, criado, json) values (?, ?, ?, ?)",
               (g["id"], g["sessao"], g["criado"], json.dumps(g, ensure_ascii=False)))
    db.commit()


def grav_obter(gid: str) -> dict | None:
    linha = db.execute("select json from gravacoes where id = ?", (gid,)).fetchone()
    return json.loads(linha[0]) if linha else None


# banco de projetos: cada matéria tem ficha social e histórico, para além da pauta de uma sessão
def proj_salvar(p: dict):
    p["atualizado"] = agora()
    db.execute("insert or replace into projetos (id, atualizado, json) values (?, ?, ?)",
               (p["id"], p["atualizado"], json.dumps(p, ensure_ascii=False)))
    db.commit()


def proj_obter(pid: str) -> dict | None:
    linha = db.execute("select json from projetos where id = ?", (pid,)).fetchone()
    return json.loads(linha[0]) if linha else None


def proj_todos() -> list[dict]:
    return [json.loads(x[0]) for x in db.execute("select json from projetos order by atualizado desc").fetchall()]


def proj_novo(**campos) -> dict:
    p = {"id": uid(), "numero": "", "ementa": "", "autor": "", "areas": [], "beneficiados": 0, "abrangencia": "",
         "vulneraveis": [], "urgencia": "", "fonte": "", "situacao": "em_tramitacao", "criado": agora(), "historico": []}
    p.update(campos)
    return p


def grav_da_sessao(sid: str) -> list[dict]:
    return [json.loads(x[0]) for x in db.execute(
        "select json from gravacoes where sessao = ? order by criado desc", (sid,)).fetchall()]


def salvar():
    db.execute("insert or replace into estado (id, json) values (1, ?)", (json.dumps(S),))
    db.commit()


CAMPOS_SIGILOSOS = ("dataUrl", "pin", "senha", "chave", "token", "segredo", "password", "jwt")


def registrar(autor: str, acao: str, args: dict):
    """Auditoria: nunca grava PIN, senha, chave ou token (o registro vai junto nos backups)."""
    limpo = {k: (f"<{len(v)} caracteres>" if isinstance(v, str) and len(v) > 2000 else v)
             for k, v in args.items() if k not in CAMPOS_SIGILOSOS}
    for k in list(limpo):
        if any(s in k.lower() for s in ("senha", "chave", "segredo", "token")):
            limpo[k] = "<oculto>"
    # cada linha encadeada à anterior por hash (server/integridade.py): adulteração aparece em /verificar
    integridade.inserir_evento(db, agora(), autor, acao, json.dumps(limpo, ensure_ascii=False))


# ---------------------------------------------------------------- estado inicial
CORES_PARTIDOS = {
    "PT": "#c8102e", "PL": "#1d3f8f", "MDB": "#2f8f3a", "PSD": "#e5a100", "PSB": "#e8590c",
    "UNIÃO": "#0b5ea8", "PSDB": "#1c64b8", "PP": "#3a7bd5", "REPUBLICANOS": "#1f7a8c",
    "PDT": "#c0392b", "PSOL": "#ffb000", "PODE": "#00875a", "REDE": "#2bb3a3",
    "NOVO": "#f26a21", "PCdoB": "#b5121b",
}


def estado_inicial() -> dict:
    nomes = [
        ("Ana Beatriz Moura", "PSD"), ("Carlos Eduardo Lima", "PT"), ("Fernanda Rocha", "MDB"),
        ("João Batista Neto", "PL"), ("Juliana Prado", "PSB"), ("Marcos Vinícius Teles", "UNIÃO"),
        ("Patrícia Albuquerque", "PSDB"), ("Ricardo Menezes", "PP"), ("Sandra Oliveira", "REPUBLICANOS"),
        ("Tiago Fontes", "PDT"), ("Vera Lúcia Campos", "PSOL"), ("Wagner Siqueira", "PODE"),
        ("Helena Castro", "REDE"), ("Otávio Brandão", "NOVO"), ("Luana Ferraz", "PCdoB"),
    ]
    parls = [{"id": uid(), "nome": n, "partido": p, "lider": False, "fotoV": 0, "canal": k + 1}
             for k, (n, p) in enumerate(nomes)]
    return {
        "rev": 0,
        "casa": {"nome": "Câmara Municipal", "titulo": "45ª Sessão Ordinária", "destaque": "#1f8a4c",
                 "tema": "escuro", "logoV": 0},
        "cfg": {"alerta": 60, "aparte": 60, "ordem": 180, "desconta": True, "corte": True, "som": True,
                "autoInscricao": False, "exigePresenca": True, "presencaCelular": True,
                "transcricaoAutoPublicar": False},
        "fases": [
            {"id": "pe", "nome": "Pequeno Expediente", "min": 5, "totalMin": 60},
            {"id": "ge", "nome": "Grande Expediente", "min": 15, "totalMin": 60},
            {"id": "cl", "nome": "Comunicações de Liderança", "min": 5, "totalMin": 0},
            {"id": "od", "nome": "Ordem do Dia — Discussão", "min": 10, "totalMin": 0},
            {"id": "ev", "nome": "Encaminhamento de Votação", "min": 5, "totalMin": 0},
            {"id": "ep", "nome": "Explicação Pessoal", "min": 5, "totalMin": 30},
            {"id": "tl", "nome": "Tribuna Livre", "min": 10, "totalMin": 0},
        ],
        "faseAtual": "pe",
        "partidos": dict(CORES_PARTIDOS),
        "parls": parls,
        "presenca": {},
        "filas": {},
        "orador": None,
        "interr": None,
        "pedidos": [],
        "pauta": [
            {"id": uid(), "numero": "PL 112/2026", "ementa": "Institui o programa municipal de hortas comunitárias.",
             "autor": "Fernanda Rocha", "situacao": "pendente"},
            {"id": uid(), "numero": "PL 118/2026", "ementa": "Dispõe sobre a transparência dos contratos de transporte público.",
             "autor": "Tiago Fontes", "situacao": "pendente"},
            {"id": uid(), "numero": "REQ 45/2026", "ementa": "Requer informações sobre a manutenção das escolas municipais.",
             "autor": "Vera Lúcia Campos", "situacao": "pendente"},
        ],
        "materiaAtual": None,
        "votacao": None,
        "votacoes": [],
        "hist": [],
        "sinal": {"n": 0, "tipo": ""},
        "_pins": {},
        "_pinsIniciais": {},
        "_tokens": {},
        **NOVOS_CAMPOS(),
    }


CAMPOS_RODAPE = ("endereco", "telefone", "whatsapp", "email", "site", "instagram", "facebook", "youtube", "linkedin")


def rodape_padrao() -> dict:
    return {"camara": {k: "" for k in CAMPOS_RODAPE},
            "aggilli": {**{k: "" for k in CAMPOS_RODAPE}, "site": "https://www.aggillintelligence.com.br",
                        "email": "adi@aggillintelligence.com.br", "instagram": "https://instagram.com/aggillisoftware"}}


def NOVOS_CAMPOS() -> dict:
    """Campos acrescentados depois da v2; também completam bancos antigos."""
    return {
        "micsManuais": {},      # pid -> True: microfone de bancada liberado pelo presidente
        "micsCortados": [],     # pids com microfone cortado (tumulto, advertência)
        "congelado": None,      # plenário congelado: {inicio, motivo, oradorRodando, interrRodando}
        "ocorrencias": [],      # cortes, congelamentos e tempo esgotado, para a ata
        "perguntasAbertas": False,  # perguntas do público ao vivo (Evento Interativo do Senado)
        "telaoPergunta": None,      # pergunta do público em destaque no telão
        "cidadaniaRev": 0,          # muda quando chegam perguntas, ideias ou respostas (as telas recarregam)
        "sessao": {"id": secrets.token_hex(8), "inicio": agora()},  # sessão em andamento
        "relevancia": {"populacao": 100000, "pesos": dict(estatisticas.PESOS_PADRAO)},
        "projetosRev": 0,       # muda quando o banco de projetos muda (as telas recarregam a lista)
        "_perfis": {},          # pid -> {areas, temas} declarados na área privada do parlamentar
        "_feedback": {},        # pid -> {prática: "salvo" | "descartado"}
        "sapl": {"url": "", "casa": None, "tipos": [], "sessoes": [], "job": None},  # integração com o SAPL
        "rodape": rodape_padrao(),  # contatos públicos da câmara e da Aggilli, no rodapé das páginas
        "virtuais": [],         # sessões virtuais assíncronas (votação online com prazo)
        "remoto": {"ativo": False, "servidor": JITSI_URL or "https://meet.jit.si"},  # sessão híbrida por videoconferência
        "presencaModo": {},     # pid -> "remoto" quando a presença foi registrada pela videoconferência
        "_remoto": {"sala": ""},  # nome da sala: nunca vai para as telas públicas
        "_pinPresidente": None,   # hash do PIN do painel depois da primeira troca
        "_pinInicialPresidente": "",  # PIN inicial aleatório (sem PIN_PRESIDENTE), até a primeira troca
        "_pinsIniciais": {},      # pid -> PIN inicial (aleatório ou definido pelo presidente), até o vereador trocar
        "backup": {"ultimo": None, "arquivo": None, "tamanho": 0, "nextcloud": None, "erro": None, "emAndamento": False},
        "mesa": dict(CFG_PADRAO),
        "_resumoAtual": None,    # rascunho da ata por IA da sessão em andamento (só o presidente vê)
        "resumoJob": None,       # andamento da geração do rascunho
        # transmissão ao vivo para Icecast/AzuraCast: o público vê só o endereço para ouvir
        "transmissao": {"configurada": False, "publico": "", "nome": ""},
        "_transmissao": {"preset": "azuracast", "host": "", "porta": 8005, "mount": "/", "usuario": "", "senha": "",
                         "tls": False, "bitrate": 128},
        "youtube": {"configurado": False, "ligado": True, "link": ""},   # sem a chave: ela fica em _youtube
        "_youtube": {"url": youtube.URL_PADRAO, "chave": ""},
    }


def definir_pin_inicial(estado: dict, pid: str, pin: str | None = None) -> str:
    """Dá a um vereador um PIN inicial (aleatório, ou o que o presidente escolheu). Vale até ele trocar."""
    pin = str(pin) if pin else novo_pin_inicial()
    estado["_pins"][pid] = seguranca.hash_pin(pin)
    estado["_pinsIniciais"][pid] = pin
    return pin


def preparar_pins(estado: dict):
    """Contas sem PIN pessoal recebem PIN inicial aleatório; bancos antigos com o 0000 de todos são corrigidos."""
    for p in estado["parls"]:
        if p["id"] not in estado["_pins"]:
            definir_pin_inicial(estado, p["id"])
    if TROCA_PIN_OBRIGATORIA and not estado.get("_pinsMigrados"):
        for pid, h in list(estado["_pins"].items()):
            if pid not in estado["_pinsIniciais"] and seguranca.verificar_pin("0000", h):
                definir_pin_inicial(estado, pid)
        estado["_pinsMigrados"] = True
    if not PIN_PRESIDENTE and not estado.get("_pinPresidente") and not estado.get("_pinInicialPresidente"):
        estado["_pinInicialPresidente"] = novo_pin_inicial() if TROCA_PIN_OBRIGATORIA else "1234"
    # tokens antigos (guardados em texto) passam a ser guardados só como hash
    estado["_tokens"] = {(t if len(t) == 64 else chave_token(t)): a for t, a in estado["_tokens"].items()}


def carregar() -> dict:
    linha = db.execute("select json from estado where id = 1").fetchone()
    if not linha:
        estado = estado_inicial()
        preparar_pins(estado)
        return estado
    estado = json.loads(linha[0])
    for k, v in NOVOS_CAMPOS().items():
        estado.setdefault(k, v)
    for k, v in CFG_PADRAO.items():
        estado["mesa"].setdefault(k, v)
    estado["cfg"].setdefault("transcricaoAutoPublicar", False)
    if JITSI_URL and estado["remoto"].get("servidor") == "https://meet.jit.si":  # instalou o Jitsi próprio
        estado["remoto"]["servidor"] = JITSI_URL
    for p in estado["parls"]:
        p.setdefault("canal", 0)
    for pid, pin in list(estado["_pins"].items()):
        if not seguranca.e_hash(pin):
            estado["_pins"][pid] = seguranca.hash_pin(pin)
    for t, a in list(estado["_tokens"].items()):
        a.setdefault("criado", agora())
        a.setdefault("uso", agora())
    for k, v in {"url": "", "casa": None, "tipos": [], "sessoes": [], "job": None}.items():
        estado["sapl"].setdefault(k, v)
    if estado["sapl"].get("job") and not estado["sapl"]["job"].get("fim"):  # servidor reiniciou no meio de uma importação
        estado["sapl"]["job"].update(fim=agora(), erro="Interrompida porque o servidor reiniciou.")
    for pid, perfil in list(estado["_perfis"].items()):  # perfis antigos eram só a lista de áreas
        if isinstance(perfil, list):
            estado["_perfis"][pid] = {"areas": perfil, "temas": ""}
    for g in estado.pop("gravacoes", []):  # versões anteriores guardavam as gravações no estado
        g.setdefault("sessao", estado["sessao"]["id"])
        grav_salvar(g)
    preparar_pins(estado)
    return estado


S = carregar()
_yt_env = os.environ.get("LEGIS_YOUTUBE_CHAVE", "").strip()
if _yt_env and S["_youtube"].get("env") != _yt_env:   # chave nova vinda da instalação; mudanças no painel valem até a próxima
    try:
        S["_youtube"].update(chave=youtube.validar_chave(_yt_env), env=_yt_env)
        S["youtube"]["configurado"] = True
    except ValueError:
        print("LEGIS_YOUTUBE_CHAVE inválida: ignorada.", flush=True)
if os.environ.get("LEGIS_YOUTUBE_LINK") and not S["youtube"].get("link"):
    S["youtube"]["link"] = os.environ["LEGIS_YOUTUBE_LINK"].strip()[:300]
if RADIO_LOCAL and not S["transmissao"].get("configurada"):  # banco novo ou antigo: a rádio local já vem configurada
    S["transmissao"].update(configurada=True, local=True, publico="/radio.mp3", nome=f"{S['casa']['nome']} — Rádio")
    S["_transmissao"]["preset"] = "local"
# modelo da cidade (cidades/<nome>/camara.json, embutido no pacote da Câmara): aplicado uma vez por banco
MODELO = os.environ.get("LEGIS_MODELO", "").strip()


def aplicar_modelo(forcar: bool = False) -> list[str] | None:
    """Aplica o modelo de LEGIS_MODELO ao banco, se ainda não foi aplicado (ou se forcar). Chamar com a trava."""
    if not MODELO or (S.get("_modelo") and not forcar):
        return None
    try:
        m = modelo_cidade.ler(MODELO)
    except (OSError, modelo_cidade.ModeloInvalido) as e:
        print(f"Modelo da cidade ignorado ({MODELO}): {e}", flush=True)
        return None
    feito = modelo_cidade.aplicar(S, m, agora())
    if m["brasao"]:   # brasão ao lado do modelo (instalação direta, sem o pacote da Câmara)
        arq = Path(MODELO).parent / m["brasao"]
        try:
            b = arq.read_bytes()
        except OSError:
            b = b""
        mime = "image/png" if b[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg" if b[:2] == b"\xff\xd8" else \
            "image/webp" if b[8:12] == b"WEBP" else None
        if mime and len(b) <= 1_500_000:
            db.execute("insert or replace into imagens (chave, mime, dados) values (?, ?, ?)", ("logo", mime, b))
            S["casa"]["logoV"] = agora()
            feito.append("brasão")
        elif b:
            print(f"Brasão do modelo ignorado ({arq.name}): use PNG, JPEG ou WebP de até 1,5 MB.", flush=True)
    registrar("instalacao", "modeloAplicar", {"nome": m["nome"], "impressao": S["_modelo"]["impressao"]})
    salvar()
    print(f"Modelo da cidade aplicado ({m['camara']}): " + "; ".join(feito), flush=True)
    return feito


with trava:
    aplicar_modelo()
if integridade.preparar_estado(S):  # chave secreta dos recibos de voto
    salvar()
integridade.instalar_registro_ia(ia, db, trava)  # toda chamada de IA entra na tabela uso_ia


def migrar_projetos():
    """Primeira execução com o banco de projetos: cria as fichas a partir das sessões arquivadas e da pauta."""
    if db.execute("select count(*) from projetos").fetchone()[0]:
        return
    projetos: dict[str, dict] = {}
    for (texto,) in db.execute("select json from sessoes order by fim").fetchall():
        r = json.loads(texto)
        for m in r["pauta"]:
            p = projetos.setdefault(m["id"], proj_novo(id=m["id"], numero=m["numero"], ementa=m["ementa"],
                                                      autor=m.get("autor", ""), criado=r["inicio"]))
            p["situacao"] = m["situacao"] if m["situacao"] in ("aprovada", "rejeitada", "retirada") else "em_tramitacao"
            p["historico"].append({"ts": r["fim"], "sessao": r["titulo"], "evento": "pauta", "situacao": m["situacao"]})
        for v in reversed(r["votacoes"]):
            p = projetos.get(v["materia"])
            if p:
                p["historico"].append({"ts": v.get("fim") or r["fim"], "sessao": r["titulo"], "evento": "votacao",
                                       "resultado": v["resultado"], "contagem": v.get("contagem"), "tipo": v["tipo"]})
    for m in S["pauta"]:
        p = projetos.setdefault(m["id"], proj_novo(id=m["id"], numero=m["numero"], ementa=m["ementa"], autor=m.get("autor", "")))
        p["situacao"] = m["situacao"] if m["situacao"] in ("aprovada", "rejeitada") else "em_pauta"
    for p in projetos.values():
        p["historico"].sort(key=lambda h: h["ts"])
        proj_salvar(p)


migrar_projetos()


# ---------------------------------------------------------------- utilidades de regra
def decorrido(t: dict) -> int:
    return t["acc"] + (agora() - t["start"] if t.get("start") else 0)


def restante(t: dict) -> int:
    return t["total"] - decorrido(t)


def parl(pid: str) -> dict:
    for p in S["parls"]:
        if p["id"] == pid:
            return p
    raise Erro("Parlamentar não encontrado.")


def fase(fid: str) -> dict:
    for f in S["fases"]:
        if f["id"] == fid:
            return f
    raise Erro("Fase não encontrada.")


def fila(fid: str | None = None) -> list:
    return S["filas"].setdefault(fid or S["faseAtual"], [])


def materia(mid: str) -> dict:
    for m in S["pauta"]:
        if m["id"] == mid:
            return m
    raise Erro("Matéria não encontrada.")


def presente(pid: str) -> bool:
    return pid in S["presenca"]


def sinal(tipo: str):
    S["sinal"] = {"n": S["sinal"]["n"] + 1, "tipo": tipo}


TIPOS = {"aparte": "Aparte", "ordem": "Questão de ordem", "pelaordem": "Pela ordem"}


# ---------------------------------------------------------------- ações
ACOES: dict[str, tuple[set, callable]] = {}


def acao(*papeis):
    def reg(fn):
        ACOES[fn.__name__] = (set(papeis), fn)
        return fn
    return reg


# --- fases e fila
@acao("presidente")
def setFase(ator, id):
    fase(id)
    S["faseAtual"] = id


@acao("presidente")
def inscrever(ator, pid, fase_id=None):
    parl(pid)
    f = fila(fase_id)
    if any(e["pid"] == pid for e in f):
        raise Erro("Já inscrito nesta fase.")
    f.append({"pid": pid, "bonus": 0, "cedentes": []})


@acao("presidente")
def desinscrever(ator, pid):
    S["filas"][S["faseAtual"]] = [e for e in fila() if e["pid"] != pid]


@acao("presidente")
def mover(ator, pid, d):
    f = fila()
    i = next((k for k, e in enumerate(f) if e["pid"] == pid), None)
    if i is None:
        return
    j = i + int(d)
    if 0 <= j < len(f):
        f[i], f[j] = f[j], f[i]


@acao("presidente")
def ceder(ator, de, para):
    """O inscrito `de` cede seu tempo ao inscrito `para` e sai da fila."""
    f = fila()
    cedente = next((e for e in f if e["pid"] == de), None)
    receptor = next((e for e in f if e["pid"] == para), None)
    if not cedente or not receptor or de == para:
        raise Erro("Os dois parlamentares precisam estar inscritos nesta fase.")
    receptor["bonus"] += fase(S["faseAtual"])["min"] * 60 + cedente["bonus"]
    receptor["cedentes"].append(de)
    f.remove(cedente)


# --- tribuna
def _finalizar():
    o = S["orador"]
    if not o:
        return
    if S["interr"]:
        _encerrar_interr(retomar=False)
    if o["start"]:
        o["acc"] += agora() - o["start"]
        o["start"] = None
    if o["inicio"]:
        p = parl(o["pid"]) if any(x["id"] == o["pid"] for x in S["parls"]) else {"nome": "?", "partido": ""}
        f = next((x for x in S["fases"] if x["id"] == o["fase"]), {"nome": "?", "min": 0})
        S["hist"].insert(0, {
            "id": uid(), "pid": o["pid"], "nome": p["nome"], "partido": p["partido"],
            "fase": o["fase"], "faseNome": f["nome"], "materia": S["materiaAtual"],
            "previsto": f["min"] * 60, "bonus": o["bonus"], "extra": o["extra"],
            "usado": round(o["acc"] / 1000), "inicio": o["inicio"], "fim": agora(),
            "apartes": o["apartes"], "cedentes": [parl(c)["nome"] for c in o["cedentes"] if any(x["id"] == c for x in S["parls"])],
        })
    S["orador"] = None


@acao("presidente")
def chamar(ator, pid):
    parl(pid)
    _finalizar()
    f = fila()
    entrada = next((e for e in f if e["pid"] == pid), {"pid": pid, "bonus": 0, "cedentes": []})
    S["filas"][S["faseAtual"]] = [e for e in f if e["pid"] != pid]
    S["pedidos"] = [x for x in S["pedidos"] if not (x["pid"] == pid and x["tipo"] == "inscricao")]
    S["orador"] = {
        "pid": pid, "fase": S["faseAtual"], "total": (fase(S["faseAtual"])["min"] * 60 + entrada["bonus"]) * 1000,
        "acc": 0, "start": None, "mic": False, "extra": 0, "bonus": entrada["bonus"], "cedentes": entrada["cedentes"],
        "inicio": None, "apartes": [], "alertou": False, "esgotou": False,
    }


@acao("presidente")
def proximo(ator):
    f = fila()
    if f:
        chamar(ator, f[0]["pid"])
    else:
        _finalizar()


@acao("presidente")
def iniciar(ator):
    o = S["orador"]
    if S["congelado"]:
        raise Erro("Plenário congelado. Retome a sessão antes.")
    if o and o.get("congelado"):
        raise Erro("Tempo congelado: use Descongelar.")
    if not o or o["start"] or (S["interr"] and S["interr"]["pausouOrador"]):
        return
    o["start"] = agora()
    o["mic"] = True
    o["inicio"] = o["inicio"] or o["start"]


@acao("presidente")
def pausar(ator):
    o = S["orador"]
    if not o or not o["start"]:
        return
    o["acc"] += agora() - o["start"]
    o["start"] = None
    o["mic"] = False


@acao("presidente")
def addTempo(ator, seg):
    o = S["orador"]
    if not o:
        return
    seg = int(seg)
    o["total"] = max(1000, o["total"] + seg * 1000)
    o["extra"] += seg
    if restante(o) > 0:
        o["esgotou"] = False
        if restante(o) > S["cfg"]["alerta"] * 1000:
            o["alertou"] = False


@acao("presidente")
def encerrar(ator):
    _finalizar()


def _interromper(tipo, pid):
    if tipo not in TIPOS:
        raise Erro("Tipo inválido.")
    if S["congelado"]:
        raise Erro("Plenário congelado. Retome a sessão antes.")
    if pid in S["micsCortados"]:
        raise Erro("Este parlamentar está com o microfone cortado.")
    if S["interr"]:
        raise Erro("Já existe um aparte ou questão em curso.")
    parl(pid)
    o = S["orador"]
    if tipo == "aparte":
        if not o or not o["start"]:
            raise Erro("Aparte só com o orador com a palavra.")
        if o["pid"] == pid:
            raise Erro("O orador não pode apartear a si mesmo.")
    pausou = False
    if o and o["start"] and not (tipo == "aparte" and S["cfg"]["desconta"]):
        o["acc"] += agora() - o["start"]
        o["start"] = None
        pausou = True
    if o:
        o["mic"] = False
    total = (S["cfg"]["aparte"] if tipo == "aparte" else S["cfg"]["ordem"]) * 1000
    S["interr"] = {"tipo": tipo, "pid": pid, "total": total, "acc": 0, "start": agora(), "pausouOrador": pausou}
    S["pedidos"] = [x for x in S["pedidos"] if not (x["pid"] == pid and x["tipo"] == tipo)]


@acao("presidente")
def interromper(ator, tipo, pid):
    _interromper(tipo, pid)


def _encerrar_interr(retomar=True):
    i, o = S["interr"], S["orador"]
    if not i:
        return
    usado = round(decorrido(i) / 1000)
    nome = parl(i["pid"])["nome"] if any(x["id"] == i["pid"] for x in S["parls"]) else "?"
    if o:
        o["apartes"].append({"tipo": i["tipo"], "nome": nome, "usado": usado})
        if i["pausouOrador"] and retomar:
            o["start"] = agora()
        if o["start"]:
            o["mic"] = True
    else:
        p = parl(i["pid"])
        S["hist"].insert(0, {
            "id": uid(), "pid": i["pid"], "nome": p["nome"], "partido": p["partido"], "fase": i["tipo"],
            "faseNome": TIPOS[i["tipo"]], "materia": S["materiaAtual"], "previsto": i["total"] // 1000,
            "bonus": 0, "extra": 0, "usado": usado, "inicio": i["start"] or agora(), "fim": agora(),
            "apartes": [], "cedentes": [],
        })
    S["interr"] = None


@acao("presidente")
def encerrarInterr(ator):
    _encerrar_interr()


@acao("presidente")
def addTempoInterr(ator, seg):
    i = S["interr"]
    if i:
        i["total"] = max(1000, i["total"] + int(seg) * 1000)


# --- congelar, cortar e liberar microfones
def _ocorrencia(tipo, pid=None, motivo=""):
    nome = next((p["nome"] for p in S["parls"] if p["id"] == pid), "") if pid else ""
    S["ocorrencias"].insert(0, {"id": uid(), "ts": agora(), "tipo": tipo, "pid": pid, "nome": nome,
                                "motivo": str(motivo or "")[:200], "materia": S["materiaAtual"]})


def _pausar_relogio(t) -> bool:
    if t and t.get("start"):
        t["acc"] += agora() - t["start"]
        t["start"] = None
        return True
    return False


@acao("presidente")
def congelar(ator, motivo=""):
    """Congela o orador: para o tempo e corta o microfone até o presidente descongelar."""
    o = S["orador"]
    if not o:
        raise Erro("Não há orador na tribuna.")
    if o.get("congelado"):
        return
    _pausar_relogio(o)
    o["mic"] = False
    o["congelado"] = True
    _ocorrencia("congelar", o["pid"], motivo)


@acao("presidente")
def descongelar(ator):
    o = S["orador"]
    if not o or not o.get("congelado"):
        return
    if S["congelado"]:
        raise Erro("Plenário congelado. Retome a sessão antes.")
    o["congelado"] = False
    if o["pid"] in S["micsCortados"]:
        S["micsCortados"].remove(o["pid"])
    _ocorrencia("descongelar", o["pid"])
    if restante(o) <= 0 and S["cfg"]["corte"]:
        return  # sem tempo: segue parado até o presidente conceder mais
    o["start"] = agora()
    o["mic"] = True
    o["inicio"] = o["inicio"] or o["start"]


@acao("presidente")
def congelarPlenario(ator, motivo=""):
    """Tumulto: para todos os cronômetros e fecha todos os microfones."""
    if S["congelado"]:
        return
    o, i = S["orador"], S["interr"]
    S["congelado"] = {"inicio": agora(), "motivo": str(motivo or "")[:200],
                      "oradorRodando": _pausar_relogio(o), "interrRodando": _pausar_relogio(i)}
    if o:
        o["mic"] = False
    _ocorrencia("plenario_congelado", None, motivo)


@acao("presidente")
def descongelarPlenario(ator):
    c = S["congelado"]
    if not c:
        return
    o, i = S["orador"], S["interr"]
    S["congelado"] = None
    if i and c["interrRodando"]:
        i["start"] = agora()
    if o and c["oradorRodando"] and not o.get("congelado"):
        o["start"] = agora()
        o["mic"] = not i  # durante aparte ou questão o microfone é de quem interrompe
    _ocorrencia("plenario_retomado", None, f"congelado por {round((agora() - c['inicio']) / 1000)} s")


@acao("presidente")
def cortarMic(ator, pid, cortar=True, motivo=""):
    parl(pid)
    if cortar:
        if S["orador"] and S["orador"]["pid"] == pid:
            congelar(ator, motivo)
        if S["interr"] and S["interr"]["pid"] == pid:
            _encerrar_interr()
        if pid not in S["micsCortados"]:
            S["micsCortados"].append(pid)
        S["micsManuais"].pop(pid, None)
        _ocorrencia("corte", pid, motivo)
    elif pid in S["micsCortados"]:
        S["micsCortados"].remove(pid)
        _ocorrencia("liberacao", pid, motivo)


@acao("presidente")
def micManual(ator, pid, aberto):
    """Abre ou fecha o microfone de bancada fora da tribuna (debate, encaminhamento)."""
    parl(pid)
    if aberto:
        if pid in S["micsCortados"]:
            raise Erro("Microfone cortado. Libere o corte antes.")
        if S["congelado"]:
            raise Erro("Plenário congelado.")
        S["micsManuais"][pid] = True
    else:
        S["micsManuais"].pop(pid, None)


@acao("presidente")
def fecharTodos(ator):
    S["micsManuais"] = {}


# --- mesa de som
@acao("presidente")
def mesaSalvar(ator, cfg):
    novo = dict(S["mesa"])
    for k, padrao in CFG_PADRAO.items():
        if k not in cfg:
            continue
        v = cfg[k]
        if isinstance(padrao, bool):
            novo[k] = bool(v)
        elif k in ("porta", "canalTribuna", "midiCanal"):
            try:
                novo[k] = int(v or 0)
            except (TypeError, ValueError):
                raise Erro("Número inválido em " + k + ".")
        elif k in ("valorAberto", "valorFechado"):
            novo[k] = v
        else:
            novo[k] = str(v).strip()
    for k in ("valorAberto", "valorFechado"):
        novo[k] = float(novo[k]) if novo.get("tipoValor") == "f" else int(float(novo[k]))
    if novo["driver"] not in DRIVERS_MESA:
        raise Erro("Driver inválido.")
    if novo["modelo"] and novo["modelo"] not in PRESETS:
        raise Erro("Modelo de mesa desconhecido.")
    if novo["driver"] != "simulado" and novo["driver"] not in ("http", "http_modelo") and not novo["host"]:
        raise Erro("Informe o IP da mesa.")
    if not 0 <= novo["porta"] <= 65535 or not 1 <= (novo["midiCanal"] or 1) <= 16:
        raise Erro("Porta ou canal MIDI inválido.")
    if novo["driver"] in ("http", "http_modelo") and novo["url"] and not re.match(r"^https?://", novo["url"]):
        raise Erro("A URL deve começar com http:// ou https://.")
    if novo["driver"] == "http_modelo" and "{canal}" not in novo["url"]:
        raise Erro("A URL precisa conter {canal}.")
    if novo["driver"] == "osc" and "{canal" not in novo["endereco"]:
        raise Erro("O endereço OSC precisa conter {canal}, por exemplo /ch/{canal:02d}/mix/on.")
    try:
        novo["endereco"].format(canal=1)
    except (KeyError, ValueError, IndexError):
        raise Erro("Endereço OSC inválido.")
    S["mesa"] = novo


@acao("presidente")
def mesaTestar(ator, canal, aberto):
    mesa.testar(S["mesa"], int(canal), bool(aberto))


@acao("presidente")
def gravacaoRemover(ator, id):
    g = grav_obter(id)
    if g:
        _apagar_gravacao(g)


def _apagar_gravacao(g: dict):
    (GRAVACOES / f"{g['id']}.{g['ext']}").unlink(missing_ok=True)
    db.execute("delete from gravacoes where id = ?", (g["id"],))
    db.commit()


# --- presença e pedidos
@acao("presidente", "parlamentar")
def presenca(ator, pid=None, presente=True):
    if ator["papel"] == "parlamentar":
        if not S["cfg"]["presencaCelular"]:
            raise Erro("Registro de presença pelo celular desativado.")
        pid = ator["pid"]
        presente = True
    parl(pid)
    if presente:
        S["presenca"].setdefault(pid, agora())
    else:
        S["presenca"].pop(pid, None)


@acao("parlamentar")
def pedir(ator, tipo):
    pid = ator["pid"]
    if tipo not in ("inscricao", *TIPOS):
        raise Erro("Pedido inválido.")
    if S["cfg"]["exigePresenca"] and not presente(pid):
        raise Erro("Registre sua presença antes.")
    if tipo == "inscricao":
        if any(e["pid"] == pid for e in fila()):
            raise Erro("Você já está inscrito nesta fase.")
        if S["orador"] and S["orador"]["pid"] == pid:
            raise Erro("Você está com a palavra.")
        if S["cfg"]["autoInscricao"]:
            fila().append({"pid": pid, "bonus": 0, "cedentes": []})
            return
    if tipo == "aparte":
        o = S["orador"]
        if not o or not o["start"] or o["pid"] == pid:
            raise Erro("Não há orador com a palavra para apartear.")
    if any(x["pid"] == pid and x["tipo"] == tipo for x in S["pedidos"]):
        raise Erro("Pedido já enviado.")
    S["pedidos"].append({"id": uid(), "pid": pid, "tipo": tipo, "fase": S["faseAtual"], "ts": agora()})


@acao("parlamentar")
def cancelarPedido(ator, id):
    S["pedidos"] = [x for x in S["pedidos"] if not (x["id"] == id and x["pid"] == ator["pid"])]


@acao("parlamentar")
def sairFila(ator):
    S["filas"][S["faseAtual"]] = [e for e in fila() if e["pid"] != ator["pid"]]


@acao("presidente")
def atenderPedido(ator, id, aprovar):
    p = next((x for x in S["pedidos"] if x["id"] == id), None)
    if not p:
        return
    if aprovar:
        if p["tipo"] == "inscricao":
            f = fila(p["fase"])
            if not any(e["pid"] == p["pid"] for e in f):
                f.append({"pid": p["pid"], "bonus": 0, "cedentes": []})
        else:
            _interromper(p["tipo"], p["pid"])
    S["pedidos"] = [x for x in S["pedidos"] if x["id"] != id]


# --- pauta
@acao("presidente")
def materiaSalvar(ator, numero, ementa, autor="", id=None):
    """Matéria criada pela pauta: vira também projeto no banco (sem ficha social ainda)."""
    if not numero.strip():
        raise Erro("Informe o número da matéria.")
    if id:
        m = materia(id)
        m.update(numero=numero.strip(), ementa=ementa.strip(), autor=autor.strip())
        p = proj_obter(id) or proj_novo(id=id)
    else:
        p = proj_novo(situacao="em_pauta")
        S["pauta"].append({"id": p["id"], "numero": numero.strip(), "ementa": ementa.strip(),
                           "autor": autor.strip(), "situacao": "pendente"})
    p.update(numero=numero.strip(), ementa=ementa.strip(), autor=autor.strip())
    proj_salvar(p)
    S["projetosRev"] += 1


def _limpar_lista(valores, permitidos) -> list:
    return [v for v in (valores or []) if v in permitidos]


@acao("presidente")
def projetoSalvar(ator, numero, ementa, autor="", id=None, areas=None, beneficiados=0, abrangencia="",
                  vulneraveis=None, urgencia="", fonte="", situacao=None):
    if not str(numero).strip() or not str(ementa).strip():
        raise Erro("Informe número e ementa.")
    try:
        beneficiados = max(0, int(beneficiados or 0))
    except (TypeError, ValueError):
        raise Erro("Número de beneficiados inválido.")
    if abrangencia and abrangencia not in estatisticas.ABRANGENCIA:
        raise Erro("Abrangência inválida.")
    if urgencia and urgencia not in estatisticas.URGENCIA:
        raise Erro("Urgência inválida.")
    if situacao and situacao not in ("proposta", "em_tramitacao", "aprovada", "rejeitada", "retirada", "arquivado"):
        raise Erro("Situação inválida.")
    p = proj_obter(id) if id else proj_novo()
    if not p:
        raise Erro("Projeto não encontrado.")
    p.update(numero=str(numero).strip(), ementa=str(ementa).strip(), autor=str(autor or "").strip(),
             areas=_limpar_lista(areas, estatisticas.AREAS), beneficiados=beneficiados, abrangencia=abrangencia,
             vulneraveis=_limpar_lista(vulneraveis, estatisticas.VULNERAVEIS), urgencia=urgencia,
             fonte=str(fonte or "").strip()[:300])
    na_pauta = next((m for m in S["pauta"] if m["id"] == p["id"]), None)
    if situacao and not na_pauta:
        p["situacao"] = situacao
    if na_pauta:
        na_pauta.update(numero=p["numero"], ementa=p["ementa"], autor=p["autor"])
    proj_salvar(p)
    S["projetosRev"] += 1


@acao("presidente")
def projetoRemover(ator, id):
    if any(m["id"] == id for m in S["pauta"]):
        raise Erro("Retire o projeto da pauta antes.")
    db.execute("delete from projetos where id = ?", (id,))
    db.commit()
    S["projetosRev"] += 1


@acao("presidente")
def pautaIncluir(ator, id):
    p = proj_obter(id)
    if not p:
        raise Erro("Projeto não encontrado.")
    if any(m["id"] == id for m in S["pauta"]):
        raise Erro("Já está na pauta.")
    if p["situacao"] in ("aprovada", "rejeitada"):
        raise Erro("Projeto já votado.")
    S["pauta"].append({"id": id, "numero": p["numero"], "ementa": p["ementa"], "autor": p["autor"], "situacao": "pendente",
                       **({"anexo": p["anexo"]} if p.get("anexo") else {})})
    p["situacao"] = "em_pauta"
    proj_salvar(p)
    S["projetosRev"] += 1


@acao("parlamentar")
def perfilInteresses(ator, areas=None, temas=None):
    perfil = S["_perfis"].setdefault(ator["pid"], {"areas": [], "temas": ""})
    if areas is not None:
        perfil["areas"] = _limpar_lista(areas, estatisticas.AREAS)
    if temas is not None:
        perfil["temas"] = str(temas).strip()[:500]


@acao("parlamentar")
def feedbackPratica(ator, praticaId, valor=None):
    """Salvar ou descartar uma recomendação (valor vazio desfaz)."""
    if praticaId not in {p["id"] for p in recomendacao.catalogo()}:
        raise Erro("Prática não encontrada.")
    if valor not in (None, "", "salvo", "descartado"):
        raise Erro("Valor inválido.")
    fb = S["_feedback"].setdefault(ator["pid"], {})
    if valor:
        fb[praticaId] = valor
    else:
        fb.pop(praticaId, None)


@acao("parlamentar")
def propor(ator, praticaId, ementa, beneficiados=0, abrangencia="", justificativa=""):
    """O parlamentar transforma uma boa prática em proposta; a secretaria protocola e numera depois."""
    pratica = next((x for x in recomendacao.catalogo() if x["id"] == praticaId), None)
    if not pratica:
        raise Erro("Prática não encontrada.")
    ementa = str(ementa or "").strip()
    if len(ementa) < 15:
        raise Erro("Escreva a ementa da proposta.")
    if abrangencia and abrangencia not in estatisticas.ABRANGENCIA:
        raise Erro("Abrangência inválida.")
    autor = parl(ator["pid"])["nome"]
    if any(p.get("origem") == praticaId and p["autor"] == autor and p["situacao"] == "proposta" for p in proj_todos()):
        raise Erro("Você já tem uma proposta pendente inspirada nesta prática.")
    ano = time.localtime().tm_year
    n = 1 + sum(1 for p in proj_todos() if str(p["numero"]).startswith("PROP ") and str(p["numero"]).endswith(f"/{ano}"))
    p = proj_novo(numero=f"PROP {n}/{ano}", ementa=ementa, autor=autor, areas=list(pratica["areas"]),
                  vulneraveis=list(pratica["vulneraveis"]), beneficiados=max(0, int(beneficiados or 0)),
                  abrangencia=abrangencia, situacao="proposta", origem=praticaId,
                  fonte=f"Inspirado em: {pratica['titulo']} ({pratica['local']})",
                  justificativa=str(justificativa or "").strip()[:2000], instrumento=pratica["instrumento"])
    p["historico"].append({"ts": agora(), "sessao": "—", "evento": "proposta", "situacao": "proposta"})
    proj_salvar(p)
    S["projetosRev"] += 1


@acao("parlamentar")
def retirarProposta(ator, id):
    p = proj_obter(id)
    if not p or p["situacao"] != "proposta" or p["autor"] != parl(ator["pid"])["nome"]:
        raise Erro("Só é possível retirar propostas suas ainda não protocoladas.")
    db.execute("delete from projetos where id = ?", (id,))
    db.commit()
    S["projetosRev"] += 1


def _limpar_contato(campo: str, valor) -> str:
    v = str(valor or "").strip()[:200]
    if not v:
        return ""
    if campo in ("site", "instagram", "facebook", "youtube", "linkedin"):
        if re.match(r"^[a-z][a-z0-9+.-]*:(?!//)", v, re.I) or (re.match(r"^[a-z][a-z0-9+.-]*://", v, re.I) and not re.match(r"^https?://", v, re.I)):
            raise Erro(f"Use um endereço http(s) em {campo}.")
        if not re.match(r"^https?://", v):
            v = "https://" + v.lstrip("/@")
        if not re.match(r"^https?://[^\s<>\"']+$", v):
            raise Erro(f"Endereço inválido em {campo}.")
    if campo == "email" and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
        raise Erro("E-mail inválido.")
    return v


@acao("presidente")
def rodapeSalvar(ator, camara, aggilli):
    S["rodape"] = {"camara": {k: _limpar_contato(k, camara.get(k)) for k in CAMPOS_RODAPE},
                   "aggilli": {k: _limpar_contato(k, aggilli.get(k)) for k in CAMPOS_RODAPE}}


@acao("presidente")
def relevanciaSalvar(ator, populacao, pesos):
    try:
        populacao = int(populacao)
        pesos = {k: max(0, float(pesos.get(k, 0))) for k in estatisticas.PESOS_PADRAO}
    except (TypeError, ValueError, AttributeError):
        raise Erro("Valores inválidos.")
    if populacao < 1000 or not sum(pesos.values()):
        raise Erro("Informe a população (mínimo 1.000) e ao menos um peso maior que zero.")
    S["relevancia"] = {"populacao": populacao, "pesos": pesos}
    S["projetosRev"] += 1


@acao("presidente")
def materiaRemover(ator, id):
    if S["votacao"] and S["votacao"]["materia"] == id and S["votacao"]["aberta"]:
        raise Erro("Há votação aberta para esta matéria.")
    S["pauta"] = [m for m in S["pauta"] if m["id"] != id]
    if S["materiaAtual"] == id:
        S["materiaAtual"] = None
    p = proj_obter(id)
    if p and p["situacao"] == "em_pauta":
        p["situacao"] = "em_tramitacao"
        proj_salvar(p)
        S["projetosRev"] += 1


@acao("presidente")
def materiaMover(ator, id, d):
    p = S["pauta"]
    i = next((k for k, m in enumerate(p) if m["id"] == id), None)
    if i is None:
        return
    j = i + int(d)
    if 0 <= j < len(p):
        p[i], p[j] = p[j], p[i]


@acao("presidente")
def materiaAtual(ator, id=None):
    if id:
        m = materia(id)
        if m["situacao"] == "pendente":
            m["situacao"] = "em_discussao"
    S["materiaAtual"] = id


@acao("presidente")
def materiaSituacao(ator, id, situacao):
    if situacao not in ("pendente", "em_discussao", "aprovada", "rejeitada", "adiada", "retirada"):
        raise Erro("Situação inválida.")
    materia(id)["situacao"] = situacao
    p = proj_obter(id)
    if p and situacao in ("aprovada", "rejeitada", "retirada"):
        p["situacao"] = situacao
        proj_salvar(p)
        S["projetosRev"] += 1


# --- votação
def _membros() -> int:
    return len(S["parls"])


def _contar(v):
    if v["tipo"] == "nominal":
        c = {"sim": 0, "nao": 0, "abst": 0}
        for valor in v["votos"].values():
            c[valor] += 1
        v["contagem"] = c


def _apurar(v) -> str:
    n = _membros()
    c = v["contagem"]
    votantes = c["sim"] + c["nao"] + c["abst"]
    if v["quorum"] == "absoluta":
        return "aprovada" if c["sim"] >= n // 2 + 1 else "rejeitada"
    if v["quorum"] == "dois_tercos":
        return "aprovada" if c["sim"] >= math.ceil(2 * n / 3) else "rejeitada"
    if votantes < n // 2 + 1:
        return "sem_quorum"
    return "aprovada" if c["sim"] > c["nao"] else "rejeitada"


@acao("presidente")
def votacaoAbrir(ator, materia_id, tipo, quorum):
    if S["votacao"] and S["votacao"]["aberta"]:
        raise Erro("Já existe uma votação aberta.")
    if tipo not in ("nominal", "simbolica", "secreta") or quorum not in ("simples", "absoluta", "dois_tercos"):
        raise Erro("Tipo ou quórum inválido.")
    m = materia(materia_id)
    m["situacao"] = "em_votacao"
    S["materiaAtual"] = materia_id
    S["votacao"] = {
        "id": uid(), "materia": materia_id, "numero": m["numero"], "ementa": m["ementa"], "tipo": tipo,
        "quorum": quorum, "aberta": True, "inicio": agora(), "fim": None, "votos": {}, "votantes": [],
        "contagem": {"sim": 0, "nao": 0, "abst": 0}, "resultado": None, "membros": _membros(),
        "presentes": len(S["presenca"]), "orientacoes": {},
    }


ORIENTACOES = {"sim": "Sim", "nao": "Não", "liberado": "Liberado", "obstrucao": "Obstrução"}


def bancada(p: dict) -> str:
    """Bancada que o líder orienta: o bloco parlamentar, se houver; senão o partido."""
    return (p.get("bloco") or p.get("partido") or "").strip()


@acao("parlamentar", "presidente")
def orientar(ator, orientacao, bancada_nome=None):
    """Orientação de bancada na votação nominal (como na Câmara): o líder orienta a sua; a Mesa registra por qualquer uma."""
    v = S["votacao"]
    if not v or not v["aberta"] or v["tipo"] != "nominal":
        raise Erro("Orientação só em votação nominal aberta.")
    if orientacao not in ORIENTACOES and orientacao != "":
        raise Erro("Orientação inválida.")
    if ator["papel"] == "parlamentar":
        eu = parl(ator["pid"])
        if not eu.get("lider"):
            raise Erro("Só o líder orienta a bancada.")
        nome = bancada(eu)
    else:
        nome = str(bancada_nome or "").strip()
        if nome not in {bancada(p) for p in S["parls"]}:
            raise Erro("Bancada inválida.")
    v.setdefault("orientacoes", {})
    if orientacao:
        v["orientacoes"][nome] = {"o": orientacao, "por": ator.get("pid"), "ts": agora()}
    else:
        v["orientacoes"].pop(nome, None)


def _divergencias(v) -> list[str]:
    """Quem votou diferente da orientação Sim/Não da própria bancada (votação nominal)."""
    if v.get("tipo") != "nominal":
        return []
    saida = []
    for pid, voto in v.get("votos", {}).items():
        p = next((x for x in S["parls"] if x["id"] == pid), None)
        o = (v.get("orientacoes") or {}).get(bancada(p) if p else "", {}).get("o")
        if o in ("sim", "nao") and voto != o:
            saida.append(pid)
    return saida


def _votar(pid, valor):
    v = S["votacao"]
    if not v or not v["aberta"]:
        raise Erro("Não há votação aberta.")
    if v["tipo"] == "simbolica":
        raise Erro("Votação simbólica: o resultado é declarado pelo presidente.")
    if valor not in ("sim", "nao", "abst"):
        raise Erro("Voto inválido.")
    if S["cfg"]["exigePresenca"] and not presente(pid):
        raise Erro("Só parlamentares presentes votam.")
    if v["tipo"] == "secreta":
        if pid in v["votantes"]:
            raise Erro("Voto já registrado.")
        v["votantes"].append(pid)
        v["contagem"][valor] += 1
    else:
        v["votos"][pid] = valor
        if pid not in v["votantes"]:
            v["votantes"].append(pid)
        _contar(v)


@acao("parlamentar")
def votar(ator, valor):
    _votar(ator["pid"], valor)


@acao("presidente")
def votarPor(ator, pid, valor):
    parl(pid)
    _votar(pid, valor)


@acao("presidente")
def votacaoEncerrar(ator, resultado=None):
    v = S["votacao"]
    if not v or not v["aberta"]:
        raise Erro("Não há votação aberta.")
    if v["tipo"] == "simbolica":
        if resultado not in ("aprovada", "rejeitada"):
            raise Erro("Declare aprovada ou rejeitada.")
        v["resultado"] = resultado
    else:
        v["resultado"] = _apurar(v)
    v["aberta"] = False
    v["fim"] = agora()
    v["divergencias"] = _divergencias(v)
    m = next((x for x in S["pauta"] if x["id"] == v["materia"]), None)
    if m:
        m["situacao"] = v["resultado"] if v["resultado"] != "sem_quorum" else "adiada"
    S["votacoes"].insert(0, json.loads(json.dumps(publico_votacao(v))))
    p = proj_obter(v["materia"])
    if p:
        if v["resultado"] in ("aprovada", "rejeitada"):
            p["situacao"] = v["resultado"]
        p["historico"].append({"ts": v["fim"], "sessao": S["casa"]["titulo"], "evento": "votacao", "tipo": v["tipo"],
                               "resultado": v["resultado"], "contagem": publico_votacao(v)["contagem"]})
        proj_salvar(p)
        S["projetosRev"] += 1
    sinal("fim")


@acao("presidente")
def votacaoCancelar(ator):
    v = S["votacao"]
    if v and v["aberta"]:
        m = next((x for x in S["pauta"] if x["id"] == v["materia"]), None)
        if m:
            m["situacao"] = "em_discussao"
    S["votacao"] = None


@acao("presidente")
def votacaoLimpar(ator):
    if S["votacao"] and S["votacao"]["aberta"]:
        raise Erro("Encerre a votação antes.")
    S["votacao"] = None


# --- configuração
@acao("presidente")
def configSalvar(ator, casa, cfg, fases, partidos):
    S["casa"].update({k: casa[k] for k in ("nome", "titulo", "destaque", "tema") if k in casa})
    for k, v in cfg.items():
        if k in S["cfg"]:
            S["cfg"][k] = type(S["cfg"][k])(v)
    fs = [{"id": f.get("id") or uid(), "nome": str(f["nome"]).strip(), "min": float(f["min"]),
           "totalMin": float(f.get("totalMin") or 0)} for f in fases if str(f["nome"]).strip() and float(f["min"]) > 0]
    if not fs:
        raise Erro("Mantenha ao menos uma fase.")
    S["fases"] = fs
    if S["faseAtual"] not in {f["id"] for f in fs}:
        S["faseAtual"] = fs[0]["id"]
    S["partidos"] = {str(k).strip().upper(): str(v) for k, v in partidos.items() if str(k).strip()}


@acao("presidente")
def parlSalvar(ator, nome, partido, lider=False, pin=None, id=None, canal=0, bloco=None):
    if not nome.strip():
        raise Erro("Informe o nome.")
    if pin is not None and pin != "" and not (str(pin).isdigit() and 4 <= len(str(pin)) <= 8):
        raise Erro("O PIN deve ter de 4 a 8 dígitos.")
    canal = int(canal or 0)
    if not 0 <= canal <= 256:
        raise Erro("Canal da mesa inválido.")
    if bloco is not None and bloco not in estatisticas.BLOCOS:
        raise Erro("Bloco inválido.")
    if id:
        p = parl(id)
        p.update(nome=nome.strip(), partido=partido.strip().upper(), lider=bool(lider), canal=canal)
    else:
        p = {"id": uid(), "nome": nome.strip(), "partido": partido.strip().upper(), "lider": bool(lider), "fotoV": 0,
             "canal": canal}
        S["parls"].append(p)
        if not pin:
            definir_pin_inicial(S, p["id"])
    if pin:
        definir_pin_inicial(S, p["id"], str(pin))  # PIN dado pelo presidente: o vereador troca no primeiro acesso
        S["_tokens"] = {t: a for t, a in S["_tokens"].items() if a.get("pid") != p["id"]}
    if bloco is not None:
        p["bloco"] = bloco
    S["partidos"].setdefault(p["partido"], "#5b6b86")


@acao("presidente")
def parlBloco(ator, id, bloco):
    if bloco not in estatisticas.BLOCOS:
        raise Erro("Bloco inválido.")
    parl(id)["bloco"] = bloco


@acao("presidente")
def blocoPorPartido(ator, partido, bloco):
    if bloco not in estatisticas.BLOCOS:
        raise Erro("Bloco inválido.")
    alvo = [p for p in S["parls"] if p["partido"] == partido]
    if not alvo:
        raise Erro("Nenhum parlamentar desse partido.")
    for p in alvo:
        p["bloco"] = bloco


@acao("presidente")
def parlRemover(ator, id):
    if (S["orador"] and S["orador"]["pid"] == id) or (S["interr"] and S["interr"]["pid"] == id):
        raise Erro("Este parlamentar está com a palavra.")
    S["parls"] = [p for p in S["parls"] if p["id"] != id]
    for k in S["filas"]:
        S["filas"][k] = [e for e in S["filas"][k] if e["pid"] != id]
    S["presenca"].pop(id, None)
    S["pedidos"] = [x for x in S["pedidos"] if x["pid"] != id]
    S["micsManuais"].pop(id, None)
    S["micsCortados"] = [x for x in S["micsCortados"] if x != id]
    S["_pins"].pop(id, None)
    S["_pinsIniciais"].pop(id, None)
    S["_tokens"] = {t: a for t, a in S["_tokens"].items() if a.get("pid") != id}


@acao("presidente")
def imagem(ator, alvo, dataUrl=None):
    """alvo = 'logo' ou o id de um parlamentar. dataUrl vazio remove a imagem."""
    if alvo not in ("logo", "logoAta"):
        p = parl(alvo)
    if not dataUrl:
        db.execute("delete from imagens where chave = ?", (alvo,))
        v = 0
    else:
        try:
            cabecalho, b64 = dataUrl.split(",", 1)
            mime = cabecalho.split(":")[1].split(";")[0]
            dados = base64.b64decode(b64, validate=True)
        except Exception:
            raise Erro("Imagem inválida.")
        if mime not in TIPOS_IMAGEM:
            raise Erro("Use PNG, JPEG ou WebP.")
        if len(dados) > 1_500_000:
            raise Erro("Imagem grande demais (máximo 1,5 MB).")
        db.execute("insert or replace into imagens (chave, mime, dados) values (?, ?, ?)", (alvo, mime, dados))
        v = agora()
    if alvo == "logo":
        S["casa"]["logoV"] = v
    elif alvo != "logoAta":
        p["fotoV"] = v


@acao("presidente")
def sessaoZerar(ator, manterPauta=True):
    S.update(filas={}, orador=None, interr=None, pedidos=[], hist=[], presenca={}, votacao=None, votacoes=[],
             micsManuais={}, micsCortados=[], congelado=None, ocorrencias=[], presencaModo={})
    inicio = S["sessao"]["inicio"]
    for m in S["pauta"]:  # a sessão descartada não conta no banco de projetos
        p = proj_obter(m["id"])
        if p:
            p["historico"] = [h for h in p["historico"] if h["ts"] < inicio or h.get("evento") == "tramitacao"]
            p["situacao"] = "em_pauta" if manterPauta else "em_tramitacao"
            proj_salvar(p)
    if not manterPauta:
        S["pauta"] = []
    else:
        for m in S["pauta"]:
            m["situacao"] = "pendente"
    S["materiaAtual"] = None
    S["projetosRev"] += 1


# --- integração com o SAPL (somente leitura; cada tarefa roda numa thread para não travar o plenário)
def _sapl_job(tipo: str, funcao, *args):
    if S["sapl"]["job"] and not S["sapl"]["job"].get("fim"):
        raise Erro("Já há uma importação do SAPL em andamento.")
    if not S["sapl"]["url"]:
        raise Erro("Informe e salve o endereço do SAPL antes.")
    S["sapl"]["job"] = {"tipo": tipo, "inicio": agora(), "fim": None, "feitos": 0, "total": 0, "mensagem": "Conectando…",
                        "erro": None, "log": []}
    url = S["sapl"]["url"]

    def rodar():
        cli = sapl_cli.Sapl(url)
        try:
            funcao(cli, *args)
            _sapl_atualiza(fim=agora())
        except Exception as e:  # rede, SAPL fora do ar, formato inesperado
            _sapl_atualiza(fim=agora(), erro=str(e)[:300], mensagem="Falhou.")
    threading.Thread(target=rodar, daemon=True).start()


def _sapl_atualiza(log: str | None = None, **campos):
    with trava:
        job = S["sapl"]["job"]
        job.update(campos)
        if log:
            job["log"] = ([f"{time.strftime('%H:%M:%S')} {log}"] + job["log"])[:40]
        confirmar()


def _sapl_testar(cli):
    casa = cli.casa()
    tipos = cli.tipos_materia()
    sessoes = cli.sessoes_recentes()
    with trava:
        S["sapl"]["casa"] = {"nome": casa.get("nome"), "municipio": casa.get("municipio"), "uf": casa.get("uf")}
        S["sapl"]["tipos"] = tipos
        S["sapl"]["sessoes"] = sessoes
    _sapl_atualiza(mensagem=f"Conectado a {casa.get('nome')}: {len(tipos)} tipos de matéria, {len(sessoes)} sessões recentes.")


def _sapl_parlamentares(cli, atualizar_fotos: bool, capitalizar: bool):
    partidos = cli.partidos()
    lista = cli.parlamentares_ativos()
    _sapl_atualiza(total=len(lista), mensagem=f"{len(lista)} parlamentares ativos no SAPL.")
    novos = atualizados = 0
    for k, sp in enumerate(lista, 1):
        nome = sp.get("nome_parlamentar") or sp.get("nome_completo") or ""
        nome = sapl_cli.nome_bonito(nome) if capitalizar else nome
        partido = cli.partido_atual(sp["id"], partidos)
        foto = cli.imagem(sp.get("fotografia")) if sp.get("fotografia") else None
        with trava:
            p = next((x for x in S["parls"] if x.get("saplId") == sp["id"]), None) or \
                next((x for x in S["parls"] if not x.get("saplId") and sapl_cli.chave_nome(x["nome"]) == sapl_cli.chave_nome(nome)), None)
            if p:
                atualizados += 1
                p.update(nome=nome, saplId=sp["id"], **({"partido": partido} if partido else {}))
            else:
                novos += 1
                p = {"id": uid(), "nome": nome, "partido": partido, "lider": False, "fotoV": 0, "canal": 0, "bloco": "",
                     "saplId": sp["id"]}
                S["parls"].append(p)
                definir_pin_inicial(S, p["id"])
            p["saplUrl"] = f"{cli.base}/parlamentar/{sp['id']}"
            if partido:
                S["partidos"].setdefault(partido, "#5b6b86")
            if foto and (atualizar_fotos or not p.get("fotoV")):
                db.execute("insert or replace into imagens (chave, mime, dados) values (?, ?, ?)", (p["id"], foto[0], foto[1]))
                p["fotoV"] = agora()
        _sapl_atualiza(feitos=k, log=f"{nome} ({partido or 'sem partido'})")
    ids = {sp["id"] for sp in lista}
    fora = [x["nome"] for x in S["parls"] if not x.get("saplId") or x["saplId"] not in ids]
    _sapl_atualiza(mensagem=f"Pronto: {novos} novo(s), {atualizados} atualizado(s)."
                            + (f" {len(fora)} parlamentar(es) daqui não estão ativos no SAPL e foram mantidos: {', '.join(fora[:5])}"
                               + ("…" if len(fora) > 5 else "") + "." if fora else ""))


def _numero_materia(m: dict, siglas: dict) -> str:
    return f"{siglas.get(m['tipo'], 'MAT')} {m['numero']}/{m['ano']}"


def _sapl_salvar_materia(cli, m: dict, siglas: dict, nomes_parl: dict) -> dict:
    autores = cli.autores(m["id"])
    autor = ""
    if autores:
        a = autores[0]
        autor = nomes_parl.get(a["objeto"]) or sapl_cli.nome_bonito(a["nome"])
    resultado = (m.get("resultado") or "").lower()
    situacao = "em_tramitacao" if m.get("em_tramitacao") else \
        "aprovada" if "aprovad" in resultado else "rejeitada" if "rejeitad" in resultado else "arquivado"
    with trava:
        p = next((x for x in proj_todos() if x.get("saplId") == m["id"]), None)
        novo = p is None
        p = p or proj_novo(situacao=situacao)
        if not novo and p["situacao"] != "em_pauta" and situacao != "em_tramitacao":
            p["situacao"] = situacao  # o SAPL já registrou o desfecho da matéria
        p.update(numero=_numero_materia(m, siglas), ementa=(m.get("ementa") or "").strip(), autor=autor, saplId=m["id"],
                 saplUrl=f"{cli.base}/materia/{m['id']}", textoUrl=url_segura(m.get("texto_original")))
        proj_salvar(p)
        S["projetosRev"] += 1
    return p


def _sapl_materias(cli, anos: list, tipos: list, limite: int):
    siglas = {t["id"]: t["sigla"] for t in cli.tipos_materia()}
    with trava:
        nomes_parl = {x["saplId"]: x["nome"] for x in S["parls"] if x.get("saplId")}
    lista = []
    for ano in anos:
        for tipo in (tipos or [None]):
            lista += cli.materias(int(ano), tipo, limite)
    lista = lista[:limite]
    _sapl_atualiza(total=len(lista), mensagem=f"{len(lista)} matéria(s) em tramitação encontradas.")
    for k, m in enumerate(lista, 1):
        p = _sapl_salvar_materia(cli, m, siglas, nomes_parl)
        _sapl_atualiza(feitos=k, log=f"{p['numero']} — {p['autor'] or 'sem autor'}")
    _sapl_atualiza(mensagem=f"Pronto: {len(lista)} matéria(s) no banco de projetos. Preencha a ficha social na aba Projetos.")


def _sapl_pauta(cli, sessao_id: int, substituir: bool):
    siglas = {t["id"]: t["sigla"] for t in cli.tipos_materia()}
    with trava:
        nomes_parl = {x["saplId"]: x["nome"] for x in S["parls"] if x.get("saplId")}
    itens = cli.itens_pauta(sessao_id)
    _sapl_atualiza(total=len(itens), mensagem=f"{len(itens)} item(ns) de pauta na sessão do SAPL.")
    projetos = []
    for k, it in enumerate(itens, 1):
        projetos.append(_sapl_salvar_materia(cli, cli.materia(it["materia"]), siglas, nomes_parl))
        _sapl_atualiza(feitos=k, log=f"{projetos[-1]['numero']} ({'expediente' if it['origem'] == 'expediente' else 'ordem do dia'})")
    with trava:
        if S["votacao"] and S["votacao"]["aberta"]:
            raise sapl_cli.ErroSapl("Há votação aberta: a pauta não foi alterada.")
        if substituir:
            for m in S["pauta"]:
                p = proj_obter(m["id"])
                if p and p["situacao"] == "em_pauta":
                    p["situacao"] = "em_tramitacao"
                    proj_salvar(p)
            S["pauta"] = [] if not S["materiaAtual"] else [m for m in S["pauta"] if m["id"] == S["materiaAtual"]]
        ja = {m["id"] for m in S["pauta"]}
        for p in projetos:
            if p["id"] not in ja:
                S["pauta"].append({"id": p["id"], "numero": p["numero"], "ementa": p["ementa"], "autor": p["autor"],
                                   "situacao": "pendente"})
                ja.add(p["id"])
                if p["situacao"] in ("em_tramitacao", "proposta"):
                    p["situacao"] = "em_pauta"
                    proj_salvar(p)
        S["projetosRev"] += 1
    _sapl_atualiza(mensagem=f"Pauta importada: {len(projetos)} matéria(s).")


@acao("presidente")
def saplConfigurar(ator, url):
    try:
        S["sapl"]["url"] = sapl_cli.normalizar_url(url) if url else ""
    except sapl_cli.ErroSapl as e:
        raise Erro(str(e))
    S["sapl"].update(casa=None, tipos=[], sessoes=[])


@acao("presidente")
def saplTestar(ator):
    _sapl_job("testar", _sapl_testar)


@acao("presidente")
def saplImportarParlamentares(ator, atualizarFotos=False, capitalizar=True):
    _sapl_job("parlamentares", _sapl_parlamentares, bool(atualizarFotos), bool(capitalizar))


@acao("presidente")
def saplImportarMaterias(ator, anos, tipos=None, limite=100):
    try:
        anos = sorted({int(a) for a in anos})[:5]
        tipos = [int(t) for t in (tipos or [])][:10]
        limite = max(1, min(500, int(limite)))
    except (TypeError, ValueError):
        raise Erro("Anos, tipos ou limite inválidos.")
    if not anos:
        raise Erro("Informe ao menos um ano.")
    _sapl_job("materias", _sapl_materias, anos, tipos, limite)


@acao("presidente")
def saplImportarPauta(ator, sessaoId, substituir=False):
    if S["votacao"] and S["votacao"]["aberta"]:
        raise Erro("Encerre a votação aberta antes.")
    _sapl_job("pauta", _sapl_pauta, int(sessaoId), bool(substituir))


# --- plenário virtual: sessões assíncronas (votação online com prazo)
def _virtual(vid: str) -> dict:
    v = next((x for x in S["virtuais"] if x["id"] == vid), None)
    if not v:
        raise Erro("Sessão virtual não encontrada.")
    return v


def _status_virtual(v: dict) -> str:
    if v.get("encerrada"):
        return "encerrada"
    if v.get("cancelada"):
        return "cancelada"
    return "aberta" if v["inicio"] <= agora() < v["fim"] else ("agendada" if agora() < v["inicio"] else "apurando")


def _item(v: dict, iid: str) -> dict:
    it = next((x for x in v["itens"] if x["id"] == iid), None)
    if not it:
        raise Erro("Item não encontrado.")
    return it


@acao("presidente")
def virtualCriar(ator, titulo, inicio, fim):
    try:
        inicio, fim = int(inicio), int(fim)
    except (TypeError, ValueError):
        raise Erro("Datas inválidas.")
    if not str(titulo).strip():
        raise Erro("Informe o título.")
    if fim <= inicio or fim - inicio < 3600_000:
        raise Erro("O prazo de votação deve ter ao menos 1 hora.")
    if fim <= agora():
        raise Erro("O encerramento precisa ser no futuro.")
    S["virtuais"].insert(0, {"id": uid(), "titulo": str(titulo).strip()[:120], "inicio": inicio, "fim": fim,
                             "criada": agora(), "itens": [], "encerrada": None, "cancelada": None})


@acao("presidente")
def virtualItem(ator, vid, projetoId, quorum="simples", remover=False):
    v = _virtual(vid)
    if _status_virtual(v) != "agendada":
        raise Erro("Só é possível alterar a pauta antes da abertura.")
    if remover:
        v["itens"] = [i for i in v["itens"] if i["projeto"] != projetoId]
        return
    if quorum not in ("simples", "absoluta", "dois_tercos"):
        raise Erro("Quórum inválido.")
    p = proj_obter(projetoId)
    if not p or p["situacao"] in ("aprovada", "rejeitada", "proposta"):
        raise Erro("Projeto indisponível para votação.")
    if any(i["projeto"] == projetoId for i in v["itens"]):
        raise Erro("Já está na pauta virtual.")
    v["itens"].append({"id": uid(), "projeto": projetoId, "numero": p["numero"], "ementa": p["ementa"], "autor": p["autor"],
                       "quorum": quorum, "votos": {}, "manifestacoes": [], "destaques": [], "resultado": None})


@acao("presidente")
def virtualCancelar(ator, vid):
    v = _virtual(vid)
    if _status_virtual(v) not in ("agendada", "aberta"):
        raise Erro("Esta sessão não pode mais ser cancelada.")
    v["cancelada"] = agora()


@acao("presidente")
def virtualEncerrarAgora(ator, vid):
    v = _virtual(vid)
    if _status_virtual(v) not in ("aberta", "apurando"):
        raise Erro("Só é possível encerrar uma sessão aberta.")
    _virtual_apurar(v, antecipada=True)


def _pode_votar(ator, v):
    if _status_virtual(v) != "aberta":
        raise Erro("A votação desta sessão virtual não está aberta.")
    parl(ator["pid"])


@acao("parlamentar")
def virtualVotar(ator, vid, itemId, valor):
    v = _virtual(vid)
    _pode_votar(ator, v)
    it = _item(v, itemId)
    if it["destaques"]:
        raise Erro("Item destacado: será votado na sessão presencial.")
    if valor not in ("sim", "nao", "abst"):
        raise Erro("Voto inválido.")
    it["votos"][ator["pid"]] = {"valor": valor, "ts": agora()}


@acao("parlamentar")
def virtualManifestar(ator, vid, itemId, texto):
    v = _virtual(vid)
    _pode_votar(ator, v)
    texto = str(texto or "").strip()
    if len(texto) < 10:
        raise Erro("Escreva a manifestação (mínimo de 10 caracteres).")
    _item(v, itemId)["manifestacoes"].append({"pid": ator["pid"], "nome": parl(ator["pid"])["nome"], "texto": texto[:3000],
                                              "ts": agora()})


@acao("parlamentar")
def virtualDestaque(ator, vid, itemId):
    """Pede destaque: o item sai da sessão virtual e vai para a sessão presencial."""
    v = _virtual(vid)
    _pode_votar(ator, v)
    it = _item(v, itemId)
    if ator["pid"] not in it["destaques"]:
        it["destaques"].append(ator["pid"])
    it["votos"] = {}


def _virtual_apurar(v: dict, antecipada: bool = False):
    fim = agora() if antecipada else v["fim"]
    membros = [{k: p.get(k) for k in ("id", "nome", "partido", "lider")} for p in S["parls"]]
    votantes, votacoes, pauta = {}, [], []
    for it in v["itens"]:
        p = proj_obter(it["projeto"])
        if it["destaques"]:
            it["resultado"] = "destacado"
            if p and p["situacao"] not in ("aprovada", "rejeitada"):
                p["situacao"] = "em_tramitacao"
                p["historico"].append({"ts": fim, "sessao": v["titulo"], "evento": "destaque",
                                       "situacao": "destacado para a sessão presencial"})
                proj_salvar(p)
            pauta.append({"id": it["projeto"], "numero": it["numero"], "ementa": it["ementa"], "autor": it["autor"],
                          "situacao": "adiada"})
            continue
        contagem = {"sim": 0, "nao": 0, "abst": 0}
        for pid, voto in it["votos"].items():
            contagem[voto["valor"]] += 1
            votantes[pid] = min(votantes.get(pid, voto["ts"]), voto["ts"])
        vt = {"contagem": contagem, "quorum": it["quorum"]}
        it["resultado"] = _apurar(vt)
        situacao = it["resultado"] if it["resultado"] != "sem_quorum" else "adiada"
        votacoes.insert(0, {"id": uid(), "materia": it["projeto"], "numero": it["numero"], "ementa": it["ementa"],
                            "tipo": "nominal", "quorum": it["quorum"], "aberta": False, "inicio": v["inicio"], "fim": fim,
                            "votos": {pid: x["valor"] for pid, x in it["votos"].items()},
                            "votantes": list(it["votos"]), "contagem": contagem, "resultado": it["resultado"],
                            "membros": len(membros), "presentes": len(it["votos"]), "virtual": True,
                            "manifestacoes": it["manifestacoes"]})
        pauta.append({"id": it["projeto"], "numero": it["numero"], "ementa": it["ementa"], "autor": it["autor"],
                      "situacao": situacao})
        if p:
            if it["resultado"] in ("aprovada", "rejeitada"):
                p["situacao"] = it["resultado"]
            p["historico"].append({"ts": fim, "sessao": v["titulo"], "evento": "votacao", "tipo": "virtual",
                                   "resultado": it["resultado"], "contagem": contagem})
            proj_salvar(p)
    r = {"id": f"virtual{v['id']}", "titulo": v["titulo"], "casa": S["casa"]["nome"], "inicio": v["inicio"], "fim": fim,
         "virtual": True, "parls": membros, "presenca": votantes, "pauta": pauta, "hist": [], "votacoes": votacoes,
         "ocorrencias": ([{"id": uid(), "ts": fim, "tipo": "encerramento_antecipado", "pid": None, "nome": "",
                           "motivo": "Sessão virtual encerrada antes do prazo pela presidência", "materia": None}]
                         if antecipada else []), "gravacoes": []}
    db.execute("insert or replace into sessoes (id, titulo, inicio, fim, resumo, json) values (?, ?, ?, ?, ?, ?)",
               (r["id"], r["titulo"], r["inicio"], fim, json.dumps(resumo(r)), json.dumps(r, ensure_ascii=False)))
    db.commit()
    v["encerrada"] = fim
    v["arquivo"] = r["id"]
    S["projetosRev"] += 1
    sinal("fim")


# --- sessão remota (videoconferência Jitsi)
@acao("presidente")
def remotoConfigurar(ator, ativo, servidor=None):
    if servidor is not None:
        servidor = str(servidor).strip().rstrip("/")
        if not re.match(r"^https://[a-z0-9.-]+(:\d+)?$", servidor, re.I):
            raise Erro("Informe o servidor Jitsi como https://meet.suacamara.leg.br")
        S["remoto"]["servidor"] = servidor
    S["remoto"]["ativo"] = bool(ativo)
    if ativo and not S["_remoto"]["sala"]:
        S["_remoto"]["sala"] = "parlatorio-" + secrets.token_urlsafe(12).replace("_", "").replace("-", "")


def token_jitsi(nome: str, uid: str, sala: str, moderador: bool) -> str:
    """JWT HS256 aceito pelo Jitsi configurado com AUTH_TYPE=jwt (vale 12 h, só para a sala da sessão)."""
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    agora_s = int(time.time())
    corpo = {"aud": "jitsi", "iss": JITSI_APP_ID, "sub": "*", "room": sala, "nbf": agora_s - 60, "exp": agora_s + 12 * 3600,
             "context": {"user": {"id": uid, "name": nome, "moderator": moderador,
                                  "affiliation": "owner" if moderador else "member"}}}
    partes = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + "." + b64(json.dumps(corpo, ensure_ascii=False).encode())
    assinatura = hmac.new(JITSI_SEGREDO.encode(), partes.encode(), hashlib.sha256).digest()
    return partes + "." + b64(assinatura)


@acao("presidente")
def remotoNovaSala(ator):
    S["_remoto"]["sala"] = "parlatorio-" + secrets.token_urlsafe(12).replace("_", "").replace("-", "")


@acao("parlamentar")
def presencaRemota(ator):
    if not S["remoto"]["ativo"]:
        raise Erro("A sessão remota não está ativa.")
    S["presenca"].setdefault(ator["pid"], agora())
    S["presencaModo"][ator["pid"]] = "remoto"


# --- backup
def _rodar_backup(motivo: str):
    with trava:
        if S["backup"].get("emAndamento"):
            return
        S["backup"]["emAndamento"] = True
        transmitir(pacote())
    resultado, erro, nuvem = None, None, None
    try:
        resultado = backup_mod.fazer_backup(BANCO, GRAVACOES, BACKUP_DIR, BACKUP_MANTER)
        try:
            nuvem = backup_mod.enviar_nextcloud(BACKUP_DIR / resultado["arquivo"])
        except Exception as e:  # nuvem fora do ar não invalida a cópia local
            nuvem = f"erro: {str(e)[:120]}"
    except Exception as e:
        erro = str(e)[:300]
    with trava:
        S["backup"].update(emAndamento=False, erro=erro, nextcloud=nuvem, motivo=motivo,
                           **({"ultimo": agora(), "arquivo": resultado["arquivo"], "tamanho": resultado["tamanho"],
                               "copias": resultado["copias"], "pasta": str(BACKUP_DIR)} if resultado else {}))
        registrar("sistema", "backup", {"motivo": motivo, "ok": not erro, "nextcloud": nuvem})
        confirmar()


@acao("presidente")
def backupAgora(ator):
    if S["backup"].get("emAndamento"):
        raise Erro("Já há um backup em andamento.")
    threading.Thread(target=_rodar_backup, args=("manual",), daemon=True).start()


def agendador_backup():
    """Backup diário no horário BACKUP_HORA (e um na partida se nunca houve)."""
    if not BACKUP_HORA:
        return
    if not S["backup"].get("ultimo"):
        _rodar_backup("primeiro")
    while True:
        time.sleep(30)
        if time.strftime("%H:%M") == BACKUP_HORA and agora() - (S["backup"].get("ultimo") or 0) > 3600_000:
            _rodar_backup("diario")


# --- sessões: encerrar, arquivar, consultar
def retrato(fim=None) -> dict:
    """Tudo o que a ata precisa da sessão em andamento (cópia independente do estado)."""
    marcos = [*S["presenca"].values(), *(h["inicio"] for h in S["hist"]), *(o["ts"] for o in S["ocorrencias"]),
              *(v["inicio"] for v in S["votacoes"])]
    r = {
        "id": S["sessao"]["id"], "titulo": S["casa"]["titulo"], "casa": S["casa"]["nome"],
        "inicio": min(marcos) if marcos else S["sessao"]["inicio"], "fim": fim,
        "parls": [{k: p.get(k) for k in ("id", "nome", "partido", "lider")} for p in S["parls"]],
        "presenca": S["presenca"], "pauta": S["pauta"], "hist": S["hist"], "votacoes": S["votacoes"],
        "ocorrencias": S["ocorrencias"], "gravacoes": grav_da_sessao(S["sessao"]["id"]),
        "resumoIA": S.get("_resumoAtual"), "video": S["sessao"].get("video"),
    }
    return json.loads(json.dumps(r))


def resumo(r: dict) -> dict:
    return {"falas": len(r["hist"]), "votacoes": len(r["votacoes"]), "presentes": len(r["presenca"]),
            "membros": len(r["parls"]), "gravacoes": len(r["gravacoes"]), "ocorrencias": len(r["ocorrencias"])}


@acao("presidente")
def sessaoEncerrar(ator, proximoTitulo, manterPauta=True):
    """Fecha a sessão: arquiva tudo (para consulta e ata) e abre a próxima em branco."""
    if S["votacao"] and S["votacao"]["aberta"]:
        raise Erro("Encerre a votação aberta antes.")
    proximoTitulo = str(proximoTitulo or "").strip()
    if not proximoTitulo:
        raise Erro("Informe o título da próxima sessão.")
    _finalizar()
    fim = agora()
    r = retrato(fim)
    r["selo"] = integridade.selo(db)  # último elo da auditoria: vai para a ata em PDF
    db.execute("insert or replace into sessoes (id, titulo, inicio, fim, resumo, json) values (?, ?, ?, ?, ?, ?)",
               (r["id"], r["titulo"], r["inicio"], fim, json.dumps(resumo(r)), json.dumps(r, ensure_ascii=False)))
    db.commit()
    nao_votadas = [dict(m, situacao="pendente") for m in S["pauta"]
                   if m["situacao"] in ("pendente", "em_discussao", "em_votacao", "adiada")] if manterPauta else []
    S.update(filas={}, orador=None, interr=None, pedidos=[], hist=[], presenca={}, votacao=None, votacoes=[],
             micsManuais={}, micsCortados=[], congelado=None, ocorrencias=[], pauta=nao_votadas, materiaAtual=None,
             sessao={"id": secrets.token_hex(8), "inicio": agora()}, _resumoAtual=None, resumoJob=None)
    seguem = {m["id"] for m in nao_votadas}  # continuam na pauta da próxima sessão
    for m in r["pauta"]:  # histórico no banco de projetos
        p = proj_obter(m["id"])
        if p:
            p["historico"].append({"ts": fim, "sessao": r["titulo"], "evento": "pauta", "situacao": m["situacao"]})
            if p["situacao"] == "em_pauta" and m["id"] not in seguem:
                p["situacao"] = "em_tramitacao"
            proj_salvar(p)
    S["projetosRev"] += 1
    S["casa"]["titulo"] = proximoTitulo


@acao("presidente")
def sessaoArquivoRemover(ator, id):
    for g in grav_da_sessao(id):
        _apagar_gravacao(g)
    db.execute("delete from sessoes where id = ?", (id,))
    db.commit()


def sessao_arquivada(sid: str) -> dict | None:
    linha = db.execute("select json from sessoes where id = ?", (sid,)).fetchone()
    if not linha:
        return None
    r = json.loads(linha[0])
    r["gravacoes"] = grav_da_sessao(sid)  # estado final dos arquivos (podem ter terminado depois)
    return r


def logo_ata() -> bytes | None:
    linha = db.execute("select dados from imagens where chave = 'logoAta'").fetchone()
    if linha:
        return linha[0]
    padrao = PUBLICO / "marca" / "logo-ata.jpg"
    return padrao.read_bytes() if padrao.exists() else None


# ---------------------------------------------------------------- regras automáticas
def sempre(laco):
    """Threads de fundo nunca morrem em silêncio: um erro inesperado vai para o log e o laço recomeça."""
    def rodar():
        while True:
            try:
                laco()
            except Exception:  # noqa: BLE001
                print(f"Erro na tarefa de fundo {laco.__name__} (recomeçando):", flush=True)
                traceback.print_exc()
                time.sleep(1)
    rodar.__name__ = laco.__name__
    return rodar


@sempre
def tique():
    """Avisos sonoros, corte de microfone e fim de aparte, 4 vezes por segundo."""
    while True:
        time.sleep(0.25)
        with trava:
            mudou = False
            o, i = S["orador"], S["interr"]
            if o and o["start"]:
                r = restante(o)
                if not o["alertou"] and 0 < r <= S["cfg"]["alerta"] * 1000:
                    o["alertou"] = True
                    sinal("alerta")
                    mudou = True
                if not o["esgotou"] and r <= 0:
                    o["esgotou"] = True
                    sinal("fim")
                    mudou = True
                    if S["cfg"]["corte"]:
                        o["acc"], o["start"], o["mic"] = o["total"], None, False
                        _ocorrencia("tempo_esgotado", o["pid"], "microfone cortado automaticamente")
                        if i and not i["pausouOrador"]:
                            _encerrar_interr(retomar=False)
            i = S["interr"]
            if i and i["start"] and restante(i) <= 0 and S["cfg"]["corte"]:
                sinal("fim")
                _encerrar_interr()
                mudou = True
            for v in S["virtuais"]:
                if not v.get("encerrada") and not v.get("cancelada") and agora() >= v["fim"]:
                    _virtual_apurar(v)
                    mudou = True
            if int(time.time()) % 5 == 0:
                for g in grav_da_sessao(S["sessao"]["id"]):
                    if not g["fim"] and agora() - g["ultimo"] > 60000:
                        g["fim"], g["interrompida"] = g["ultimo"], True  # navegador que gravava caiu
                        grav_salvar(g)
                        mudou = True
            if mudou:
                confirmar()


# ---------------------------------------------------------------- transmissão
def publico_votacao(v):
    if not v:
        return None
    v = dict(v)
    if v["tipo"] == "secreta":
        v["votos"] = {}
        if v["aberta"]:
            v["contagem"] = None
    return v


def microfones() -> tuple[dict, bool]:
    """Estado de cada microfone: aberto, fechado, cortado ou tribuna (orador falando pelo canal da tribuna)."""
    o, i = S["orador"], S["interr"]
    tribuna_canal = int(S["mesa"].get("canalTribuna") or 0)
    estados = {}
    for p in S["parls"]:
        pid = p["id"]
        if pid in S["micsCortados"]:
            estados[pid] = "cortado"
            continue
        aberto = bool(S["micsManuais"].get(pid)) or bool(i and i["start"] and i["pid"] == pid)
        if o and o["mic"] and o["pid"] == pid:
            estados[pid] = "tribuna" if tribuna_canal else "aberto"
            continue
        estados[pid] = "aberto" if aberto else "fechado"
    tribuna = bool(tribuna_canal and o and o["mic"] and o["pid"] not in S["micsCortados"])
    if S["congelado"]:
        estados = {k: "cortado" if v == "cortado" else "fechado" for k, v in estados.items()}
        tribuna = False
    return estados, tribuna


def canais_desejados() -> dict[int, bool]:
    estados, tribuna = microfones()
    canais: dict[int, bool] = {}
    for p in S["parls"]:
        c = int(p.get("canal") or 0)
        if c:
            canais[c] = canais.get(c, False) or estados[p["id"]] == "aberto"
    t = int(S["mesa"].get("canalTribuna") or 0)
    if t:
        canais[t] = canais.get(t, False) or tribuna
    return canais


def pacote() -> str:
    pub = {k: v for k, v in S.items() if not k.startswith("_")}
    pub["votacao"] = publico_votacao(S["votacao"])
    # telas públicas: só o tipo da mesa de som (endereço e URL, que podem ter credenciais, ficam em /api/mesa)
    pub["mesa"] = {k: S["mesa"].get(k) for k in ("driver", "modelo", "canalTribuna")}
    pub["backup"] = {k: v for k, v in S["backup"].items() if k != "pasta"}
    pub["microfones"], pub["tribunaAberta"] = microfones()
    pub["mesaStatus"] = mesa.publico()
    pub["mesaPresets"] = PRESETS
    pub["gravacoes"] = [grav_publica(g) for g in grav_da_sessao(S["sessao"]["id"])]
    pub["transcricaoMotor"] = transcritor.descricao()
    pub["transmissaoStatus"] = transmissor.status() if "transmissor" in globals() else {"ativa": False}
    pub["youtubeStatus"] = canal_youtube.status() if "canal_youtube" in globals() else {"ativa": False}
    pub["iaMotor"] = ia.descricao()
    pub["radioLocal"] = RADIO_LOCAL
    pub["camaraConfigurada"] = camara_configurada()
    try:
        pub["perguntasPendentes"] = db.execute("select count(*) from perguntas where json like ? and json like ?",
                                               (f'%"sessao": "{S["sessao"]["id"]}"%', '%"situacao": "pendente"%')).fetchone()[0]
    except sqlite3.OperationalError:   # tabela ainda não criada (início do servidor)
        pub["perguntasPendentes"] = 0
    pub["legendasLigadas"] = bool(getattr(globals().get("legendador"), "ligado", False))  # a mesa avisa antes de tudo carregar
    pub["portaHttps"] = PORTA_HTTPS if HTTPS_ATIVO else None
    pub["virtuais"] = [{**v, "status": _status_virtual(v)} for v in S["virtuais"][:20]]
    return json.dumps({"tipo": "estado", "agora": agora(), "estado": pub}, ensure_ascii=False)


def transmitir(msg: str):
    for q in list(clientes):
        try:
            q.put_nowait(msg)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            q.put_nowait(msg)


def confirmar():
    S["rev"] += 1
    salvar()
    transmitir(pacote())
    mesa.atualizar(S["mesa"], canais_desejados())


def _aviso_mesa():
    with trava:
        transmitir(pacote())


mesa = Mesa(_aviso_mesa)
mesa.atualizar(S["mesa"], canais_desejados())


# ---------------------------------------------------------------- gravação
def _gravacao(gid: str) -> dict:
    g = grav_obter(gid)
    if not g:
        raise Erro("Gravação não encontrada.")
    return g


def gravacao_finalizar_arquivo(g: dict):
    """O WebM do navegador sai sem duração no cabeçalho; se houver ffmpeg, regrava (sem recodificar)."""
    ffmpeg = shutil.which("ffmpeg")
    arquivo = GRAVACOES / f"{g['id']}.{g['ext']}"
    if not ffmpeg or not arquivo.exists() or g["ext"] != "webm":
        return
    tmp = arquivo.with_suffix(".tmp.webm")
    try:
        subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(arquivo), "-c", "copy", str(tmp)], check=True, timeout=300)
        tmp.replace(arquivo)
        with trava:
            g = grav_obter(g["id"]) or g
            g["bytes"] = arquivo.stat().st_size
            g["duracao"] = True
            grav_salvar(g)
            confirmar()
    except (subprocess.SubprocessError, OSError):
        tmp.unlink(missing_ok=True)


def gravacao_iniciar(pid: str, inicio: int, mime: str) -> dict:
    mime_base = (mime or "").split(";")[0].strip()
    if mime_base not in TIPOS_AUDIO:
        raise Erro("Formato de áudio não suportado.")
    o = S["orador"]
    p = parl(pid)
    f = next((x for x in S["fases"] if o and x["id"] == o["fase"]), {"nome": ""})
    g = {"id": secrets.token_hex(8), "sessao": S["sessao"]["id"], "pid": pid, "nome": p["nome"], "partido": p["partido"], "faseNome": f["nome"],
         "materia": S["materiaAtual"], "inicio": int(inicio or agora()), "criado": agora(), "fim": None,
         "ultimo": agora(), "bytes": 0, "mime": mime_base, "ext": TIPOS_AUDIO[mime_base]}
    GRAVACOES.mkdir(parents=True, exist_ok=True)
    grav_salvar(g)
    return g


# ---------------------------------------------------------------- transcrição dos discursos
transcritor = transcricao.Motor(BANCO.parent)
fila_transcricao: "queue.Queue[str]" = queue.Queue()
LIMITE_TRANSCRICAO = 200_000  # caracteres


def grav_publica(g: dict) -> dict:
    """O que vai no estado transmitido a todos: sem o texto (pesado e, antes de publicado, não é público)."""
    t = g.get("transcricao")
    if not t:
        return g
    resumo = {k: t.get(k) for k in ("status", "publicada", "revisada", "motor", "erro")}
    resumo["palavras"] = len((t.get("texto") or "").split())
    return {**g, "transcricao": resumo}


def transcricao_enfileirar(g: dict):
    if not transcritor.ligado or not g.get("fim"):
        return
    g["transcricao"] = {**(g.get("transcricao") or {}), "status": "pendente", "erro": None}
    grav_salvar(g)
    fila_transcricao.put(g["id"])


def transcricao_retomar():
    """Ao iniciar: transcreve o que ficou pela metade e as gravações que ainda não têm transcrição."""
    if not transcritor.ligado:
        return
    for (texto_json,) in db.execute("select json from gravacoes order by criado").fetchall():
        g = json.loads(texto_json)
        if g.get("fim") and (g.get("transcricao") or {}).get("status") in (None, "pendente", "processando"):
            transcricao_enfileirar(g)


def _pos_gravacao(g: dict):
    gravacao_finalizar_arquivo(g)
    with trava:
        g = grav_obter(g["id"])
        if g:
            transcricao_enfileirar(g)
            confirmar()


@sempre
def trabalhador_transcricao():
    while True:
        gid = fila_transcricao.get()
        with trava:
            g = grav_obter(gid)
            if not g or (g.get("transcricao") or {}).get("status") != "pendente":
                continue
            g["transcricao"]["status"] = "processando"
            grav_salvar(g)
            confirmar()
        duracao = ((g.get("fim") or g["ultimo"]) - g["criado"]) / 1000
        try:
            r, erro = transcritor.transcrever(GRAVACOES / f"{g['id']}.{g['ext']}", duracao), None
            if not r["texto"]:
                r, erro = None, "Nenhuma fala reconhecida no áudio."
        except Exception as e:  # motor externo: qualquer falha vira status de erro, sem derrubar o servidor
            r, erro = None, (str(e) or e.__class__.__name__)[:300]
        with trava:
            g = grav_obter(gid)
            if not g:
                continue
            t = g.get("transcricao") or {}
            if t.get("revisada"):  # alguém corrigiu à mão enquanto o motor trabalhava: vale o texto revisado
                t["status"] = "pronta"
            elif r:
                t.update(status="pronta", texto=r["texto"][:LIMITE_TRANSCRICAO], segmentos=r["segmentos"],
                         motor=r["motor"], erro=None, revisada=False, quando=agora(),
                         publicada=bool(S["cfg"].get("transcricaoAutoPublicar")))
            else:
                t.update(status="erro", erro=erro, quando=agora())
            g["transcricao"] = t
            grav_salvar(g)
            confirmar()


@acao("presidente")
def transcricaoRefazer(ator, id):
    g = _gravacao(id)
    if not transcritor.ligado:
        raise Erro("A transcrição automática está desligada neste servidor. Você pode digitar o texto.")
    if not g.get("fim"):
        raise Erro("A gravação ainda não terminou.")
    if (g.get("transcricao") or {}).get("status") in ("pendente", "processando"):
        raise Erro("Esta gravação já está na fila de transcrição.")
    g["transcricao"] = {"status": "pendente"}
    transcricao_enfileirar(g)


@acao("presidente")
def transcricaoSalvar(ator, id, texto=None, publicada=None):
    g = _gravacao(id)
    t = g.get("transcricao") or {}
    if texto is not None:
        texto = re.sub(r"[ \t]+", " ", str(texto)).strip()
        if len(texto) > LIMITE_TRANSCRICAO:
            raise Erro("Texto grande demais.")
        if texto != (t.get("texto") or ""):
            t.update(texto=texto, segmentos=[], revisada=True, motor=t.get("motor") or "digitada")
        if t.get("status") != "processando" or texto:
            t["status"] = "pronta" if texto else "vazia"
    if publicada is not None:
        if publicada and not (t.get("texto") or "").strip():
            raise Erro("Não há texto para publicar.")
        t["publicada"] = bool(publicada)
    t["quando"] = agora()
    g["transcricao"] = t
    grav_salvar(g)
    registrar("presidente", "transcricao", {"id": id, "publicada": t.get("publicada", False)})


def _trecho(texto: str, termos: list[str], raio: int = 140) -> str:
    base = _sem_acento(texto.lower())
    pos = min((base.find(t) for t in termos if base.find(t) >= 0), default=0)
    ini, fim = max(0, pos - raio), min(len(texto), pos + raio)
    return ("…" if ini else "") + texto[ini:fim].strip() + ("…" if fim < len(texto) else "")


def discursos_publicos(q: str = "", pid: str = "", limite: int = 30) -> dict:
    termos = [t for t in _sem_acento(q.lower()).split() if len(t) > 1][:8]
    titulos = dict(db.execute("select id, titulo from sessoes").fetchall())
    titulos[S["sessao"]["id"]] = S["casa"]["titulo"]
    achados = []
    for (texto_json,) in db.execute("select json from gravacoes order by criado desc").fetchall():
        g = json.loads(texto_json)
        t = g.get("transcricao") or {}
        if not t.get("publicada") or not t.get("texto") or (pid and g["pid"] != pid):
            continue
        base = _sem_acento(t["texto"].lower())
        if termos and not all(x in base for x in termos):
            continue
        achados.append((sum(base.count(x) for x in termos), g, t))
    if termos:
        achados.sort(key=lambda a: (-a[0], -a[1]["inicio"]))
    return {"total": len(achados), "termos": termos, "itens": [
        {"id": g["id"], "pid": g["pid"], "nome": g["nome"], "partido": g["partido"], "fase": g.get("faseNome", ""),
         "sessao": titulos.get(g["sessao"], ""), "inicio": g["inicio"], "audio": f"/gravacao/{g['id']}.{g['ext']}",
         "palavras": len(t["texto"].split()), "revisada": bool(t.get("revisada")), "ocorrencias": n,
         "trecho": _trecho(t["texto"], termos)} for n, g, t in achados[:max(1, min(limite, 100))]]}


# ---------------------------------------------------------------- transmissão ao vivo
def _transmissao_mudou():
    with trava:
        confirmar()


transmissor = transmissao.Transmissor(_transmissao_mudou)


def _quadro_youtube() -> dict:
    """Retrato da sessão para a imagem do YouTube (orador, aparte, votação, fase)."""
    with trava:
        q = {"casa": S["casa"]["nome"], "titulo": S["casa"]["titulo"], "logoV": S["casa"].get("logoV") or 0,
             "fase": fase(S["faseAtual"])["nome"] if S.get("faseAtual") else "", "orador": None, "votacao": None}
        i, o = S.get("interr"), S.get("orador")
        alvo = (i, TIPOS.get(i["tipo"], "Aparte")) if i and i.get("pid") else (o, None) if o and o.get("pid") else (None, None)
        if alvo[0]:
            t, rotulo = alvo
            p = next((x for x in S["parls"] if x["id"] == t["pid"]), None)
            if p:
                q["orador"] = {"id": p["id"], "nome": p["nome"], "partido": p["partido"], "fotoV": p.get("fotoV") or 0,
                               "fase": rotulo or fase(t.get("fase") or S["faseAtual"])["nome"], "restante": restante(t)}
        v = S.get("votacao")
        if v and v.get("aberta"):
            q["votacao"] = {"numero": v.get("numero"), "ementa": v.get("ementa")}
        return q


def _imagem_youtube(chave: str) -> bytes | None:
    with trava:
        linha = db.execute("select dados from imagens where chave = ?", (chave,)).fetchone()
    return linha[0] if linha else None


canal_youtube = youtube.YouTube(_transmissao_mudou, _quadro_youtube, _imagem_youtube)


@acao("presidente")
def youtubeConfigurar(ator, chave=None, url="", link="", ligado=True):
    cfg = S["_youtube"]
    try:
        cfg["url"] = youtube.validar_url(url)
        if chave is not None and str(chave).strip():
            cfg["chave"] = youtube.validar_chave(chave)
    except ValueError as e:
        raise Erro(str(e))
    link = str(link or "").strip()
    if link and not re.fullmatch(r"https://[^\s<>\"']{3,300}", link):
        raise Erro("O link público deve começar com https:// (ex.: https://www.youtube.com/@suacamara/live).")
    S["youtube"].update(configurado=bool(cfg["chave"]), ligado=bool(ligado), link=link)
    registrar("presidente", "youtubeConfigurar", {"url": cfg["url"], "ligado": bool(ligado)})


@acao("presidente")
def youtubeRemoverChave(ator):
    canal_youtube.desligar(avisar=False)
    S["_youtube"]["chave"] = ""
    S["youtube"]["configurado"] = False


def _youtube_vai() -> bool:
    return bool(S["youtube"]["configurado"] and S["youtube"]["ligado"] and S["_youtube"]["chave"])


@acao("presidente")
def transmissaoConfigurar(ator, preset="azuracast", host="", porta=8005, mount="/", usuario="", senha=None, tls=False,
                          bitrate=128, publico="", nome=""):
    if preset == "local":  # a rádio puxa de /aovivo: não há servidor de destino para validar
        host, porta, mount, usuario = "", 8005, "/", ""
    host = str(host or "").strip().lower()
    for prefixo in ("https://", "http://", "icecast://"):
        host = host.removeprefix(prefixo)
    host = host.split("/")[0]
    if host and not re.fullmatch(r"[a-z0-9.-]{1,253}|\[[0-9a-f:]+\]", host):
        raise Erro("Endereço do servidor de rádio inválido (use só o nome ou IP, sem http://).")
    try:
        porta = int(porta)
    except (TypeError, ValueError):
        raise Erro("Porta inválida.")
    if not 1 <= porta <= 65535:
        raise Erro("Porta inválida.")
    mount = "/" + str(mount or "").strip().lstrip("/")
    if not re.fullmatch(r"/[A-Za-z0-9._/-]{0,100}", mount):
        raise Erro("Ponto de montagem inválido (ex.: / ou /sessao).")
    usuario = str(usuario or "").strip()
    if len(usuario) > 100 or any(c in usuario for c in "\r\n"):
        raise Erro("Usuário inválido.")
    if int(bitrate) not in transmissao.BITRATES:
        raise Erro("Qualidade inválida.")
    publico = str(publico or "").strip()
    if publico and not re.fullmatch(r"https?://[^\s<>\"']{3,300}", publico):
        raise Erro("O endereço para ouvir deve começar com https:// (ou http://).")
    local = preset == "local"
    cfg = S["_transmissao"]
    cfg.update(preset=preset if preset in transmissao.PRESETS else "icecast", host=host, porta=porta, mount=mount,
               usuario=usuario, tls=bool(tls), bitrate=int(bitrate))
    if senha is not None and str(senha) != "":
        cfg["senha"] = str(senha)[:200]
    S["transmissao"].update(configurada=local or bool(cfg["host"] and cfg["senha"]), publico=publico,
                            nome=str(nome or "").strip()[:120], local=local)
    registrar("presidente", "transmissaoConfigurar", {"host": host, "porta": porta, "mount": mount})


@acao("presidente")
def transmissaoLigar(ator, camera=False):
    if not shutil.which("ffmpeg"):
        raise Erro("O servidor precisa do ffmpeg para transmitir (apt install ffmpeg).")
    vai_youtube = _youtube_vai()
    if not S["transmissao"]["configurada"] and not vai_youtube:
        raise Erro("Configure a rádio ou o YouTube em Configurações antes de transmitir.")
    # sem rádio configurada, o áudio ainda vira MP3 aqui dentro (legendas e /aovivo), sem enviar a lugar nenhum
    cfg = S["_transmissao"] if S["transmissao"]["configurada"] else {**S["_transmissao"], "preset": "local"}
    try:
        transmissor.ligar_em_segundo_plano(cfg, f"{S['casa']['nome']} · {S['casa']['titulo']}")
    except RuntimeError as e:
        raise Erro(str(e))
    if vai_youtube:
        url, chave = S["_youtube"]["url"], S["_youtube"]["chave"]

        def ligar_youtube():
            try:
                canal_youtube.ligar(url, chave, bool(camera))
            except (RuntimeError, ValueError, OSError) as e:
                canal_youtube.erro = str(e)
            _transmissao_mudou()
        threading.Thread(target=ligar_youtube, daemon=True).start()
    registrar("presidente", "transmissaoLigar", {"camera": bool(camera), "youtube": vai_youtube})


@acao("presidente")
def transmissaoDesligar(ator):
    transmissor.desligar(avisar=False)
    canal_youtube.desligar(avisar=False)
    registrar("presidente", "transmissaoDesligar", {})


def ao_vivo() -> dict:
    o = S.get("orador")
    falando = None
    if o and o.get("pid"):
        p = next((x for x in S["parls"] if x["id"] == o["pid"]), None)
        falando = p and {"nome": p["nome"], "partido": p["partido"]}
    yt = S["youtube"]["link"] if canal_youtube.ativa and S["youtube"]["link"] else None
    return {"ativa": (transmissor.ativa and bool(S["transmissao"]["publico"])) or bool(yt), "publico": S["transmissao"]["publico"],
            "youtube": yt,
            "nome": S["transmissao"]["nome"], "casa": S["casa"]["nome"], "titulo": S["casa"]["titulo"], "falando": falando,
            "desde": transmissor.desde if transmissor.ativa else None,
            "radio": "/radio.mp3" if RADIO_LOCAL else None, "perguntas": S["perguntasAbertas"]}


# ---------------------------------------------------------------- pacote 2026: IA, legendas, participação, vídeo, dados abertos
participacao.criar_tabela(db)
cidadania.criar_tabelas(db)
regimento.criar_tabela(db)
base_regimento = regimento.Base()


@acao("presidente")
def normaSalvar(ator, nome, texto="", dataUrl=""):
    """Regimento Interno, Lei Orgânica…: texto colado, .txt lido no navegador ou PDF (lido pelo pdftotext)."""
    if dataUrl:
        cab, _, b64 = str(dataUrl).partition(",")
        if cab != "data:application/pdf;base64":
            raise Erro("Envie o PDF ou cole o texto.")
        try:
            texto = regimento.pdf_para_texto(base64.b64decode(b64, validate=True))
        except ValueError as e:
            raise Erro(str(e))
    try:
        r = regimento.salvar(db, nome, texto)
    except ValueError as e:
        raise Erro(str(e))
    S["normasRev"] = S.get("normasRev", 0) + 1
    return r


@acao("presidente")
def normaRemover(ator, id):
    regimento.remover(db, str(id))
    S["normasRev"] = S.get("normasRev", 0) + 1


def meta_ideia() -> int:
    return int(S["cfg"].get("ideiaMeta") or cidadania.meta_ideia(S["relevancia"].get("populacao")))


@acao("presidente")
def projetoAnalisar(ator, id):
    """Rascunho da análise prévia (regras + IA, se ligada), em segundo plano: a IA pode levar um minuto."""
    p = proj_obter(id)
    if not p:
        raise Erro("Projeto não encontrado.")
    if (p.get("analise") or {}).get("emAndamento") and agora() - p["analise"].get("gerado", 0) < 300_000:
        raise Erro("A análise deste projeto já está sendo feita.")
    p["analise"] = {"emAndamento": True, "gerado": agora()}
    proj_salvar(p)
    S["projetosRev"] += 1
    copia = json.loads(json.dumps(p))

    def rodar():
        try:
            with integridade.contexto_ia(f"projeto:{id}"):
                r = analise.analisar(copia)
        except Exception as e:  # nunca deixa a análise "em andamento" para sempre
            r = {**analise.regras(copia), "resumo": None, "observacoes": None, "motor": "regras", "gerado": agora(),
                 "revisada": False, "revisor": None, "aviso": f"Falha na IA: {e.__class__.__name__}"}
        with trava:
            q = proj_obter(id)
            if q:
                q["analise"] = r
                proj_salvar(q)
                S["projetosRev"] += 1
                confirmar()
    threading.Thread(target=rodar, daemon=True).start()


@acao("presidente")
def projetoAnaliseRevisar(ator, id, resumo=None, revisada=True):
    """Revisão humana: só a análise revisada (e o resumo editado) aparece para o público."""
    p = proj_obter(id)
    a = (p or {}).get("analise") or {}
    if not a or a.get("emAndamento"):
        raise Erro("Gere a análise antes de revisar.")
    if resumo is not None:
        a["resumo"] = " ".join(str(resumo).split())[:900] or None
    a.update(revisada=bool(revisada), revisor="Mesa Diretora" if revisada else None, revisadaEm=agora() if revisada else None)
    if revisada:
        integridade.marcar_revisado(db, f"projeto:{id}", "Mesa Diretora", a["revisadaEm"])
    p["analise"] = a
    proj_salvar(p)
    S["projetosRev"] += 1


@acao("presidente")
def perguntasAbrir(ator, abertas=True):
    S["perguntasAbertas"] = bool(abertas)
    if not abertas:
        S["telaoPergunta"] = None


@acao("presidente")
def perguntaModerar(ator, id, situacao):
    try:
        p = cidadania.pergunta_moderar(db, str(id), situacao)
    except (LookupError, ValueError) as e:
        raise Erro(str(e))
    if situacao != "aprovada" and (S.get("telaoPergunta") or {}).get("id") == p["id"]:
        S["telaoPergunta"] = None
    S["cidadaniaRev"] += 1


@acao("presidente")
def perguntaTelao(ator, id=None):
    """Mostra uma pergunta aprovada no telão (ou tira, sem id)."""
    if not id:
        S["telaoPergunta"] = None
        return
    p = cidadania.pergunta_obter(db, str(id))
    if not p or p["situacao"] not in ("aprovada", "respondida"):
        raise Erro("Aprove a pergunta antes de mostrar no telão.")
    S["telaoPergunta"] = {k: p[k] for k in ("id", "nome", "bairro", "texto")}


@acao("presidente")
def ideiaResponder(ator, id, situacao, texto):
    try:
        cidadania.ideia_responder(db, str(id), situacao, texto, "Mesa Diretora")
    except (LookupError, ValueError) as e:
        raise Erro(str(e))
    S["cidadaniaRev"] += 1


@acao("presidente")
def ideiaOcultar(ator, id, oculta=True):
    try:
        cidadania.ideia_ocultar(db, str(id), oculta)
    except LookupError as e:
        raise Erro(str(e))
    S["cidadaniaRev"] += 1


@acao("presidente")
def ideiaMeta(ator, meta=0):
    meta = int(meta or 0)
    if meta and not 5 <= meta <= 100_000:
        raise Erro("Meta entre 5 e 100000 apoios (0 = automática pela população).")
    S["cfg"]["ideiaMeta"] = meta


# --- ata por IA (rascunho por item da pauta; nunca é público antes de revisado na ata)
def _ler_resumo(sessao: str) -> dict | None:
    if sessao == "atual":
        return S.get("_resumoAtual")
    r = sessao_arquivada(sessao) if str(sessao).isalnum() else None
    return (r or {}).get("resumoIA")


def _guardar_resumo(sessao: str, res: dict):
    if sessao == "atual":
        S["_resumoAtual"] = res
        return
    linha = db.execute("select json from sessoes where id = ?", (sessao,)).fetchone()
    if linha:
        r = json.loads(linha[0])
        r["resumoIA"] = res
        db.execute("update sessoes set json = ? where id = ?", (json.dumps(r, ensure_ascii=False), sessao))
        db.commit()


def _rodar_resumo(sessao: str, r: dict, casa: str):
    def avancar(n, total):
        with trava:
            S["resumoJob"].update(feitos=n, total=total)
            confirmar()
    try:
        with integridade.contexto_ia(f"ata:{r['id']}"):
            res, erro = resumo_mod.gerar(r, casa, avancar), None
    except Exception as e:  # o rascunho é auxiliar: falha vira aviso no painel
        res, erro = None, (str(e) or e.__class__.__name__)[:200]
    with trava:
        if res:
            _guardar_resumo(sessao, res)
        S["resumoJob"].update(fim=agora(), erro=erro)
        confirmar()


@acao("presidente")
def ataResumoGerar(ator, sessao="atual"):
    job = S.get("resumoJob")
    if job and not job.get("fim"):
        raise Erro("Já estou gerando um resumo; aguarde terminar.")
    r = retrato() if sessao == "atual" else (sessao_arquivada(sessao) if str(sessao).isalnum() else None)
    if not r:
        raise Erro("Sessão não encontrada.")
    if not resumo_mod.blocos(r):
        raise Erro("Ainda não há falas nem votações registradas nesta sessão.")
    S["resumoJob"] = {"sessao": sessao, "inicio": agora(), "fim": None, "feitos": 0, "total": len(resumo_mod.blocos(r)),
                      "erro": None, "motor": ia.descricao()}
    threading.Thread(target=_rodar_resumo, args=(sessao, r, S["casa"]["nome"]), daemon=True).start()


@acao("presidente")
def ataResumoEditar(ator, sessao, chave, texto=None, revisado=None):
    res = _ler_resumo(sessao)
    item = next((i for i in (res or {}).get("itens", []) if i["chave"] == chave), None)
    if not item:
        raise Erro("Item do resumo não encontrado.")
    if texto is not None:
        texto = str(texto).strip()
        if len(texto) > 20000:
            raise Erro("Texto grande demais.")
        item["texto"] = texto
    if revisado is not None:
        item["revisado"] = bool(revisado)
    _guardar_resumo(sessao, res)


# --- legendas ao vivo
def _ao_legenda(item: dict):
    transmitir(json.dumps({"tipo": "legenda", "agora": agora(), **item}, ensure_ascii=False))


legendador = legendas_mod.Legendador(transmissor, transcritor.cmd[0] if transcritor.nome == "local" else None, _ao_legenda)


# --- participação cidadã (consulta pública artigo por artigo)
@acao("presidente")
def projetoTexto(ator, id, texto):
    p = proj_obter(id)
    if not p:
        raise Erro("Projeto não encontrado.")
    texto = str(texto or "").replace("\r", "").strip()
    if len(texto) > 200_000:
        raise Erro("Texto grande demais.")
    p["texto"] = texto
    proj_salvar(p)
    S["projetosRev"] += 1


@acao("presidente")
def consultaAbrir(ator, id, aberta=True, prazo=None):
    p = proj_obter(id)
    if not p:
        raise Erro("Projeto não encontrado.")
    if aberta and not participacao.artigos(p.get("texto", "")):
        raise Erro("Cadastre o texto do projeto (com os artigos) antes de abrir a consulta.")
    if p["situacao"] == "proposta":
        raise Erro("Protocole o projeto antes de abrir a consulta pública.")
    try:
        prazo = int(prazo) if prazo else None
    except (TypeError, ValueError):
        raise Erro("Prazo inválido.")
    p["consulta"] = {"aberta": bool(aberta), "prazo": prazo, "desde": (p.get("consulta") or {}).get("desde") or agora()}
    proj_salvar(p)
    S["projetosRev"] += 1


def _consulta_aberta(p: dict) -> bool:
    c = p.get("consulta") or {}
    return bool(c.get("aberta")) and (not c.get("prazo") or agora() < c["prazo"])


@acao("presidente", "parlamentar")
def consultaResponder(ator, comentario, texto, situacao):
    c = participacao.obter(db, comentario)
    if not c:
        raise Erro("Comentário não encontrado.")
    p = proj_obter(c["projeto"]) or {}
    if ator["papel"] == "parlamentar":
        nome = parl(ator["pid"])["nome"]
        if (p.get("autor") or "").strip().lower() != nome.strip().lower():
            raise Erro("Só a autoria do projeto ou a presidência respondem às contribuições.")
        autor = nome
    else:
        autor = "Presidência"
    try:
        participacao.responder(db, comentario, texto, situacao, autor)
    except (ValueError, LookupError) as e:
        raise Erro(str(e))
    S["projetosRev"] += 1


@acao("presidente")
def consultaOcultar(ator, comentario, oculto=True):
    try:
        participacao.ocultar(db, comentario, oculto)
    except LookupError as e:
        raise Erro(str(e))
    S["projetosRev"] += 1


# --- emendas estruturadas, comparativo e redação final (lógica em server/emendas.py)
emendas_mod.criar_tabela(db)


def _emenda(eid) -> dict:
    e = emendas_mod.obter(db, str(eid or "")) if str(eid or "").isalnum() else None
    if not e:
        raise Erro("Emenda não encontrada.")
    return e


@acao("presidente", "parlamentar")
def emendaPropor(ator, projeto, tipo, alvo="", texto="", justificativa="", autorPid=None, aglutina=None):
    """O parlamentar emenda só em nome próprio; a presidência registra em nome de um vereador ou da Mesa."""
    p = proj_obter(str(projeto)) if str(projeto).isalnum() else None
    if not p:
        raise Erro("Projeto não encontrado.")
    if p["situacao"] not in ("em_tramitacao", "em_pauta"):
        raise Erro("Só projetos em tramitação recebem emendas.")
    if ator["papel"] == "parlamentar":
        autor = parl(ator["pid"])
        pid, nome = autor["id"], autor["nome"]
    elif autorPid:
        autor = parl(str(autorPid))
        pid, nome = autor["id"], autor["nome"]
    else:
        pid, nome = None, "Mesa Diretora"
    try:
        emendas_mod.propor(db, p, str(tipo), str(alvo or ""), texto, justificativa, pid, nome,
                           aglutina if isinstance(aglutina, list) else None)
    except ValueError as e:
        raise Erro(str(e))
    S["projetosRev"] += 1


@acao("presidente", "parlamentar")
def emendaRetirar(ator, id):
    """Retirada pelo autor (ou pela presidência, a pedido dele) antes do resultado."""
    e = _emenda(id)
    if any(m.get("emenda") == e["id"] and m["situacao"] == "em_votacao" for m in S["pauta"]):
        raise Erro("A emenda está em votação.")
    try:
        emendas_mod.retirar(db, e, ator.get("pid"), ator["papel"] == "presidente")
    except (ValueError, PermissionError) as ex:
        raise Erro(str(ex))
    S["pauta"] = [m for m in S["pauta"] if m.get("emenda") != e["id"] or m["situacao"] != "pendente"]
    S["projetosRev"] += 1


@acao("presidente")
def emendaResultado(ator, id, situacao, nota=""):
    """Registro manual do resultado (votação fora do sistema, prejudicialidade declarada, desfazer)."""
    e = _emenda(id)
    try:
        emendas_mod.registrar_resultado(db, e, str(situacao), {"por": "presidência", "nota": str(nota or "")[:300]})
    except ValueError as ex:
        raise Erro(str(ex))
    S["projetosRev"] += 1


@acao("presidente")
def emendaPautar(ator, id):
    """Põe a emenda na pauta como matéria própria: a votação existente (nominal, simbólica, secreta) decide."""
    e = _emenda(id)
    if e["situacao"] != "apresentada":
        raise Erro("Só emenda apresentada vai à votação.")
    if any(m["id"] == e["id"] for m in S["pauta"]):
        raise Erro("Já está na pauta.")
    p = proj_obter(e["projeto"]) or {}
    S["pauta"].append({"id": e["id"], "numero": emendas_mod.titulo(e, p.get("numero", "")),
                       "ementa": f"{emendas_mod.TIPOS[e['tipo']]} — {e['alvoRef']}: {(e['texto'] or 'suprime o dispositivo')[:240]}",
                       "autor": e["autor"], "situacao": "pendente", "emenda": e["id"], "projeto": e["projeto"]})


def _emenda_da_pauta(mid, resultado, extra: dict):
    m = next((x for x in S["pauta"] if x["id"] == mid and x.get("emenda")), None)
    e = emendas_mod.obter(db, m["emenda"]) if m else None
    if e and resultado in ("aprovada", "rejeitada") and e["situacao"] != "retirada":
        emendas_mod.registrar_resultado(db, e, resultado, extra)
        S["projetosRev"] += 1


def _com_emendas(nome: str, depois):
    """Embrulha uma ação existente sem mexer no corpo dela: o resultado da matéria-emenda vai para a emenda."""
    papeis, original = ACOES[nome]

    def embrulho(ator, **args):
        original(ator, **args)
        depois(**args)
    ACOES[nome] = (papeis, embrulho)


def _apos_encerrar_votacao(resultado=None):
    v = S["votacao"] or {}
    _emenda_da_pauta(v.get("materia"), v.get("resultado"),
                     {"votacao": v.get("tipo"), "contagem": publico_votacao(v)["contagem"] if v else None})


_com_emendas("votacaoEncerrar", _apos_encerrar_votacao)
_com_emendas("materiaSituacao", lambda id, situacao: _emenda_da_pauta(id, situacao, {"por": "presidência (pauta)"}))


def emendas_get(h, caminho: str, qs: dict):
    """GET /api/emendas, /api/emendas/<id>, /api/emendas/<id>/comparativo[?emenda=], /redacao-final/<id>[.pdf] e
    /dados/akn/projeto/<id>/redacao-final.xml — públicos para projeto protocolado; None se o caminho não é daqui."""
    m = re.fullmatch(r"/api/emendas(?:/([A-Za-z0-9]+)(/comparativo)?)?|/redacao-final/([A-Za-z0-9]+)(\.pdf)?"
                     r"|/dados/akn/projeto/([A-Za-z0-9]+)/redacao-final\.xml", caminho)
    if not m:
        return None
    with trava:
        try:
            ator = h._ator()
        except TrocaPin:
            ator = None
        casa = S["casa"]["nome"]
        if caminho == "/api/emendas":
            lista = [{k: p.get(k) for k in ("id", "numero", "ementa", "autor", "situacao")}
                     | {"emendas": emendas_mod.contagem(db, p["id"])}
                     for p in proj_todos() if p["situacao"] != "proposta" and p.get("texto")]
            return h._json(200, {"projetos": lista, "casa": casa})
        pid = m.group(1) or m.group(3) or m.group(5)
        p = proj_obter(pid)
        if not p or p["situacao"] == "proposta" or not p.get("texto"):
            return h._json(404, {"erro": "Projeto não encontrado."})
        ems = emendas_mod.do_projeto(db, pid)
        eu = parl(ator["pid"]) if ator and ator["papel"] == "parlamentar" else None
    publico_p = {k: p.get(k) for k in ("id", "numero", "ementa", "autor", "situacao")}
    if m.group(1) and not m.group(2):
        cons = emendas_mod.consolidar(p["texto"], ems)
        return h._json(200, {"projeto": publico_p, "casa": casa,
                             "dispositivos": emendas_mod.com_referencias(cons["original"]),
                             "emendas": [e | {"titulo": emendas_mod.titulo(e, p.get("numero", ""))} for e in ems],
                             "tipos": emendas_mod.TIPOS, "situacoes": emendas_mod.SITUACOES,
                             "final": {"texto": cons["texto"], "aplicadas": cons["aplicadas"], "avisos": cons["avisos"]},
                             "pode": {"emendar": bool(ator) and p["situacao"] in ("em_tramitacao", "em_pauta"),
                                      "presidente": bool(ator and ator["papel"] == "presidente"),
                                      "pid": eu["id"] if eu else None}})
    if m.group(2):
        eid = (qs.get("emenda") or [""])[0]
        if eid and not any(e["id"] == eid for e in ems):
            return h._json(404, {"erro": "Emenda não encontrada."})
        cons = emendas_mod.consolidar(p["texto"], ems, so=eid or None)
        return h._json(200, emendas_mod.comparativo(cons["original"], cons["dispositivos"])
                       | {"avisos": cons["avisos"], "aplicadas": cons["aplicadas"]})
    cons = emendas_mod.consolidar(p["texto"], ems)
    nome = None
    if m.group(5):
        corpo, tipo = akn.projeto_final(p, cons["dispositivos"], ems, casa).encode(), "application/akn+xml; charset=utf-8"
    elif m.group(4):
        corpo, tipo = emendas_mod.documento_pdf(p, cons, ems, casa), "application/pdf"
        nome = f"Redação final - {p.get('numero') or p['id']}.pdf"
    else:
        corpo, tipo = emendas_mod.documento_html(p, cons, ems, casa).encode(), "text/html; charset=utf-8"
    h.send_response(200)
    h.send_header("Content-Type", tipo)
    h.send_header("Content-Length", str(len(corpo)))
    h.send_header("Cache-Control", "no-cache")
    if nome:
        h.send_header("Content-Disposition", "inline; filename*=UTF-8''" + urllib.parse.quote(nome))
    if tipo.startswith("text/html"):
        h._cabecalhos_seguranca()
    else:
        h.send_header("X-Content-Type-Options", "nosniff")
    h.end_headers()
    h.wfile.write(corpo)
    return True


# --- vídeo da sessão com marcação por orador e por item
def _validar_video(url: str) -> str:
    url = str(url or "").strip()
    if url and not re.fullmatch(r"https://[^\s<>\"']{8,400}", url):
        raise Erro("Informe o endereço do vídeo começando com https://")
    return url


@acao("presidente")
def sessaoVideo(ator, sessao="atual", url="", inicio=None):
    url = _validar_video(url)
    try:
        inicio = int(inicio) if inicio else None
    except (TypeError, ValueError):
        raise Erro("Horário de início do vídeo inválido.")
    video = {"url": url, "inicio": inicio} if url else None
    if sessao == "atual":
        S["sessao"]["video"] = video
        return
    linha = db.execute("select json from sessoes where id = ?", (sessao,)).fetchone() if str(sessao).isalnum() else None
    if not linha:
        raise Erro("Sessão não encontrada.")
    r = json.loads(linha[0])
    r["video"] = video
    db.execute("update sessoes set json = ? where id = ?", (json.dumps(r, ensure_ascii=False), sessao))
    db.commit()


def sessao_publica(r: dict) -> dict:
    """Só dados públicos: pauta, falas (com áudio e transcrição publicada), votações e capítulos do vídeo."""
    grav = {}
    for g in r.get("gravacoes") or []:
        t = g.get("transcricao") or {}
        grav.setdefault((g["pid"], g["inicio"]), {"audio": f"/gravacao/{g['id']}.{g['ext']}",
                                                  "transcricao": g["id"] if t.get("publicada") else None})
    video = r.get("video") or None
    base = (video or {}).get("inicio")
    capitulos = []
    falas = []
    for h in sorted(r.get("hist") or [], key=lambda h: h["inicio"]):
        extra = grav.get((h["pid"], h["inicio"])) or next((v for (pid, ini), v in grav.items()
                                                           if pid == h["pid"] and abs(ini - h["inicio"]) < 15000), {})
        falas.append({"nome": h["nome"], "partido": h["partido"], "fase": h.get("faseNome", ""), "inicio": h["inicio"],
                      "usado": h.get("usado"), "materia": h.get("materia"), **extra})
        if base:
            capitulos.append({"tempo": max(0, round((h["inicio"] - base) / 1000)), "rotulo": f"{h['nome']} — {h.get('faseNome', '')}"})
    votacoes = [{k: v.get(k) for k in ("materia", "numero", "ementa", "tipo", "quorum", "resultado", "contagem", "inicio", "fim")}
                | {"votos": v.get("votos") if v.get("tipo") == "nominal" else None} for v in r.get("votacoes") or []]
    if base:
        capitulos += [{"tempo": max(0, round(((v.get("inicio") or base) - base) / 1000)), "rotulo": f"Votação: {v['numero']}"}
                      for v in votacoes]
        capitulos.sort(key=lambda c: c["tempo"])
    nomes = {p["id"]: p["nome"] for p in r.get("parls") or []}
    return {"id": r["id"], "titulo": r.get("titulo"), "casa": r.get("casa"), "inicio": r.get("inicio"), "fim": r.get("fim"),
            "video": video, "capitulos": capitulos, "falas": falas, "votacoes": votacoes, "nomes": nomes,
            "pauta": [{k: m.get(k) for k in ("id", "numero", "ementa", "autor", "situacao")} for m in r.get("pauta") or []],
            "presentes": len(r.get("presenca") or {}), "membros": len(r.get("parls") or [])}


# --- primeira configuração: de qual cidade é esta Câmara (TSE + IBGE)
trava_cidade = threading.Lock()


def camara_configurada() -> bool:
    """Falso só no banco de exemplo: sem município, sem vereadores do TSE, nome padrão e nenhuma sessão encerrada."""
    if S["casa"].get("municipio") or any(p.get("tse") for p in S["parls"]) or S["sapl"].get("casa"):
        return True
    if S["casa"].get("nome") != "Câmara Municipal":
        return True
    return bool(db.execute("select count(*) from sessoes").fetchone()[0])


def aplicar_cidade(dados: dict):
    """Troca os dados de exemplo pelos do município (já baixados por cidade.buscar). Chamar com a trava."""
    S["casa"].update(nome=dados.get("camara") or f"Câmara Municipal de {dados['municipio']}", titulo="Sessão Ordinária",
                     municipio=dados["municipio"], uf=dados["uf"], ibge=dados["ibge"])
    logo = dados.get("logo")
    if logo and logo.get("fonte") and S["casa"].get("logoV"):
        logo = None   # brasão automático (Wikidata) nunca troca o que a Câmara já enviou
    if logo and logo.get("mime") in TIPOS_IMAGEM and logo.get("dados"):   # brasão do pacote da Câmara ou do Wikidata
        db.execute("insert or replace into imagens (chave, mime, dados) values (?, ?, ?)", ("logo", logo["mime"], logo["dados"]))
        S["casa"]["logoV"] = agora()
        S["casa"]["brasaoFonte"] = logo.get("fonte", "")   # de onde veio o brasão automático (Wikimedia Commons)
    if dados.get("portal"):   # portal legislativomunicipal.com: a instalação importa sessões e matérias de lá
        S["casa"]["portal"] = dados["portal"]
        if not S["rodape"]["camara"].get("site"):
            S["rodape"]["camara"]["site"] = dados["portal"]
    for p in S["parls"]:
        db.execute("delete from imagens where chave = ?", (p["id"],))
    novo = estado_inicial()
    for k in ("presenca", "filas", "orador", "interr", "pedidos", "votacao"):
        S[k] = novo[k]
    S["pauta"], S["parls"], S["_pins"], S["_pinsIniciais"] = [], [], {}, {}
    S["_tokens"] = {t: a for t, a in S["_tokens"].items() if a.get("papel") == "presidente"}
    if dados.get("populacao"):
        S["relevancia"]["populacao"] = dados["populacao"]
    db.execute("delete from projetos")
    S["projetosRev"] = S.get("projetosRev", 0) + 1
    for k, e in enumerate(dados["eleitos"], 1):
        p = {"id": uid(), "nome": e["nome"], "partido": e["partido"], "lider": False, "fotoV": 0, "canal": k, "bloco": "",
             "tse": {"numero": e["numero"], "votos": e["votos"], "sq": e["sq"], "situacao": e["situacao"]}}
        if e.get("foto"):
            db.execute("insert or replace into imagens (chave, mime, dados) values (?, ?, ?)", (p["id"], "image/jpeg", e["foto"]))
            p["fotoV"] = agora()
        S["parls"].append(p)
        definir_pin_inicial(S, p["id"])
        S["partidos"].setdefault(e["partido"], "#5b6b86")
    if S["transmissao"].get("local"):
        S["transmissao"]["nome"] = f"{S['casa']['nome']} — Rádio"
    try:
        (BANCO.parent / ".sem-municipio").unlink(missing_ok=True)   # aviso do instalador .deb
    except OSError:
        pass


# --- programação da rádio local
@acao("presidente")
def radioRemover(ator, nome):
    alvo = (RADIO_DIR / Path(str(nome)).name)
    if alvo.parent != RADIO_DIR or not alvo.is_file():
        raise Erro("Arquivo não encontrado.")
    alvo.unlink()


# --- anexos das matérias (PDF), vistos no celular dos vereadores e no público
@acao("presidente")
def materiaAnexo(ator, id, nome="", dataUrl=""):
    p = proj_obter(id)
    if not p:
        raise Erro("Matéria não encontrada.")
    if not dataUrl:
        p.pop("anexo", None)
        db.execute("delete from imagens where chave = ?", (f"anexo-{id}",))
    else:
        cab, _, b64 = str(dataUrl).partition(",")
        if cab != "data:application/pdf;base64":
            raise Erro("Envie o anexo em PDF.")
        try:
            dados = base64.b64decode(b64, validate=True)
        except ValueError:
            raise Erro("Arquivo inválido.")
        if not dados.startswith(b"%PDF") or len(dados) > 2_800_000:
            raise Erro("PDF inválido ou maior que 2,8 MB.")
        db.execute("insert or replace into imagens (chave, mime, dados) values (?, ?, ?)", (f"anexo-{id}", "application/pdf", dados))
        p["anexo"] = {"nome": str(nome or "documento.pdf")[:120], "v": agora(), "bytes": len(dados)}
    db.commit()
    proj_salvar(p)
    for m in S["pauta"]:
        if m["id"] == id:
            m["anexo"] = p.get("anexo")
    S["projetosRev"] += 1


# --- protocolo eletrônico, tramitação com comissões e pedidos ao Executivo (regras em tramitacao.py)
tramitacao.completar(S)
LIMITE_PDF_RESPOSTA = 2_800_000


def _quem(ator) -> str:
    return "Presidência" if ator["papel"] == "presidente" else parl(ator["pid"])["nome"]


def _nomes_parls() -> dict:
    return {p["id"]: p["nome"] for p in S["parls"]}


def _tram(mid, regra, *args):
    """Lê a matéria, aplica a regra e só então grava: um erro não deixa o banco de projetos pela metade."""
    p = proj_obter(mid) if isinstance(mid, str) and mid.isalnum() else None
    if not p:
        raise Erro("Matéria não encontrada.")
    try:
        r = regra(p, *args)
    except tramitacao.ErroTramitacao as e:
        raise Erro(str(e))
    proj_salvar(p)
    for m in S["pauta"]:
        if m["id"] == p["id"]:
            m.update(numero=p["numero"], ementa=p["ementa"])
    S["projetosRev"] += 1
    return r


def _regra(fn, *args):
    try:
        return fn(*args)
    except tramitacao.ErroTramitacao as e:
        raise Erro(str(e))


def _numeros() -> list[str]:
    return [p.get("numero", "") for p in proj_todos()]


def _nova_proposta(ator, tipo, ementa, texto, justificativa, autor, autor_pid, provisorio=True) -> dict:
    cfg = S["tramitacao"]
    dados = _regra(tramitacao.dados_proposta, cfg, tipo, ementa, texto, justificativa)
    numero = tramitacao.proximo_numero(cfg, "PROP", time.localtime().tm_year, _numeros()) if provisorio else ""
    p = proj_novo(numero=numero, autor=autor, autorPid=autor_pid, situacao="proposta", **dados)
    p["tram"]["etapaEm"] = agora()
    return p


@acao("parlamentar")
def protocoloEnviar(ator, tipo, ementa, texto="", justificativa="", id=None):
    """O vereador protocola pelo app, sempre em seu nome; a secretaria confere, numera e protocola."""
    autor = parl(ator["pid"])["nome"]
    if id:  # proposta devolvida, corrigida pelo autor
        p = proj_obter(id) if isinstance(id, str) and id.isalnum() else None
        if not p or p.get("autorPid") != ator["pid"]:
            raise Erro("Proposta não encontrada.")
        return _tram(id, tramitacao.reenviar, S["tramitacao"], tipo, ementa, texto, justificativa, agora(), autor)
    pendentes = sum(1 for p in proj_todos() if p.get("autorPid") == ator["pid"] and p["situacao"] == "proposta")
    if pendentes >= tramitacao.MAX_PROPOSTAS_PENDENTES:
        raise Erro("Você tem muitas propostas aguardando protocolo. Aguarde a secretaria.")
    p = _nova_proposta(ator, tipo, ementa, texto, justificativa, autor, ator["pid"])
    tramitacao.evento(p, agora(), "recebida", f"Proposta ({tramitacao.tipo(S['tramitacao'], p['tram']['tipo'])['nome']}) enviada pelo autor", autor)
    proj_salvar(p)
    S["projetosRev"] += 1


@acao("presidente")
def tramNova(ator, tipo, ementa, texto="", justificativa="", autor="", autorPid=None):
    """A secretaria protocola direto (matéria do Executivo, da Mesa, de comissão ou entregue em papel)."""
    if autorPid:
        autor = parl(autorPid)["nome"]
    autor = str(autor or "").strip()
    if not 3 <= len(autor) <= 120:
        raise Erro("Informe a autoria.")
    p = _nova_proposta(ator, tipo, ementa, texto, justificativa, autor, autorPid or None, provisorio=False)
    _regra(tramitacao.protocolar, p, S["tramitacao"], p["tram"]["tipo"], agora(), _quem(ator), _numeros())
    proj_salvar(p)
    S["projetosRev"] += 1


@acao("presidente")
def tramProtocolar(ator, id, tipo=None):
    _tram(id, tramitacao.protocolar, S["tramitacao"], tipo, agora(), _quem(ator), _numeros())


@acao("presidente")
def tramDevolver(ator, id, motivo):
    _tram(id, tramitacao.devolver, motivo, agora(), _quem(ator))


@acao("presidente")
def tramIniciar(ator, id, tipo=""):
    _tram(id, tramitacao.iniciar, S["tramitacao"], tipo, agora(), _quem(ator))


@acao("presidente")
def tramAvancar(ator, id, etapa, resultado=None, obs="", forcar=False):
    _tram(id, tramitacao.avancar, etapa, agora(), _quem(ator), resultado, obs, bool(forcar))


@acao("presidente")
def tramDistribuir(ator, id, comissoes):
    _tram(id, tramitacao.distribuir, S["tramitacao"], comissoes, agora(), _quem(ator))


@acao("presidente")
def tramRelator(ator, id, comissao, relator):
    _tram(id, tramitacao.designar_relator, S["tramitacao"], comissao, relator, parl(relator)["nome"], agora(), _quem(ator))


@acao("presidente", "parlamentar")
def tramParecer(ator, id, comissao, voto, texto):
    """Parecer do relator: o vereador só envia onde é relator; a presidência pode registrar por ele."""
    _tram(id, tramitacao.registrar_parecer, S["tramitacao"], comissao, voto, texto, agora(), _quem(ator),
          ator["pid"] if ator["papel"] == "parlamentar" else None)


@acao("presidente")
def tramVista(ator, id, comissao, pid):
    _tram(id, tramitacao.pedir_vista, S["tramitacao"], comissao, pid, parl(pid)["nome"], agora(), _quem(ator))


@acao("presidente")
def tramVistaDevolver(ator, id, comissao):
    _tram(id, tramitacao.devolver_vista, S["tramitacao"], comissao, agora(), _quem(ator))


@acao("presidente")
def tramComissaoSalvar(ator, nome, membros, presidente=None, id=None):
    cfg = S["tramitacao"]
    dados = _regra(tramitacao.validar_comissao, cfg, nome, membros, presidente, {p["id"] for p in S["parls"]}, id)
    if id:
        _regra(tramitacao.comissao, cfg, id).update(dados)
    else:
        if len(cfg["comissoes"]) >= tramitacao.MAX_COMISSOES:
            raise Erro("Limite de comissões atingido.")
        cfg["comissoes"].append({"id": uid(), **dados})


@acao("presidente")
def tramComissaoRemover(ator, id):
    cfg = S["tramitacao"]
    c = _regra(tramitacao.comissao, cfg, id)
    if any(tramitacao.etapa(p) == "em_comissao" and any(x["comissao"] == id and x["situacao"] not in ("concluida", "parecer_rejeitado")
                                                       for x in p["tram"]["comissoes"]) for p in proj_todos()):
        raise Erro("Há matérias aguardando parecer nesta comissão.")
    if any(r["comissao"] == id and not r["fim"] for r in cfg["reunioes"]):
        raise Erro("Encerre a reunião aberta desta comissão antes.")
    cfg["comissoes"].remove(c)


@acao("presidente")
def tramConfigurar(ator, tipos, prazos):
    S["tramitacao"]["tipos"], S["tramitacao"]["prazos"] = _regra(tramitacao.validar_config, tipos, prazos)


@acao("presidente")
def tramReuniaoAbrir(ator, comissao):
    _regra(tramitacao.reuniao_abrir, S["tramitacao"], comissao, uid(), agora())


@acao("presidente")
def tramReuniaoPresenca(ator, id, pid, presente=True):
    cfg = S["tramitacao"]
    _regra(tramitacao.reuniao_presenca, cfg, _regra(tramitacao.reuniao, cfg, id), pid, bool(presente))


@acao("presidente")
def tramReuniaoVotar(ator, id, materia, votos):
    cfg = S["tramitacao"]
    r = _regra(tramitacao.reuniao, cfg, id)
    if r["fim"]:
        raise Erro("Reunião encerrada.")
    r["itens"].append(_tram(materia, tramitacao.votar_parecer, cfg, r, votos, agora(), _quem(ator)))


@acao("presidente")
def tramReuniaoEncerrar(ator, id):
    r = _regra(tramitacao.reuniao, S["tramitacao"], id)
    if r["fim"]:
        raise Erro("Reunião já encerrada.")
    r["fim"] = agora()


@acao("presidente")
def tramEnviarExecutivo(ator, id, prazoDias=None, oficio=""):
    _tram(id, tramitacao.enviar_executivo, S["tramitacao"], prazoDias, oficio, agora(), _quem(ator))


@acao("presidente")
def tramRespostaExecutivo(ator, id, texto, recebidaEm="", nome="", dataUrl=""):
    """Resposta da prefeitura: resumo e, se houver, o ofício em PDF (público na página de fiscalização)."""
    recebida = agora()
    if recebidaEm:
        try:
            recebida = int(time.mktime(time.strptime(str(recebidaEm)[:10], "%Y-%m-%d")) * 1000) + 12 * 3600_000
        except ValueError:
            raise Erro("Data de recebimento inválida.")
        recebida = min(recebida, agora())
    dados, anexo = None, None
    if dataUrl:
        cab, _, b64 = str(dataUrl).partition(",")
        if cab != "data:application/pdf;base64":
            raise Erro("Envie a resposta em PDF.")
        try:
            dados = base64.b64decode(b64, validate=True)
        except ValueError:
            raise Erro("Arquivo inválido.")
        if not dados.startswith(b"%PDF") or len(dados) > LIMITE_PDF_RESPOSTA:
            raise Erro("PDF inválido ou maior que 2,8 MB.")
        anexo = {"nome": str(nome or "resposta.pdf")[:120], "v": agora(), "bytes": len(dados)}
    _tram(id, tramitacao.responder_executivo, texto, recebida, agora(), _quem(ator), anexo)
    if dados:
        db.execute("insert or replace into imagens (chave, mime, dados) values (?, ?, ?)", (f"resposta-{id}", "application/pdf", dados))
        db.commit()


def tram_get(h, caminho: str):
    """Rotas GET /api/tramitacao/… (h é o Manipulador). Devolve None quando o caminho não é delas."""
    m = re.fullmatch(r"/api/tramitacao/(comprovante|resposta)/([A-Za-z0-9]+)\.pdf", caminho)
    if m:
        with trava:
            p = proj_obter(m.group(2))
            casa, cor = S["casa"]["nome"], S["casa"].get("destaque", "")
            tipos = {t["sigla"]: t["nome"] for t in S["tramitacao"]["tipos"]}
            linha = db.execute("select dados from imagens where chave = ?", (f"resposta-{m.group(2)}",)).fetchone() \
                if m.group(1) == "resposta" else None
        if m.group(1) == "comprovante":
            if not p or not p.get("protocolo"):
                return h._json(404, {"erro": "Protocolo não encontrado."})
            pdf = tramitacao.comprovante_pdf(p, casa, cor, tipos.get((p.get("tram") or {}).get("tipo"), ""))
            nome = re.sub(r"[^A-Za-z0-9]+", "-", p["protocolo"]["numero"]).strip("-")
            h._enviar(200, pdf, "application/pdf", {"Content-Disposition": f'inline; filename="protocolo-{nome}.pdf"', "Cache-Control": "no-store"})
            return True
        if not linha or not p or p["situacao"] == "proposta":
            return h._json(404, {"erro": "Anexo não encontrado."})
        h._enviar(200, linha[0], "application/pdf", {"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})
        return True
    if caminho == "/api/tramitacao/painel":
        with trava:
            ator = h._ator()
            if not ator or ator["papel"] != "presidente":
                return h._json(401, {"erro": "Entre no painel do presidente."})
            cfg, nomes, ts = json.loads(json.dumps(S["tramitacao"])), _nomes_parls(), agora()
            projetos = proj_todos()
        materias, propostas, sem = [], [], []
        for p in projetos:
            if p["situacao"] == "proposta":
                propostas.append({**tramitacao.resumo(p, cfg, ts, nomes), "texto": p.get("texto", ""),
                                  "justificativa": p.get("justificativa", ""), "instrumento": p.get("instrumento", "")})
            elif p.get("tram"):
                materias.append(tramitacao.resumo(p, cfg, ts, nomes))
            elif p["situacao"] in ("em_tramitacao", "em_pauta"):
                sem.append({k: p.get(k) for k in ("id", "numero", "ementa", "autor", "situacao", "saplUrl")})
        return h._json(200, {"cfg": cfg, "etapas": tramitacao.ETAPAS, "transicoes": {k: sorted(v) for k, v in tramitacao.TRANSICOES.items()},
                             "votosParecer": tramitacao.VOTOS_PARECER, "materias": materias, "propostas": propostas,
                             "semTramitacao": sem, "alertas": tramitacao.alertas(projetos, cfg, ts, nomes),
                             "placar": tramitacao.placar(projetos, ts), "agora": ts})
    if caminho == "/api/tramitacao/minhas":
        with trava:
            ator = h._ator()
            if not ator or ator["papel"] != "parlamentar":
                return h._json(401, {"erro": "Entre com seu PIN de parlamentar."})
            pid, nome = ator["pid"], parl(ator["pid"])["nome"]
            cfg, nomes, ts = json.loads(json.dumps(S["tramitacao"])), _nomes_parls(), agora()
            projetos = proj_todos()
        minhas, relatorias = [], []
        for p in projetos:
            if p.get("tram") and (p.get("autorPid") == pid or (not p.get("autorPid") and p.get("autor") == nome)):
                minhas.append({**tramitacao.resumo(p, cfg, ts, nomes), "texto": p.get("texto", ""), "justificativa": p.get("justificativa", "")})
            if tramitacao.etapa(p) == "em_comissao":
                for c in tramitacao.resumo(p, cfg, ts, nomes)["comissoes"]:
                    if c["relatorPid"] == pid and c["situacao"] in ("com_relator", "parecer"):
                        relatorias.append({"materia": p["id"], "numero": p["numero"], "ementa": p["ementa"], "autor": p.get("autor"),
                                           "texto": p.get("texto", ""), **c})
        return h._json(200, {"tipos": cfg["tipos"], "votosParecer": tramitacao.VOTOS_PARECER, "etapas": tramitacao.ETAPAS,
                             "minhas": sorted(minhas, key=lambda x: -(x.get("etapaEm") or x.get("criado") or 0)), "relatorias": relatorias,
                             "comissoes": [{"nome": c["nome"], "presidente": c.get("presidente") == pid} for c in cfg["comissoes"] if pid in c["membros"]],
                             "limites": tramitacao.LIMITES, "agora": ts})
    if caminho == "/api/tramitacao/publico":
        with trava:
            cfg, nomes, ts = json.loads(json.dumps(S["tramitacao"])), _nomes_parls(), agora()
            projetos = [p for p in proj_todos() if p["situacao"] != "proposta" and p.get("tram")]
            casa = {k: S["casa"][k] for k in ("nome", "logoV", "destaque", "tema")}
            rodape = json.loads(json.dumps(S["rodape"]))
        executivo = {t["sigla"] for t in cfg["tipos"] if t["executivo"]}
        lista = [tramitacao.resumo(p, cfg, ts, nomes) for p in projetos]
        for x in lista:
            if x["executivo"] and x["executivo"].get("resposta"):
                x["executivo"]["resposta"].pop("por", None)
        return h._json(200, {"casa": casa, "rodape": rodape, "tipos": cfg["tipos"], "prazos": cfg["prazos"],
                             "placar": tramitacao.placar(projetos, ts), "agora": ts,
                             "pedidos": [x for x in lista if x["tipo"] in executivo or x["executivo"]],
                             "materias": [{k: x[k] for k in ("id", "numero", "ementa", "autor", "tipo", "tipoNome", "etapa", "etapaNome",
                                                             "etapaEm", "resultado")} for x in lista],
                             "comissoes": [{"nome": c["nome"], "membros": [nomes.get(m, "—") for m in c["membros"]],
                                            "presidente": nomes.get(c.get("presidente"))} for c in cfg["comissoes"]]})
    m = re.fullmatch(r"/api/tramitacao/materia/([A-Za-z0-9]+)", caminho)
    if m:
        with trava:
            p = proj_obter(m.group(1))
            cfg, nomes, ts = json.loads(json.dumps(S["tramitacao"])), _nomes_parls(), agora()
            casa = {k: S["casa"][k] for k in ("nome", "logoV", "destaque", "tema")}
        if not p or p["situacao"] == "proposta":
            return h._json(404, {"erro": "Matéria não encontrada."})
        return h._json(200, {"casa": casa, "materia": {**tramitacao.resumo(p, cfg, ts, nomes), "texto": p.get("texto", ""),
                                                       "justificativa": p.get("justificativa", "")},
                             "linhaDoTempo": tramitacao.linha_do_tempo(p), "agora": ts})
    return None


# --- perguntas sobre discursos e assistente do cidadão
base_conhecimento = conhecimento.Base()
_perguntas_recentes: dict[str, list[float]] = {}
_respostas_cache: dict = {}


def _chave_conhecimento():
    n_pub = db.execute("select count(*), max(criado) from gravacoes where json like '%\"publicada\": true%'").fetchone()
    n_ses = db.execute("select count(*), max(fim) from sessoes").fetchone()
    return (S["projetosRev"], tuple(n_pub), tuple(n_ses), len(S["parls"]))


def _documentos_conhecimento() -> list[dict]:
    titulos = dict(db.execute("select id, titulo from sessoes").fetchall())
    discursos = []
    for (texto_json,) in db.execute("select json from gravacoes order by criado").fetchall():
        g = json.loads(texto_json)
        t = g.get("transcricao") or {}
        if t.get("publicada") and t.get("texto"):
            discursos.append({"nome": g["nome"], "partido": g["partido"], "inicio": g["inicio"], "texto": t["texto"],
                              "sessao": titulos.get(g["sessao"], ""), "link": f"/sessao/{g['sessao']}"})
    projetos = [p for p in proj_todos() if p["situacao"] != "proposta"]
    sessoes = []
    for i, t, ini, fim, res in db.execute("select id, titulo, inicio, fim, resumo from sessoes order by fim").fetchall():
        rr = json.loads(res or "{}")
        sessoes.append({"id": i, "titulo": t, "inicio": ini, "presentes": rr.get("presentes", 0), "falas": rr.get("falas", 0),
                        "votacoes": rr.get("votacoes", 0)})
    autoria = {}
    for p in projetos:
        autoria[(p.get("autor") or "").lower()] = autoria.get((p.get("autor") or "").lower(), 0) + 1
    parls = [{"id": p["id"], "nome": p["nome"], "partido": p["partido"], "bloco": estatisticas.BLOCOS.get(p.get("bloco"), ""),
              "resumo": f"Autor(a) de {autoria.get(p['nome'].lower(), 0)} projeto(s) registrados." + (" Líder." if p.get("lider") else "")}
             for p in S["parls"]]
    return conhecimento.montar_documentos(discursos, projetos, parls, sessoes, S["casa"]["nome"], S["rodape"])


def perguntar(pergunta: str, escopo: str, historico: list, ip: str) -> dict:
    agora_s = time.time()
    recentes = [t for t in _perguntas_recentes.get(ip, []) if agora_s - t < 60]
    if len(recentes) >= 8:
        raise PermissionError("Muitas perguntas seguidas. Aguarde um minuto.")
    _perguntas_recentes[ip] = recentes + [agora_s]
    if len(_perguntas_recentes) > 5000:
        _perguntas_recentes.clear()
    with trava:
        chave = _chave_conhecimento()
        base_conhecimento.atualizar(chave, _documentos_conhecimento)
    tipos = {"discurso"} if escopo == "discursos" else None
    cache = (pergunta.strip().lower(), escopo, chave) if not historico else None
    if cache and cache in _respostas_cache:
        return _respostas_cache[cache]
    r = conhecimento.responder(base_conhecimento, pergunta, tipos, historico)
    if cache:
        if len(_respostas_cache) > 300:
            _respostas_cache.clear()
        _respostas_cache[cache] = r
    return r


# --- integridade: recibo do voto, revisão do conteúdo de IA e política de uso de IA (server/integridade.py)
def votacao_por_id(vid: str) -> dict | None:
    """Votação da sessão atual ou de uma sessão arquivada."""
    vid = str(vid or "")
    if not vid.isalnum():
        return None
    for v in [S["votacao"], *S["votacoes"]]:
        if v and v.get("id") == vid:
            return v
    for (texto,) in db.execute("select json from sessoes where json like ?", (f'%"{vid}"%',)).fetchall():
        v = next((x for x in json.loads(texto).get("votacoes", []) if x.get("id") == vid), None)
        if v:
            return v
    return None


def recibo_do_voto(pid: str, vid: str | None) -> dict:
    v = votacao_por_id(vid) if vid else (S["votacao"] or (S["votacoes"][0] if S["votacoes"] else None))
    if not v:
        return {"votou": False, "mensagem": "Nenhuma votação encontrada."}
    base = {"votacao": v["id"], "numero": v.get("numero"), "tipo": v["tipo"], "aberta": bool(v.get("aberta"))}
    if v["tipo"] == "secreta":  # nunca o valor, nem um código que dependa dele
        contado = pid in (v.get("votantes") or [])
        return {**base, "votou": contado, "mensagem": "Seu voto foi contado. Votação secreta não tem recibo." if contado
                else "Você não votou nesta votação."}
    valor = (v.get("votos") or {}).get(pid)
    if v["tipo"] != "nominal" or not valor:
        return {**base, "votou": False, "mensagem": "Não há voto nominal seu nesta votação."}
    return {**base, "votou": True, "valor": valor, "recibo": integridade.recibo(S["_chaveRecibo"], v["id"], pid, valor),
            "mensagem": "Guarde o recibo: em /verificar qualquer pessoa confere se ele corresponde a um voto registrado."}


@acao("presidente")
def iaRevisarAta(ator, sessao="atual", revisor="", revisado=True):
    """Marca o rascunho da ata gerado por IA como revisado (ou volta a rascunho), com nome de quem revisou."""
    res = _ler_resumo(sessao)
    if not res or not res.get("itens"):
        raise Erro("Gere o rascunho da ata antes de revisar.")
    if revisado:
        quem = " ".join(str(revisor or "").split())[:80] or "Presidência"
        res["revisao"] = {"por": quem, "em": agora(), "modelo": integridade.modelo_do_resumo(res)}
        for i in res["itens"]:
            i["revisado"] = True
        sid = S["sessao"]["id"] if sessao == "atual" else sessao
        integridade.marcar_revisado(db, f"ata:{sid}", quem, res["revisao"]["em"])
    else:
        res.pop("revisao", None)
    _guardar_resumo(sessao, res)


@acao("presidente")
def iaPoliticaSalvar(ator, texto=""):
    """Política de uso de IA da Câmara (página pública /ia). Vazio volta ao texto padrão."""
    texto = str(texto or "").strip()
    if len(texto) > 20000:
        raise Erro("Texto grande demais.")
    S["_politicaIA"] = texto


# --- dados abertos
def dados_abertos_sessoes() -> list[dict]:
    saida = []
    for (texto_json,) in db.execute("select json from sessoes order by inicio").fetchall():
        r = json.loads(texto_json)
        pub = sessao_publica({**r, "gravacoes": []})
        saida.append({k: pub[k] for k in ("id", "titulo", "inicio", "fim", "pauta", "votacoes", "presentes", "membros")}
                     | {"presenca": sorted(pub["nomes"].get(pid, pid) for pid in (r.get("presenca") or {})),
                        "pronunciamentos": [{k: f[k] for k in ("nome", "partido", "fase", "inicio", "usado")} for f in pub["falas"]]})
    return saida


# --- acompanhar (feeds, agenda, e-mail), opinião na pauta, Tribuna Livre, citações LexML e OAI-PMH: server/acompanhar.py
import acompanhar
acompanhar.instalar(globals())

# ---------------------------------------------------------------- HTTP
class Manipulador(BaseHTTPRequestHandler):
    server_version = "ParlatorioLegis/2"
    timeout = TEMPO_LIMITE_CONEXAO  # conexão parada (slowloris) cai sozinha; o /eventos manda sinal a cada 10 s

    def log_message(self, fmt, *args):
        if os.environ.get("PARLATORIO_LOG"):
            super().log_message(fmt, *args)

    def _json(self, codigo, obj):
        corpo = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)
        return True

    def _tamanho(self) -> int:
        """Content-Length validado: negativo ou inválido seria lido até o cliente fechar (memória sem limite)."""
        bruto = (self.headers.get("Content-Length") or "0").strip()
        if not bruto.isdigit():
            raise Erro("Content-Length inválido.")
        return int(bruto)

    def _ler(self) -> dict:
        n = self._tamanho()
        if n > LIMITE_CORPO:
            raise Erro("Requisição grande demais.")
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            raise Erro("JSON inválido.")

    def _ator(self, troca_ok: bool = False):
        """Quem fez o pedido. Com o PIN inicial ainda não trocado, só /api/eu e a troca de PIN são aceitos."""
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        ct = chave_token(token) if token else None
        a = S["_tokens"].get(ct) if ct else None
        if not a:
            return None
        ocioso = OCIOSO_PRESIDENTE if a["papel"] == "presidente" else OCIOSO_PARLAMENTAR
        if agora() - a.get("uso", 0) > ocioso or agora() - a.get("criado", 0) > VALIDADE_MAXIMA:
            S["_tokens"].pop(ct, None)
            return None
        if agora() - a.get("uso", 0) > 300_000:
            a["uso"] = agora()
        if a.get("deveTrocar") and not troca_ok:
            raise TrocaPin()
        return a

    def _ip(self) -> str:
        ip = self.client_address[0] if self.client_address else "?"
        if PROXY_CONFIAVEL and _de_proxy(ip):
            encaminhado = (self.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
            if encaminhado:
                return encaminhado[:64]  # o último é o que o nosso proxy acrescentou: não dá para forjar
        return ip

    # ----- GET
    def setup(self):
        if isinstance(self.request, ssl.SSLSocket):  # aperto de mão TLS na thread do pedido, não no laço principal
            self.request.settimeout(15)
            self.request.do_handshake()
            self.request.settimeout(None)
        super().setup()

    def _redirecionar(self) -> bool:
        """Com HTTPS ativo, a porta HTTP manda tudo para https:// (menos localhost e o download do certificado)."""
        if not getattr(self.server, "redireciona", False):
            return False
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        caminho = urlparse(self.path).path
        if host in ("localhost", "127.0.0.1", "::1", "") or caminho == "/certificado" or caminho.startswith(("/certificado.", "/certificado/")) \
                or caminho.startswith("/.well-known/acme-challenge/") or caminho in ("/aovivo", "/radio.mp3"):
            return False
        self.send_response(308 if self.command == "POST" else 301)
        self.send_header("Location", f"https://{host}:{PORTA_HTTPS}{self.path}")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def do_GET(self):
        try:
            return self._do_get()
        except TrocaPin:
            return self._json(403, {"erro": "Troque o PIN inicial antes de continuar.", "codigo": "trocarPin"})
        except Erro as e:
            return self._json(400, {"erro": str(e)})

    def _do_get(self):
        if self._redirecionar():
            return
        caminho = urlparse(self.path).path
        if caminho == "/certificado" or caminho.startswith(("/certificado.", "/certificado/")):
            return self._certificado(caminho)
        if caminho.startswith("/.well-known/acme-challenge/"):
            return self._acme(caminho.rsplit("/", 1)[1])
        if caminho.startswith("/api/sessoes") or caminho.startswith("/api/ata/"):
            return self._sessoes(caminho)
        if caminho == "/api/transparencia":
            return self._transparencia()
        if caminho == "/api/recomendacoes":
            return self._recomendacoes()
        if caminho == "/api/remoto":
            with trava:
                ator = self._ator()
                if not ator or not S["remoto"]["ativo"]:
                    return self._json(403, {"erro": "Sessão remota indisponível."})
                nome = "Presidência" if ator["papel"] == "presidente" else parl(ator["pid"])["nome"]
                moderador = ator["papel"] == "presidente"
                resposta = {"servidor": S["remoto"]["servidor"], "sala": S["_remoto"]["sala"], "nome": nome, "moderador": moderador}
                if JITSI_SEGREDO and JITSI_URL and S["remoto"]["servidor"] == JITSI_URL:
                    resposta["jwt"] = token_jitsi(nome, ator["pid"] or "presidente", S["_remoto"]["sala"], moderador)
                return self._json(200, resposta)
        if caminho.startswith("/api/perfil/"):
            return self._perfil(caminho.rsplit("/", 1)[1])
        if caminho == "/aovivo":
            return self._aovivo_audio()
        novo = self._get_novos(caminho)
        if novo is not None:
            return novo
        novo = acompanhar.get(self, caminho)   # feeds, agenda, OAI-PMH, pauta cidadã e Tribuna Livre
        if novo is not None:
            return novo
        if caminho == "/api/aovivo":
            with trava:
                return self._json(200, ao_vivo())
        if caminho == "/api/transmissao":
            with trava:
                ator = self._ator()
                if not ator or ator["papel"] != "presidente":
                    return self._json(403, {"erro": "Só o painel do presidente."})
                cfg = {k: v for k, v in S["_transmissao"].items() if k != "senha"}
                cfg["youtube"] = {"url": S["_youtube"]["url"], "temChave": bool(S["_youtube"]["chave"]),
                                  "link": S["youtube"]["link"], "ligado": S["youtube"]["ligado"]}
                return self._json(200, {**cfg, "temSenha": bool(S["_transmissao"]["senha"]), "publico": S["transmissao"]["publico"],
                                        "nome": S["transmissao"]["nome"], "presets": transmissao.PRESETS,
                                        "bitrates": transmissao.BITRATES, "ffmpeg": bool(shutil.which("ffmpeg"))})
        if caminho == "/api/discursos":
            return self._discursos()
        if caminho.startswith("/api/transcricao/"):
            return self._transcricao(caminho.rsplit("/", 1)[1])
        if caminho == "/eventos":
            return self._eventos()
        if caminho.startswith("/imagem/"):
            return self._imagem(caminho.rsplit("/", 1)[1])
        if caminho.startswith("/gravacao/"):
            return self._audio(caminho.rsplit("/", 1)[1])
        if caminho == "/api/mesa":
            with trava:
                ator = self._ator()
                if not ator or ator["papel"] != "presidente":
                    return self._json(403, {"erro": "Só o painel do presidente."})
                return self._json(200, {**S["mesa"], "pastaBackup": S["backup"].get("pasta", "")})
        if caminho == "/api/pins-iniciais":
            with trava:
                ator = self._ator()
                if not ator or ator["papel"] != "presidente":
                    return self._json(403, {"erro": "Só o painel do presidente."})
                return self._json(200, {"pins": dict(S["_pinsIniciais"])})
        if caminho == "/api/eu":
            with trava:
                a = self._ator(troca_ok=True)
            return self._json(200, {"ator": {k: a.get(k) for k in ("papel", "pid", "deveTrocar")} if a else None})
        return self._estatico(caminho)

    def _estatico(self, caminho):
        if caminho in ("", "/"):
            caminho = "/index.html"
        if caminho.startswith("/parlamentares/"):  # página pública de cada parlamentar
            caminho = "/perfil.html"
        if caminho.startswith("/sessao/"):  # vídeo e registros de cada sessão
            caminho = "/sessao.html"
        alvo = (PUBLICO / caminho.lstrip("/")).resolve()
        if PUBLICO not in alvo.parents or not alvo.is_file():
            alvo = (PUBLICO / (caminho.lstrip("/") + ".html")).resolve()
            if PUBLICO not in alvo.parents or not alvo.is_file():
                return self._json(404, {"erro": "Não encontrado."})
        tipos = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".json": "application/json", ".webmanifest": "application/manifest+json"}
        dados = alvo.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", tipos.get(alvo.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-cache")
        if alvo.suffix == ".html":
            self._cabecalhos_seguranca()
        self.end_headers()
        self.wfile.write(dados)

    def _cabecalhos_seguranca(self):
        """Nenhum outro site põe as telas num iframe (clique escondido em "encerrar votação") e só rodam scripts
        daqui, do VLibras e do Jitsi da sessão remota."""
        with trava:
            jitsi = urlparse(str(S["remoto"].get("servidor") or ""))
        origem_jitsi = f"{jitsi.scheme}://{jitsi.netloc}" if jitsi.scheme in ("http", "https") and \
            re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", jitsi.netloc or "") else ""
        self.send_header("Content-Security-Policy",
                         "frame-ancestors 'self'; object-src 'none'; base-uri 'self'; "
                         f"script-src 'self' 'unsafe-inline' https://vlibras.gov.br https://*.vlibras.gov.br {origem_jitsi}".strip())
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")

    def _imagem(self, chave):
        with trava:
            linha = db.execute("select mime, dados from imagens where chave = ?", (chave,)).fetchone()
        if not linha:
            return self._json(404, {"erro": "Sem imagem."})
        self.send_response(200)
        self.send_header("Content-Type", linha[0])
        self.send_header("Content-Length", str(len(linha[1])))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(linha[1])

    def _audio(self, nome):
        gid = nome.split(".")[0]
        with trava:
            g = grav_obter(gid) if gid.isalnum() else None
        arquivo = GRAVACOES / f"{g['id']}.{g['ext']}" if g else None
        if not arquivo or not arquivo.exists():
            return self._json(404, {"erro": "Gravação não encontrada."})
        tamanho = arquivo.stat().st_size
        ini, fim = 0, tamanho - 1
        faixa = self.headers.get("Range", "")
        if faixa.startswith("bytes="):
            a, _, b = faixa[6:].split(",")[0].partition("-")
            try:
                ini = int(a) if a else max(0, tamanho - int(b))
                fim = min(int(b), tamanho - 1) if a and b else fim
            except ValueError:
                ini, fim = 0, tamanho - 1
        if tamanho == 0 or ini > fim:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{tamanho}")
            self.end_headers()
            return
        self.send_response(206 if faixa else 200)
        self.send_header("Content-Type", g["mime"])
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(fim - ini + 1))
        if faixa:
            self.send_header("Content-Range", f"bytes {ini}-{fim}/{tamanho}")
        if "baixar" in (urlparse(self.path).query or ""):
            nome_arq = f"{time.strftime('%Y%m%d-%H%M', time.localtime(g['inicio'] / 1000))}-{g['nome']}.{g['ext']}"
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(nome_arq))
        self.end_headers()
        with open(arquivo, "rb") as f:
            f.seek(ini)
            resta = fim - ini + 1
            while resta > 0:
                bloco = f.read(min(65536, resta))
                if not bloco:
                    break
                self.wfile.write(bloco)
                resta -= len(bloco)

    def _transparencia(self):
        """Estatísticas das sessões arquivadas e banco de projetos. ?interno=1 (presidente) traz mais detalhes."""
        interno = "interno" in urllib.parse.parse_qs(urlparse(self.path).query)
        with trava:
            ator = self._ator()
            if interno and not (ator and ator["papel"] == "presidente"):
                return self._json(401, {"erro": "Entre no painel do presidente."})
            sessoes = [json.loads(x[0]) for x in db.execute("select json from sessoes order by fim").fetchall()]
            projetos = proj_todos()
            if not interno:  # propostas ainda não protocoladas não são públicas
                projetos = [p for p in projetos if p["situacao"] != "proposta"]
            parls, rel = json.loads(json.dumps(S["parls"])), dict(S["relevancia"])
            blocos = {p["id"]: p.get("bloco", "") for p in S["parls"]}
            casa = {k: S["casa"][k] for k in ("nome", "titulo", "logoV", "destaque", "tema")}
            partidos = dict(S["partidos"])
        dados = estatisticas.calcular(sessoes, projetos, parls, rel["populacao"], rel["pesos"], interno, blocos)
        dados["casa"], dados["partidos"] = casa, partidos
        with trava:
            dados["rodape"] = json.loads(json.dumps(S["rodape"]))
            dados["virtuaisAbertas"] = [{"titulo": v["titulo"], "inicio": v["inicio"], "fim": v["fim"], "status": _status_virtual(v),
                                         "itens": [{"numero": it["numero"], "ementa": it["ementa"], "autor": it["autor"],
                                                    "contagem": {x: sum(1 for y in it["votos"].values() if y["valor"] == x) for x in ("sim", "nao", "abst")},
                                                    "votaram": len(it["votos"]), "destacado": bool(it["destaques"]),
                                                    "manifestacoes": it["manifestacoes"]} for it in v["itens"]]}
                                        for v in S["virtuais"] if _status_virtual(v) in ("aberta", "agendada")]
            dados["membrosTotal"] = len(S["parls"])
        return self._json(200, dados)

    def _perfil(self, chave):
        """Página pública de um parlamentar: números das sessões encerradas, linha do tempo, projetos e votos."""
        pid = chave.split("-", 1)[0]
        with trava:
            sessoes = [json.loads(x[0]) for x in db.execute("select json from sessoes order by fim").fetchall()]
            projetos = [p for p in proj_todos() if p["situacao"] != "proposta"]
            parls = json.loads(json.dumps(S["parls"]))
            ex = json.loads(json.dumps(S.get("exParls", [])))
            rel = dict(S["relevancia"])
            blocos = {p["id"]: p.get("bloco", "") for p in S["parls"]}
            casa = {k: S["casa"][k] for k in ("nome", "titulo", "logoV", "destaque", "tema")}
            partidos = dict(S["partidos"])
        d = estatisticas.calcular(sessoes, projetos, parls, rel["populacao"], rel["pesos"], False, blocos)
        eu = next((a for a in d["parlamentares"] if a["id"] == pid), None)
        atual = next((p for p in parls if p["id"] == pid), None) or next((p for p in ex if p["id"] == pid), None)
        if not eu and not atual:
            return self._json(404, {"erro": "Parlamentar não encontrado."})
        if not eu:  # membro sem sessões encerradas ainda
            eu = {"id": pid, "nome": atual["nome"], "partido": atual["partido"], "membro": True, "sessoes": 0}
        base = atual or {}
        linha = []
        for r in sessoes:
            if not any(p["id"] == pid for p in r["parls"]):
                continue
            falas = [h for h in r["hist"] if h["pid"] == pid]
            votos = [{"numero": v["numero"], "voto": v["votos"].get(pid)} for v in r["votacoes"] if v.get("votos")]
            linha.append({"titulo": r["titulo"], "data": r["inicio"], "presente": pid in r["presenca"],
                          "falas": len(falas), "tempoFala": sum(h["usado"] for h in falas), "votos": votos})
        votos = [{"numero": v["numero"], "ementa": v["ementa"], "data": v["data"], "resultado": v["resultado"],
                  "voto": next((x["voto"] for x in v["votos"] if x["pid"] == pid), None)}
                 for v in d["votacoes"] if any(x["pid"] == pid for x in v["votos"])]
        meus = [p for p in d["projetos"] if p.get("autor") == eu["nome"]]
        posicao = next((k + 1 for k, a in enumerate(d["maisAtuantes"]) if a["id"] == pid), None)
        membros = [a for a in d["parlamentares"] if a.get("membro") and a.get("sessoes")]
        media = lambda campo: round(sum(a.get(campo) or 0 for a in membros) / len(membros), 1) if membros else None
        with trava:
            rodape = json.loads(json.dumps(S["rodape"]))
        return self._json(200, {
            "casa": casa, "partidos": partidos, "blocosNomes": d["blocosNomes"], "pesosAtuacao": d["pesosAtuacao"],
            "rodape": rodape,
            "areas": d["areas"], "parlamentar": {**eu, "fotoV": base.get("fotoV", 0), "lider": base.get("lider", False),
                                                  "bloco": base.get("bloco", eu.get("bloco", "")),
                                                  "saplUrl": base.get("saplUrl"), "tse": base.get("tse"),
                                                  "nomeCivil": base.get("nomeCivil"), "cargoMesa": base.get("cargoMesa"),
                                                  "portal": base.get("portal"), "suplente": base.get("suplente", False)},
            "posicaoAtuacao": posicao, "totalRanking": len(d["maisAtuantes"]),
            "mediaCasa": {"presencaPct": media("presencaPct"), "falas": media("falas"), "tempoFala": media("tempoFala"),
                          "autoria": media("autoria")},
            "linhaDoTempo": linha, "votos": votos, "projetos": meus,
            "outros": [{"id": p["id"], "nome": p["nome"], "partido": p["partido"], "fotoV": p.get("fotoV", 0),
                        "bloco": p.get("bloco", "")} for p in parls],
            "semHorario": any(r.get("semHorario") for r in sessoes),
        })

    def _recomendacoes(self):
        """Área privada do parlamentar: boas práticas recomendadas e suas propostas."""
        with trava:
            ator = self._ator()
            if not ator or ator["papel"] != "parlamentar":
                return self._json(401, {"erro": "Entre com seu PIN de parlamentar."})
            nome = parl(ator["pid"])["nome"]
            perfil = dict(S["_perfis"].get(ator["pid"]) or {"areas": [], "temas": ""})
            feedback_todos = json.loads(json.dumps(S["_feedback"]))
            projetos = proj_todos()
        itens = recomendacao.recomendar(nome, perfil, feedback_todos.get(ator["pid"], {}), feedback_todos, projetos,
                                        estatisticas.AREAS, ator["pid"])
        minhas = [{k: p.get(k) for k in ("id", "numero", "ementa", "situacao", "origem", "historico", "atualizado")}
                  for p in projetos if p.get("autor") == nome]
        return self._json(200, {"interesses": perfil.get("areas", []), "temas": perfil.get("temas", ""),
                                "areas": estatisticas.AREAS, "itens": itens,
                                "minhas": sorted(minhas, key=lambda p: -(p.get("atualizado") or 0)),
                                "abrangenciaNomes": {k: v[0] for k, v in estatisticas.ABRANGENCIA.items()},
                                "vulneraveisNomes": estatisticas.VULNERAVEIS})

    def _aovivo_audio(self):
        """GET /aovivo: o MP3 da sessão, direto do Parlatório (a rádio puxa daqui no modo "local")."""
        try:
            blocos = transmissor.ouvir()
        except RuntimeError as e:
            return self._json(503, {"erro": str(e)})
        if blocos is None:
            return self._json(404, {"erro": "Nenhuma sessão no ar agora."})
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("icy-name", (S["transmissao"].get("nome") or S["casa"]["nome"]).replace("—", "-").encode("latin-1", "replace").decode("latin-1"))
        self.end_headers()
        try:
            for bloco in blocos:
                self.wfile.write(bloco)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            blocos.close()
        self.close_connection = True

    def _radio_audio(self):
        """GET /radio.mp3: repassa a rádio da Câmara (Liquidsoap local) no mesmo endereço e certificado do LEGIS."""
        global radio_ouvintes
        if not RADIO_LOCAL:
            return self._json(404, {"erro": "Esta Câmara não tem rádio local."})
        if radio_ouvintes >= RADIO_MAX_OUVINTES:
            return self._json(503, {"erro": "Rádio lotada no momento. Tente de novo em instantes."})
        try:
            origem = urllib.request.urlopen(f"http://127.0.0.1:{RADIO_PORTA}/radio.mp3", timeout=10)
        except OSError:
            return self._json(503, {"erro": "A rádio está fora do ar."})
        radio_ouvintes += 1
        try:
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("icy-name", (S["transmissao"].get("nome") or S["casa"]["nome"]).replace("—", "-").encode("latin-1", "replace").decode("latin-1"))
            self.end_headers()
            while True:
                bloco = origem.read(8192)
                if not bloco:
                    break
                self.wfile.write(bloco)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            radio_ouvintes -= 1
            origem.close()
        self.close_connection = True
        return True

    def _radio_arquivo(self):
        """POST /api/radio/arquivo?nome=vinheta.mp3 (corpo = arquivo): entra na programação da rádio."""
        with trava:
            ator = self._ator()
        if not ator or ator["papel"] != "presidente":
            return self._json(401, {"erro": "Entre no painel do presidente."})
        if not RADIO_LOCAL:
            return self._json(400, {"erro": "Esta Câmara não tem rádio local."})
        nome = urllib.parse.parse_qs(urlparse(self.path).query).get("nome", [""])[0]
        nome = re.sub(r"[^\w.\- ]+", "_", Path(nome).name).strip(" .")[:120]
        if not nome or Path(nome).suffix.lower() not in RADIO_FORMATOS:
            return self._json(400, {"erro": "Envie MP3, OGG, M4A, WAV ou FLAC."})
        n = self._tamanho()
        if n <= 0 or n > 80 * 1024 * 1024:
            return self._json(413, {"erro": "Arquivo vazio ou maior que 80 MB."})
        RADIO_DIR.mkdir(parents=True, exist_ok=True)
        usado = sum(f.stat().st_size for f in RADIO_DIR.iterdir() if f.is_file())
        if usado + n > RADIO_MAX_GB * 1024 ** 3:
            return self._json(507, {"erro": f"A programação da rádio já ocupa o espaço máximo ({RADIO_MAX_GB:g} GB). "
                                             "Apague arquivos antigos antes de enviar outros."})
        tmp = RADIO_DIR / f".{nome}.enviando"
        restante = n
        with open(tmp, "wb") as f:
            while restante:
                bloco = self.rfile.read(min(65536, restante))
                if not bloco:
                    break
                f.write(bloco)
                restante -= len(bloco)
        if restante:
            tmp.unlink(missing_ok=True)
            return self._json(400, {"erro": "Envio interrompido."})
        tmp.replace(RADIO_DIR / nome)  # a playlist recarrega sozinha quando a pasta muda
        with trava:
            registrar("presidente", "radioArquivo", {"nome": nome, "bytes": n})
        return self._json(200, {"ok": True, "nome": nome})

    def _transmissao_parte(self):
        """POST /api/transmissao/parte (corpo binário, ~1 s de áudio): segue para o ffmpeg da transmissão."""
        with trava:
            ator = self._ator()
        if not ator or ator["papel"] != "presidente":
            return self._json(401, {"erro": "Entre no painel do presidente."})
        n = self._tamanho()
        if n > 4 * 1024 * 1024:
            return self._json(413, {"erro": "Parte grande demais."})
        dados = self.rfile.read(n)
        try:
            transmissor.enviar(dados)
        except RuntimeError as e:
            return self._json(409, {"erro": str(e)})
        try:
            canal_youtube.enviar(dados)
        except RuntimeError as e:   # o YouTube cai sozinho; a rádio e as legendas continuam
            canal_youtube.desligar(avisar=False)
            canal_youtube.erro = str(e)
            _transmissao_mudou()
        return self._json(200, {"ok": True, "bytes": transmissor.bytes})

    def _discursos(self):
        """GET /api/discursos?q=saude&pid=…: busca pública nos discursos com transcrição publicada."""
        qs = urllib.parse.parse_qs(urlparse(self.path).query)
        q = (qs.get("q") or [""])[0][:200]
        pid = (qs.get("pid") or [""])[0][:40]
        try:
            limite = int((qs.get("limite") or ["30"])[0])
        except ValueError:
            limite = 30
        with trava:
            return self._json(200, discursos_publicos(q, pid, limite))

    def _transcricao(self, gid):
        """GET /api/transcricao/<id>: texto completo. Publicada: qualquer um; antes disso, só o presidente."""
        with trava:
            g = grav_obter(gid) if gid.isalnum() else None
            ator = self._ator()
        t = (g or {}).get("transcricao") or {}
        presidente = bool(ator and ator["papel"] == "presidente")
        if not g or not (t.get("publicada") or presidente):
            return self._json(404, {"erro": "Transcrição não encontrada."})
        return self._json(200, {"id": g["id"], "nome": g["nome"], "partido": g["partido"], "fase": g.get("faseNome", ""),
                                "inicio": g["inicio"], "audio": f"/gravacao/{g['id']}.{g['ext']}",
                                "transcricao": t if presidente else {k: t.get(k) for k in ("texto", "segmentos", "revisada", "motor")}})

    def _acme(self, ficha):
        """Desafio do Let's Encrypt (certbot --webroot em dados/acme): certificado real para um domínio público."""
        alvo = BANCO.parent / "acme" / ".well-known" / "acme-challenge" / ficha
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", ficha) or not alvo.is_file():
            return self._json(404, {"erro": "Não encontrado."})
        dados = alvo.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    # ----- pacote 2026: rotas
    def _get_novos(self, caminho: str):
        """Rotas GET dos recursos novos; devolve None quando o caminho não é deles."""
        if caminho.startswith("/api/passkey/"):
            return self._passkey("GET", caminho, None)
        qs = urllib.parse.parse_qs(urlparse(self.path).query)
        if caminho == "/api/integridade":  # público: recalcula a cadeia inteira da auditoria
            with trava:
                linhas = db.execute("select id, ts, autor, acao, args, hash from eventos order by id").fetchall()
                selos = [(t, json.loads(j).get("selo") or {}) for t, j in
                         db.execute("select titulo, json from sessoes order by fim").fetchall()]
            return self._json(200, integridade.conferir_cadeia(linhas, selos))
        if caminho == "/api/recibo":
            with trava:
                ator = self._ator()
                if not ator or ator["papel"] != "parlamentar":
                    return self._json(401, {"erro": "Entre no app do parlamentar."})
                return self._json(200, recibo_do_voto(ator["pid"], (qs.get("votacao") or [""])[0]))
        if caminho == "/api/ia":  # público: política e estatísticas (sem perguntas de cidadãos, sem IP)
            with trava:
                texto, padrao = integridade.politica(S)
                stats = integridade.estatisticas_ia(db)
            return self._json(200, {"politica": texto, "padrao": padrao, "modelo": ia.descricao(), "ligada": ia.ligada(), **stats})
        if caminho.startswith(("/api/emendas", "/redacao-final/", "/dados/akn/projeto/")):
            r = emendas_get(self, caminho, qs)
            if r is not None:
                return r
        if caminho.startswith("/api/tramitacao/"):
            return tram_get(self, caminho)
        if caminho.startswith("/api/resumo/"):
            sessao = caminho.rsplit("/", 1)[1]
            with trava:
                ator = self._ator()
                if not ator or ator["papel"] != "presidente":
                    return self._json(401, {"erro": "Entre no painel do presidente."})
                res = _ler_resumo(sessao)
                return self._json(200, {"resumo": res, "job": S.get("resumoJob"), "ia": ia.descricao(), "seloIA": integridade.selo_ia(res)})
        if caminho.startswith("/api/projeto/"):
            pid = caminho.rsplit("/", 1)[1]
            with trava:
                ator = self._ator()
                if not ator or ator["papel"] != "presidente":
                    return self._json(401, {"erro": "Entre no painel do presidente."})
                p = proj_obter(pid) if pid.isalnum() else None
                if not p:
                    return self._json(404, {"erro": "Projeto não encontrado."})
                coms = [participacao.publico(c) for c in participacao.do_projeto(db, pid)]
            return self._json(200, {"projeto": p, "artigos": participacao.artigos(p.get("texto", "")), "comentarios": coms,
                                    "aberta": _consulta_aberta(p), "situacoes": participacao.SITUACOES})
        if caminho == "/radio.mp3":
            return self._radio_audio()
        if caminho == "/api/radio":
            with trava:
                ator = self._ator()
            if not ator or ator["papel"] != "presidente":
                return self._json(401, {"erro": "Entre no painel do presidente."})
            arquivos = []
            if RADIO_DIR.is_dir():
                arquivos = sorted(({"nome": f.name, "bytes": f.stat().st_size} for f in RADIO_DIR.iterdir()
                                   if f.is_file() and f.suffix.lower() in RADIO_FORMATOS), key=lambda x: x["nome"].lower())
            return self._json(200, {"ligada": RADIO_LOCAL, "arquivos": arquivos, "ouvintes": radio_ouvintes,
                                    "endereco": "/radio.mp3"})
        if caminho == "/api/legendas":
            try:
                desde = int((qs.get("desde") or ["0"])[0])
            except ValueError:
                desde = 0
            return self._json(200, {"ativa": transmissor.ativa, "ligadas": legendador.ligado, "itens": legendador.desde(desde)})
        m = re.fullmatch(r"/api/analise/([A-Za-z0-9]+)", caminho)
        if m:
            with trava:
                ator = self._ator()
                if not ator or ator["papel"] != "presidente":
                    return self._json(401, {"erro": "Entre como presidente."})
                p = proj_obter(m.group(1))
                if not p:
                    return self._json(404, {"erro": "Projeto não encontrado."})
                return self._json(200, {"analise": p.get("analise"), "ia": ia.descricao(), "temTexto": bool(p.get("texto")),
                                        "numero": p["numero"], "ementa": p["ementa"], "seloIA": integridade.selo_analise(p.get("analise"))})
        if caminho == "/api/normas":
            with trava:
                ator = self._ator()
                if not ator or ator["papel"] not in ("presidente", "parlamentar"):
                    return self._json(401, {"erro": "Entre no painel."})
                return self._json(200, {"normas": regimento.listar(db), "ia": ia.descricao(),
                                        "pdf": bool(shutil.which("pdftotext"))})
        if caminho == "/api/enquetes":
            with trava:
                ip = self._ip()
                lista = [{k: p.get(k) for k in ("id", "numero", "ementa", "autor", "situacao")}
                         | {"enquete": cidadania.enquete_resultado(db, p["id"])["total"], "meuVoto": cidadania.enquete_meu_voto(db, p["id"], ip),
                            "resumo": (p.get("analise") or {}).get("resumo") if (p.get("analise") or {}).get("revisada") else None}
                         for p in proj_todos() if p["situacao"] == "em_tramitacao"]
            lista.sort(key=lambda x: -x["enquete"])
            return self._json(200, {"projetos": lista[:60], "opcoes": cidadania.ENQUETE})
        m = re.fullmatch(r"/api/enquete/([A-Za-z0-9]+)", caminho)
        if m:
            with trava:
                p = proj_obter(m.group(1))
                if not p or p["situacao"] == "proposta":
                    return self._json(404, {"erro": "Projeto não encontrado."})
                r = cidadania.enquete_resultado(db, p["id"]) | {"meuVoto": cidadania.enquete_meu_voto(db, p["id"], self._ip()),
                                                              "aberta": p["situacao"] == "em_tramitacao"}
            return self._json(200, r)
        if caminho == "/api/ideias":
            with trava:
                ator = self._ator()
                interno = bool(ator and ator["papel"] == "presidente")
                r = {"ideias": cidadania.ideias(db, meta_ideia(), interno), "meta": meta_ideia(),
                     "prazoDias": cidadania.IDEIA_PRAZO_DIAS, "situacoes": cidadania.IDEIA_SITUACOES, "casa": S["casa"]["nome"]}
            return self._json(200, r)
        if caminho == "/api/perguntas":
            with trava:
                ator = self._ator()
                interno = bool(ator and ator["papel"] == "presidente")
                r = {"abertas": S["perguntasAbertas"], "perguntas": cidadania.perguntas(db, S["sessao"]["id"], interno),
                     "titulo": S["casa"]["titulo"], "casa": S["casa"]["nome"]}
            return self._json(200, r)
        if caminho == "/api/consultas":
            with trava:
                lista = []
                for p in proj_todos():
                    if p["situacao"] == "proposta" or not (p.get("consulta") or {}).get("desde"):
                        continue
                    n = db.execute("select count(*) from participacoes where projeto = ?", (p["id"],)).fetchone()[0]
                    lista.append({k: p.get(k) for k in ("id", "numero", "ementa", "autor", "situacao", "consulta", "areas")}
                                 | {"aberta": _consulta_aberta(p), "contribuicoes": n})
            lista.sort(key=lambda x: (not x["aberta"], -(x["consulta"] or {}).get("desde", 0)))
            return self._json(200, {"consultas": lista, "casa": S["casa"]["nome"], "situacoes": participacao.SITUACOES})
        if caminho.startswith("/api/consulta/"):
            pid = caminho.rsplit("/", 1)[1]
            with trava:
                ator = self._ator()
                p = proj_obter(pid)
                if not p or p["situacao"] == "proposta" or not (p.get("consulta") or {}).get("desde"):
                    return self._json(404, {"erro": "Consulta não encontrada."})
                interno = bool(ator and ator["papel"] == "presidente")
                autor = bool(ator and ator["papel"] == "parlamentar" and parl(ator["pid"])["nome"].lower() == (p.get("autor") or "").lower())
                coms = [participacao.publico(c) for c in participacao.do_projeto(db, pid) if interno or not c.get("oculto")]
            return self._json(200, {"projeto": {k: p.get(k) for k in ("id", "numero", "ementa", "autor", "situacao", "consulta", "anexo")}
                                    | {"aberta": _consulta_aberta(p)},
                                    "artigos": participacao.artigos(p.get("texto", "")), "comentarios": coms,
                                    "situacoes": participacao.SITUACOES, "podeResponder": interno or autor, "podeOcultar": interno})
        if caminho.startswith("/api/sessao-publica/"):
            sid = caminho.rsplit("/", 1)[1]
            with trava:
                r = retrato() if sid == "atual" else (sessao_arquivada(sid) if sid.isalnum() else None)
            if not r:
                return self._json(404, {"erro": "Sessão não encontrada."})
            return self._json(200, sessao_publica(r))
        if caminho.startswith("/anexo/") and caminho.endswith(".pdf"):
            pid = caminho[len("/anexo/"):-4]
            with trava:
                linha = db.execute("select dados from imagens where chave = ?", (f"anexo-{pid}",)).fetchone() if pid.isalnum() else None
            if not linha:
                return self._json(404, {"erro": "Anexo não encontrado."})
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(linha[0])))
            self.send_header("Content-Disposition", "inline")
            self.end_headers()
            self.wfile.write(linha[0])
            return True
        if caminho == "/api/dados/projetos.json":
            with trava:
                lista = [{k: p.get(k) for k in ("id", "numero", "ementa", "autor", "situacao", "areas", "beneficiados", "abrangencia",
                                                  "criado", "atualizado", "historico")}
                         | {"akn": f"/dados/akn/projeto/{p['id']}.xml", "temTexto": bool(p.get("texto"))}
                         for p in proj_todos() if p["situacao"] != "proposta"]
            return self._json(200, {"casa": S["casa"]["nome"], "licenca": "CC-BY 4.0", "gerado": agora(), "projetos": lista})
        if caminho == "/api/dados/sessoes.json":
            with trava:
                dados = dados_abertos_sessoes()
            return self._json(200, {"casa": S["casa"]["nome"], "licenca": "CC-BY 4.0", "gerado": agora(), "sessoes": dados})
        if caminho == "/api/dados/votacoes.csv":
            with trava:
                linhas = ["sessao;data;materia;ementa;tipo;resultado;sim;nao;abstencao;parlamentar;voto"]
                for s in dados_abertos_sessoes():
                    for v in s["votacoes"]:
                        c = v.get("contagem") or {}
                        base_linha = [s["titulo"], time.strftime("%Y-%m-%d", time.localtime((s["inicio"] or 0) / 1000)),
                                      v.get("numero") or "", (v.get("ementa") or "").replace(";", ","), v.get("tipo") or "",
                                      v.get("resultado") or "", str(c.get("sim", "")), str(c.get("nao", "")), str(c.get("abst", ""))]
                        votos = v.get("votos") or {}
                        if votos:
                            linhas += [";".join(base_linha + [s_nome, voto.get("valor") if isinstance(voto, dict) else str(voto)])
                                       for s_nome, voto in sorted((self._nome_parl(pid), x) for pid, x in votos.items())]
                        else:
                            linhas.append(";".join(base_linha + ["", ""]))
            corpo = ("﻿" + "\n".join(linhas) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
            return True
        m = re.fullmatch(r"/dados/akn/(projeto|sessao)/([A-Za-z0-9]+)\.xml", caminho)
        if m:
            with trava:
                if m.group(1) == "projeto":
                    p = proj_obter(m.group(2))
                    xml = akn.projeto(p, participacao.artigos(p.get("texto", "")), S["casa"]["nome"]) \
                        if p and p["situacao"] != "proposta" else None
                else:
                    r = sessao_arquivada(m.group(2))
                    xml = None
                    if r:
                        discursos = [{"pid": g["pid"], "nome": g["nome"], "partido": g["partido"], "texto": g["transcricao"]["texto"]}
                                     for g in sorted(r.get("gravacoes") or [], key=lambda g: g["inicio"])
                                     if (g.get("transcricao") or {}).get("publicada") and g["transcricao"].get("texto")]
                        xml = akn.debate(r, discursos, S["casa"]["nome"])
            if not xml:
                return self._json(404, {"erro": "Documento não encontrado."})
            corpo = xml.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/akn+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
            return True
        return None

    def _nome_parl(self, pid: str) -> str:
        p = next((x for x in S["parls"] if x["id"] == pid), None) or next((x for x in S.get("exParls", []) if x["id"] == pid), None)
        return (p or {}).get("nome", pid)

    def _cidade(self, corpo: dict):
        """POST /api/cidade {uf, municipio}: primeira configuração pelo painel do presidente."""
        with trava:
            ator = self._ator()
            if not ator or ator["papel"] != "presidente":
                return self._json(401, {"erro": "Entre como presidente."})
            if ator.get("deveTrocar"):
                return self._json(403, {"erro": "Troque o PIN padrão antes de continuar.", "codigo": "trocarPin"})
            if camara_configurada():
                return self._json(409, {"erro": "Esta Câmara já está configurada. Ajuste os vereadores em Plenário."})
        if not trava_cidade.acquire(blocking=False):
            return self._json(409, {"erro": "A busca já está em andamento."})
        try:
            try:
                dados = cidade.buscar(corpo.get("uf"), corpo.get("municipio"), log=lambda m: print("cidade:", m, flush=True))
            except ValueError as e:
                return self._json(400, {"erro": str(e)})
            except Exception as e:
                return self._json(502, {"erro": f"Não foi possível falar com o TSE agora ({e.__class__.__name__}). Verifique a internet e tente de novo."})
            with trava:
                if camara_configurada():
                    return self._json(409, {"erro": "Esta Câmara já está configurada."})
                aplicar_cidade(dados)
                registrar("presidente", "configurarCidade", {"uf": dados["uf"], "municipio": dados["municipio"],
                                                              "vereadores": len(dados["eleitos"])})
                confirmar()
            return self._json(200, {"ok": True, "nome": S["casa"]["nome"], "vereadores": len(dados["eleitos"]),
                                    "fotos": sum(1 for e in dados["eleitos"] if e.get("foto")), "populacao": dados["populacao"]})
        finally:
            trava_cidade.release()

    def _post_novos(self, caminho: str, corpo: dict):
        if caminho.startswith("/api/passkey/"):
            return self._passkey("POST", caminho, corpo)
        if caminho == "/api/cidade":
            return self._cidade(corpo)
        if caminho == "/api/verificar/selo":  # público: o selo de uma ata bate com uma sessão arquivada?
            h = str(corpo.get("selo") or "").strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", h):
                return self._json(200, {"confere": False})
            with trava:
                achada = None
                for t, fim, j in db.execute("select titulo, fim, json from sessoes where json like ?", (f"%{h}%",)).fetchall():
                    sel = json.loads(j).get("selo") or {}
                    if sel.get("hash") == h:
                        achada = (t, fim, sel)
                        break
                na_cadeia = bool(achada) and db.execute("select count(*) from eventos where id = ? and hash = ?",
                                                        (achada[2].get("evento"), h)).fetchone()[0] == 1
            if not achada:
                return self._json(200, {"confere": False})
            return self._json(200, {"confere": True, "naCadeia": na_cadeia, "sessao": {"titulo": achada[0], "fim": achada[1]}})
        if caminho == "/api/verificar/recibo":  # público: só "confere" ou "não confere"
            with trava:
                v = votacao_por_id(str(corpo.get("votacao") or "").strip())
                confere = integridade.conferir_recibo(S["_chaveRecibo"], v, corpo.get("recibo"))
            return self._json(200, {"confere": confere})
        if caminho == "/api/perguntar":
            pergunta = str(corpo.get("pergunta") or "").strip()
            if len(pergunta) < 4:
                return self._json(400, {"erro": "Escreva sua pergunta."})
            historico = corpo.get("historico") if isinstance(corpo.get("historico"), list) else []
            historico = [m for m in historico if isinstance(m, dict)]
            try:
                return self._json(200, integridade.anotar_resposta(
                    perguntar(pergunta, str(corpo.get("escopo") or "tudo"), historico[-6:], self._ip())))
            except PermissionError as e:
                return self._json(429, {"erro": str(e)})
        m = re.fullmatch(r"/api/consulta/([A-Za-z0-9]+)/comentar", caminho)
        if m:
            if corpo.get("site"):  # campo-isca invisível: robôs preenchem
                return self._json(200, {"ok": True})
            with trava:
                p = proj_obter(m.group(1))
                if not p or not _consulta_aberta(p):
                    return self._json(400, {"erro": "Esta consulta não está aberta."})
                try:
                    c = participacao.comentar(db, p, str(corpo.get("artigo") or "geral"), corpo.get("nome"), corpo.get("bairro"),
                                              corpo.get("texto"), corpo.get("email"), self._ip())
                except ValueError as e:
                    return self._json(400, {"erro": str(e)})
                except PermissionError as e:
                    return self._json(429, {"erro": str(e)})
                registrar("cidadao", "consultaComentar", {"projeto": p["id"], "comentario": c["id"]})
                S["projetosRev"] += 1
                confirmar()
            return self._json(200, {"ok": True, "comentario": participacao.publico(c)})
        if caminho == "/api/regimento/perguntar":
            with trava:
                ator = self._ator()
                if not ator or ator["papel"] not in ("presidente", "parlamentar"):
                    return self._json(401, {"erro": "Entre no painel."})
                base_regimento.atualizar(db)
            try:
                return self._json(200, integridade.anotar_resposta(regimento.perguntar(base_regimento, corpo.get("pergunta"))))
            except ValueError as e:
                return self._json(400, {"erro": str(e)})
        m = re.fullmatch(r"/api/enquete/([A-Za-z0-9]+)", caminho)
        if m:
            if corpo.get("site"):   # campo-isca: robôs preenchem
                return self._json(200, {"ok": True})
            with trava:
                p = proj_obter(m.group(1))
                if not p or p["situacao"] != "em_tramitacao":
                    return self._json(400, {"erro": "A enquete deste projeto não está aberta."})
                try:
                    r = cidadania.enquete_votar(db, p["id"], str(corpo.get("opcao") or ""), self._ip())
                except ValueError as e:
                    return self._json(400, {"erro": str(e)})
            return self._json(200, r | {"meuVoto": corpo.get("opcao")})
        if caminho == "/api/ideias":
            if corpo.get("site"):
                return self._json(200, {"ok": True})
            with trava:
                try:
                    i = cidadania.ideia_criar(db, corpo.get("titulo"), corpo.get("texto"), corpo.get("nome"), corpo.get("bairro"),
                                              corpo.get("email"), self._ip())
                except ValueError as e:
                    return self._json(400, {"erro": str(e)})
                except PermissionError as e:
                    return self._json(429, {"erro": str(e)})
                registrar("cidadao", "ideiaCriar", {"ideia": i["id"]})
                S["cidadaniaRev"] += 1
                confirmar()
            return self._json(200, {"ok": True, "ideia": cidadania.ideia_publica(i, meta_ideia())})
        m = re.fullmatch(r"/api/ideias/([0-9a-f]{10})/apoiar", caminho)
        if m:
            with trava:
                try:
                    i = cidadania.ideia_apoiar(db, m.group(1), self._ip(), meta_ideia())
                except LookupError as e:
                    return self._json(404, {"erro": str(e)})
                except ValueError as e:
                    return self._json(400, {"erro": str(e)})
                if i["situacao"] == "meta":
                    S["cidadaniaRev"] += 1
                    confirmar()
            return self._json(200, {"ok": True, "ideia": i})
        if caminho == "/api/perguntas":
            if corpo.get("site"):
                return self._json(200, {"ok": True})
            with trava:
                if not S["perguntasAbertas"]:
                    return self._json(400, {"erro": "A Câmara não está recebendo perguntas agora."})
                try:
                    p = cidadania.pergunta_enviar(db, S["sessao"]["id"], corpo.get("nome"), corpo.get("bairro"), corpo.get("texto"), self._ip())
                except ValueError as e:
                    return self._json(400, {"erro": str(e)})
                except PermissionError as e:
                    return self._json(429, {"erro": str(e)})
                S["cidadaniaRev"] += 1
                confirmar()
            return self._json(200, {"ok": True, "pergunta": cidadania.pergunta_publica(p)})
        if caminho == "/api/consulta/apoiar":
            with trava:
                try:
                    n = participacao.apoiar(db, str(corpo.get("id") or ""), self._ip())
                except LookupError as e:
                    return self._json(404, {"erro": str(e)})
            return self._json(200, {"ok": True, "apoios": n})
        return None

    def _certificado(self, caminho="/certificado"):
        """Primeiro acesso de cada aparelho (certificado.py): a página que reconhece o aparelho e instala a autoridade
        da Câmara, o instalador do Windows, o perfil da Apple, o .crt e /certificado/ok (teste pelo https://)."""
        if caminho == "/certificado/ok":
            return self._enviar(204, b"", "text/plain", {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})
        ca = TLS_DIR / "ca.crt"
        if not ca.exists():
            return self._json(404, {"erro": "HTTPS local não configurado."})
        dados = ca.read_bytes()
        host = certificado_mod.host_seguro(self.headers.get("Host", "")) or "localhost"
        url = certificado_mod.endereco_https(host, PORTA_HTTPS)
        with trava:
            nome = str((S.get("casa") or {}).get("nome") or "Câmara Municipal")
        quer_pagina = "text/html" in (self.headers.get("Accept") or "")
        if caminho == "/certificado" and quer_pagina:
            return self._enviar(200, certificado_mod.pagina(url, nome), "text/html; charset=utf-8", {"Cache-Control": "no-store"})
        if caminho == "/certificado/windows.cmd":
            return self._enviar(200, certificado_mod.windows_cmd(dados, url), "application/octet-stream",
                                {"Content-Disposition": 'attachment; filename="instalar-certificado-legis.cmd"'})
        if caminho == "/certificado/legis.mobileconfig":
            return self._enviar(200, certificado_mod.mobileconfig(dados, nome), "application/x-apple-aspen-config",
                                {"Content-Disposition": 'attachment; filename="legis.mobileconfig"'})
        if caminho not in ("/certificado", "/certificado.crt"):
            return self._json(404, {"erro": "Não encontrado."})
        return self._enviar(200, dados, "application/x-x509-ca-cert",
                            {"Content-Disposition": 'attachment; filename="legis-camara.crt"'})

    def _enviar(self, codigo, corpo, tipo, extras=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        for k, v in (extras or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        if corpo and self.command != "HEAD":
            self.wfile.write(corpo)

    def _sessoes(self, caminho):
        """GET /api/sessoes, /api/sessoes/<id>, /api/ata/<id|atual>.pdf — só o presidente."""
        with trava:
            ator = self._ator()
        if not ator or ator["papel"] != "presidente":
            return self._json(401, {"erro": "Entre no painel do presidente."})
        partes = caminho.strip("/").split("/")
        if partes == ["api", "sessoes"]:
            with trava:
                linhas = db.execute("select id, titulo, inicio, fim, resumo from sessoes order by fim desc").fetchall()
            return self._json(200, {"sessoes": [{"id": i, "titulo": t, "inicio": a, "fim": f, **json.loads(r)}
                                                 for i, t, a, f, r in linhas]})
        if len(partes) == 3 and partes[1] == "sessoes" and partes[2].isalnum():
            with trava:
                r = sessao_arquivada(partes[2])
            return self._json(200, r) if r else self._json(404, {"erro": "Sessão não encontrada."})
        if len(partes) == 3 and partes[1] == "ata" and partes[2].endswith(".pdf"):
            sid = partes[2][:-4]
            with trava:
                r = retrato() if sid == "atual" else sessao_arquivada(sid) if sid.isalnum() else None
                logo = logo_ata()
            if not r:
                return self._json(404, {"erro": "Sessão não encontrada."})
            pdf = gerar_ata(r, logo, cor=S["casa"].get("destaque", ""))
            nome = f"Ata - {r['titulo']} - {time.strftime('%Y-%m-%d', time.localtime(r['inicio'] / 1000))}.pdf"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(pdf)))
            self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + urllib.parse.quote(nome))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(pdf)
            return
        return self._json(404, {"erro": "Não encontrado."})

    def _gravacao_post(self, caminho):
        """/api/gravacao/iniciar, /api/gravacao/<id>/parte (corpo binário), /api/gravacao/<id>/fim"""
        partes = caminho.strip("/").split("/")
        if len(partes) < 3:
            return self._json(404, {"erro": "Não encontrado."})
        with trava:
            ator = self._ator()
        if not ator or ator["papel"] != "presidente":
            return self._json(401, {"erro": "Só o painel do presidente grava."})
        if partes[2] == "iniciar":
            corpo = self._ler()
            with trava:
                g = gravacao_iniciar(corpo.get("pid"), corpo.get("inicio"), corpo.get("mime"))
                confirmar()
            return self._json(200, {"id": g["id"]})
        if len(partes) != 4 or not partes[2].isalnum():
            return self._json(404, {"erro": "Não encontrado."})
        gid, passo = partes[2], partes[3]
        with trava:
            g = _gravacao(gid)
        if passo == "parte":
            n = self._tamanho()
            if n > LIMITE_PARTE:
                raise Erro("Parte grande demais.")
            dados = self.rfile.read(n)
            with open(GRAVACOES / f"{g['id']}.{g['ext']}", "ab") as f:
                f.write(dados)
            with trava:
                g = grav_obter(gid)
                g["bytes"] += len(dados)
                g["ultimo"] = agora()
                grav_salvar(g)
                transmitir(pacote())
            return self._json(200, {"ok": True})
        if passo == "fim":
            self._ler()
            with trava:
                g = grav_obter(gid)
                g["fim"] = agora()
                g["ultimo"] = g["fim"]
                grav_salvar(g)
                registrar("presidente", "gravacao", {"id": g["id"], "pid": g["pid"], "bytes": g["bytes"]})
                confirmar()
            threading.Thread(target=_pos_gravacao, args=(g,), daemon=True).start()
            return self._json(200, {"ok": True})
        return self._json(404, {"erro": "Não encontrado."})

    def _eventos(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q: queue.Queue = queue.Queue(maxsize=2)
        with trava:
            clientes.add(q)
            inicial = pacote()
        try:
            self.wfile.write(f"retry: 2000\ndata: {inicial}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=10)
                except queue.Empty:
                    msg = json.dumps({"tipo": "relogio", "agora": agora()})
                self.wfile.write(f"data: {msg}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with trava:
                clientes.discard(q)

    # ----- POST
    def do_POST(self):
        if self._redirecionar():
            return
        caminho = urlparse(self.path).path
        try:
            if caminho.startswith("/api/gravacao/"):
                return self._gravacao_post(caminho)
            if caminho == "/api/transmissao/parte":
                return self._transmissao_parte()
            if caminho == "/api/radio/arquivo":
                return self._radio_arquivo()
            if acompanhar.form(self, caminho) is not None:   # formulários de confirmação/descadastro (não são JSON)
                return
            corpo = self._ler()
            if caminho == "/api/entrar":
                return self._entrar(corpo)
            if caminho == "/api/sair":
                with trava:
                    token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
                    if token:
                        S["_tokens"].pop(chave_token(token), None)
                    salvar()
                return self._json(200, {"ok": True})
            if caminho == "/api/acao":
                return self._acao(corpo)
            if caminho == "/api/trocar-pin":
                return self._trocar_pin(corpo)
            novo = self._post_novos(caminho, corpo)
            if novo is not None:
                return novo
            novo = acompanhar.post(self, caminho, corpo)
            if novo is not None:
                return novo
            return self._json(404, {"erro": "Não encontrado."})
        except TrocaPin:
            return self._json(403, {"erro": "Troque o PIN inicial antes de continuar.", "codigo": "trocarPin"})
        except Erro as e:
            return self._json(400, {"erro": str(e)})

    def _pin_guardado(self, papel: str, pid) -> str | None:
        if papel == "presidente":
            return S.get("_pinPresidente") or PIN_PRESIDENTE or S.get("_pinInicialPresidente")
        return S["_pins"].get(pid)

    def _entrar(self, corpo):
        papel, pin, pid = corpo.get("papel"), str(corpo.get("pin") or ""), corpo.get("pid")
        if papel not in ("presidente", "parlamentar"):
            return self._json(400, {"erro": "Acesso inválido."})
        if papel == "presidente":
            pid = None
        elif not isinstance(pid, str) or len(pid) > 32:
            pid = None
        with trava:
            guardado = self._pin_guardado(papel, pid)
        # a conta do bloqueio nunca vem livre do pedido: presidente é uma só; parlamentar só se o id existir
        conta = "presidente" if papel == "presidente" else (f"parlamentar:{pid}" if guardado else "parlamentar:?")
        ip = self._ip()
        espera, marca = bloqueio.tentar(conta, ip)  # reserva a tentativa antes do PBKDF2 (sem corrida)
        if espera:
            return self._json(429, {"erro": f"Muitas tentativas erradas. Tente de novo em {max(1, espera // 60)} minuto(s)."})
        if not guardado or not seguranca.verificar_pin(pin, guardado):  # PBKDF2 fora da trava do plenário
            time.sleep(0.4)
            return self._json(403, {"erro": "PIN incorreto."})
        bloqueio.acertou(conta, ip, marca)
        with trava:
            ator = {"papel": papel, "pid": pid if papel == "parlamentar" else None, "criado": agora(), "uso": agora(),
                    "deveTrocar": TROCA_PIN_OBRIGATORIA and pin_exige_troca(papel, pin, pid)}
            token = secrets.token_urlsafe(24)
            S["_tokens"][chave_token(token)] = ator
            registrar(ator["pid"] or "presidente", "entrar", {})
            salvar()
        return self._json(200, {"token": token, "ator": {k: ator[k] for k in ("papel", "pid", "deveTrocar")}})

    def _trocar_pin(self, corpo):
        with trava:
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            ator = self._ator(troca_ok=True)
            if not ator:
                return self._json(401, {"erro": "Sessão expirada. Entre novamente."})
            guardado = self._pin_guardado(ator["papel"], ator["pid"])
        conta = "presidente" if ator["papel"] == "presidente" else f"parlamentar:{ator['pid']}"
        ip = self._ip()
        espera, marca = bloqueio.tentar(conta, ip)
        if espera:
            return self._json(429, {"erro": "Muitas tentativas erradas. Aguarde alguns minutos."})
        if not seguranca.verificar_pin(str(corpo.get("atual") or ""), guardado):
            return self._json(403, {"erro": "PIN atual incorreto."})
        bloqueio.acertou(conta, ip, marca)
        novo = str(corpo.get("novo") or "")
        erro = seguranca.validar_novo_pin(novo, 6 if ator["papel"] == "presidente" else 4)
        if erro:
            return self._json(400, {"erro": erro})
        if novo == str(corpo.get("atual")):
            return self._json(400, {"erro": "O novo PIN deve ser diferente do atual."})
        h = seguranca.hash_pin(novo)
        with trava:
            if ator["papel"] == "presidente":
                S["_pinPresidente"] = h
                S["_pinInicialPresidente"] = ""
            else:
                S["_pins"][ator["pid"]] = h
                S["_pinsIniciais"].pop(ator["pid"], None)
            # encerra os outros acessos desta conta; este continua valendo
            ct = chave_token(token)
            S["_tokens"] = {t: a for t, a in S["_tokens"].items()
                            if t == ct or not (a["papel"] == ator["papel"] and a.get("pid") == ator["pid"])}
            S["_tokens"][ct]["deveTrocar"] = False
            registrar(ator["pid"] or "presidente", "trocarPin", {})
            salvar()
        return self._json(200, {"ok": True})

    # ----- passkey / WebAuthn (passkey.py)
    def _origem_passkey(self):
        """(origem, rpId) esperados para este pedido, ou o ErroPasskey que explica por que não dá (IP, http)."""
        https = isinstance(self.request, ssl.SSLSocket) or (
            PROXY_CONFIAVEL and _de_proxy(self.client_address[0]) and self.headers.get("X-Forwarded-Proto") == "https")
        try:
            return passkey.origem_esperada(self.headers.get("Host", ""), https)
        except passkey.ErroPasskey as e:
            return e

    def _passkey(self, metodo, caminho, corpo):
        with trava:
            ator = self._ator()
        r = passkeys.rota(metodo, caminho, corpo, ator, self._ip(), self._origem_passkey())
        return self._json(*r) if r else None

    def _acao(self, corpo):
        nome, args = corpo.get("nome"), dict(corpo.get("args") or {})
        with trava:
            ator_pk = self._ator()
        pk = passkeys.confirmar_acao(nome, args, ator_pk, self._ip(), self._origem_passkey())
        if isinstance(pk, tuple):
            return self._json(*pk)
        if nome in ACOES_COM_PIN and pk is not True:  # confirma o PIN antes, fora da trava (PBKDF2 é lento de propósito)
            with trava:
                ator = self._ator()
                guardado = self._pin_guardado("parlamentar", ator["pid"]) if ator and ator["papel"] == "parlamentar" else None
            if guardado:
                conta, ip = f"parlamentar:{ator['pid']}", self._ip()
                espera, marca = bloqueio.tentar(conta, ip)
                if espera:
                    return self._json(429, {"erro": "Muitas tentativas erradas. Aguarde alguns minutos."})
                if not seguranca.verificar_pin(str(args.pop("pin", "")), guardado):
                    return self._json(403, {"erro": "PIN incorreto: o voto não foi registrado.", "codigo": "pin"})
                bloqueio.acertou(conta, ip, marca)
        args.pop("pin", None)
        with trava:
            ator = self._ator()
            if not ator:
                return self._json(401, {"erro": "Sessão expirada. Entre novamente."})
            if nome not in ACOES or ator["papel"] not in ACOES[nome][0]:
                return self._json(403, {"erro": "Ação não permitida."})
            antes = json.dumps(S)
            # voto secreto: a auditoria guarda só que a pessoa votou, nunca o voto
            secreta = nome in ("votar", "votarPor") and (S.get("votacao") or {}).get("tipo") == "secreta"
            try:
                ACOES[nome][1](ator, **args)
            except Erro:
                S.clear()
                S.update(json.loads(antes))
                raise
            except (TypeError, ValueError, KeyError, AttributeError, IndexError):
                # qualquer parâmetro malformado: desfaz tudo (nada fica pela metade) e responde, sem derrubar o pedido
                S.clear()
                S.update(json.loads(antes))
                raise Erro("Parâmetros inválidos.")
            except Exception:
                S.clear()
                S.update(json.loads(antes))
                print(f"Erro inesperado na ação {nome}:", flush=True)
                traceback.print_exc()
                raise Erro("Erro interno ao executar a ação. Nada foi alterado.")
            if secreta:
                args = {"votou": True, **({"pid": args.get("pid")} if nome == "votarPor" else {})}
            registrar(ator["pid"] or "presidente", nome, args)
            confirmar()
        return self._json(200, {"ok": True})


def _pin_confere(pid: str, pin: str) -> bool:
    with trava:
        guardado = S["_pins"].get(pid)
    return bool(guardado) and seguranca.verificar_pin(pin, guardado)  # PBKDF2 fora da trava


passkeys = passkey.Passkeys(db, S, trava, registrar, confirmar, bloqueio, _pin_confere,
                            lambda pid: parl(pid)["nome"], ACOES_COM_PIN)


class ServidorLimitado(ThreadingHTTPServer):
    """Uma thread por conexão, mas com teto (MAX_CONEXOES): acima dele a conexão nova é fechada na hora."""
    daemon_threads = True

    def __init__(self, *a, **k):
        self._vagas = threading.BoundedSemaphore(MAX_CONEXOES)
        super().__init__(*a, **k)

    def process_request(self, request, client_address):
        if not self._vagas.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._vagas.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._vagas.release()


class ServidorTLS(ServidorLimitado):
    daemon_threads = True

    def __init__(self, endereco, manipulador, contexto: ssl.SSLContext):
        super().__init__(endereco, manipulador)
        self.contexto = contexto

    def get_request(self):
        sock, endereco = self.socket.accept()
        return self.contexto.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), endereco

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ssl.SSLError, OSError)):
            return  # navegador que ainda não confia no certificado, conexão caída etc.
        super().handle_error(request, client_address)


def certificado() -> tuple[Path, Path] | None:
    crt, key = os.environ.get("TLS_CERT"), os.environ.get("TLS_KEY")
    if crt and key:
        return Path(crt), Path(key)
    if HTTPS == "0":
        return None
    try:
        return tls.gerar(TLS_DIR)
    except Exception as e:
        print(f"HTTPS desativado: {e}")
        return None


def vigiar_certificado(ctx, intervalo=300):
    """IP novo pelo DHCP ou nome trocado com o LEGIS ligado: renova o certificado do servidor (mesma autoridade) e
    passa a usá-lo nas conexões novas, sem reiniciar."""
    while True:
        time.sleep(intervalo)
        try:
            crt = TLS_DIR / "servidor.crt"
            if tls.precisa_renovar(crt):
                novo, chave = tls.gerar(TLS_DIR, renovar=True)
                ctx.load_cert_chain(str(novo), str(chave))
                print(f"Certificado renovado para os endereços atuais: {tls.nomes_no_certificado(novo)}", flush=True)
        except Exception as e:  # noqa: BLE001 - o LEGIS continua com o certificado que tem
            print(f"Renovação do certificado falhou: {e}", flush=True)


def kit_certificado(pasta: Path, nome: str, porta: int) -> list[Path]:
    """Arquivos para instalar o certificado nos aparelhos sem internet (pendrive): Windows, iPhone/Mac e o .crt."""
    crt, _ = tls.gerar(TLS_DIR)
    pem = (TLS_DIR / "ca.crt").read_bytes()
    url = certificado_mod.endereco_https(f"{nome}.local", porta)
    pasta.mkdir(parents=True, exist_ok=True)
    arquivos = {
        "LEGIS-certificado.crt": pem,
        "Windows - instalar certificado.cmd": certificado_mod.windows_cmd(pem, url),
        "iPhone-iPad-Mac - instalar.mobileconfig": certificado_mod.mobileconfig(pem, S["casa"]["nome"]),
        "LEIA-ME.txt": (f"Certificado do LEGIS de {S['casa']['nome']}\r\n\r\n"
                        f"Com o aparelho na rede da Câmara, o mais fácil é abrir http://{nome}.local/certificado .\r\n"
                        "Sem rede, use estes arquivos (copie para um pendrive):\r\n"
                        "- Windows: dois cliques em 'Windows - instalar certificado.cmd' (não precisa de administrador).\r\n"
                        "- iPhone, iPad e Mac: abra o .mobileconfig e confirme em Ajustes > Geral > VPN e Gerenciamento;\r\n"
                        "  no iPhone, ative também em Ajustes > Geral > Sobre > Ajustes de Confiança de Certificados.\r\n"
                        "- Android: Configurações > Segurança > Criptografia > Instalar certificado > Certificado de CA,\r\n"
                        "  e escolha LEGIS-certificado.crt.\r\n"
                        "- Linux: sudo cp LEGIS-certificado.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates\r\n"
                        f"\r\nDepois abra {url}\r\n"
                        "Este certificado é desta Câmara: foi criado na instalação deste servidor e só vale para a rede local.\r\n"
                        ).encode("utf-8"),
    }
    saida = []
    for nome_arq, dados in arquivos.items():
        (pasta / nome_arq).write_bytes(dados)
        saida.append(pasta / nome_arq)
    return saida


def main():
    if "--reaplicar-modelo" in sys.argv:
        with trava:
            feito = aplicar_modelo(forcar=True)
        print("Modelo reaplicado." if feito else "Sem modelo (LEGIS_MODELO) para aplicar.")
        return
    if "--kit-certificado" in sys.argv:
        pasta = Path(sys.argv[sys.argv.index("--kit-certificado") + 1])
        nome = os.environ.get("LEGIS_NOME") or socket.gethostname()
        for arq in kit_certificado(pasta, nome, PORTA_HTTPS):
            print(arq)
        return
    if "--renovar-certificado" in sys.argv:
        crt, _ = tls.gerar(TLS_DIR, renovar=True)
        print(f"Certificado renovado: {crt}\n{tls.nomes_no_certificado(crt)}")
        return
    global HTTPS_ATIVO
    threading.Thread(target=tique, daemon=True).start()
    threading.Thread(target=agendador_backup, daemon=True).start()
    threading.Thread(target=trabalhador_transcricao, daemon=True).start()
    legendador.iniciar()
    with trava:
        transcricao_retomar()
    par = certificado()
    if par:
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(str(par[0]), str(par[1]))
        try:
            https = ServidorTLS((HOST, PORTA_HTTPS), Manipulador, ctx)   # mesmo HOST do HTTP (atrás de proxy: 127.0.0.1)
        except OSError as e:
            print(f"HTTPS desativado: porta {PORTA_HTTPS} indisponível ({e.strerror}). Use PORTA_HTTPS=outra porta.")
            par = None
    if par:
        HTTPS_ATIVO = True
        threading.Thread(target=https.serve_forever, daemon=True).start()
        if not os.environ.get("TLS_CERT"):
            threading.Thread(target=vigiar_certificado, args=(ctx,), daemon=True).start()
        ips = [i for i in tls.ips_locais() if i != "127.0.0.1"]
        print(f"HTTPS: https://localhost:{PORTA_HTTPS}" + "".join(f"  https://{i}:{PORTA_HTTPS}" for i in ips[:3]))
        print("Primeiro acesso de cada aparelho (instala o certificado e abre o LEGIS): "
              + "  ".join(f"http://{i}{'' if PORTA == 80 else f':{PORTA}'}/certificado" for i in ips[:3]))
    servidor = ServidorLimitado((HOST, PORTA), Manipulador)
    servidor.daemon_threads = True
    servidor.redireciona = bool(par)
    print(f"Parlatório Legis em http://{'localhost' if HOST in ('0.0.0.0', '::') else HOST}:{PORTA}", flush=True)
    with trava:
        inicial = S.get("_pinInicialPresidente") if not S.get("_pinPresidente") and not PIN_PRESIDENTE else ""
    if inicial:
        print(f"PIN inicial do presidente: {inicial} (troque no primeiro acesso)", flush=True)
    elif not S.get("_pinPresidente"):
        print("PIN inicial do presidente: o da variável PIN_PRESIDENTE (troque no primeiro acesso)", flush=True)
    if not TROCA_PIN_OBRIGATORIA:
        print("ATENÇÃO: troca obrigatória de PIN desligada (TROCA_PIN=0) — use só em testes.", flush=True)
    print(f"Transcrição dos discursos: {transcritor.descricao()}", flush=True)
    servidor.serve_forever()


if __name__ == "__main__":
    main()
