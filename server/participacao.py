"""Consulta pública dos projetos, artigo por artigo, com devolutiva ao cidadão.

Inspirado no Wikilegis (Câmara dos Deputados) e no Decidim: o cidadão comenta um artigo ou apoia um
comentário já feito (evita repetição), e a autoria ou a presidência responde dizendo se a sugestão foi
acatada. A falta dessa devolutiva é a principal crítica a consultas como a do Sejm polonês.
"""
import hashlib
import json
import re
import secrets
import time

SITUACOES = {"em_analise": "Em análise", "acatada": "Acatada", "parcial": "Acatada em parte",
             "nao_acatada": "Não acatada"}
LIMITE_POR_HORA = 6


def agora() -> int:
    return int(time.time() * 1000)


def artigos(texto: str) -> list[dict]:
    """Divide o texto do projeto em dispositivos: preâmbulo, Art. 1º, Art. 2º... (parágrafos ficam com o artigo)."""
    texto = (texto or "").replace("\r", "").strip()
    if not texto:
        return []
    partes = re.split(r"(?m)^(?=\s*Art\.?\s*\d+)", texto)
    saida = []
    for parte in partes:
        parte = parte.strip()
        if not parte:
            continue
        m = re.match(r"Art\.?\s*(\d+)", parte)
        rotulo = f"Art. {m.group(1)}" if m else "Ementa e preâmbulo"
        chave = f"art{m.group(1)}" if m else "inicio"
        saida.append({"chave": chave, "rotulo": rotulo, "texto": parte})
    return saida


def criar_tabela(db):
    db.execute("create table if not exists participacoes (id text primary key, projeto text, criado integer, json text)")
    db.commit()


def salvar(db, c: dict):
    db.execute("insert or replace into participacoes (id, projeto, criado, json) values (?, ?, ?, ?)",
               (c["id"], c["projeto"], c["criado"], json.dumps(c, ensure_ascii=False)))
    db.commit()


def obter(db, cid: str) -> dict | None:
    linha = db.execute("select json from participacoes where id = ?", (cid,)).fetchone()
    return json.loads(linha[0]) if linha else None


def do_projeto(db, pid: str) -> list[dict]:
    return [json.loads(x[0]) for x in db.execute(
        "select json from participacoes where projeto = ? order by criado", (pid,)).fetchall()]


def _ip_hash(ip: str) -> str:
    return hashlib.sha256(("parlatorio:" + ip).encode()).hexdigest()[:16]


def publico(c: dict) -> dict:
    """Sem e-mail nem IP: só o que aparece na página."""
    return {k: c.get(k) for k in ("id", "artigo", "nome", "bairro", "texto", "criado", "apoios", "resposta", "oculto")} | \
        {"apoios": len(c.get("apoiadores", []))}


def comentar(db, projeto: dict, artigo: str, nome: str, bairro: str, texto: str, email: str, ip: str) -> dict:
    nome, bairro, texto = (str(x or "").strip() for x in (nome, bairro, texto))
    if len(nome) < 3 or len(nome) > 80:
        raise ValueError("Informe seu nome (3 a 80 letras).")
    if len(texto) < 10 or len(texto) > 2000:
        raise ValueError("O comentário deve ter entre 10 e 2000 caracteres.")
    if artigo not in {a["chave"] for a in artigos(projeto.get("texto", ""))} | {"geral"}:
        raise ValueError("Dispositivo inválido.")
    email = str(email or "").strip()[:120]
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("E-mail inválido.")
    h = _ip_hash(ip)
    hora_atras = agora() - 3600_000
    recentes = db.execute("select count(*) from participacoes where criado > ? and json like ?",
                          (hora_atras, f'%"ip": "{h}"%')).fetchone()[0]
    if recentes >= LIMITE_POR_HORA:
        raise PermissionError("Muitos comentários seguidos deste endereço. Tente de novo mais tarde.")
    c = {"id": secrets.token_hex(6), "projeto": projeto["id"], "artigo": artigo, "nome": nome, "bairro": bairro[:60],
         "texto": texto, "email": email, "ip": h, "criado": agora(), "apoiadores": [], "resposta": None, "oculto": False}
    salvar(db, c)
    return c


def apoiar(db, cid: str, ip: str) -> int:
    c = obter(db, cid)
    if not c or c.get("oculto"):
        raise LookupError("Comentário não encontrado.")
    h = _ip_hash(ip)
    if h not in c["apoiadores"]:
        c["apoiadores"].append(h)
        salvar(db, c)
    return len(c["apoiadores"])


def responder(db, cid: str, texto: str, situacao: str, autor: str) -> dict:
    c = obter(db, cid)
    if not c:
        raise LookupError("Comentário não encontrado.")
    if situacao not in SITUACOES:
        raise ValueError("Situação inválida.")
    texto = str(texto or "").strip()
    if len(texto) < 5:
        raise ValueError("Escreva a resposta ao cidadão.")
    c["resposta"] = {"texto": texto[:2000], "situacao": situacao, "autor": autor, "ts": agora()}
    salvar(db, c)
    return c


def ocultar(db, cid: str, oculto: bool) -> dict:
    c = obter(db, cid)
    if not c:
        raise LookupError("Comentário não encontrado.")
    c["oculto"] = bool(oculto)
    salvar(db, c)
    return c
