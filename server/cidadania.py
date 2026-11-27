"""Participação do cidadão, nos moldes do Senado e da Câmara dos Deputados.

  * Enquete por projeto (Enquetes da Câmara): concordo/discordo, totalmente ou na maior parte; um voto por
    endereço e por projeto; resultado público com o aviso de que não tem rigor estatístico.
  * Ideia Legislativa (e-Cidadania do Senado): o cidadão propõe, outros apoiam; ao atingir a meta de apoios
    dentro do prazo, a ideia vai para a análise da Mesa, que responde publicamente.
  * Perguntas ao vivo (Evento Interativo do Senado): durante a sessão ou audiência, o cidadão envia perguntas;
    a presidência aprova, e a pergunta escolhida aparece no telão.

Sem cadastro: o endereço de origem vira um hash (não guardamos IP) e limita repetições e abusos.
"""
import hashlib
import json
import re
import secrets
import time

ENQUETE = {"concordo_total": "Concordo totalmente", "concordo_parte": "Concordo na maior parte",
           "discordo_parte": "Discordo na maior parte", "discordo_total": "Discordo totalmente"}
IDEIA_SITUACOES = {"aberta": "Recebendo apoios", "meta": "Meta atingida: em análise", "virou_projeto": "Virou projeto",
                   "arquivada": "Arquivada", "encerrada": "Prazo encerrado"}
IDEIA_PRAZO_DIAS = 120
IDEIAS_POR_DIA = 3
PERGUNTAS_POR_HORA = 5


def agora() -> int:
    return int(time.time() * 1000)


def hash_ip(ip: str) -> str:
    return hashlib.sha256(("legis-cidadania:" + ip).encode()).hexdigest()[:16]


def criar_tabelas(db):
    db.execute("create table if not exists enquete_votos (projeto text, quem text, opcao text, criado integer, primary key (projeto, quem))")
    db.execute("create table if not exists ideias (id text primary key, criado integer, json text)")
    db.execute("create table if not exists perguntas (id text primary key, criado integer, json text)")
    db.commit()


def _texto(valor, minimo: int, maximo: int, rotulo: str) -> str:
    t = " ".join(str(valor or "").split()) if maximo <= 120 else str(valor or "").strip()
    if len(t) < minimo or len(t) > maximo:
        raise ValueError(f"{rotulo}: de {minimo} a {maximo} caracteres.")
    return t


# ---------------------------------------------------------------- enquete por projeto
def enquete_resultado(db, projeto: str) -> dict:
    cont = {k: 0 for k in ENQUETE}
    for opcao, n in db.execute("select opcao, count(*) from enquete_votos where projeto = ? group by opcao", (projeto,)):
        if opcao in cont:
            cont[opcao] = n
    return {"contagem": cont, "total": sum(cont.values()), "opcoes": ENQUETE}


def enquete_votar(db, projeto: str, opcao: str, ip: str) -> dict:
    if opcao not in ENQUETE:
        raise ValueError("Escolha uma das opções.")
    db.execute("insert or replace into enquete_votos (projeto, quem, opcao, criado) values (?, ?, ?, ?)",
               (projeto, hash_ip(ip), opcao, agora()))   # quem já votou pode mudar a opinião
    db.commit()
    return enquete_resultado(db, projeto)


def enquete_meu_voto(db, projeto: str, ip: str) -> str | None:
    r = db.execute("select opcao from enquete_votos where projeto = ? and quem = ?", (projeto, hash_ip(ip))).fetchone()
    return r[0] if r else None


# ---------------------------------------------------------------- Ideia Legislativa
def meta_ideia(populacao: int | None) -> int:
    """Meta de apoios proporcional ao município (o Senado pede 20 mil no país): 0,3% da população, de 20 a 1000."""
    return max(20, min(1000, round((populacao or 0) * 0.003)))


def _ideia_salvar(db, i: dict):
    db.execute("insert or replace into ideias (id, criado, json) values (?, ?, ?)", (i["id"], i["criado"], json.dumps(i, ensure_ascii=False)))
    db.commit()


def ideia_obter(db, iid: str) -> dict | None:
    r = db.execute("select json from ideias where id = ?", (iid,)).fetchone()
    return json.loads(r[0]) if r else None


def _situacao_atual(i: dict) -> str:
    if i["situacao"] == "aberta" and agora() > i["prazo"]:
        return "encerrada"
    return i["situacao"]


def ideia_publica(i: dict, meta: int) -> dict:
    return {k: i.get(k) for k in ("id", "titulo", "texto", "nome", "bairro", "criado", "prazo", "resposta", "oculta")} | \
        {"apoios": len(i.get("apoiadores", [])), "meta": meta, "situacao": _situacao_atual(i)}


def ideias(db, meta: int, internas: bool = False) -> list[dict]:
    saida = [ideia_publica(json.loads(r[0]), meta) for r in db.execute("select json from ideias order by criado desc")]
    return [i for i in saida if internas or not i["oculta"]]


def ideia_criar(db, titulo, texto, nome, bairro, email, ip) -> dict:
    titulo = _texto(titulo, 8, 120, "Título")
    texto = _texto(texto, 30, 3000, "Descrição")
    nome = _texto(nome, 3, 80, "Nome")
    email = str(email or "").strip()[:120]
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("E-mail inválido.")
    h = hash_ip(ip)
    ontem = agora() - 86_400_000
    recentes = db.execute("select count(*) from ideias where criado > ? and json like ?", (ontem, f'%"autorIp": "{h}"%')).fetchone()[0]
    if recentes >= IDEIAS_POR_DIA:
        raise PermissionError("Limite de ideias por dia atingido neste endereço. Tente amanhã.")
    i = {"id": secrets.token_hex(5), "titulo": titulo, "texto": texto, "nome": nome, "bairro": " ".join(str(bairro or "").split())[:60],
         "email": email, "autorIp": h, "criado": agora(), "prazo": agora() + IDEIA_PRAZO_DIAS * 86_400_000,
         "apoiadores": [h], "situacao": "aberta", "resposta": None, "oculta": False}
    _ideia_salvar(db, i)
    return i


def ideia_apoiar(db, iid: str, ip: str, meta: int) -> dict:
    i = ideia_obter(db, iid)
    if not i or i.get("oculta"):
        raise LookupError("Ideia não encontrada.")
    if _situacao_atual(i) != "aberta":
        raise ValueError("Esta ideia não está recebendo apoios.")
    h = hash_ip(ip)
    if h not in i["apoiadores"]:
        i["apoiadores"].append(h)
        if len(i["apoiadores"]) >= meta:
            i["situacao"] = "meta"   # vai para a análise da Mesa
        _ideia_salvar(db, i)
    return ideia_publica(i, meta)


def ideia_responder(db, iid: str, situacao: str, texto: str, autor: str) -> dict:
    i = ideia_obter(db, iid)
    if not i:
        raise LookupError("Ideia não encontrada.")
    if situacao not in ("meta", "virou_projeto", "arquivada", "aberta"):
        raise ValueError("Situação inválida.")
    texto = str(texto or "").strip()
    if len(texto) < 5:
        raise ValueError("Escreva a resposta ao cidadão.")
    i["situacao"] = situacao
    i["resposta"] = {"texto": texto[:2000], "autor": autor, "ts": agora()}
    _ideia_salvar(db, i)
    return i


def ideia_ocultar(db, iid: str, oculta: bool) -> dict:
    i = ideia_obter(db, iid)
    if not i:
        raise LookupError("Ideia não encontrada.")
    i["oculta"] = bool(oculta)
    _ideia_salvar(db, i)
    return i


# ---------------------------------------------------------------- perguntas ao vivo
def _pergunta_salvar(db, p: dict):
    db.execute("insert or replace into perguntas (id, criado, json) values (?, ?, ?)", (p["id"], p["criado"], json.dumps(p, ensure_ascii=False)))
    db.commit()


def pergunta_obter(db, pid: str) -> dict | None:
    r = db.execute("select json from perguntas where id = ?", (pid,)).fetchone()
    return json.loads(r[0]) if r else None


def pergunta_publica(p: dict) -> dict:
    return {k: p.get(k) for k in ("id", "nome", "bairro", "texto", "criado", "situacao", "sessao")}


def perguntas(db, sessao: str, internas: bool = False) -> list[dict]:
    saida = [json.loads(r[0]) for r in db.execute("select json from perguntas order by criado")]
    saida = [p for p in saida if p.get("sessao") == sessao]
    return [pergunta_publica(p) for p in saida if internas or p["situacao"] in ("aprovada", "respondida")]


def pergunta_enviar(db, sessao: str, nome, bairro, texto, ip) -> dict:
    nome = _texto(nome, 3, 80, "Nome")
    texto = _texto(texto, 10, 500, "Pergunta")
    h = hash_ip(ip)
    hora = agora() - 3_600_000
    recentes = db.execute("select count(*) from perguntas where criado > ? and json like ?", (hora, f'%"quem": "{h}"%')).fetchone()[0]
    if recentes >= PERGUNTAS_POR_HORA:
        raise PermissionError("Muitas perguntas seguidas deste endereço. Aguarde um pouco.")
    p = {"id": secrets.token_hex(5), "sessao": sessao, "nome": nome, "bairro": " ".join(str(bairro or "").split())[:60],
         "texto": texto, "quem": h, "criado": agora(), "situacao": "pendente"}
    _pergunta_salvar(db, p)
    return p


def pergunta_moderar(db, pid: str, situacao: str) -> dict:
    p = pergunta_obter(db, pid)
    if not p:
        raise LookupError("Pergunta não encontrada.")
    if situacao not in ("pendente", "aprovada", "recusada", "respondida"):
        raise ValueError("Situação inválida.")
    p["situacao"] = situacao
    _pergunta_salvar(db, p)
    return p
