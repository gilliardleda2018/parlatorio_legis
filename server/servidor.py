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
import json
import math
import os
import queue
import re
import secrets
import ssl
import shutil
import subprocess
import sys
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.parse
from urllib.parse import urlparse

import tls
from ata import gerar_ata
import estatisticas
import recomendacao
import sapl as sapl_cli
import seguranca
import backup as backup_mod
import transcricao
from texto import _sem_acento
from mesa import CFG_PADRAO, PRESETS, Mesa

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
PIN_PRESIDENTE = os.environ.get("PIN_PRESIDENTE", "1234")  # PIN inicial; depois da troca vale o salvo (com hash)
# Período de testes: TROCA_PIN=0 deixa entrar com PIN padrão (0000) sem obrigar a troca. Em produção, deixe ligado.
TROCA_PIN_OBRIGATORIA = os.environ.get("TROCA_PIN", "1") != "0"
OCIOSO_PRESIDENTE = 12 * 3600_000      # login do painel expira após 12 h sem uso
OCIOSO_PARLAMENTAR = 30 * 86400_000    # no celular, após 30 dias sem uso
VALIDADE_MAXIMA = 90 * 86400_000       # nenhum login dura mais que 90 dias
ACOES_COM_PIN = {"virtualVotar", "virtualDestaque"}  # confirmação do próprio PIN
bloqueio = seguranca.Bloqueio()
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", BANCO.parent / "backups"))
BACKUP_HORA = os.environ.get("BACKUP_HORA", "03:00")   # backup diário automático (vazio desliga)
BACKUP_MANTER = int(os.environ.get("BACKUP_MANTER", "14"))
LIMITE_CORPO = 4 * 1024 * 1024
TIPOS_IMAGEM = {"image/png", "image/jpeg", "image/webp"}

trava = threading.RLock()
clientes: set[queue.Queue] = set()


def agora() -> int:
    return int(time.time() * 1000)


def uid() -> str:
    return secrets.token_hex(4)


class Erro(Exception):
    """Erro de regra: volta para a tela como mensagem."""


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


def registrar(autor: str, acao: str, args: dict):
    limpo = {k: v for k, v in args.items() if k not in ("dataUrl", "pin")}
    db.execute(
        "insert into eventos (ts, autor, acao, args) values (?, ?, ?, ?)",
        (agora(), autor, acao, json.dumps(limpo, ensure_ascii=False)),
    )


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
        "casa": {"nome": "Câmara Municipal", "titulo": "45ª Sessão Ordinária", "destaque": "#d66060",
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
        "_pins": {p["id"]: seguranca.hash_pin("0000") for p in parls},
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
        "sessao": {"id": secrets.token_hex(8), "inicio": agora()},  # sessão em andamento
        "relevancia": {"populacao": 100000, "pesos": dict(estatisticas.PESOS_PADRAO)},
        "projetosRev": 0,       # muda quando o banco de projetos muda (as telas recarregam a lista)
        "_perfis": {},          # pid -> {areas, temas} declarados na área privada do parlamentar
        "_feedback": {},        # pid -> {prática: "salvo" | "descartado"}
        "sapl": {"url": "", "casa": None, "tipos": [], "sessoes": [], "job": None},  # integração com o SAPL
        "rodape": rodape_padrao(),  # contatos públicos da câmara e da Aggilli, no rodapé das páginas
        "virtuais": [],         # sessões virtuais assíncronas (votação online com prazo)
        "remoto": {"ativo": False, "servidor": "https://meet.jit.si"},  # sessão híbrida por videoconferência
        "presencaModo": {},     # pid -> "remoto" quando a presença foi registrada pela videoconferência
        "_remoto": {"sala": ""},  # nome da sala: nunca vai para as telas públicas
        "_pinPresidente": None,   # hash do PIN do painel depois da primeira troca
        "backup": {"ultimo": None, "arquivo": None, "tamanho": 0, "nextcloud": None, "erro": None, "emAndamento": False},
        "mesa": dict(CFG_PADRAO),
    }


def carregar() -> dict:
    linha = db.execute("select json from estado where id = 1").fetchone()
    if not linha:
        return estado_inicial()
    estado = json.loads(linha[0])
    for k, v in NOVOS_CAMPOS().items():
        estado.setdefault(k, v)
    for k, v in CFG_PADRAO.items():
        estado["mesa"].setdefault(k, v)
    estado["cfg"].setdefault("transcricaoAutoPublicar", False)
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
    return estado


S = carregar()


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
        elif k in ("porta", "canalTribuna"):
            novo[k] = int(v or 0)
        elif k in ("valorAberto", "valorFechado"):
            novo[k] = v
        else:
            novo[k] = str(v).strip()
    for k in ("valorAberto", "valorFechado"):
        novo[k] = float(novo[k]) if novo.get("tipoValor") == "f" else int(float(novo[k]))
    if novo["driver"] not in ("simulado", "osc", "http"):
        raise Erro("Driver inválido.")
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
    S["pauta"].append({"id": id, "numero": p["numero"], "ementa": p["ementa"], "autor": p["autor"], "situacao": "pendente"})
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
        "presentes": len(S["presenca"]),
    }


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
        S["_pins"][p["id"]] = seguranca.hash_pin("0000")
    if pin:
        S["_pins"][p["id"]] = seguranca.hash_pin(str(pin))
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
            p["historico"] = [h for h in p["historico"] if h["ts"] < inicio]
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
                S["_pins"][p["id"]] = seguranca.hash_pin("0000")
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
                 saplUrl=f"{cli.base}/materia/{m['id']}", textoUrl=m.get("texto_original") or "")
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
    db.execute("insert or replace into sessoes (id, titulo, inicio, fim, resumo, json) values (?, ?, ?, ?, ?, ?)",
               (r["id"], r["titulo"], r["inicio"], fim, json.dumps(resumo(r)), json.dumps(r, ensure_ascii=False)))
    db.commit()
    nao_votadas = [dict(m, situacao="pendente") for m in S["pauta"]
                   if m["situacao"] in ("pendente", "em_discussao", "em_votacao", "adiada")] if manterPauta else []
    S.update(filas={}, orador=None, interr=None, pedidos=[], hist=[], presenca={}, votacao=None, votacoes=[],
             micsManuais={}, micsCortados=[], congelado=None, ocorrencias=[], pauta=nao_votadas, materiaAtual=None,
             sessao={"id": secrets.token_hex(8), "inicio": agora()})
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
    pub["microfones"], pub["tribunaAberta"] = microfones()
    pub["mesaStatus"] = mesa.publico()
    pub["mesaPresets"] = PRESETS
    pub["gravacoes"] = [grav_publica(g) for g in grav_da_sessao(S["sessao"]["id"])]
    pub["transcricaoMotor"] = transcritor.descricao()
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


# ---------------------------------------------------------------- HTTP
class Manipulador(BaseHTTPRequestHandler):
    server_version = "ParlatorioLegis/2"

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

    def _ler(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > LIMITE_CORPO:
            raise Erro("Requisição grande demais.")
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            raise Erro("JSON inválido.")

    def _ator(self):
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        a = S["_tokens"].get(token) if token else None
        if not a:
            return None
        ocioso = OCIOSO_PRESIDENTE if a["papel"] == "presidente" else OCIOSO_PARLAMENTAR
        if agora() - a.get("uso", 0) > ocioso or agora() - a.get("criado", 0) > VALIDADE_MAXIMA:
            S["_tokens"].pop(token, None)
            return None
        if agora() - a.get("uso", 0) > 300_000:
            a["uso"] = agora()
        return a

    def _ip(self) -> str:
        return self.client_address[0] if self.client_address else "?"

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
        if host in ("localhost", "127.0.0.1", "::1", "") or caminho in ("/certificado", "/certificado.crt"):
            return False
        self.send_response(308 if self.command == "POST" else 301)
        self.send_header("Location", f"https://{host}:{PORTA_HTTPS}{self.path}")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def do_GET(self):
        if self._redirecionar():
            return
        caminho = urlparse(self.path).path
        if caminho in ("/certificado", "/certificado.crt"):
            return self._certificado()
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
                return self._json(200, {"servidor": S["remoto"]["servidor"], "sala": S["_remoto"]["sala"], "nome": nome,
                                        "moderador": ator["papel"] == "presidente"})
        if caminho.startswith("/api/perfil/"):
            return self._perfil(caminho.rsplit("/", 1)[1])
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
        if caminho == "/api/eu":
            with trava:
                a = self._ator()
            return self._json(200, {"ator": {k: a.get(k) for k in ("papel", "pid", "deveTrocar")} if a else None})
        return self._estatico(caminho)

    def _estatico(self, caminho):
        if caminho in ("", "/"):
            caminho = "/index.html"
        if caminho.startswith("/parlamentares/"):  # página pública de cada parlamentar
            caminho = "/perfil.html"
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
        self.end_headers()
        self.wfile.write(dados)

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

    def _certificado(self):
        ca = TLS_DIR / "ca.crt"
        if not ca.exists():
            return self._json(404, {"erro": "HTTPS local não configurado."})
        dados = ca.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-x509-ca-cert")
        self.send_header("Content-Disposition", 'attachment; filename="parlatorio-legis-ca.crt"')
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

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
            pdf = gerar_ata(r, logo)
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
            n = int(self.headers.get("Content-Length") or 0)
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
            corpo = self._ler()
            if caminho == "/api/entrar":
                return self._entrar(corpo)
            if caminho == "/api/sair":
                with trava:
                    token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
                    S["_tokens"].pop(token, None)
                    salvar()
                return self._json(200, {"ok": True})
            if caminho == "/api/acao":
                return self._acao(corpo)
            if caminho == "/api/trocar-pin":
                return self._trocar_pin(corpo)
            return self._json(404, {"erro": "Não encontrado."})
        except Erro as e:
            return self._json(400, {"erro": str(e)})

    def _pin_guardado(self, papel: str, pid) -> str | None:
        if papel == "presidente":
            return S.get("_pinPresidente") or PIN_PRESIDENTE
        return S["_pins"].get(pid)

    def _entrar(self, corpo):
        papel, pin, pid = corpo.get("papel"), str(corpo.get("pin") or ""), corpo.get("pid")
        if papel not in ("presidente", "parlamentar"):
            return self._json(400, {"erro": "Acesso inválido."})
        conta = f"{papel}:{pid or ''}"
        espera = bloqueio.espera(conta, self._ip())
        if espera:
            return self._json(429, {"erro": f"Muitas tentativas erradas. Tente de novo em {max(1, espera // 60)} minuto(s)."})
        with trava:
            guardado = self._pin_guardado(papel, pid)
        if not guardado or not seguranca.verificar_pin(pin, guardado):  # PBKDF2 fora da trava do plenário
            bloqueio.falhou(conta, self._ip())
            time.sleep(0.4)
            return self._json(403, {"erro": "PIN incorreto."})
        bloqueio.acertou(conta)
        with trava:
            ator = {"papel": papel, "pid": pid if papel == "parlamentar" else None, "criado": agora(), "uso": agora(),
                    "deveTrocar": TROCA_PIN_OBRIGATORIA and seguranca.pin_fraco(pin)}
            token = secrets.token_urlsafe(24)
            S["_tokens"][token] = ator
            registrar(ator["pid"] or "presidente", "entrar", {})
            salvar()
        return self._json(200, {"token": token, "ator": {k: ator[k] for k in ("papel", "pid", "deveTrocar")}})

    def _trocar_pin(self, corpo):
        with trava:
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            ator = self._ator()
            if not ator:
                return self._json(401, {"erro": "Sessão expirada. Entre novamente."})
            guardado = self._pin_guardado(ator["papel"], ator["pid"])
        conta = f"{ator['papel']}:{ator['pid'] or ''}"
        if bloqueio.espera(conta, self._ip()):
            return self._json(429, {"erro": "Muitas tentativas erradas. Aguarde alguns minutos."})
        if not seguranca.verificar_pin(str(corpo.get("atual") or ""), guardado):
            bloqueio.falhou(conta, self._ip())
            return self._json(403, {"erro": "PIN atual incorreto."})
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
            else:
                S["_pins"][ator["pid"]] = h
            # encerra os outros acessos desta conta; este continua valendo
            S["_tokens"] = {t: a for t, a in S["_tokens"].items()
                            if t == token or not (a["papel"] == ator["papel"] and a.get("pid") == ator["pid"])}
            S["_tokens"][token]["deveTrocar"] = False
            registrar(ator["pid"] or "presidente", "trocarPin", {})
            salvar()
        return self._json(200, {"ok": True})

    def _acao(self, corpo):
        nome, args = corpo.get("nome"), dict(corpo.get("args") or {})
        if nome in ACOES_COM_PIN:  # confirma o PIN antes, fora da trava (PBKDF2 é lento de propósito)
            with trava:
                ator = self._ator()
                guardado = self._pin_guardado("parlamentar", ator["pid"]) if ator and ator["papel"] == "parlamentar" else None
            conta = f"parlamentar:{ator['pid'] if ator else ''}"
            if guardado:
                if bloqueio.espera(conta, self._ip()):
                    return self._json(429, {"erro": "Muitas tentativas erradas. Aguarde alguns minutos."})
                if not seguranca.verificar_pin(str(args.pop("pin", "")), guardado):
                    bloqueio.falhou(conta, self._ip())
                    return self._json(403, {"erro": "PIN incorreto: o voto não foi registrado.", "codigo": "pin"})
                bloqueio.acertou(conta)
        args.pop("pin", None)
        with trava:
            ator = self._ator()
            if not ator:
                return self._json(401, {"erro": "Sessão expirada. Entre novamente."})
            if ator.get("deveTrocar"):
                return self._json(403, {"erro": "Troque o PIN padrão antes de continuar.", "codigo": "trocarPin"})
            if nome not in ACOES or ator["papel"] not in ACOES[nome][0]:
                return self._json(403, {"erro": "Ação não permitida."})
            antes = json.dumps(S)
            try:
                ACOES[nome][1](ator, **args)
            except Erro:
                S.clear()
                S.update(json.loads(antes))
                raise
            except TypeError:
                S.clear()
                S.update(json.loads(antes))
                raise Erro("Parâmetros inválidos.")
            registrar(ator["pid"] or "presidente", nome, args)
            confirmar()
        return self._json(200, {"ok": True})


class ServidorTLS(ThreadingHTTPServer):
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


def main():
    if "--renovar-certificado" in sys.argv:
        crt, _ = tls.gerar(TLS_DIR, renovar=True)
        print(f"Certificado renovado: {crt}\n{tls.nomes_no_certificado(crt)}")
        return
    global HTTPS_ATIVO
    threading.Thread(target=tique, daemon=True).start()
    threading.Thread(target=agendador_backup, daemon=True).start()
    threading.Thread(target=trabalhador_transcricao, daemon=True).start()
    with trava:
        transcricao_retomar()
    par = certificado()
    if par:
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(str(par[0]), str(par[1]))
        try:
            https = ServidorTLS(("0.0.0.0", PORTA_HTTPS), Manipulador, ctx)
        except OSError as e:
            print(f"HTTPS desativado: porta {PORTA_HTTPS} indisponível ({e.strerror}). Use PORTA_HTTPS=outra porta.")
            par = None
    if par:
        HTTPS_ATIVO = True
        threading.Thread(target=https.serve_forever, daemon=True).start()
        ips = [i for i in tls.ips_locais() if i != "127.0.0.1"]
        print(f"HTTPS: https://localhost:{PORTA_HTTPS}" + "".join(f"  https://{i}:{PORTA_HTTPS}" for i in ips[:3]))
        print(f"Instale o certificado da autoridade nos aparelhos: http://<ip>:{PORTA}/certificado")
    servidor = ThreadingHTTPServer(("0.0.0.0", PORTA), Manipulador)
    servidor.daemon_threads = True
    servidor.redireciona = bool(par)
    print(f"Parlatório Legis em http://localhost:{PORTA}  (PIN do presidente: variável PIN_PRESIDENTE)", flush=True)
    if not TROCA_PIN_OBRIGATORIA:
        print("ATENÇÃO: troca obrigatória de PIN desligada (TROCA_PIN=0) — use só em testes.", flush=True)
    print(f"Transcrição dos discursos: {transcritor.descricao()}", flush=True)
    servidor.serve_forever()


if __name__ == "__main__":
    main()
