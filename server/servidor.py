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
import secrets
import shutil
import subprocess
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.parse
from urllib.parse import urlparse

from mesa import CFG_PADRAO, PRESETS, Mesa

RAIZ = Path(__file__).resolve().parent.parent
PUBLICO = RAIZ / "public"
BANCO = Path(os.environ.get("PARLATORIO_BANCO", RAIZ / "dados" / "parlatorio.db"))
GRAVACOES = BANCO.parent / "gravacoes"
LIMITE_PARTE = 32 * 1024 * 1024
TIPOS_AUDIO = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a"}
PORTA = int(os.environ.get("PORTA", "8090"))
PIN_PRESIDENTE = os.environ.get("PIN_PRESIDENTE", "1234")
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
        """
    )
    return db


db = abrir_banco()


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
                "autoInscricao": False, "exigePresenca": True, "presencaCelular": True},
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
        "_pins": {p["id"]: "0000" for p in parls},
        "_tokens": {},
        **NOVOS_CAMPOS(),
    }


def NOVOS_CAMPOS() -> dict:
    """Campos acrescentados depois da v2; também completam bancos antigos."""
    return {
        "micsManuais": {},      # pid -> True: microfone de bancada liberado pelo presidente
        "micsCortados": [],     # pids com microfone cortado (tumulto, advertência)
        "congelado": None,      # plenário congelado: {inicio, motivo, oradorRodando, interrRodando}
        "ocorrencias": [],      # cortes, congelamentos e tempo esgotado, para a ata
        "gravacoes": [],        # áudio de cada fala
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
    for p in estado["parls"]:
        p.setdefault("canal", 0)
    return estado


S = carregar()


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
    g = next((x for x in S["gravacoes"] if x["id"] == id), None)
    if not g:
        return
    arquivo = GRAVACOES / f"{g['id']}.{g['ext']}"
    if arquivo.exists():
        arquivo.unlink()
    S["gravacoes"].remove(g)


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
    if not numero.strip():
        raise Erro("Informe o número da matéria.")
    if id:
        m = materia(id)
        m.update(numero=numero.strip(), ementa=ementa.strip(), autor=autor.strip())
    else:
        S["pauta"].append({"id": uid(), "numero": numero.strip(), "ementa": ementa.strip(),
                           "autor": autor.strip(), "situacao": "pendente"})


@acao("presidente")
def materiaRemover(ator, id):
    if S["votacao"] and S["votacao"]["materia"] == id and S["votacao"]["aberta"]:
        raise Erro("Há votação aberta para esta matéria.")
    S["pauta"] = [m for m in S["pauta"] if m["id"] != id]
    if S["materiaAtual"] == id:
        S["materiaAtual"] = None


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
def parlSalvar(ator, nome, partido, lider=False, pin=None, id=None, canal=0):
    if not nome.strip():
        raise Erro("Informe o nome.")
    if pin is not None and pin != "" and not (str(pin).isdigit() and 4 <= len(str(pin)) <= 8):
        raise Erro("O PIN deve ter de 4 a 8 dígitos.")
    canal = int(canal or 0)
    if not 0 <= canal <= 256:
        raise Erro("Canal da mesa inválido.")
    if id:
        p = parl(id)
        p.update(nome=nome.strip(), partido=partido.strip().upper(), lider=bool(lider), canal=canal)
    else:
        p = {"id": uid(), "nome": nome.strip(), "partido": partido.strip().upper(), "lider": bool(lider), "fotoV": 0,
             "canal": canal}
        S["parls"].append(p)
        S["_pins"][p["id"]] = "0000"
    if pin:
        S["_pins"][p["id"]] = str(pin)
        S["_tokens"] = {t: a for t, a in S["_tokens"].items() if a.get("pid") != p["id"]}
    S["partidos"].setdefault(p["partido"], "#5b6b86")


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
    if alvo != "logo":
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
    else:
        p["fotoV"] = v


@acao("presidente")
def sessaoZerar(ator, manterPauta=True):
    S.update(filas={}, orador=None, interr=None, pedidos=[], hist=[], presenca={}, votacao=None, votacoes=[],
             micsManuais={}, micsCortados=[], congelado=None, ocorrencias=[])
    if not manterPauta:
        S["pauta"] = []
    else:
        for m in S["pauta"]:
            m["situacao"] = "pendente"
    S["materiaAtual"] = None


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
            for g in S["gravacoes"]:
                if not g["fim"] and agora() - g["ultimo"] > 60000:
                    g["fim"], g["interrompida"] = g["ultimo"], True  # navegador que gravava caiu
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
    g = next((x for x in S["gravacoes"] if x["id"] == gid), None)
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
            g["bytes"] = arquivo.stat().st_size
            g["duracao"] = True
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
    g = {"id": secrets.token_hex(8), "pid": pid, "nome": p["nome"], "partido": p["partido"], "faseNome": f["nome"],
         "materia": S["materiaAtual"], "inicio": int(inicio or agora()), "criado": agora(), "fim": None,
         "ultimo": agora(), "bytes": 0, "mime": mime_base, "ext": TIPOS_AUDIO[mime_base]}
    GRAVACOES.mkdir(parents=True, exist_ok=True)
    S["gravacoes"].insert(0, g)
    return g


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
        return S["_tokens"].get(token) if token else None

    # ----- GET
    def do_GET(self):
        caminho = urlparse(self.path).path
        if caminho == "/eventos":
            return self._eventos()
        if caminho.startswith("/imagem/"):
            return self._imagem(caminho.rsplit("/", 1)[1])
        if caminho.startswith("/gravacao/"):
            return self._audio(caminho.rsplit("/", 1)[1])
        if caminho == "/api/eu":
            with trava:
                a = self._ator()
            return self._json(200, {"ator": a})
        return self._estatico(caminho)

    def _estatico(self, caminho):
        if caminho in ("", "/"):
            caminho = "/index.html"
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
            g = next((x for x in S["gravacoes"] if x["id"] == gid), None)
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
                g["bytes"] += len(dados)
                g["ultimo"] = agora()
                transmitir(pacote())
            return self._json(200, {"ok": True})
        if passo == "fim":
            self._ler()
            with trava:
                g["fim"] = agora()
                g["ultimo"] = g["fim"]
                registrar("presidente", "gravacao", {"id": g["id"], "pid": g["pid"], "bytes": g["bytes"]})
                confirmar()
            threading.Thread(target=gravacao_finalizar_arquivo, args=(g,), daemon=True).start()
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
            return self._json(404, {"erro": "Não encontrado."})
        except Erro as e:
            return self._json(400, {"erro": str(e)})

    def _entrar(self, corpo):
        papel, pin = corpo.get("papel"), str(corpo.get("pin") or "")
        with trava:
            if papel == "presidente" and secrets.compare_digest(pin, PIN_PRESIDENTE):
                ator = {"papel": "presidente", "pid": None}
            elif papel == "parlamentar" and corpo.get("pid") in S["_pins"] and \
                    secrets.compare_digest(pin, S["_pins"][corpo["pid"]]):
                ator = {"papel": "parlamentar", "pid": corpo["pid"]}
            else:
                ator = None
            if ator:
                token = secrets.token_urlsafe(24)
                S["_tokens"][token] = ator
                registrar(ator["pid"] or "presidente", "entrar", {})
                salvar()
                return self._json(200, {"token": token, "ator": ator})
        time.sleep(0.6)
        return self._json(403, {"erro": "PIN incorreto."})

    def _acao(self, corpo):
        nome, args = corpo.get("nome"), corpo.get("args") or {}
        with trava:
            ator = self._ator()
            if not ator:
                return self._json(401, {"erro": "Sessão expirada. Entre novamente."})
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


def main():
    threading.Thread(target=tique, daemon=True).start()
    servidor = ThreadingHTTPServer(("0.0.0.0", PORTA), Manipulador)
    servidor.daemon_threads = True
    print(f"Parlatório Legis em http://localhost:{PORTA}  (PIN do presidente: variável PIN_PRESIDENTE)")
    servidor.serve_forever()


if __name__ == "__main__":
    main()
