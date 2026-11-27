"""Protocolo eletrônico, tramitação com comissões e pedidos de informação ao Executivo.

Inspirado no SAPL 3.1 (Interlegis), no SPLegis da Câmara Municipal de São Paulo, no Granicus
Legistar e nas Written Questions do Parlamento do Reino Unido (placar de respostas no prazo).

Só regras e formatação: não conhece o servidor. Trabalha sobre as fichas do banco de projetos
(dict de cada matéria) e sobre a configuração guardada no estado (S["tramitacao"]). Quem chama
lê e salva as fichas; aqui nada é gravado. Erros de regra saem como ErroTramitacao.

Campos acrescentados à ficha da matéria:
  tram       {tipo, etapa, etapaEm, comissoes: [...], resultado, motivo}
  protocolo  {numero, ts, autor, autorPid, hash, base, por, enviado}
  executivo  {enviado, prazoDias, vence, oficio, resposta: {texto, ts, por, anexo}}
  texto, justificativa, autorPid
Cada passo também vai para o "historico" da matéria (evento "tramitacao").
"""
import hashlib
import re
import time

from ata import PDF, CINZA, ESCURO, _rgb

DIA = 86_400_000

ETAPAS = {
    "recebida": "Aguardando protocolo",
    "devolvida": "Devolvida ao autor",
    "protocolada": "Protocolada",
    "lida": "Lida em plenário",
    "em_comissao": "Em comissão",
    "pronta_pauta": "Pronta para a pauta",
    "votada": "Votada",
    "sancao": "Enviada à sanção",
    "arquivada": "Arquivada",
}
# para onde cada etapa pode ir pelo comando "avançar" (a ida para comissão é pela distribuição)
TRANSICOES = {
    "protocolada": {"lida", "pronta_pauta", "arquivada"},
    "lida": {"pronta_pauta", "arquivada"},
    "em_comissao": {"pronta_pauta", "arquivada"},
    "pronta_pauta": {"votada", "arquivada"},
    "votada": {"sancao", "arquivada"},
    "sancao": {"arquivada"},
}
ANTES_DA_VOTACAO = ("protocolada", "lida", "em_comissao", "pronta_pauta")
SIT_COMISSAO = {
    "aguardando_relator": "Aguardando relator",
    "com_relator": "Relator designado",
    "parecer": "Parecer apresentado",
    "vista": "Pedido de vista",
    "concluida": "Parecer aprovado na comissão",
    "parecer_rejeitado": "Parecer rejeitado na comissão",
}
VOTOS_PARECER = {"favoravel": "Favorável", "contrario": "Contrário", "com_emendas": "Favorável com emendas"}
SIT_EXECUTIVO = {"aguardando": "Aguardando resposta", "em_atraso": "Em atraso", "respondido": "Respondido"}

TIPOS_PADRAO = [
    {"sigla": "PL", "nome": "Projeto de Lei", "executivo": False},
    {"sigla": "PLC", "nome": "Projeto de Lei Complementar", "executivo": False},
    {"sigla": "PR", "nome": "Projeto de Resolução", "executivo": False},
    {"sigla": "PDL", "nome": "Projeto de Decreto Legislativo", "executivo": False},
    {"sigla": "PELO", "nome": "Proposta de Emenda à Lei Orgânica", "executivo": False},
    {"sigla": "REQ", "nome": "Requerimento", "executivo": False},
    {"sigla": "IND", "nome": "Indicação", "executivo": True},
    {"sigla": "PI", "nome": "Pedido de Informação", "executivo": True},
    {"sigla": "MOC", "nome": "Moção", "executivo": False},
]
# dias corridos; a Lei Orgânica de cada município define o prazo do Executivo (30 dias é o mais comum)
PRAZOS_PADRAO = {"parecer": 15, "vista": 3, "executivo": 30, "alerta": 5}

LIMITES = {"ementa": 1000, "texto": 100_000, "justificativa": 20_000, "parecer": 50_000, "resposta": 50_000, "motivo": 1000}
MAX_PROPOSTAS_PENDENTES = 30   # por vereador, aguardando protocolo
MAX_COMISSOES = 30
MAX_REUNIOES = 200


class ErroTramitacao(Exception):
    """Erro de regra da tramitação: volta para a tela como mensagem."""


# ------------------------------------------------------------------ configuração
def completar(S: dict):
    """Garante a configuração no estado (bancos novos e antigos)."""
    t = S.setdefault("tramitacao", {})
    t.setdefault("tipos", [dict(x) for x in TIPOS_PADRAO])
    t.setdefault("prazos", dict(PRAZOS_PADRAO))
    for k, v in PRAZOS_PADRAO.items():
        t["prazos"].setdefault(k, v)
    t.setdefault("comissoes", [])
    t.setdefault("reunioes", [])
    t.setdefault("seq", {})   # "PL/2026" -> último número dado (números de protocolo nunca se repetem)


def _texto(v, nome: str, maximo: int, minimo: int = 0) -> str:
    s = str(v if v is not None else "").replace("\r", "").strip()
    if len(s) < minimo:
        raise ErroTramitacao(f"Escreva {nome}." if not s else f"{nome[0].upper() + nome[1:]} curto demais.")
    if len(s) > maximo:
        raise ErroTramitacao(f"{nome[0].upper() + nome[1:]} grande demais (máximo {maximo:,} caracteres).".replace(",", "."))
    return s


def tipo(cfg: dict, sigla) -> dict:
    t = next((x for x in cfg["tipos"] if x["sigla"] == sigla), None)
    if not t:
        raise ErroTramitacao("Tipo de matéria não encontrado.")
    return t


def validar_config(tipos, prazos) -> tuple[list, dict]:
    if not isinstance(tipos, list) or not 1 <= len(tipos) <= 30:
        raise ErroTramitacao("Informe de 1 a 30 tipos de matéria.")
    limpos, vistos = [], set()
    for x in tipos:
        if not isinstance(x, dict):
            raise ErroTramitacao("Tipo de matéria inválido.")
        sigla = str(x.get("sigla") or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{2,6}", sigla) or sigla == "PROP":
            raise ErroTramitacao(f"Sigla inválida: {sigla or '(vazia)'} (2 a 6 letras; PROP é reservada).")
        if sigla in vistos:
            raise ErroTramitacao(f"Sigla repetida: {sigla}.")
        vistos.add(sigla)
        limpos.append({"sigla": sigla, "nome": _texto(x.get("nome"), "o nome do tipo", 80, 3),
                       "executivo": bool(x.get("executivo"))})
    if not isinstance(prazos, dict):
        raise ErroTramitacao("Prazos inválidos.")
    p = {}
    for k, padrao in PRAZOS_PADRAO.items():
        try:
            n = int(prazos.get(k, padrao))
        except (TypeError, ValueError):
            raise ErroTramitacao("Prazo inválido.")
        if not 1 <= n <= 365:
            raise ErroTramitacao("Os prazos vão de 1 a 365 dias.")
        p[k] = n
    return limpos, p


def validar_comissao(cfg: dict, nome, membros, presidente, ids_parls: set, cid=None) -> dict:
    nome = _texto(nome, "o nome da comissão", 120, 3)
    if not isinstance(membros, list) or not membros:
        raise ErroTramitacao("Escolha os membros da comissão.")
    membros = list(dict.fromkeys(str(m) for m in membros))
    if any(m not in ids_parls for m in membros) or len(membros) > 50:
        raise ErroTramitacao("Membro inválido.")
    if presidente and presidente not in membros:
        raise ErroTramitacao("O presidente da comissão precisa ser membro dela.")
    if any(c["nome"].lower() == nome.lower() and c["id"] != cid for c in cfg["comissoes"]):
        raise ErroTramitacao("Já existe uma comissão com esse nome.")
    return {"nome": nome, "membros": membros, "presidente": presidente or None}


def comissao(cfg: dict, cid) -> dict:
    c = next((x for x in cfg["comissoes"] if x["id"] == cid), None)
    if not c:
        raise ErroTramitacao("Comissão não encontrada.")
    return c


# ------------------------------------------------------------------ protocolo
def evento(p: dict, ts: int, etapa: str, descricao: str, por: str):
    """Passo da tramitação no histórico da matéria ("sessao" guarda a descrição, para as telas antigas)."""
    p.setdefault("historico", []).append({"ts": ts, "sessao": descricao, "evento": "tramitacao", "etapa": etapa,
                                          "descricao": descricao, "por": por})


def _mudar_etapa(p: dict, etapa: str, ts: int):
    p["tram"]["etapa"] = etapa
    p["tram"]["etapaEm"] = ts


def dados_proposta(cfg: dict, sigla, ementa, texto, justificativa) -> dict:
    """Valida o que o vereador (ou a secretaria) escreveu para protocolar."""
    t = tipo(cfg, str(sigla or "").upper())
    return {"tram": {"tipo": t["sigla"], "etapa": "recebida", "etapaEm": 0, "comissoes": []},
            "ementa": _texto(ementa, "a ementa", LIMITES["ementa"], 15),
            "texto": _texto(texto, "o texto", LIMITES["texto"]),
            "justificativa": _texto(justificativa, "a justificativa", LIMITES["justificativa"])}


def hash_conteudo(p: dict) -> tuple[str, str]:
    """SHA-256 do texto protocolado (ou da ementa, quando não há texto): qualquer alteração muda o hash."""
    base = "texto" if (p.get("texto") or "").strip() else "ementa"
    return hashlib.sha256(str(p.get(base) or "").strip().encode("utf-8")).hexdigest(), base


def proximo_numero(cfg: dict, sigla: str, ano: int, numeros: list[str]) -> str:
    """Número por tipo e ano. Considera também os já existentes (importados do SAPL, digitados à mão)."""
    padrao = re.compile(rf"^{re.escape(sigla)}\s+(\d+)\s*/\s*{ano}$", re.I)
    maior = max((int(m.group(1)) for n in numeros if (m := padrao.match(str(n or "").strip()))), default=0)
    chave = f"{sigla}/{ano}"
    n = max(maior, int(cfg["seq"].get(chave, 0))) + 1
    cfg["seq"][chave] = n
    return f"{sigla} {n}/{ano}"


def protocolar(p: dict, cfg: dict, sigla, ts: int, por: str, numeros: list[str]):
    """Dá número, data/hora e hash à matéria. Ela passa a tramitar (e pode ir à pauta)."""
    tram = p.get("tram") or {}
    if p.get("protocolo"):
        raise ErroTramitacao("Matéria já protocolada.")
    if p.get("situacao") not in ("proposta", "em_pauta") and tram.get("etapa") not in ("recebida", "devolvida"):
        raise ErroTramitacao("Só propostas aguardando protocolo podem ser protocoladas.")
    t = tipo(cfg, str(sigla or tram.get("tipo") or "").upper())
    numero = proximo_numero(cfg, t["sigla"], time.localtime(ts / 1000).tm_year, numeros)
    h, base = hash_conteudo(p)
    enviado = next((x["ts"] for x in p.get("historico", []) if x.get("evento") in ("proposta", "tramitacao")), None)
    p["protocolo"] = {"numero": numero, "ts": ts, "autor": p.get("autor", ""), "autorPid": p.get("autorPid"),
                      "hash": h, "base": base, "por": por, "enviado": enviado, "anterior": p.get("numero", "")}
    p["numero"] = numero
    p["tram"] = {**tram, "tipo": t["sigla"], "comissoes": tram.get("comissoes") or []}
    p["tram"].pop("motivo", None)
    _mudar_etapa(p, "protocolada", ts)
    if p.get("situacao") == "proposta":
        p["situacao"] = "em_tramitacao"
    evento(p, ts, "protocolada", f"Protocolada como {numero}", por)


def devolver(p: dict, motivo, ts: int, por: str):
    if (p.get("tram") or {}).get("etapa") not in ("recebida",) or p.get("situacao") != "proposta":
        raise ErroTramitacao("Só propostas aguardando protocolo podem ser devolvidas.")
    motivo = _texto(motivo, "o motivo da devolução", LIMITES["motivo"], 5)
    p["tram"]["motivo"] = motivo
    _mudar_etapa(p, "devolvida", ts)
    evento(p, ts, "devolvida", f"Devolvida ao autor: {motivo}", por)


def reenviar(p: dict, cfg: dict, sigla, ementa, texto, justificativa, ts: int, por: str):
    """O autor corrige a proposta devolvida e manda de novo."""
    if (p.get("tram") or {}).get("etapa") != "devolvida":
        raise ErroTramitacao("Só propostas devolvidas podem ser reenviadas.")
    novos = dados_proposta(cfg, sigla, ementa, texto, justificativa)
    p.update(ementa=novos["ementa"], texto=novos["texto"], justificativa=novos["justificativa"])
    p["tram"].update(tipo=novos["tram"]["tipo"])
    p["tram"].pop("motivo", None)
    _mudar_etapa(p, "recebida", ts)
    evento(p, ts, "recebida", "Proposta corrigida e reenviada", por)


def iniciar(p: dict, cfg: dict, sigla, ts: int, por: str):
    """Matéria antiga ou importada do SAPL passa a tramitar aqui (sem novo número: mantém o que tem)."""
    if p.get("tram"):
        raise ErroTramitacao("Esta matéria já tramita pelo LEGIS.")
    if p.get("situacao") == "proposta":
        raise ErroTramitacao("Protocole a proposta primeiro.")
    sigla = str(sigla or "").upper() or str(p.get("numero") or "").split(" ")[0].upper()
    t = next((x for x in cfg["tipos"] if x["sigla"] == sigla), None)
    votada = p.get("situacao") in ("aprovada", "rejeitada")
    p["tram"] = {"tipo": t["sigla"] if t else "", "etapa": "votada" if votada else "protocolada", "etapaEm": ts,
                 "comissoes": [], **({"resultado": p["situacao"]} if votada else {})}
    evento(p, ts, p["tram"]["etapa"], "Tramitação eletrônica iniciada" + (" (importada do SAPL)" if p.get("saplId") else ""), por)


# ------------------------------------------------------------------ etapas
def etapa(p: dict) -> str:
    """Etapa atual. A votação no plenário (presencial ou virtual) já leva a matéria a "votada"."""
    tram = p.get("tram") or {}
    e = tram.get("etapa") or ""
    if e in ANTES_DA_VOTACAO and p.get("situacao") in ("aprovada", "rejeitada"):
        return "votada"
    return e


def resultado(p: dict) -> str | None:
    tram = p.get("tram") or {}
    return tram.get("resultado") or (p.get("situacao") if p.get("situacao") in ("aprovada", "rejeitada") else None)


def _exigir_tramitando(p: dict):
    e = etapa(p)
    if not e or e in ("recebida", "devolvida"):
        raise ErroTramitacao("A matéria ainda não foi protocolada.")
    return e


def avancar(p: dict, destino: str, ts: int, por: str, resultado_votacao=None, obs="", forcar=False):
    e = _exigir_tramitando(p)
    if destino not in ETAPAS:
        raise ErroTramitacao("Etapa inválida.")
    if destino not in TRANSICOES.get(e, set()):
        raise ErroTramitacao(f"Não é possível passar de “{ETAPAS.get(e, e)}” para “{ETAPAS[destino]}”.")
    obs = _texto(obs, "a observação", 1000)
    if e == "em_comissao" and destino == "pronta_pauta" and not forcar:
        pendentes = [c for c in p["tram"]["comissoes"] if c["situacao"] != "concluida"]
        if pendentes:
            raise ErroTramitacao("Ainda há comissão sem parecer aprovado. Marque “dispensar” para seguir mesmo assim.")
    if destino == "votada":
        if resultado_votacao not in ("aprovada", "rejeitada"):
            raise ErroTramitacao("Informe se foi aprovada ou rejeitada.")
        p["tram"]["resultado"] = resultado_votacao
        if p.get("situacao") not in ("aprovada", "rejeitada"):
            p["situacao"] = resultado_votacao
    if destino == "sancao" and resultado(p) != "aprovada":
        raise ErroTramitacao("Só matéria aprovada vai à sanção.")
    if e == "votada" and not p["tram"].get("resultado"):
        p["tram"]["resultado"] = resultado(p)
    if destino == "arquivada" and p.get("situacao") in ("em_tramitacao", "proposta"):
        p["situacao"] = "arquivado"
    _mudar_etapa(p, destino, ts)
    texto = ETAPAS[destino] + (f": {'aprovada' if resultado_votacao == 'aprovada' else 'rejeitada'}" if destino == "votada" else "")
    if forcar and e == "em_comissao":
        texto += " (parecer de comissão dispensado)"
    evento(p, ts, destino, texto + (f" — {obs}" if obs else ""), por)


def _entrada(p: dict, cid: str) -> dict:
    c = next((x for x in (p.get("tram") or {}).get("comissoes", []) if x["comissao"] == cid), None)
    if not c:
        raise ErroTramitacao("A matéria não está nesta comissão.")
    return c


def distribuir(p: dict, cfg: dict, cids, ts: int, por: str):
    e = _exigir_tramitando(p)
    if e not in ("protocolada", "lida", "em_comissao", "pronta_pauta"):
        raise ErroTramitacao("Nesta etapa a matéria não pode ir para comissão.")
    if not isinstance(cids, list) or not cids:
        raise ErroTramitacao("Escolha ao menos uma comissão.")
    ja = {c["comissao"] for c in p["tram"]["comissoes"] if c["situacao"] not in ("concluida", "parecer_rejeitado")}
    novas = []
    for cid in dict.fromkeys(str(x) for x in cids):
        c = comissao(cfg, cid)
        if cid in ja:
            raise ErroTramitacao(f"A matéria já está na {c['nome']}.")
        novas.append(c)
    # uma nova passagem pela mesma comissão substitui a anterior já concluída
    p["tram"]["comissoes"] = [x for x in p["tram"]["comissoes"] if x["comissao"] not in {c["id"] for c in novas}]
    for c in novas:
        p["tram"]["comissoes"].append({"comissao": c["id"], "entrada": ts, "situacao": "aguardando_relator",
                                       "relator": None, "relatorEm": None, "prazoParecer": None, "parecer": None,
                                       "vista": None, "votacao": None})
    _mudar_etapa(p, "em_comissao", ts)
    evento(p, ts, "em_comissao", "Distribuída à " + ", ".join(c["nome"] for c in novas), por)


def designar_relator(p: dict, cfg: dict, cid, relator, nome_relator: str, ts: int, por: str):
    if etapa(p) != "em_comissao":
        raise ErroTramitacao("A matéria não está em comissão.")
    c = comissao(cfg, cid)
    ent = _entrada(p, cid)
    if ent["situacao"] in ("concluida",):
        raise ErroTramitacao("Esta comissão já concluiu o parecer.")
    if relator not in c["membros"]:
        raise ErroTramitacao("O relator precisa ser membro da comissão.")
    ent.update(relator=relator, relatorEm=ts, prazoParecer=ts + cfg["prazos"]["parecer"] * DIA, situacao="com_relator",
               parecer=None, vista=None, votacao=None)
    evento(p, ts, "relator", f"{c['nome']}: relator designado, {nome_relator} (prazo de {cfg['prazos']['parecer']} dias)", por)


def registrar_parecer(p: dict, cfg: dict, cid, voto, texto, ts: int, por: str, pid_autor=None):
    """Parecer do relator. pid_autor: quando quem envia é o próprio parlamentar (precisa ser o relator)."""
    if etapa(p) != "em_comissao":
        raise ErroTramitacao("A matéria não está em comissão.")
    c = comissao(cfg, cid)
    ent = _entrada(p, cid)
    if pid_autor is not None and ent.get("relator") != pid_autor:
        raise ErroTramitacao("Só o relator designado pode enviar o parecer.")
    if ent["situacao"] not in ("com_relator", "parecer"):
        raise ErroTramitacao("A comissão não está aguardando parecer (designe o relator ou devolva a vista).")
    if voto not in VOTOS_PARECER:
        raise ErroTramitacao("Voto do parecer inválido.")
    texto = _texto(texto, "o texto do parecer", LIMITES["parecer"], 20)
    ent["parecer"] = {"voto": voto, "texto": texto, "ts": ts, "por": por,
                      "noPrazo": not ent.get("prazoParecer") or ts <= ent["prazoParecer"]}
    ent["situacao"] = "parecer"
    evento(p, ts, "parecer", f"{c['nome']}: parecer {VOTOS_PARECER[voto].lower()} apresentado pelo relator", por)


def pedir_vista(p: dict, cfg: dict, cid, pid, nome: str, ts: int, por: str):
    c = comissao(cfg, cid)
    ent = _entrada(p, cid)
    if ent["situacao"] != "parecer":
        raise ErroTramitacao("Vista só depois do parecer apresentado e antes da votação na comissão.")
    if pid not in c["membros"]:
        raise ErroTramitacao("Só membro da comissão pede vista.")
    ent["vista"] = {"pid": pid, "ts": ts, "prazo": ts + cfg["prazos"]["vista"] * DIA, "devolvida": None}
    ent["situacao"] = "vista"
    evento(p, ts, "vista", f"{c['nome']}: pedido de vista de {nome} (prazo de {cfg['prazos']['vista']} dias)", por)


def devolver_vista(p: dict, cfg: dict, cid, ts: int, por: str):
    c = comissao(cfg, cid)
    ent = _entrada(p, cid)
    if ent["situacao"] != "vista":
        raise ErroTramitacao("Não há vista aberta nesta comissão.")
    ent["vista"]["devolvida"] = ts
    ent["situacao"] = "parecer"
    evento(p, ts, "vista_devolvida", f"{c['nome']}: vista devolvida", por)


def votar_parecer(p: dict, cfg: dict, reuniao: dict, votos, ts: int, por: str) -> dict:
    """Votação do parecer na reunião da comissão: contagem sim/não de cada membro presente."""
    c = comissao(cfg, reuniao["comissao"])
    ent = _entrada(p, c["id"])
    if ent["situacao"] != "parecer":
        raise ErroTramitacao("O parecer não está pronto para votação (falta parecer ou há vista aberta).")
    if not isinstance(votos, dict) or not votos:
        raise ErroTramitacao("Registre os votos.")
    presentes = set(reuniao["presentes"])
    if len(presentes) < len(c["membros"]) // 2 + 1:
        raise ErroTramitacao("Sem quórum: é preciso maioria dos membros presentes.")
    sim, nao = [], []
    for pid, v in votos.items():
        if pid not in presentes:
            raise ErroTramitacao("Só membros presentes votam.")
        if v == "sim":
            sim.append(pid)
        elif v == "nao":
            nao.append(pid)
        elif v not in ("", None, "abst"):
            raise ErroTramitacao("Voto inválido.")
    if not sim and not nao:
        raise ErroTramitacao("Registre os votos.")
    aprovado = len(sim) > len(nao)
    ent["votacao"] = {"sim": sim, "nao": nao, "ts": ts, "reuniao": reuniao["id"], "aprovado": aprovado}
    ent["situacao"] = "concluida" if aprovado else "parecer_rejeitado"
    evento(p, ts, "parecer_votado", f"{c['nome']}: parecer {'aprovado' if aprovado else 'rejeitado'} ({len(sim)} sim × {len(nao)} não)", por)
    return {"materia": p["id"], "numero": p.get("numero"), "sim": len(sim), "nao": len(nao), "aprovado": aprovado, "ts": ts}


# ------------------------------------------------------------------ reuniões de comissão
def reuniao(cfg: dict, rid) -> dict:
    r = next((x for x in cfg["reunioes"] if x["id"] == rid), None)
    if not r:
        raise ErroTramitacao("Reunião não encontrada.")
    return r


def reuniao_abrir(cfg: dict, cid, rid: str, ts: int) -> dict:
    c = comissao(cfg, cid)
    if any(r["comissao"] == cid and not r["fim"] for r in cfg["reunioes"]):
        raise ErroTramitacao("Esta comissão já tem reunião aberta.")
    r = {"id": rid, "comissao": cid, "nome": c["nome"], "inicio": ts, "fim": None, "presentes": [], "itens": [],
         "presidente": c.get("presidente")}
    cfg["reunioes"].insert(0, r)
    del cfg["reunioes"][MAX_REUNIOES:]
    return r


def reuniao_presenca(cfg: dict, r: dict, pid, presente: bool):
    if r["fim"]:
        raise ErroTramitacao("Reunião encerrada.")
    if pid not in comissao(cfg, r["comissao"])["membros"]:
        raise ErroTramitacao("Não é membro da comissão.")
    if presente and pid not in r["presentes"]:
        r["presentes"].append(pid)
    if not presente and pid in r["presentes"]:
        r["presentes"].remove(pid)


# ------------------------------------------------------------------ Executivo (pedidos de informação, indicações)
def enviar_executivo(p: dict, cfg: dict, prazo_dias, oficio, ts: int, por: str):
    e = _exigir_tramitando(p)
    t = next((x for x in cfg["tipos"] if x["sigla"] == p["tram"].get("tipo")), None)
    if not t or not t.get("executivo"):
        raise ErroTramitacao("Só pedidos de informação e indicações (tipos marcados “ao Executivo”) vão à prefeitura.")
    if e == "arquivada":
        raise ErroTramitacao("Matéria arquivada.")
    if resultado(p) == "rejeitada":
        raise ErroTramitacao("Matéria rejeitada não vai ao Executivo.")
    if p.get("executivo"):
        raise ErroTramitacao("Já enviada ao Executivo.")
    try:
        dias = int(prazo_dias) if prazo_dias not in (None, "") else cfg["prazos"]["executivo"]
    except (TypeError, ValueError):
        raise ErroTramitacao("Prazo inválido.")
    if not 1 <= dias <= 365:
        raise ErroTramitacao("O prazo vai de 1 a 365 dias.")
    p["executivo"] = {"enviado": ts, "prazoDias": dias, "vence": ts + dias * DIA,
                      "oficio": _texto(oficio, "o número do ofício", 120), "resposta": None}
    evento(p, ts, "executivo", f"Enviada ao Executivo{' (' + p['executivo']['oficio'] + ')' if p['executivo']['oficio'] else ''}, prazo de {dias} dias", por)


def responder_executivo(p: dict, texto, recebida: int, ts: int, por: str, anexo: dict | None):
    ex = p.get("executivo")
    if not ex:
        raise ErroTramitacao("A matéria não foi enviada ao Executivo.")
    if ex.get("resposta"):
        raise ErroTramitacao("Resposta já registrada.")
    texto = _texto(texto, "o resumo da resposta", LIMITES["resposta"], 5)
    if recebida < ex["enviado"] - DIA or recebida > ts + 60_000:
        raise ErroTramitacao("Data de recebimento inválida.")
    ex["resposta"] = {"texto": texto, "ts": recebida, "registrada": ts, "por": por, "anexo": anexo,
                      "noPrazo": recebida <= ex["vence"]}
    evento(p, ts, "resposta", "Resposta do Executivo recebida " + ("no prazo" if recebida <= ex["vence"] else "fora do prazo"), por)


def situacao_executivo(p: dict, ts: int) -> str | None:
    ex = p.get("executivo")
    if not ex:
        return None
    if ex.get("resposta"):
        return "respondido"
    return "em_atraso" if ts > ex["vence"] else "aguardando"


def placar(projetos: list[dict], ts: int) -> dict:
    n = {"enviados": 0, "noPrazo": 0, "foraDoPrazo": 0, "aguardando": 0, "emAtraso": 0}
    for p in projetos:
        s = situacao_executivo(p, ts)
        if not s:
            continue
        n["enviados"] += 1
        if s == "respondido":
            n["noPrazo" if p["executivo"]["resposta"]["noPrazo"] else "foraDoPrazo"] += 1
        else:
            n["aguardando" if s == "aguardando" else "emAtraso"] += 1
    vencidos = n["noPrazo"] + n["foraDoPrazo"] + n["emAtraso"]  # os que já deviam ter resposta
    n["pctNoPrazo"] = round(100 * n["noPrazo"] / vencidos) if vencidos else None
    return n


# ------------------------------------------------------------------ alertas de prazo
def alertas(projetos: list[dict], cfg: dict, ts: int, nomes: dict) -> list[dict]:
    """Prazos que vencem em até N dias (cfg alerta) ou já venceram: parecer, vista e resposta do Executivo."""
    limite = ts + cfg["prazos"]["alerta"] * DIA
    comissoes = {c["id"]: c["nome"] for c in cfg["comissoes"]}
    saida = []

    def add(p, tipo_, vence, texto):
        if vence and vence <= limite:
            saida.append({"materia": p["id"], "numero": p.get("numero"), "ementa": p.get("ementa"), "tipo": tipo_,
                          "vence": vence, "atrasado": ts > vence, "dias": (vence - ts) // DIA if vence >= ts else -((ts - vence) // DIA + 1),
                          "texto": texto})

    for p in projetos:
        if etapa(p) == "em_comissao":
            for c in p["tram"]["comissoes"]:
                nome_c = comissoes.get(c["comissao"], "comissão")
                if c["situacao"] == "com_relator":
                    add(p, "parecer", c.get("prazoParecer"), f"Parecer na {nome_c} (relator: {nomes.get(c['relator'], '—')})")
                if c["situacao"] == "vista" and c.get("vista"):
                    add(p, "vista", c["vista"]["prazo"], f"Vista na {nome_c} ({nomes.get(c['vista']['pid'], '—')})")
        if situacao_executivo(p, ts) in ("aguardando", "em_atraso"):
            add(p, "executivo", p["executivo"]["vence"], "Resposta do Executivo")
    return sorted(saida, key=lambda a: a["vence"])


# ------------------------------------------------------------------ visões públicas
def _publico_comissoes(p: dict, cfg: dict, nomes: dict) -> list[dict]:
    comissoes = {c["id"]: c["nome"] for c in cfg["comissoes"]}
    saida = []
    for c in (p.get("tram") or {}).get("comissoes", []):
        v = c.get("votacao")
        saida.append({"comissao": c["comissao"], "nome": comissoes.get(c["comissao"], "(comissão removida)"),
                      "situacao": c["situacao"], "situacaoNome": SIT_COMISSAO.get(c["situacao"], c["situacao"]),
                      "entrada": c["entrada"], "relator": nomes.get(c["relator"]) if c.get("relator") else None,
                      "relatorPid": c.get("relator"), "prazoParecer": c.get("prazoParecer"),
                      "parecer": ({**c["parecer"], "votoNome": VOTOS_PARECER.get(c["parecer"]["voto"])} if c.get("parecer") else None),
                      "vista": ({"nome": nomes.get(c["vista"]["pid"], "—"), "ts": c["vista"]["ts"], "prazo": c["vista"]["prazo"],
                                 "devolvida": c["vista"]["devolvida"], "pid": c["vista"]["pid"]} if c.get("vista") else None),
                      "votacao": ({"sim": [nomes.get(x, "—") for x in v["sim"]], "nao": [nomes.get(x, "—") for x in v["nao"]],
                                   "ts": v["ts"], "aprovado": v["aprovado"]} if v else None)})
    return saida


def resumo(p: dict, cfg: dict, ts: int, nomes: dict) -> dict:
    """Ficha da tramitação de uma matéria (painel e páginas públicas)."""
    e = etapa(p)
    tram = p.get("tram") or {}
    t = next((x for x in cfg["tipos"] if x["sigla"] == tram.get("tipo")), None)
    ex = p.get("executivo")
    return {"id": p["id"], "numero": p.get("numero"), "ementa": p.get("ementa"), "autor": p.get("autor"),
            "autorPid": p.get("autorPid"), "situacao": p.get("situacao"), "tipo": tram.get("tipo"),
            "tipoNome": t["nome"] if t else "", "paraExecutivo": bool(t and t.get("executivo")),
            "etapa": e, "etapaNome": ETAPAS.get(e, "Sem tramitação eletrônica" if not e else e), "etapaEm": tram.get("etapaEm"),
            "resultado": resultado(p), "motivo": tram.get("motivo"),
            "protocolo": p.get("protocolo"), "comissoes": _publico_comissoes(p, cfg, nomes),
            "executivo": ({**ex, "situacao": situacao_executivo(p, ts), "situacaoNome": SIT_EXECUTIVO[situacao_executivo(p, ts)]}
                          if ex else None),
            "saplUrl": p.get("saplUrl"), "textoUrl": p.get("textoUrl"), "criado": p.get("criado")}


def linha_do_tempo(p: dict) -> list[dict]:
    """Histórico completo (tramitação, pautas e votações) em ordem."""
    itens = []
    for h in p.get("historico", []):
        if h.get("evento") == "tramitacao":
            itens.append({"ts": h["ts"], "tipo": h.get("etapa"), "texto": h.get("descricao") or h.get("sessao"), "por": h.get("por")})
        elif h.get("evento") == "votacao":
            c = h.get("contagem")
            itens.append({"ts": h["ts"], "tipo": "votacao", "por": h.get("sessao"),
                          "texto": f"Votação em plenário: {h.get('resultado')}" + (f" ({c['sim']} sim × {c['nao']} não)" if c else "")})
        elif h.get("evento") == "pauta":
            itens.append({"ts": h["ts"], "tipo": "pauta", "texto": f"Na pauta ({h.get('situacao')})", "por": h.get("sessao")})
        elif h.get("evento") == "proposta":
            itens.append({"ts": h["ts"], "tipo": "recebida", "texto": "Proposta enviada pelo autor", "por": p.get("autor")})
        elif h.get("evento") == "destaque":
            itens.append({"ts": h["ts"], "tipo": "destaque", "texto": "Destacada do plenário virtual", "por": h.get("sessao")})
    return sorted(itens, key=lambda x: x["ts"])


# ------------------------------------------------------------------ comprovante em PDF
def _dh(ms) -> str:
    return time.strftime("%d/%m/%Y às %H:%M:%S", time.localtime(ms / 1000)) if ms else "—"


def comprovante_pdf(p: dict, casa: str, cor: str = "", tipo_nome: str = "") -> bytes:
    pr = p["protocolo"]
    pdf = PDF(f"{casa} · comprovante de protocolo eletrônico {pr['numero']} · Parlatório Legis")
    pdf.cor = _rgb(cor)
    topo = pdf.y
    pdf.texto(pdf.m, topo - 16, casa.upper(), 13, True)
    pdf.texto(pdf.m, topo - 34, "COMPROVANTE DE PROTOCOLO ELETRÔNICO", 11.5, True, pdf.cor)
    pdf.y = topo - 48
    pdf.linha(pdf.m, pdf.y, pdf.L - pdf.m, pdf.y, ESCURO, 1)
    pdf.espaco(14)
    pdf.texto(pdf.m, pdf.y - 22, pr["numero"], 26, True)
    pdf.y -= 34
    if tipo_nome:
        pdf.paragrafo(tipo_nome, 11, False, CINZA)
    pdf.espaco(10)
    linhas = [["Número", pr["numero"]], ["Protocolado em", _dh(pr["ts"])], ["Autoria", pr.get("autor") or "—"]]
    if pr.get("enviado"):
        linhas.append(["Enviado pelo autor em", _dh(pr["enviado"])])
    if pr.get("anterior") and pr["anterior"] != pr["numero"]:
        linhas.append(["Identificação anterior", pr["anterior"]])
    linhas.append(["Protocolado por", pr.get("por") or "—"])
    pdf.tabela([("Campo", .3, "e"), ("Valor", .7, "e")], linhas, 9.5)
    pdf.espaco(14)
    pdf.paragrafo("EMENTA", 9, True, CINZA)
    pdf.paragrafo(p.get("ementa") or "—", 10.5)
    pdf.espaco(14)
    pdf.paragrafo(f"HASH SHA-256 DO {'TEXTO' if pr.get('base') == 'texto' else 'CONTEÚDO (EMENTA)'} PROTOCOLADO", 9, True, CINZA)
    pdf.paragrafo(pr["hash"], 9.5, True)
    pdf.espaco(8)
    pdf.paragrafo("O hash identifica de forma única o conteúdo entregue: qualquer mudança no texto, mesmo de uma vírgula, "
                  "gera outro valor. Para conferir, calcule o SHA-256 do texto publicado na página da matéria "
                  f"(/tramitacao#{p['id']}) e compare com o valor acima.", 8.5, False, CINZA)
    return pdf.gerar()
