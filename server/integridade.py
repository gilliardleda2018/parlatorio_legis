"""Integridade do LEGIS: auditoria à prova de adulteração, selo da ata, recibo do voto e registro do uso de IA.

* Cadeia de hashes na tabela ``eventos``: cada linha nova guarda
  ``hash = sha256(hash_anterior|ts|autor|acao|args)`` (hex). Bancos antigos ganham a coluna ``hash`` na
  primeira partida; as linhas que já existiam ficam sem hash e formam o "início" da cadeia (não podem ser
  conferidas). A partir da primeira linha com hash, qualquer alteração, exclusão no meio ou linha sem hash
  quebra a conferência.
* Selo da sessão: ao arquivar, o último hash da cadeia vai para o registro da sessão e para a ata em PDF.
  Quem guarda a ata pode conferir depois, em /verificar, se o selo bate com uma sessão arquivada e se a
  cadeia até ali continua íntegra (apagar o fim do registro também é percebido: o selo some da cadeia).
* Recibo do voto nominal: HMAC-SHA256 (chave secreta do servidor) sobre votação + parlamentar + voto,
  em 10 caracteres base32. Voto secreto nunca tem recibo com o valor.
* Uso de IA (diretrizes da UIP para IA nos parlamentos, 2024; Portaria 227/2025 da Câmara dos Deputados):
  cada chamada ao modelo fica na tabela ``uso_ia`` (recurso, modelo, tamanho da entrada; nunca o texto de
  perguntas de cidadãos nem IP), com a revisão humana quando houver.
"""
import base64
import contextlib
import hashlib
import hmac
import secrets
import sys
import threading
import time

_trava_cadeia = threading.Lock()  # o "último hash" e a linha nova andam juntos
_contexto = threading.local()      # recurso/conteúdo da chamada de IA em andamento nesta thread

RECURSOS_IA = {  # módulo que chama ia.gerar -> recurso
    "resumo": "rascunho_ata",
    "analise": "analise_previa",
    "regimento": "regimento",
    "conhecimento": "assistente",
}
NOMES_IA = {
    "rascunho_ata": "Rascunho da ata",
    "analise_previa": "Análise prévia de projetos",
    "regimento": "Pergunte ao Regimento",
    "assistente": "Assistente do cidadão e busca nos discursos",
}
REVISAVEIS = {"rascunho_ata", "analise_previa"}  # os outros são respostas imediatas, sempre com aviso

POLITICA_PADRAO = """Política de uso de inteligência artificial

1. Finalidade. A Câmara usa inteligência artificial (IA) só como apoio: rascunho do resumo da ata, análise prévia de proposições, consulta ao Regimento Interno e assistente de dúvidas do cidadão. A IA não vota, não decide, não publica sozinha e não substitui servidores nem parlamentares.

2. Revisão humana. Todo texto gerado por IA que entra em documento oficial (como a ata) é rascunho até ser revisado por uma pessoa identificada da Câmara. A ata e as telas mostram o selo "Rascunho gerado por IA (modelo), revisado por quem, em que data" ou "não revisado".

3. Transparência. Cada uso de IA fica registrado (recurso, modelo e data). As estatísticas deste registro são públicas nesta página. Respostas automáticas ao cidadão indicam que foram geradas por IA e trazem as fontes consultadas.

4. Privacidade e soberania dos dados. Preferimos modelos abertos rodando no servidor da própria Câmara. Quando um serviço externo é usado, isso é informado aqui. Perguntas de cidadãos não são publicadas nem usadas para identificar pessoas; o registro de uso não guarda o texto das perguntas nem o endereço IP.

5. Responsabilidade. Erros de conteúdo gerado por IA são responsabilidade de quem o revisou e publicou. Qualquer pessoa pode apontar um erro pelos canais de contato da Câmara.

6. Referências. Diretrizes para IA nos Parlamentos da União Interparlamentar (UIP, dezembro de 2024) e Portaria nº 227/2025 da Câmara dos Deputados, adaptadas à realidade municipal."""


# ---------------------------------------------------------------- banco
def preparar_banco(db):
    """Migração idempotente: coluna hash em eventos e tabela uso_ia."""
    colunas = {c[1] for c in db.execute("pragma table_info(eventos)").fetchall()}
    if "hash" not in colunas:
        db.execute("alter table eventos add column hash text")
    db.execute("create table if not exists uso_ia (id integer primary key, ts integer, recurso text, modelo text, "
               "entrada_resumo text, ref text, ok integer, revisor text, revisado_em integer)")
    db.commit()


# ---------------------------------------------------------------- cadeia de hashes
def calcular(anterior: str, ts, autor, acao, args) -> str:
    return hashlib.sha256(f"{anterior}|{ts}|{autor}|{acao}|{args}".encode()).hexdigest()


def ultimo_hash(db) -> str:
    linha = db.execute("select hash from eventos where hash is not null order by id desc limit 1").fetchone()
    return linha[0] if linha else ""


def inserir_evento(db, ts: int, autor: str, acao: str, args: str) -> str:
    """Grava uma linha da auditoria já encadeada (o commit fica com quem chamou, como antes)."""
    with _trava_cadeia:
        h = calcular(ultimo_hash(db), ts, autor, acao, args)
        db.execute("insert into eventos (ts, autor, acao, args, hash) values (?, ?, ?, ?, ?)", (ts, autor, acao, args, h))
    return h


def selo(db) -> dict:
    """Selo da sessão que está sendo arquivada: o último elo da cadeia."""
    linha = db.execute("select id, ts, hash from eventos where hash is not null order by id desc limit 1").fetchone()
    return {"hash": linha[2], "evento": linha[0], "ts": linha[1]} if linha else {"hash": "", "evento": 0, "ts": 0}


def conferir_cadeia(linhas: list, selos: list) -> dict:
    """Recalcula a cadeia inteira. ``linhas``: (id, ts, autor, acao, args, hash) em ordem de id.
    ``selos``: (titulo, {"hash", "evento"}) das sessões arquivadas."""
    anterior, inicio, encadeados, quebra, comecou = "", 0, 0, None, False
    validos: dict[int, str] = {}
    for i, ts, autor, acao, args, h in linhas:
        if h is None and not comecou:
            inicio += 1
            continue
        comecou = True
        if h is None or not hmac.compare_digest(calcular(anterior, ts, autor, acao, args), h):
            quebra = {"evento": i, "ts": ts, "motivo": "linha sem hash" if h is None else "hash não confere"}
            break
        anterior = h
        validos[i] = h
        encadeados += 1
    ausentes = [t for t, s in selos if s.get("hash") and validos.get(s.get("evento")) != s["hash"]]
    if ausentes and not quebra:
        quebra = {"evento": None, "ts": None, "motivo": "o selo de uma sessão arquivada não está mais na cadeia "
                                                         "(registro apagado ou alterado)"}
    r = {"ok": quebra is None, "total": len(linhas), "inicio": inicio, "encadeados": encadeados,
         "ultimo_hash": anterior, "primeira_quebra": quebra, "selos": len([s for _, s in selos if s.get("hash")]),
         "selos_ausentes": ausentes}
    return r


# ---------------------------------------------------------------- recibo do voto
def preparar_estado(S: dict) -> bool:
    """Cria a chave secreta dos recibos (fica só no servidor: chaves com _ não vão para as telas). True se criou."""
    if S.get("_chaveRecibo"):
        return False
    S["_chaveRecibo"] = secrets.token_hex(32)
    return True


def recibo(chave: str, votacao_id: str, pid: str, valor: str) -> str:
    d = hmac.new(chave.encode(), f"{votacao_id}|{pid}|{valor}".encode(), hashlib.sha256).digest()
    c = base64.b32encode(d).decode()[:10]
    return c[:5] + "-" + c[5:]


def normalizar_recibo(codigo) -> str:
    c = "".join(x for x in str(codigo or "").upper() if x.isalnum())[:10]
    return c[:5] + "-" + c[5:] if len(c) == 10 else ""


def conferir_recibo(chave: str, votacao: dict | None, codigo) -> bool:
    """Confere se o código corresponde a algum voto nominal registrado na votação (não diz qual)."""
    codigo = normalizar_recibo(codigo)
    if not codigo or not votacao or votacao.get("tipo") != "nominal":
        return False
    achou = False
    for pid, valor in (votacao.get("votos") or {}).items():
        achou |= hmac.compare_digest(recibo(chave, votacao["id"], pid, valor), codigo)
    return achou


# ---------------------------------------------------------------- uso de IA
@contextlib.contextmanager
def contexto_ia(ref: str, recurso: str | None = None):
    """Marca as chamadas de IA desta thread como parte de um conteúdo (ex.: "ata:<sessão>", "projeto:<id>")."""
    antes = (getattr(_contexto, "ref", None), getattr(_contexto, "recurso", None))
    _contexto.ref, _contexto.recurso = ref, recurso
    try:
        yield
    finally:
        _contexto.ref, _contexto.recurso = antes


def instalar_registro_ia(ia_mod, db, trava):
    """Envolve ia.gerar: toda chamada (de resumo, analise, regimento, conhecimento) entra em uso_ia.
    Grava só o tamanho e um hash curto da entrada: nada do texto de perguntas de cidadãos."""
    if getattr(ia_mod.gerar, "_registrado", False):
        return
    original = ia_mod.gerar

    def gerar(sistema, pedido, max_tokens=2000):
        modulo = sys._getframe(1).f_globals.get("__name__", "")
        recurso = getattr(_contexto, "recurso", None) or RECURSOS_IA.get(modulo, modulo or "outro")
        ref = getattr(_contexto, "ref", None)
        ok = False
        try:
            resposta = original(sistema, pedido, max_tokens)
            ok = True
            return resposta
        finally:
            entrada = f"{len(pedido or '')} caracteres · sha256 {hashlib.sha256(str(pedido).encode()).hexdigest()[:12]}"
            try:
                with trava:
                    db.execute("insert into uso_ia (ts, recurso, modelo, entrada_resumo, ref, ok) values (?, ?, ?, ?, ?, ?)",
                               (int(time.time() * 1000), recurso, ia_mod.descricao(), entrada, ref, int(ok)))
                    db.commit()
            except Exception as e:  # o registro nunca derruba o recurso
                print(f"uso_ia: não foi possível registrar ({e})", flush=True)

    gerar._registrado = True
    ia_mod.gerar = gerar


def marcar_revisado(db, ref: str, revisor: str, quando: int):
    db.execute("update uso_ia set revisor = ?, revisado_em = ? where ref = ? and revisado_em is null", (revisor, quando, ref))


def estatisticas_ia(db) -> dict:
    """Números públicos do registro: usos por recurso e % dos conteúdos revisados (sem entradas, sem IP)."""
    linhas = db.execute("select recurso, ref, ts, ok, revisado_em from uso_ia order by id").fetchall()
    por: dict[str, dict] = {}
    for recurso, ref, ts, ok, revisado in linhas:
        e = por.setdefault(recurso, {"recurso": recurso, "nome": NOMES_IA.get(recurso, recurso), "usos": 0, "falhas": 0,
                                     "revisavel": recurso in REVISAVEIS, "_refs": {}, "ultimo": 0})
        e["usos"] += 1
        e["falhas"] += 0 if ok else 1
        e["ultimo"] = max(e["ultimo"], ts or 0)
        if ref:
            e["_refs"][ref] = bool(revisado)  # vale a geração mais recente de cada conteúdo
    saida = []
    for e in por.values():
        refs = e.pop("_refs")
        e["conteudos"] = len(refs)
        e["revisados"] = sum(refs.values())
        e["pctRevisado"] = round(100 * e["revisados"] / len(refs)) if refs and e["revisavel"] else None
        saida.append(e)
    saida.sort(key=lambda e: -e["usos"])
    return {"recursos": saida, "total": len(linhas), "desde": linhas[0][2] if linhas else None}


def _data(ms) -> str:
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(ms / 1000)) if ms else ""


def modelo_do_resumo(res: dict | None) -> str:
    motores = [i.get("motor") for i in (res or {}).get("itens", []) if i.get("motor") not in (None, "", "registros")]
    return motores[0] if motores else ""


def selo_ia(res: dict | None) -> str | None:
    """Selo do rascunho da ata: quem revisou, quando e com qual modelo (None se não houve IA)."""
    modelo = modelo_do_resumo(res)
    if not modelo:
        return None
    rev = (res or {}).get("revisao")
    if rev:
        return f"Rascunho gerado por IA ({modelo}), revisado por {rev.get('por') or 'Presidência'} em {_data(rev.get('em'))}."
    return f"Rascunho gerado por IA ({modelo}), não revisado."


def selo_analise(a: dict | None) -> str | None:
    a = a or {}
    if a.get("motor") in (None, "", "regras") or not a.get("resumo"):
        return None
    if a.get("revisada"):
        return f"Resumo gerado por IA ({a['motor']}), revisado por {a.get('revisor') or 'Mesa Diretora'} em {_data(a.get('revisadaEm'))}."
    return f"Resumo gerado por IA ({a['motor']}), não revisado."


def anotar_resposta(r: dict) -> dict:
    """Respostas imediatas (Regimento, assistente): aviso de que vieram da IA, sem revisão humana."""
    if isinstance(r, dict) and r.get("modo") == "ia" and r.get("resposta"):
        return {**r, "seloIA": f"Resposta gerada por IA ({r.get('ia') or 'modelo'}), sem revisão humana: confira nas fontes."}
    return r


def politica(S: dict) -> tuple[str, bool]:
    texto = str(S.get("_politicaIA") or "").strip()
    return (texto, False) if texto else (POLITICA_PADRAO, True)
