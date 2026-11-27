"""Opinião do cidadão sobre cada item da pauta e inscrição na Tribuna Livre (inspirado no eComment da Granicus).

  * Opinião por item da pauta: a favor / contra / neutro, com comentário. Nome e e-mail obrigatórios; o e-mail
    nunca é público. Nada aparece (nem conta no placar público) antes da aprovação da presidência.
  * Tribuna Livre: o cidadão pede o uso da palavra (nome, entidade ou bairro, tema, contato). A presidência aprova
    ou recusa e define a ordem; a lista pública mostra só os aprovados, sem o contato.

Como nas perguntas ao vivo, o endereço de origem vira um hash só para limitar abusos.
"""
import hashlib
import json
import re
import secrets
import time

POSICOES = {"favor": "A favor", "contra": "Contra", "neutro": "Neutro"}
SITUACOES = ("pendente", "aprovado", "recusado")
OPINIOES_POR_HORA = 6
TRIBUNA_POR_DIA = 2
EMAIL_RE = re.compile(r"[^@\s<>\"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}")


def agora() -> int:
    return int(time.time() * 1000)


def hash_ip(ip: str) -> str:
    return hashlib.sha256(("legis-tribuna:" + ip).encode()).hexdigest()[:16]


def criar_tabelas(db):
    db.execute("create table if not exists pauta_opinioes (id text primary key, item text, quem text, criado integer, json text)")
    db.execute("create table if not exists tribuna_inscricoes (id text primary key, sessao text, quem text, criado integer, json text)")
    db.commit()


def _curto(v, minimo, maximo, rotulo) -> str:
    t = " ".join(str(v or "").split())
    if not minimo <= len(t) <= maximo:
        raise ValueError(f"{rotulo}: de {minimo} a {maximo} caracteres.")
    return t


# ---------------------------------------------------------------- opinião por item da pauta
def opiniao_publica(o: dict) -> dict:
    return {k: o.get(k) for k in ("id", "item", "nome", "bairro", "posicao", "texto", "criado")}


def opiniao_interna(o: dict) -> dict:
    """Para o painel da presidência (moderação): inclui situação e e-mail, nunca o hash de origem."""
    return opiniao_publica(o) | {"situacao": o["situacao"], "email": o.get("email")}


def opinar(db, item: str, nome, email, bairro, posicao, texto, ip) -> dict:
    nome = _curto(nome, 3, 80, "Nome")
    email = str(email or "").strip().lower()[:254]
    if not EMAIL_RE.fullmatch(email):
        raise ValueError("Informe um e-mail válido (ele não é publicado).")
    if posicao not in POSICOES:
        raise ValueError("Escolha: a favor, contra ou neutro.")
    texto = str(texto or "").strip()
    if not 5 <= len(texto) <= 1500:
        raise ValueError("Comentário: de 5 a 1500 caracteres.")
    h = hash_ip(ip)
    n = db.execute("select count(*) from pauta_opinioes where quem = ? and criado > ?", (h, agora() - 3_600_000)).fetchone()[0]
    if n >= OPINIOES_POR_HORA:
        raise PermissionError("Muitos comentários seguidos deste endereço. Tente mais tarde.")
    o = {"id": secrets.token_hex(5), "item": item, "nome": nome, "email": email, "bairro": " ".join(str(bairro or "").split())[:60],
         "posicao": posicao, "texto": texto, "criado": agora(), "situacao": "pendente"}
    db.execute("insert into pauta_opinioes values (?, ?, ?, ?, ?)", (o["id"], item, h, o["criado"], json.dumps(o, ensure_ascii=False)))
    db.commit()
    return o


def opiniao_obter(db, oid: str) -> dict | None:
    r = db.execute("select json from pauta_opinioes where id = ?", (oid,)).fetchone()
    return json.loads(r[0]) if r else None


def opiniao_moderar(db, oid: str, situacao: str) -> dict:
    o = opiniao_obter(db, oid)
    if not o:
        raise LookupError("Comentário não encontrado.")
    if situacao not in SITUACOES:
        raise ValueError("Situação inválida.")
    o["situacao"] = situacao
    db.execute("update pauta_opinioes set json = ? where id = ?", (json.dumps(o, ensure_ascii=False), oid))
    db.commit()
    return o


def opinioes(db, itens: list[str]) -> list[dict]:
    if not itens:
        return []
    marcas = ",".join("?" * len(itens))
    return [json.loads(r[0]) for r in db.execute(f"select json from pauta_opinioes where item in ({marcas}) order by criado", itens)]


def placar(lista: list[dict], so_aprovadas: bool = True) -> dict:
    c = {k: 0 for k in POSICOES}
    for o in lista:
        if (not so_aprovadas or o["situacao"] == "aprovado") and o["posicao"] in c:
            c[o["posicao"]] += 1
    return c


# ---------------------------------------------------------------- Tribuna Livre
def inscricao_publica(i: dict) -> dict:
    return {k: i.get(k) for k in ("id", "nome", "entidade", "tema", "ordem", "criado")}


def inscricao_interna(i: dict) -> dict:
    return inscricao_publica(i) | {"situacao": i["situacao"], "contato": i.get("contato")}


def inscrever(db, sessao: str, nome, entidade, tema, contato, ip) -> dict:
    nome = _curto(nome, 3, 80, "Nome")
    entidade = _curto(entidade, 2, 100, "Entidade ou bairro")
    tema = _curto(tema, 5, 200, "Tema")
    contato = " ".join(str(contato or "").split())[:120]
    if not (EMAIL_RE.fullmatch(contato) or re.fullmatch(r"[\d\s()+.-]{8,20}", contato)):
        raise ValueError("Contato: informe um e-mail ou telefone (não é publicado).")
    h = hash_ip(ip)
    n = db.execute("select count(*) from tribuna_inscricoes where quem = ? and criado > ?", (h, agora() - 86_400_000)).fetchone()[0]
    if n >= TRIBUNA_POR_DIA:
        raise PermissionError("Limite de inscrições por dia atingido neste endereço.")
    i = {"id": secrets.token_hex(5), "sessao": sessao, "nome": nome, "entidade": entidade, "tema": tema, "contato": contato,
         "criado": agora(), "situacao": "pendente", "ordem": None}
    db.execute("insert into tribuna_inscricoes values (?, ?, ?, ?, ?)", (i["id"], sessao, h, i["criado"], json.dumps(i, ensure_ascii=False)))
    db.commit()
    return i


def _salvar(db, i: dict):
    db.execute("update tribuna_inscricoes set json = ? where id = ?", (json.dumps(i, ensure_ascii=False), i["id"]))


def inscricoes(db, sessao: str) -> list[dict]:
    lista = [json.loads(r[0]) for r in db.execute("select json from tribuna_inscricoes where sessao = ? order by criado", (sessao,))]
    return sorted(lista, key=lambda i: (i["situacao"] != "aprovado", i["ordem"] if i["ordem"] is not None else 10**9, i["criado"]))


def inscricao_moderar(db, iid: str, situacao: str) -> dict:
    r = db.execute("select json from tribuna_inscricoes where id = ?", (iid,)).fetchone()
    if not r:
        raise LookupError("Inscrição não encontrada.")
    if situacao not in SITUACOES:
        raise ValueError("Situação inválida.")
    i = json.loads(r[0])
    if situacao == "aprovado" and i["situacao"] != "aprovado":
        aprovados = [x for x in inscricoes(db, i["sessao"]) if x["situacao"] == "aprovado"]
        i["ordem"] = max((x["ordem"] or 0 for x in aprovados), default=0) + 1
    elif situacao != "aprovado":
        i["ordem"] = None
    i["situacao"] = situacao
    _salvar(db, i)
    _renumerar(db, i["sessao"])
    db.commit()
    return i


def _renumerar(db, sessao: str):
    for k, x in enumerate([x for x in inscricoes(db, sessao) if x["situacao"] == "aprovado"], 1):
        if x["ordem"] != k:
            x["ordem"] = k
            _salvar(db, x)


def inscricao_mover(db, iid: str, d: int) -> list[dict]:
    """Sobe (d=-1) ou desce (d=1) um inscrito aprovado na ordem de uso da palavra."""
    r = db.execute("select json from tribuna_inscricoes where id = ?", (iid,)).fetchone()
    if not r:
        raise LookupError("Inscrição não encontrada.")
    i = json.loads(r[0])
    aprovados = [x for x in inscricoes(db, i["sessao"]) if x["situacao"] == "aprovado"]
    k = next((n for n, x in enumerate(aprovados) if x["id"] == iid), None)
    if k is None:
        raise ValueError("Aprove a inscrição antes de definir a ordem.")
    j = k + (1 if d > 0 else -1)
    if 0 <= j < len(aprovados):
        aprovados[k], aprovados[j] = aprovados[j], aprovados[k]
        for n, x in enumerate(aprovados, 1):
            x["ordem"] = n
            _salvar(db, x)
        db.commit()
    return aprovados
