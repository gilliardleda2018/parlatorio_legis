"""Links automáticos de citações de normas e identificadores LexML (urn:lex), mais um provedor OAI-PMH mínimo.

Inspirado no Linkoln (CNR italiano), que reconhece citações em textos jurídicos, e no LexML Brasil, que dá a cada
norma um nome uniforme (URN LEX, RFC 9676 de 2025) e coleta os acervos por OAI-PMH.

  * reconhecer(texto): acha "Lei nº 1.234/2020", "art. 5º da Lei nº 8.080/1990", "Constituição Federal, art. 30"…
  * urn(...): monta o nome LexML — urn:lex:br:federal:lei:2021-04-01;14133 (federal) ou
    urn:lex:br;sao.paulo;campinas:municipal:lei:2019-05-05;123 (municipal). Sem a data completa, usamos só o ano
    (urn:lex:br:federal:lei:1993;8666), forma que a especificação LexML aceita quando o dia não é conhecido.
    Dispositivo vai depois de "!": urn:lex:br:federal:constituicao:1988-10-05;1988!art30.
  * linkar(texto): devolve HTML com <a> só nos trechos reconhecidos; todo o resto é escapado antes (sem XSS).
  * normas municipais cadastradas pela presidência (tabela normas_municipais): citação que bate com uma norma
    cadastrada vira link interno; as demais municipais ficam só com a URN (sem link externo).
  * oai(...): Identify, ListMetadataFormats, ListSets, ListIdentifiers, ListRecords e GetRecord (oai_dc).

A esfera de uma "Lei nº X/AAAA" sem qualificativo é deduzida: municipal se estiver cadastrada; federal se o número
cai na faixa que a numeração federal usava naquele ano; senão municipal (é o caso comum numa Câmara Municipal).
"""
import base64
import calendar
import csv
import html
import io
import json
import re
import secrets
import time
import unicodedata
import xml.etree.ElementTree as ET

LEXML_RESOLVEDOR = "https://www.lexml.gov.br/urn/"

UFS = {"AC": "acre", "AL": "alagoas", "AP": "amapa", "AM": "amazonas", "BA": "bahia", "CE": "ceara",
       "DF": "distrito.federal", "ES": "espirito.santo", "GO": "goias", "MA": "maranhao", "MT": "mato.grosso",
       "MS": "mato.grosso.do.sul", "MG": "minas.gerais", "PA": "para", "PB": "paraiba", "PR": "parana",
       "PE": "pernambuco", "PI": "piaui", "RJ": "rio.de.janeiro", "RN": "rio.grande.do.norte",
       "RS": "rio.grande.do.sul", "RO": "rondonia", "RR": "roraima", "SC": "santa.catarina", "SP": "sao.paulo",
       "SE": "sergipe", "TO": "tocantins"}

# tipo interno -> tipo de norma na URN LexML
TIPOS_URN = {"lei": "lei", "lei_complementar": "lei.complementar", "decreto": "decreto", "decreto_lei": "decreto.lei",
             "decreto_legislativo": "decreto.legislativo", "lei_organica": "lei.organica", "resolucao": "resolucao",
             "constituicao": "constituicao", "emenda_constitucional": "emenda.constitucional"}
TIPOS_NOMES = {"lei": "Lei", "lei_complementar": "Lei Complementar", "decreto": "Decreto", "decreto_lei": "Decreto-Lei",
               "decreto_legislativo": "Decreto Legislativo", "lei_organica": "Lei Orgânica", "resolucao": "Resolução",
               "constituicao": "Constituição", "emenda_constitucional": "Emenda Constitucional"}
TIPOS_MUNICIPAIS = ("lei", "lei_complementar", "decreto", "decreto_legislativo", "lei_organica", "resolucao")

MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
         "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}

# Primeiro número de lei ordinária federal de cada ano (aproximado, para separar federal × municipal sem qualificativo)
_FEDERAL_INICIO = {1985: 7300, 1986: 7450, 1987: 7590, 1988: 7650, 1989: 7720, 1990: 7980, 1991: 8150, 1992: 8380,
                   1993: 8600, 1994: 8860, 1995: 8980, 1996: 9250, 1997: 9430, 1998: 9600, 1999: 9780, 2000: 9960,
                   2001: 10170, 2002: 10400, 2003: 10640, 2004: 10830, 2005: 11090, 2006: 11280, 2007: 11440,
                   2008: 11640, 2009: 11900, 2010: 12190, 2011: 12380, 2012: 12590, 2013: 12790, 2014: 12950,
                   2015: 13090, 2016: 13250, 2017: 13420, 2018: 13630, 2019: 13790, 2020: 13980, 2021: 14100,
                   2022: 14290, 2023: 14520, 2024: 14780, 2025: 15060, 2026: 15300, 2027: 15550}


def _slug_lex(t: str) -> str:
    """Nome de localidade no estilo LexML: minúsculas, sem acento, palavras separadas por ponto."""
    t = "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower()
    return ".".join(re.findall(r"[a-z0-9]+", t))


def localidade(uf: str | None, municipio: str | None) -> str | None:
    """br;sao.paulo;campinas — None se a Câmara ainda não informou UF e município."""
    estado = UFS.get(str(uf or "").upper())
    mun = _slug_lex(municipio or "")
    return f"br;{estado};{mun}" if estado and mun else None


def _numero_federal_provavel(numero: str, ano: int | None) -> bool:
    if not ano or not numero.isdigit() or ano not in _FEDERAL_INICIO:
        return False
    n = int(numero)
    return _FEDERAL_INICIO[ano] - 40 <= n <= _FEDERAL_INICIO.get(ano + 1, _FEDERAL_INICIO[ano] + 400) + 40


# ---------------------------------------------------------------- reconhecimento
def _art(g: str) -> str:
    return (rf"(?P<{g}>art(?:igo)?s?\.?\s*(?P<{g}n>\d+)(?:\s*[º°o](?![a-zà-ú]))?(?:-[A-Z])?"
            rf"(?:\s*,?\s*(?:§+\s*\d+[º°o]?|inciso\s+[IVXLC]+|par[áa]grafo\s+[úu]nico|caput|al[íi]nea\s+[\"“]?[a-z][\"”]?))*)")


_MES = r"janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"
_TIPO = (r"(?P<tipo>lei\s+complementar|lei\s+org[âa]nica(?:\s+d[oe]\s+munic[íi]pio)?|decreto[\s-]+lei|"
         r"decreto\s+legislativo|decreto|lei|resolu[çc][ãa]o|emenda\s+constitucional)")
_NORMA = (_TIPO + r"(?:\s+(?P<esfera>federal|municipal|estadual))?"
          r"(?:\s+(?:n\.?\s*[º°o]\.?|n\.|n[úu]mero)?\s*(?P<num>\d{1,3}(?:\.\d{3})+|\d+)(?![\d.,]\d)"
          r"(?:\s*/\s*(?P<ano>\d{4}|\d{2})(?!\d)"
          rf"|\s*,?\s+de\s+(?:(?P<dia>\d{{1,2}})\s*(?:º|°|o)?\s+de\s+(?P<mes>{_MES})\s+de\s+)?(?P<anod>\d{{4}})(?!\d))?)?")
_CONST = (r"(?P<const>constitui[çc][ãa]o(?:\s+(?P<cq>federal|da\s+rep[úu]blica(?:\s+federativa\s+do\s+brasil)?|estadual|"
          r"do\s+estado))?(?:\s+de\s+1988)?|(?-i:CF)(?:/(?:88|1988))?(?![\w/]))")
PADRAO = re.compile(rf"(?<![\w])(?:{_art('a1')}\s*,?\s*(?:d[aoe]s?|desta|dessa|nesta)\s+)?(?:{_NORMA}|{_CONST})"
                    rf"(?:\s*,\s*(?:em\s+seu\s+)?{_art('a2')})?", re.I)


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").lower()


def reconhecer(texto: str) -> list[dict]:
    """Citações encontradas, com offsets (inicio, fim) no texto original."""
    saida = []
    for m in PADRAO.finditer(texto or ""):
        g = m.groupdict()
        artigo = g.get("a1n") or g.get("a2n")
        if g.get("const"):
            cq = _sem_acento(g.get("cq") or "")
            esfera = "estadual" if cq in ("estadual", "do estado") else "federal"
            c = {"tipo": "constituicao", "esfera": esfera, "numero": "1988" if esfera == "federal" else None,
                 "ano": 1988 if esfera == "federal" else None, "data": "1988-10-05" if esfera == "federal" else None}
        else:
            bruto = _sem_acento(re.sub(r"[\s-]+", " ", g["tipo"]))
            tipo = {"lei complementar": "lei_complementar", "decreto lei": "decreto_lei", "decreto legislativo": "decreto_legislativo",
                    "decreto": "decreto", "lei": "lei", "resolucao": "resolucao",
                    "emenda constitucional": "emenda_constitucional"}.get(bruto, "lei_organica" if bruto.startswith("lei organica") else None)
            if not tipo:
                continue
            numero = (g.get("num") or "").replace(".", "") or None
            if not numero and tipo != "lei_organica":
                # sem número não é citação ("a lei municipal prevê…"); a parte do artigo, se houver, não vale sozinha
                continue
            ano, data = None, None
            if g.get("ano"):
                a = int(g["ano"])
                ano = a if a > 99 else (2000 + a if a <= int(time.strftime("%y")) + 1 else 1900 + a)
            elif g.get("anod"):
                ano = int(g["anod"])
                if g.get("dia") and g.get("mes"):
                    mes = MESES.get(_sem_acento(g["mes"]), 0)
                    dia = int(g["dia"])
                    if 1 <= mes <= 12 and 1 <= dia <= 31:
                        data = f"{ano:04d}-{mes:02d}-{dia:02d}"
            esfera = (g.get("esfera") or "").lower() or None
            if tipo in ("decreto_lei", "emenda_constitucional"):
                esfera = esfera or "federal"
            if tipo == "lei_organica":
                esfera = "municipal"
            if tipo == "lei_organica" and numero:   # "Lei Orgânica nº 1" não é usual: trata só como a Lei Orgânica
                numero = None
            c = {"tipo": tipo, "esfera": esfera, "numero": numero, "ano": ano, "data": data}
        c.update(inicio=m.start(), fim=m.end(), texto=m.group(0), artigo=artigo)
        saida.append(c)
    return saida


# ---------------------------------------------------------------- URN LexML
def urn(esfera: str, tipo: str, numero: str | None, ano: int | None, data: str | None, local: str | None = None,
        artigo: str | None = None) -> str | None:
    """Nome LexML da norma; None quando faltam dados (ex.: município não configurado, lei sem ano)."""
    t = TIPOS_URN.get(tipo)
    if not t:
        return None
    if esfera == "federal":
        autoridade = "br:federal"
    elif esfera == "estadual":
        estado = (local or "").split(";")
        if len(estado) < 2:
            return None
        autoridade = f"br;{estado[1]}:estadual"
    else:
        if not local:
            return None
        autoridade = f"{local}:municipal"
    quando = data or (str(ano) if ano else None)
    if not quando:
        return None
    ident = numero or (str(ano) if tipo in ("constituicao", "lei_organica") and ano else None)
    if not ident:
        return None
    u = f"urn:lex:{autoridade}:{t}:{quando};{ident}"
    if artigo and str(artigo).isdigit():
        u += f"!art{int(artigo)}"
    return u


URN_VALIDA = re.compile(r"urn:lex:[a-z0-9.;:\-!]+")


def resolver(c: dict, local: str | None, buscar_norma=None) -> dict:
    """Completa a citação com esfera, URN e link (interno se a norma municipal estiver cadastrada)."""
    c = dict(c)
    norma = None
    if buscar_norma and c["esfera"] in (None, "municipal") and c["tipo"] in TIPOS_MUNICIPAIS:
        norma = buscar_norma(c["tipo"], c.get("numero"), c.get("ano"))
    if not c["esfera"]:
        c["esfera"] = "municipal" if norma else (
            "federal" if c["tipo"] == "lei" and _numero_federal_provavel(c["numero"] or "", c["ano"]) else "municipal")
    if norma:
        c["data"] = c.get("data") or norma.get("data")
        c["ano"] = c.get("ano") or norma.get("ano")
        c["norma"] = {k: norma.get(k) for k in ("id", "tipo", "numero", "ano", "data", "ementa")}
    c["urn"] = urn(c["esfera"], c["tipo"], c.get("numero"), c.get("ano"), c.get("data"), local, c.get("artigo"))
    if c["urn"] and not URN_VALIDA.fullmatch(c["urn"]):
        c["urn"] = None
    if norma:
        c["href"] = f"/normas#n-{norma['id']}"
    elif c["urn"] and c["esfera"] in ("federal", "estadual"):
        c["href"] = LEXML_RESOLVEDOR + c["urn"]
    else:
        c["href"] = None
    return c


def linkar(texto: str, local: str | None = None, buscar_norma=None) -> tuple[str, list[dict]]:
    """HTML seguro: escapa todo o texto e envolve só os trechos reconhecidos (pelos offsets do texto original)."""
    texto = texto or ""
    partes, pos, achadas = [], 0, []
    for c in reconhecer(texto):
        c = resolver(c, local, buscar_norma)
        partes.append(html.escape(texto[pos:c["inicio"]], quote=True))
        trecho = html.escape(texto[c["inicio"]:c["fim"]], quote=True)
        titulo = html.escape(c["urn"] or "Norma citada (sem identificador LexML)", quote=True)
        if c["href"]:
            externo = c["href"].startswith("https://")
            partes.append(f'<a class="citacao" href="{html.escape(c["href"], quote=True)}" title="{titulo}"'
                          + (f' data-urn="{html.escape(c["urn"], quote=True)}"' if c["urn"] else "")
                          + (' target="_blank" rel="noopener noreferrer"' if externo else "") + f">{trecho}</a>")
        else:
            partes.append(f'<span class="citacao" title="{titulo}"'
                          + (f' data-urn="{html.escape(c["urn"], quote=True)}"' if c["urn"] else "") + f">{trecho}</span>")
        pos = c["fim"]
        achadas.append({k: c.get(k) for k in ("texto", "tipo", "esfera", "numero", "ano", "data", "artigo", "urn", "href", "inicio", "fim")})
    partes.append(html.escape(texto[pos:], quote=True))
    return "".join(partes), achadas


# ---------------------------------------------------------------- normas municipais cadastradas
def criar_tabela(db):
    # "normas" já é usada pelo Regimento/Lei Orgânica em texto (regimento.py): esta guarda o catálogo de normas do município
    db.execute("create table if not exists normas_municipais (id text primary key, tipo text, numero text, ano integer, "
               "data text, ementa text, link text, criado integer, atualizado integer)")
    db.execute("create index if not exists normas_municipais_busca on normas_municipais (tipo, numero, ano)")
    db.commit()


def _data_norma(valor) -> tuple[int | None, str | None]:
    v = str(valor or "").strip()
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v):
        return int(m.group(1)), v
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", v):
        return int(m.group(3)), f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    if re.fullmatch(r"\d{4}", v):
        return int(v), None
    raise ValueError("Data inválida: use AAAA-MM-DD, DD/MM/AAAA ou só o ano.")


def _linha_norma(r) -> dict:
    return {"id": r[0], "tipo": r[1], "numero": r[2], "ano": r[3], "data": r[4], "ementa": r[5], "link": r[6],
            "criado": r[7], "atualizado": r[8]}


def norma_salvar(db, tipo, numero, data, ementa="", link="", nid=None) -> dict:
    if tipo not in TIPOS_MUNICIPAIS:
        raise ValueError("Tipo de norma inválido.")
    numero = str(numero or "").replace(".", "").strip()
    if tipo != "lei_organica" and not re.fullmatch(r"\d{1,6}(-[A-Za-z])?", numero):
        raise ValueError("Número inválido.")
    ano, data_iso = _data_norma(data)
    if not 1800 <= ano <= 2100:
        raise ValueError("Ano inválido.")
    if data_iso:
        try:
            time.strptime(data_iso, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Data inválida.")
    link = str(link or "").strip()[:500]
    if link and not re.fullmatch(r"https?://[^\s\"'<>]+", link):
        raise ValueError("Link deve começar com http:// ou https://.")
    ementa = " ".join(str(ementa or "").split())[:1000]
    agora = int(time.time() * 1000)
    if nid:
        r = db.execute("select criado from normas_municipais where id = ?", (nid,)).fetchone()
        if not r:
            raise LookupError("Norma não encontrada.")
        criado = r[0]
    else:
        existe = db.execute("select id, criado from normas_municipais where tipo = ? and numero = ? and ano = ?",
                            (tipo, numero, ano)).fetchone()
        nid, criado = (existe[0], existe[1]) if existe else (secrets.token_hex(5), agora)
    db.execute("insert or replace into normas_municipais values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (nid, tipo, numero, ano, data_iso, ementa, link, criado, agora))
    db.commit()
    return norma_obter(db, nid)


def norma_obter(db, nid: str) -> dict | None:
    r = db.execute("select * from normas_municipais where id = ?", (nid,)).fetchone()
    return _linha_norma(r) if r else None


def norma_remover(db, nid: str):
    db.execute("delete from normas_municipais where id = ?", (nid,))
    db.commit()


def normas_listar(db) -> list[dict]:
    return [_linha_norma(r) for r in db.execute("select * from normas_municipais order by ano desc, cast(numero as integer) desc")]


def norma_buscar(db, tipo: str, numero: str | None, ano: int | None) -> dict | None:
    if tipo == "lei_organica":
        r = db.execute("select * from normas_municipais where tipo = 'lei_organica' order by ano desc limit 1").fetchone()
    elif ano:
        r = db.execute("select * from normas_municipais where tipo = ? and numero = ? and ano = ?", (tipo, numero, ano)).fetchone()
    else:
        r = db.execute("select * from normas_municipais where tipo = ? and numero = ? order by ano desc limit 1", (tipo, numero)).fetchone()
    return _linha_norma(r) if r else None


def _tipo_csv(v: str) -> str:
    t = _sem_acento(re.sub(r"[\s_-]+", " ", v or "")).strip()
    return {"lei": "lei", "lei ordinaria": "lei", "lei complementar": "lei_complementar", "lc": "lei_complementar",
            "decreto": "decreto", "decreto legislativo": "decreto_legislativo", "lei organica": "lei_organica",
            "resolucao": "resolucao"}.get(t, t.replace(" ", "_"))


def importar_csv(db, texto: str, limite: int = 5000) -> dict:
    """CSV simples (vírgula ou ponto e vírgula) com cabeçalho: tipo, numero, data, ementa, link."""
    texto = (texto or "").lstrip("﻿")
    if not texto.strip():
        raise ValueError("CSV vazio.")
    primeira = texto.splitlines()[0]
    sep = ";" if primeira.count(";") >= primeira.count(",") else ","
    leitor = csv.DictReader(io.StringIO(texto), delimiter=sep)
    campos = {_sem_acento(c or "").strip(): c for c in (leitor.fieldnames or [])}
    for obrig in ("tipo", "numero", "data"):
        if obrig not in campos:
            raise ValueError("O cabeçalho precisa de: tipo, numero, data (ementa e link são opcionais).")
    ok, erros = 0, []
    for k, linha in enumerate(leitor, 2):
        if k - 1 > limite:
            erros.append(f"Parou em {limite} linhas.")
            break
        pega = lambda c: (linha.get(campos[c]) or "").strip() if c in campos else ""
        try:
            norma_salvar(db, _tipo_csv(pega("tipo")), pega("numero"), pega("data"), pega("ementa"), pega("link"))
            ok += 1
        except (ValueError, LookupError) as e:
            if len(erros) < 20:
                erros.append(f"Linha {k}: {e}")
    return {"importadas": ok, "erros": erros}


def nome_norma(n: dict) -> str:
    base = TIPOS_NOMES.get(n["tipo"], n["tipo"])
    if n["tipo"] == "lei_organica":
        return f"{base} do Município"
    return f"{base} nº {n['numero']}/{n['ano']}"


# ---------------------------------------------------------------- OAI-PMH 2.0 (oai_dc)
OAI = "http://www.openarchives.org/OAI/2.0/"
OAI_DC = "http://www.openarchives.org/OAI/2.0/oai_dc/"
DC = "http://purl.org/dc/elements/1.1/"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
POR_PAGINA = 50
VERBOS = {"Identify": (set(), set()), "ListMetadataFormats": (set(), {"identifier"}), "ListSets": (set(), {"resumptionToken"}),
          "GetRecord": ({"identifier", "metadataPrefix"}, set()),
          "ListIdentifiers": ({"metadataPrefix"}, {"from", "until", "set", "resumptionToken"}),
          "ListRecords": ({"metadataPrefix"}, {"from", "until", "set", "resumptionToken"})}
CONJUNTOS = {"normas": "Normas municipais", "projetos": "Projetos (proposições) em tramitação ou já votados"}


def _utc(ms) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((ms or 0) / 1000))


def _ler_data(v: str, fim: bool) -> int:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        t = time.strptime(v, "%Y-%m-%d")
        base = int(calendar.timegm(t)) * 1000
        return base + (86_399_999 if fim else 0)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", v):
        return int(calendar.timegm(time.strptime(v, "%Y-%m-%dT%H:%M:%SZ"))) * 1000 + (999 if fim else 0)
    raise ValueError(v)


def _token(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).decode().rstrip("=")


def _ler_token(t: str) -> dict:
    d = json.loads(base64.urlsafe_b64decode(t + "=" * (-len(t) % 4)))
    if not isinstance(d, dict) or not isinstance(d.get("o"), int) or d["o"] < 0:
        raise ValueError
    return d


def oai(params: dict, base_url: str, repositorio: str, repo_id: str, email: str, registros: list[dict]) -> bytes:
    """Responde um pedido OAI-PMH. params: {nome: [valores]} (parse_qs). registros: dicts com
    {id, datestamp (ms), set, dc: {title, creator, subject, description, date, type, identifier[], publisher}}."""
    ET.register_namespace("", OAI)
    ET.register_namespace("oai_dc", OAI_DC)
    ET.register_namespace("dc", DC)
    ET.register_namespace("xsi", XSI)
    raiz = ET.Element(f"{{{OAI}}}OAI-PMH", {f"{{{XSI}}}schemaLocation": f"{OAI} http://www.openarchives.org/OAI/2.0/OAI-PMH.xsd"})
    ET.SubElement(raiz, f"{{{OAI}}}responseDate").text = _utc(time.time() * 1000)
    pedido = ET.SubElement(raiz, f"{{{OAI}}}request")
    pedido.text = base_url

    def erro(codigo, msg):
        ET.SubElement(raiz, f"{{{OAI}}}error", {"code": codigo}).text = msg
        return ET.tostring(raiz, encoding="utf-8", xml_declaration=True)

    verbo = params.get("verb", [None])
    if len(verbo) != 1 or verbo[0] not in VERBOS:
        return erro("badVerb", "Verbo ausente, repetido ou desconhecido.")
    verbo = verbo[0]
    obrig, opc = VERBOS[verbo]
    args = {k: v for k, v in params.items() if k != "verb"}
    if any(len(v) != 1 for v in args.values()) or set(args) - obrig - opc - ({"metadataPrefix"} if "resumptionToken" in args else set()):
        return erro("badArgument", "Argumento repetido ou não permitido.")
    a = {k: v[0] for k, v in args.items()}
    if "resumptionToken" in a and set(a) != {"resumptionToken"}:
        return erro("badArgument", "resumptionToken deve vir sozinho.")
    if "resumptionToken" not in a and not obrig <= set(a):
        return erro("badArgument", "Faltam argumentos obrigatórios: " + ", ".join(sorted(obrig - set(a))))
    pedido.set("verb", verbo)
    for k, v in a.items():
        pedido.set(k, v)
    prefixo_id = f"oai:{repo_id}:"
    por_id = {prefixo_id + r["id"]: r for r in registros}

    if verbo == "Identify":
        ident = ET.SubElement(raiz, f"{{{OAI}}}Identify")
        for tag, valor in (("repositoryName", repositorio), ("baseURL", base_url), ("protocolVersion", "2.0"),
                           ("adminEmail", email), ("earliestDatestamp", _utc(min((r["datestamp"] for r in registros), default=0))),
                           ("deletedRecord", "no"), ("granularity", "YYYY-MM-DDThh:mm:ssZ")):
            ET.SubElement(ident, f"{{{OAI}}}{tag}").text = valor
        return ET.tostring(raiz, encoding="utf-8", xml_declaration=True)
    if verbo == "ListMetadataFormats":
        if "identifier" in a and a["identifier"] not in por_id:
            return erro("idDoesNotExist", "Identificador desconhecido.")
        lista = ET.SubElement(raiz, f"{{{OAI}}}ListMetadataFormats")
        f = ET.SubElement(lista, f"{{{OAI}}}metadataFormat")
        ET.SubElement(f, f"{{{OAI}}}metadataPrefix").text = "oai_dc"
        ET.SubElement(f, f"{{{OAI}}}schema").text = "http://www.openarchives.org/OAI/2.0/oai_dc.xsd"
        ET.SubElement(f, f"{{{OAI}}}metadataNamespace").text = OAI_DC
        return ET.tostring(raiz, encoding="utf-8", xml_declaration=True)
    if verbo == "ListSets":
        if "resumptionToken" in a:
            return erro("badResumptionToken", "Lista de conjuntos não é paginada.")
        lista = ET.SubElement(raiz, f"{{{OAI}}}ListSets")
        for spec, nome in CONJUNTOS.items():
            s = ET.SubElement(lista, f"{{{OAI}}}set")
            ET.SubElement(s, f"{{{OAI}}}setSpec").text = spec
            ET.SubElement(s, f"{{{OAI}}}setName").text = nome
        return ET.tostring(raiz, encoding="utf-8", xml_declaration=True)

    def cabecalho(pai, r):
        h = ET.SubElement(pai, f"{{{OAI}}}header")
        ET.SubElement(h, f"{{{OAI}}}identifier").text = prefixo_id + r["id"]
        ET.SubElement(h, f"{{{OAI}}}datestamp").text = _utc(r["datestamp"])
        ET.SubElement(h, f"{{{OAI}}}setSpec").text = r["set"]

    def registro(pai, r):
        rec = ET.SubElement(pai, f"{{{OAI}}}record")
        cabecalho(rec, r)
        meta = ET.SubElement(rec, f"{{{OAI}}}metadata")
        dc = ET.SubElement(meta, f"{{{OAI_DC}}}dc", {f"{{{XSI}}}schemaLocation": f"{OAI_DC} http://www.openarchives.org/OAI/2.0/oai_dc.xsd"})
        for campo in ("title", "creator", "subject", "description", "publisher", "date", "type", "identifier", "language", "rights"):
            valores = r["dc"].get(campo)
            for v in (valores if isinstance(valores, list) else [valores]):
                if v:
                    ET.SubElement(dc, f"{{{DC}}}{campo}").text = str(v)

    if verbo == "GetRecord":
        if a["metadataPrefix"] != "oai_dc":
            return erro("cannotDisseminateFormat", "Só oai_dc.")
        r = por_id.get(a["identifier"])
        if not r:
            return erro("idDoesNotExist", "Identificador desconhecido.")
        registro(ET.SubElement(raiz, f"{{{OAI}}}GetRecord"), r)
        return ET.tostring(raiz, encoding="utf-8", xml_declaration=True)

    # ListIdentifiers / ListRecords
    if "resumptionToken" in a:
        try:
            filtro = _ler_token(a["resumptionToken"])
        except (ValueError, TypeError, json.JSONDecodeError):
            return erro("badResumptionToken", "resumptionToken inválido.")
    else:
        if a["metadataPrefix"] != "oai_dc":
            return erro("cannotDisseminateFormat", "Só oai_dc.")
        filtro = {"o": 0, "f": a.get("from"), "u": a.get("until"), "s": a.get("set")}
        if filtro["f"] and filtro["u"] and len(filtro["f"]) != len(filtro["u"]):
            return erro("badArgument", "from e until com granularidades diferentes.")
    try:
        ini = _ler_data(filtro["f"], False) if filtro.get("f") else None
        fim = _ler_data(filtro["u"], True) if filtro.get("u") else None
    except ValueError:
        return erro("badArgument", "Data inválida (use AAAA-MM-DD ou AAAA-MM-DDThh:mm:ssZ).")
    if filtro.get("s") and filtro["s"] not in CONJUNTOS:
        return erro("badArgument", "Conjunto desconhecido.")
    selec = sorted((r for r in registros if (ini is None or r["datestamp"] >= ini) and (fim is None or r["datestamp"] <= fim)
                    and (not filtro.get("s") or r["set"] == filtro["s"])), key=lambda r: (r["datestamp"], r["id"]))
    if not selec:
        return erro("noRecordsMatch", "Nenhum registro com esses critérios.")
    o = filtro["o"]
    if o >= len(selec):
        return erro("badResumptionToken", "resumptionToken expirado ou fora da lista.")
    lista = ET.SubElement(raiz, f"{{{OAI}}}{verbo}")
    for r in selec[o:o + POR_PAGINA]:
        (registro if verbo == "ListRecords" else cabecalho)(lista, r)
    if o + POR_PAGINA < len(selec) or o > 0:
        tk = ET.SubElement(lista, f"{{{OAI}}}resumptionToken", {"completeListSize": str(len(selec)), "cursor": str(o)})
        if o + POR_PAGINA < len(selec):
            tk.text = _token({**filtro, "o": o + POR_PAGINA})
    return ET.tostring(raiz, encoding="utf-8", xml_declaration=True)
