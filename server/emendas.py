"""Emendas estruturadas, comparativo (redline) e redação final, sem bibliotecas externas.

Inspirado no AT4AM (Parlamento Europeu), no LEOS (Comissão Europeia), no LexComp (Senado Federal) e na
compilação de textos do SAPL 3.1:

* o texto do projeto vira uma lista de dispositivos com identificador estável (art1, art1_par2,
  art1_par2_inc3, art1_inc1_alia, art3_parunico...), cada um com rótulo e texto;
* a emenda aponta para um dispositivo e tem tipo: supressiva, substitutiva, modificativa, aditiva
  (inserir após o dispositivo) ou aglutinativa (substitutiva que funde outras emendas);
* o comparativo usa difflib (palavra a palavra), por dispositivo e no texto todo;
* a redação final aplica as emendas aprovadas na ordem de apresentação e renumera os dispositivos
  (artigos em sequência, parágrafo único quando sobra um só, incisos romanos, alíneas a, b, c...).

Remissões dentro do texto ("conforme o art. 3º") não são atualizadas pela renumeração: o comparativo
mostra o que foi renumerado para a revisão da redação final.
"""
import copy
import difflib
import html
import json
import re
import secrets
import time

TIPOS = {"supressiva": "Supressiva", "substitutiva": "Substitutiva", "modificativa": "Modificativa",
         "aditiva": "Aditiva", "aglutinativa": "Aglutinativa"}
SITUACOES = {"apresentada": "Apresentada", "aprovada": "Aprovada", "rejeitada": "Rejeitada",
             "prejudicada": "Prejudicada", "retirada": "Retirada"}
NIVEL = {"preambulo": 0, "agrupador": 0, "artigo": 1, "paragrafo": 2, "inciso": 3, "alinea": 4, "item": 5}
NOME_TIPO = {"artigo": "art.", "paragrafo": "§", "inciso": "inciso", "alinea": "alínea", "item": "item"}
LIMITE_TEXTO = 20_000
LIMITE_JUSTIFICATIVA = 5_000
MAX_POR_PROJETO = 300


def agora() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------- texto articulado
_ORD = r"\s*(?:º|°|ª|o(?![a-zà-ú])|\.)?"
_SUF = r"\s*(?:-([A-Z])(?![a-zà-ú]))?"
_SEP = r"\s*[.\-–—:]?\s*"
R_ART = re.compile(r"^Art(?:igo)?\.?\s*(\d{1,4})" + _ORD + _SUF + _SEP + r"(.*)$", re.S)
R_PAR = re.compile(r"^§\s*(\d{1,3})" + _ORD + _SUF + _SEP + r"(.*)$", re.S)
R_UNICO = re.compile(r"^Par[áa]grafo\s+[úu]nico" + _SEP + r"(.*)$", re.S | re.I)
R_INC = re.compile(r"^([IVXLCDM]{1,8})" + _SUF + r"\s*[-–—.)]\s*(.*)$", re.S)
R_ALI = re.compile(r"^([a-z])\s*\)\s*(.*)$", re.S)
R_ITEM = re.compile(r"^(\d{1,3})\s*[.)]\s+(.*)$", re.S)
R_GRUPO = re.compile(r"^(T[ÍI]TULO|CAP[ÍI]TULO|SE[ÇC][ÃA]O|SUBSE[ÇC][ÃA]O|LIVRO|Se[çc][ãa]o|Subse[çc][ãa]o)\s+"
                     r"([IVXLCDM]+|[ÚU]NIC[OA]|[ÚU]nic[oa])\b(.*)$", re.S)
_ROMANOS = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
            (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def romano(n: int) -> str:
    saida = ""
    for v, s in _ROMANOS:
        while n >= v:
            saida += s
            n -= v
    return saida


def de_romano(s: str) -> int | None:
    valores = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for i, c in enumerate(s):
        v = valores[c]
        total += -v if i + 1 < len(s) and valores[s[i + 1]] > v else v
    return total if total and romano(total) == s else None


def _ordinal(n: int) -> str:
    return f"{n}º" if n < 10 else f"{n}."


def prefixo(d: dict) -> str:
    """Rótulo como aparece no início da linha: "Art. 1º", "Art. 10.", "§ 2º", "Parágrafo único.", "III –", "a)"."""
    t, n, suf = d["tipo"], d.get("num"), d.get("suf")

    def numero(n):
        if suf:
            return f"{n}{'º' if n < 10 else ''}-{suf}."
        return _ordinal(n)

    if t == "artigo":
        return f"Art. {numero(n)}"
    if t == "paragrafo":
        return "Parágrafo único." if n == 0 else f"§ {numero(n)}"
    if t == "inciso":
        return f"{romano(n)}{'-' + suf if suf else ''} –"
    if t == "alinea":
        return f"{n})"
    if t == "item":
        return f"{n}."
    return ""


def rotulo(d: dict) -> str:
    """Rótulo curto: "Art. 1º", "§ 2º", "Parágrafo único", "inciso III", "alínea a"."""
    t, n = d["tipo"], d.get("num")
    s = f"-{d['suf']}" if d.get("suf") else ""
    if t == "artigo":
        return f"Art. {n}{'º' if n < 10 else ''}{s}"
    if t == "paragrafo":
        return "Parágrafo único" if n == 0 else f"§ {n}{'º' if n < 10 else ''}{s}"
    if t == "inciso":
        return f"inciso {romano(n)}{s}"
    if t == "alinea":
        return f"alínea {n}"
    if t == "item":
        return f"item {n}"
    return "Ementa e preâmbulo" if t == "preambulo" else (d.get("texto") or "").split("\n")[0][:60]


def referencia(d: dict, por_id: dict) -> str:
    """Referência completa: "art. 2º, § 1º, inciso III, alínea a"."""
    partes, atual = [], d
    while atual:
        partes.append(rotulo(atual))
        atual = por_id.get(atual.get("pai"))
    partes.reverse()
    if partes and partes[0].startswith("Art."):
        partes[0] = "art." + partes[0][4:]
    return ", ".join(partes)


def _classificar(linha: str):
    """(tipo, num, suf, texto) ou None se a linha não abre dispositivo."""
    if m := R_ART.match(linha):
        return "artigo", int(m.group(1)), m.group(2), m.group(3)
    if m := R_UNICO.match(linha):
        return "paragrafo", 0, None, m.group(1)
    if m := R_PAR.match(linha):
        return "paragrafo", int(m.group(1)), m.group(2), m.group(3)
    if (m := R_INC.match(linha)) and de_romano(m.group(1)):
        return "inciso", de_romano(m.group(1)), m.group(2), m.group(3)
    if m := R_ALI.match(linha):
        return "alinea", m.group(1), None, m.group(2)
    if m := R_ITEM.match(linha):
        return "item", int(m.group(1)), None, m.group(2)
    if R_GRUPO.match(linha):
        return "agrupador", None, None, linha
    return None


def _id_de(tipo: str, num, suf, pai: str | None, texto: str) -> str:
    s = (suf or "").lower()
    base = {"artigo": f"art{num}{s}", "paragrafo": "parunico" if num == 0 else f"par{num}{s}",
            "inciso": f"inc{num}{s}", "alinea": f"ali{num}", "item": f"item{num}"}.get(tipo)
    if tipo == "agrupador":
        return "grp_" + (re.sub(r"[^a-z0-9]+", "", texto.split("\n")[0].lower()[:30]) or "x")
    if tipo == "preambulo":
        return "preambulo"
    return f"{pai}_{base}" if pai else base


def dispositivos(texto: str) -> list[dict]:
    """Divide o texto em dispositivos (pré-ordem): [{id, tipo, num, suf, texto, pai, origem}]."""
    texto = (texto or "").replace("\r", "").strip()
    saida: list[dict] = []
    vistos: set[str] = set()
    ultimo = {"artigo": None, "paragrafo": None, "inciso": None, "alinea": None}
    for bruta in texto.split("\n"):
        linha = bruta.strip()
        if not linha:
            continue
        c = _classificar(linha)
        # item só existe dentro de alínea; fora disso é continuação do texto
        if c and c[0] == "item" and not ultimo["alinea"]:
            c = None
        if c and c[0] == "alinea" and not ultimo["inciso"] and saida and saida[-1]["tipo"] in ("preambulo", "agrupador"):
            c = None
        if not c:
            if saida:
                saida[-1]["texto"] = (saida[-1]["texto"] + "\n" + linha).strip()
            else:
                saida.append({"id": "preambulo", "tipo": "preambulo", "num": None, "suf": None, "texto": linha, "pai": None})
                vistos.add("preambulo")
            continue
        tipo, num, suf, resto = c
        if tipo == "agrupador":
            pai = None
            ultimo = {k: None for k in ultimo}
        elif tipo == "artigo":
            pai = None
            ultimo.update(artigo=None, paragrafo=None, inciso=None, alinea=None)
        elif tipo == "paragrafo":
            pai = ultimo["artigo"]
            ultimo.update(paragrafo=None, inciso=None, alinea=None)
        elif tipo == "inciso":
            pai = ultimo["paragrafo"] or ultimo["artigo"]
            ultimo.update(inciso=None, alinea=None)
        elif tipo == "alinea":
            pai = ultimo["inciso"]
            ultimo.update(alinea=None)
        else:
            pai = ultimo["alinea"]
        did = _id_de(tipo, num, suf, pai, resto)
        base, k = did, 2
        while did in vistos:
            did = f"{base}_{k}"
            k += 1
        vistos.add(did)
        d = {"id": did, "tipo": tipo, "num": num, "suf": suf, "texto": resto.strip(), "pai": pai}
        saida.append(d)
        if tipo in ultimo:
            ultimo[tipo] = did
    for d in saida:
        d["origem"] = d["id"]
    return saida


def linha(d: dict) -> str:
    p = prefixo(d)
    return f"{p} {d['texto']}".strip() if p else d["texto"]


def montar(lista: list[dict]) -> str:
    return "\n".join(linha(d) for d in lista)


def com_referencias(lista: list[dict]) -> list[dict]:
    por_id = {d["id"]: d for d in lista}
    return [{**d, "rotulo": rotulo(d), "ref": referencia(d, por_id), "linha": linha(d), "nivel": NIVEL[d["tipo"]]}
            for d in lista]


# ---------------------------------------------------------------- aplicação das emendas
def _desce_de(lista_por_id: dict, d: dict, ancestral: str) -> bool:
    p = d.get("pai")
    while p:
        if p == ancestral:
            return True
        p = (lista_por_id.get(p) or {}).get("pai")
    return False


def _fim_sub(lista: list[dict], i: int) -> int:
    por_id = {d["id"]: d for d in lista}
    j = i + 1
    while j < len(lista) and _desce_de(por_id, lista[j], lista[i]["id"]):
        j += 1
    return j


def _indice(lista: list[dict], did: str) -> int:
    for i, d in enumerate(lista):
        if d["id"] == did:
            return i
    raise ValueError("o dispositivo alvo não existe mais no texto (suprimido ou substituído por outra emenda).")


def trecho(texto: str, tipo_padrao: str) -> list[dict]:
    """Dispositivos do texto novo da emenda. Sem rótulo no começo, vira um dispositivo do tipo padrão."""
    novos = dispositivos(texto)
    if not novos:
        raise ValueError("escreva o texto novo.")
    if novos[0]["tipo"] in ("preambulo",) and tipo_padrao not in ("preambulo", "agrupador"):
        primeiro = novos[0]
        primeiro.update(tipo=tipo_padrao, num=1 if tipo_padrao != "alinea" else "a", id="novo", origem="novo")
        for d in novos[1:]:
            if d["pai"] is None and NIVEL[d["tipo"]] > NIVEL[tipo_padrao]:
                d["pai"] = "novo"
    return novos


def _sem_prefixo(texto: str, tipo: str) -> str:
    texto = str(texto or "").strip()
    c = _classificar(texto.split("\n")[0])
    if c and c[0] == tipo:
        resto = c[3]
        return (resto + ("\n" + texto.split("\n", 1)[1] if "\n" in texto else "")).strip()
    return texto


def _renomear(novos: list[dict], prefixo_id: str, existentes: set, reaproveitar: dict | None = None) -> list[dict]:
    """Ids únicos para os dispositivos novos (e{n}_...), mantendo o id do dispositivo substituído quando há par."""
    mapa = {}
    for d in novos:
        novo_id = (reaproveitar or {}).get(d["id"]) or f"{prefixo_id}_{d['id']}"
        base, k = novo_id, 2
        while novo_id in existentes:
            novo_id = f"{base}_{k}"
            k += 1
        existentes.add(novo_id)
        mapa[d["id"]] = novo_id
    for d in novos:
        d["id"] = mapa[d["id"]]
        d["pai"] = mapa.get(d["pai"], d["pai"]) if d.get("pai") else None
    return novos


def aplicar(lista: list[dict], e: dict) -> list[dict]:
    """Aplica uma emenda e devolve a nova lista (a original não muda). ValueError se não der."""
    lista = copy.deepcopy(lista)
    tipo, alvo, n = e["tipo"], e.get("alvo") or "", e.get("numero", 0)
    existentes = {d["id"] for d in lista}
    if tipo == "supressiva":
        i = _indice(lista, alvo)
        if lista[i]["tipo"] == "preambulo":
            raise ValueError("a ementa e o preâmbulo não podem ser suprimidos.")
        del lista[i:_fim_sub(lista, i)]
        return lista
    if tipo == "modificativa":
        i = _indice(lista, alvo)
        novo = _sem_prefixo(e.get("texto"), lista[i]["tipo"])
        if not novo:
            raise ValueError("escreva o novo texto do dispositivo.")
        extras = dispositivos(novo)
        if len(extras) > 1 and lista[i]["tipo"] not in ("preambulo", "agrupador"):
            raise ValueError("a emenda modificativa muda só o texto do dispositivo; para acrescentar use aditiva ou substitutiva.")
        lista[i]["texto"] = novo
        lista[i]["emenda"] = n
        return lista
    if tipo in ("substitutiva", "aglutinativa"):
        if not alvo:  # substitutivo: o texto inteiro
            novos = dispositivos(e.get("texto"))
            if not novos:
                raise ValueError("escreva o texto do substitutivo.")
            originais = {d["origem"] for d in lista}
            for d in novos:
                d["origem"] = d["id"] if d["id"] in originais else None
                d["emenda"] = n
            return novos
        i = _indice(lista, alvo)
        j = _fim_sub(lista, i)
        antigos = lista[i:j]
        novos = trecho(e.get("texto"), antigos[0]["tipo"])
        pares = {}
        for k, d in enumerate(novos):
            if k < len(antigos) and antigos[k]["tipo"] == d["tipo"]:
                pares[d["id"]] = antigos[k]["id"]
        origens = {a["id"]: a["origem"] for a in antigos}
        for d in antigos:
            existentes.discard(d["id"])
        _renomear(novos, f"e{n}", existentes, pares)
        for d in novos:
            d["origem"] = origens.get(d["id"])
            d["emenda"] = n
            if d["pai"] is None:
                d["pai"] = antigos[0]["pai"]
        lista[i:j] = novos
        return lista
    if tipo == "aditiva":
        if not alvo:
            novos = trecho(e.get("texto"), "artigo")
            topo = [d for d in novos if d["pai"] is None]
            if any(d["tipo"] != "artigo" for d in topo):
                raise ValueError("no fim do texto só se acrescentam artigos; escolha o dispositivo de referência.")
            pos, pai = len(lista), None
        else:
            i = _indice(lista, alvo)
            a = lista[i]
            novos = trecho(e.get("texto"), a["tipo"] if a["tipo"] not in ("preambulo", "agrupador") else "artigo")
            nv = NIVEL[next(d for d in novos if d["pai"] is None)["tipo"]]
            por_id = {d["id"]: d for d in lista}
            na = NIVEL[a["tipo"]]
            if a["tipo"] in ("preambulo", "agrupador"):
                if nv != 1:
                    raise ValueError("depois da ementa ou de um título só se acrescentam artigos.")
                pos, pai = i + 1, None
            elif nv == na:
                pos, pai = _fim_sub(lista, i), a["pai"]
            else:
                ref = a
                if nv < na:  # sobe até o ancestral do mesmo nível (ou o primeiro acima dele)
                    while ref and NIVEL[ref["tipo"]] > nv:
                        ref = por_id.get(ref.get("pai"))
                    if ref is None:
                        raise ValueError("não há onde encaixar o dispositivo novo; escolha outro de referência.")
                if NIVEL[ref["tipo"]] == nv:
                    k = _indice(lista, ref["id"])
                    pos, pai = _fim_sub(lista, k), ref["pai"]
                else:  # filho de ref: depois dos filhos que vêm antes (incisos do caput antes dos parágrafos)
                    k = _indice(lista, ref["id"])
                    fim = _fim_sub(lista, k)
                    pos = k + 1
                    while pos < fim and lista[pos]["pai"] == ref["id"] and NIVEL[lista[pos]["tipo"]] >= nv:
                        pos = _fim_sub(lista, pos)
                    pai = ref["id"]
        _renomear(novos, f"e{n}", existentes)
        for d in novos:
            d["origem"] = None
            d["emenda"] = n
            if d["pai"] is None:
                d["pai"] = pai
        lista[pos:pos] = novos
        return lista
    raise ValueError("tipo de emenda inválido.")


def renumerar(lista: list[dict]) -> list[dict]:
    """Numera em sequência (art. 1º, 2º...; parágrafo único quando há só um; incisos I, II...; alíneas a, b...)
    e recalcula os ids. Marca "renumerado" no que mudou de rótulo."""
    lista = copy.deepcopy(lista)
    total: dict = {}
    for d in lista:
        total[(d.get("pai"), d["tipo"])] = total.get((d.get("pai"), d["tipo"]), 0) + 1
    conta: dict = {}
    novo_id = {}
    for d in lista:
        if d["tipo"] in ("preambulo", "agrupador"):
            novo_id[d["id"]] = d["id"]
            continue
        chave = (d.get("pai"), d["tipo"])
        k = conta[chave] = conta.get(chave, 0) + 1
        if d["tipo"] == "paragrafo":
            num = 0 if total[chave] == 1 else k
        elif d["tipo"] == "alinea":
            num = chr(ord("a") + k - 1) if k <= 26 else f"a{k}"
        else:
            num = k
        antes = prefixo(d)
        d["num"], d["suf"] = num, None
        if prefixo(d) != antes:
            d["renumerado"] = antes
    for d in lista:  # ids de novo, de cima para baixo (o pai sempre vem antes)
        if d["tipo"] in ("preambulo", "agrupador"):
            continue
        pai = novo_id.get(d.get("pai")) if d.get("pai") else None
        novo_id[d["id"]] = _id_de(d["tipo"], d["num"], None, pai, d["texto"])
    vistos = set()
    for d in lista:
        nid = novo_id.get(d["id"], d["id"])
        base, k = nid, 2
        while nid in vistos:
            nid = f"{base}_{k}"
            k += 1
        vistos.add(nid)
        d["id"] = nid
        d["pai"] = novo_id.get(d["pai"]) if d.get("pai") else None
    return lista


def consolidar(texto: str, emendas: list[dict], so: str | None = None, renumera: bool = True) -> dict:
    """Texto com as emendas aprovadas (ou só a emenda `so`, para o comparativo de uma emenda)."""
    original = dispositivos(texto)
    lista, aplicadas, avisos = original, [], []
    escolhidas = [e for e in emendas if e["id"] == so] if so else \
        [e for e in emendas if e.get("situacao") == "aprovada"]
    for e in sorted(escolhidas, key=lambda x: x.get("numero", 0)):
        try:
            lista = aplicar(lista, e)
            aplicadas.append(e["numero"])
        except ValueError as ex:
            avisos.append(f"Emenda nº {e.get('numero')}: {ex}")
    final = renumerar(lista) if renumera else lista
    renumerados = [d for d in final if d.get("renumerado")]
    if renumerados:
        avisos.append(f"{len(renumerados)} dispositivo(s) renumerado(s): confira as remissões no texto (ex.: “art. 3º”).")
    return {"original": original, "dispositivos": final, "texto": montar(final), "aplicadas": aplicadas, "avisos": avisos}


# ---------------------------------------------------------------- comparativo
_TOKENS = re.compile(r"\s+|\w+|[^\w\s]", re.U)


def diff_ops(a: str, b: str) -> list[list[str]]:
    """Diferença palavra a palavra: [["=", "texto"], ["-", "removido"], ["+", "inserido"]]."""
    ta, tb = _TOKENS.findall(a or ""), _TOKENS.findall(b or "")
    ops: list[list[str]] = []

    def add(op, partes):
        s = "".join(partes)
        if not s:
            return
        if ops and ops[-1][0] == op:
            ops[-1][1] += s
        else:
            ops.append([op, s])

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if tag == "equal":
            add("=", ta[i1:i2])
        else:
            add("-", ta[i1:i2])
            add("+", tb[j1:j2])
    # um espaço igual entre duas mudanças entra no bloco: "<del>na data</del><ins>em 90</ins>", não palavra a palavra
    saida: list[list[str]] = []
    k = 0
    while k < len(ops):
        if ops[k][0] == "=":
            saida.append(ops[k])
            k += 1
            continue
        menos, mais = [], []
        while k < len(ops):
            op, t = ops[k]
            if op == "=" and not (t.isspace() and k + 1 < len(ops) and ops[k + 1][0] != "="):
                break
            if op in ("-", "="):
                menos.append(t)
            if op in ("+", "="):
                mais.append(t)
            k += 1
        if menos:
            saida.append(["-", "".join(menos)])
        if mais:
            saida.append(["+", "".join(mais)])
    return saida


def ops_html(ops: list[list[str]], lado: str = "") -> str:
    """HTML com <ins>/<del>; o texto é escapado ANTES de ser envolvido pelas marcas."""
    saida = []
    for op, t in ops:
        t = html.escape(t)
        if op == "=":
            saida.append(t)
        elif op == "-" and lado != "depois":
            saida.append(f"<del>{t}</del>")
        elif op == "+" and lado != "antes":
            saida.append(f"<ins>{t}</ins>")
    return "".join(saida)


def diff_html(a: str, b: str, lado: str = "") -> str:
    return ops_html(diff_ops(a, b), lado)


def comparativo(original: list[dict], final: list[dict]) -> dict:
    """Linhas alinhadas por dispositivo (pelo id de origem) e a diferença do texto todo."""
    pos = {d["id"]: i for i, d in enumerate(original)}
    usados = {d["origem"] for d in final if d.get("origem") in pos}
    emitidos: set = set()
    linhas = []

    def suprimido(d):
        linhas.append({"estado": "suprimido", "antes": linha(d), "depois": None, "ops": [["-", linha(d)]], "id": d["id"]})

    i = 0
    for n in final:
        o = n.get("origem")
        if o in pos and o not in emitidos:
            while i <= pos[o]:
                d = original[i]
                i += 1
                if d["id"] not in usados:
                    suprimido(d)
            emitidos.add(o)
            a, b = linha(original[pos[o]]), linha(n)
            estado = "igual" if a == b else "renumerado" if original[pos[o]]["texto"] == n["texto"] else "alterado"
            linhas.append({"estado": estado, "antes": a, "depois": b, "ops": diff_ops(a, b) if a != b else [["=", a]],
                           "id": n["id"], "origem": o, "emenda": n.get("emenda")})
        else:
            linhas.append({"estado": "acrescido", "antes": None, "depois": linha(n), "ops": [["+", linha(n)]],
                           "id": n["id"], "emenda": n.get("emenda")})
    for d in original[i:]:
        if d["id"] not in usados:
            suprimido(d)
    resumo = {k: sum(1 for x in linhas if x["estado"] == k) for k in ("alterado", "acrescido", "suprimido", "renumerado")}
    return {"linhas": linhas, "texto": diff_ops(montar(original), montar(final)), "resumo": resumo}


# ---------------------------------------------------------------- banco
def criar_tabela(db):
    db.execute("create table if not exists emendas (id text primary key, projeto text, numero integer, json text)")
    db.commit()


def salvar(db, e: dict):
    db.execute("insert or replace into emendas (id, projeto, numero, json) values (?, ?, ?, ?)",
               (e["id"], e["projeto"], e["numero"], json.dumps(e, ensure_ascii=False)))
    db.commit()


def obter(db, eid: str) -> dict | None:
    linha_db = db.execute("select json from emendas where id = ?", (str(eid),)).fetchone()
    return json.loads(linha_db[0]) if linha_db else None


def do_projeto(db, pid: str) -> list[dict]:
    return [json.loads(x[0]) for x in db.execute(
        "select json from emendas where projeto = ? order by numero", (pid,)).fetchall()]


def contagem(db, pid: str) -> dict:
    c = {k: 0 for k in SITUACOES}
    for (j,) in db.execute("select json from emendas where projeto = ?", (pid,)).fetchall():
        c[json.loads(j).get("situacao", "apresentada")] += 1
    return c


def titulo(e: dict, numero_projeto: str) -> str:
    return f"Emenda nº {e['numero']} ao {numero_projeto or 'projeto'}".strip()


def _texto_limpo(v, limite: int, nome: str) -> str:
    v = str(v or "").replace("\r", "").strip()
    if len(v) > limite:
        raise ValueError(f"{nome} grande demais (até {limite} caracteres).")
    return v


def propor(db, projeto: dict, tipo: str, alvo: str, texto: str, justificativa: str, autor_pid: str | None,
           autor_nome: str, aglutina=None) -> dict:
    if tipo not in TIPOS:
        raise ValueError("Tipo de emenda inválido.")
    texto = _texto_limpo(texto, LIMITE_TEXTO, "Texto da emenda")
    justificativa = _texto_limpo(justificativa, LIMITE_JUSTIFICATIVA, "Justificativa")
    alvo = str(alvo or "").strip()[:120]
    if len(justificativa) < 10:
        raise ValueError("Escreva a justificativa da emenda (ao menos 10 caracteres).")
    lista = dispositivos(projeto.get("texto", ""))
    if not lista:
        raise ValueError("Cadastre o texto do projeto (com os artigos) antes de receber emendas.")
    por_id = {d["id"]: d for d in lista}
    if alvo and alvo not in por_id:
        raise ValueError("Dispositivo alvo inválido.")
    if not alvo and tipo not in ("substitutiva", "aglutinativa", "aditiva"):
        raise ValueError("Escolha o dispositivo que a emenda altera.")
    if tipo == "supressiva":
        texto = ""
    elif not texto:
        raise ValueError("Escreva o texto novo da emenda.")
    existentes = do_projeto(db, projeto["id"])
    if len(existentes) >= MAX_POR_PROJETO:
        raise ValueError("Limite de emendas deste projeto atingido.")
    agl = []
    if tipo == "aglutinativa":
        numeros = {e["numero"]: e for e in existentes}
        try:
            agl = sorted({int(x) for x in (aglutina or [])})
        except (TypeError, ValueError):
            raise ValueError("Informe os números das emendas aglutinadas.")
        if not agl or any(x not in numeros or numeros[x]["situacao"] != "apresentada" for x in agl):
            raise ValueError("A aglutinativa funde emendas apresentadas: informe os números delas.")
    numero = max((e["numero"] for e in existentes), default=0) + 1
    e = {"id": secrets.token_hex(5), "projeto": projeto["id"], "numero": numero, "tipo": tipo, "alvo": alvo,
         "alvoRef": referencia(por_id[alvo], por_id) if alvo else ("fim do texto" if tipo == "aditiva" else "texto integral (substitutivo)"),
         "alvoTexto": linha(por_id[alvo])[:LIMITE_TEXTO] if alvo else "",
         "texto": texto, "justificativa": justificativa, "autorPid": autor_pid, "autor": str(autor_nome)[:120],
         "aglutina": agl, "criado": agora(), "situacao": "apresentada", "resultado": None}
    aplicar(lista, e)  # valida: a emenda precisa caber no texto atual
    salvar(db, e)
    return e


def retirar(db, e: dict, pid: str | None, presidente: bool) -> dict:
    if not presidente and (not pid or e.get("autorPid") != pid):
        raise PermissionError("Só o autor retira a própria emenda.")
    if e["situacao"] != "apresentada":
        raise ValueError("Só dá para retirar emenda ainda não votada.")
    e["situacao"] = "retirada"
    e["resultado"] = {"ts": agora(), "por": "autor" if not presidente else "presidência"}
    salvar(db, e)
    return e


def registrar_resultado(db, e: dict, situacao: str, extra: dict | None = None) -> list[int]:
    """Aprovada, rejeitada, prejudicada (ou apresentada, para desfazer). Devolve as emendas prejudicadas junto."""
    if situacao not in ("aprovada", "rejeitada", "prejudicada", "apresentada"):
        raise ValueError("Situação inválida.")
    if e["situacao"] == "retirada":
        raise ValueError("Emenda retirada pelo autor.")
    e["situacao"] = situacao
    e["resultado"] = None if situacao == "apresentada" else {"ts": agora(), **(extra or {})}
    salvar(db, e)
    prejudicadas = []
    if situacao == "aprovada" and e["tipo"] == "aglutinativa":
        for outra in do_projeto(db, e["projeto"]):
            if outra["numero"] in e.get("aglutina", []) and outra["situacao"] == "apresentada":
                outra["situacao"] = "prejudicada"
                outra["resultado"] = {"ts": agora(), "motivo": f"aglutinada na Emenda nº {e['numero']}"}
                salvar(db, outra)
                prejudicadas.append(outra["numero"])
    return prejudicadas


# ---------------------------------------------------------------- redação final em HTML e PDF
def _data(ms) -> str:
    return time.strftime("%d/%m/%Y", time.localtime((ms or agora()) / 1000))


def documento_html(projeto: dict, cons: dict, emendas: list[dict], casa: str) -> str:
    esc = html.escape
    aprovado = projeto.get("situacao") == "aprovada"
    titulo_doc = "Autógrafo — redação final" if aprovado else "Texto consolidado com as emendas aprovadas"
    corpo = []
    for d in cons["dispositivos"]:
        recuo = {"paragrafo": 0, "inciso": 1.5, "alinea": 3, "item": 4.5}.get(d["tipo"], 0)
        p = prefixo(d)
        rot = f"<b>{esc(p)}</b> " if p else ""
        classe = "grupo" if d["tipo"] in ("agrupador", "preambulo") else ""
        corpo.append(f'<p class="{classe}" style="margin-left:{recuo}em" id="{esc(d["id"])}">{rot}'
                     f'{esc(d["texto"]).replace(chr(10), "<br>")}</p>')
    aplicadas = [e for e in emendas if e["numero"] in cons["aplicadas"]]
    lista_em = "".join(f"<li>Emenda nº {e['numero']} ({esc(TIPOS[e['tipo']].lower())}, {esc(e['alvoRef'])}) — "
                       f"{esc(e['autor'])}</li>" for e in aplicadas) or "<li>Nenhuma emenda aprovada.</li>"
    avisos = "".join(f"<li>{esc(a)}</li>" for a in cons["avisos"])
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(projeto.get('numero') or 'Projeto')} — {esc(titulo_doc)}</title>
<style>
body{{font:16px/1.6 Georgia,'Times New Roman',serif;max-width:780px;margin:0 auto;padding:32px 18px;color:#111;background:#fff}}
h1{{font-size:20px;text-align:center;margin:0}} h2{{font-size:15px;text-align:center;font-weight:normal;margin:4px 0 24px}}
.ementa{{margin-left:40%;font-style:italic;margin-bottom:24px}} p{{margin:8px 0;text-align:justify}}
.grupo{{text-align:center;font-weight:bold}} .meta{{font:13px/1.5 system-ui,sans-serif;color:#444;border-top:1px solid #ccc;margin-top:32px;padding-top:12px}}
@media print{{.meta a{{display:none}}}}
</style></head><body>
<h1>{esc(casa)}</h1><h2>{esc(titulo_doc)}<br><b>{esc(projeto.get('numero') or '')}</b></h2>
<p class="ementa">{esc(projeto.get('ementa') or '')}</p>
{''.join(corpo)}
<div class="meta"><b>Emendas incorporadas</b><ul>{lista_em}</ul>
{f'<b>Avisos para a revisão</b><ul>{avisos}</ul>' if avisos else ''}
Gerado pelo LEGIS em {_data(None)}. Autoria do projeto: {esc(projeto.get('autor') or '—')}.
<a href="/redacao-final/{esc(projeto['id'])}.pdf">PDF</a> · <a href="/dados/akn/projeto/{esc(projeto['id'])}/redacao-final.xml">Akoma Ntoso</a></div>
</body></html>"""


def documento_pdf(projeto: dict, cons: dict, emendas: list[dict], casa: str) -> bytes:
    from ata import PDF, CINZA, ESCURO
    aprovado = projeto.get("situacao") == "aprovada"
    titulo_doc = "AUTÓGRAFO — REDAÇÃO FINAL" if aprovado else "TEXTO CONSOLIDADO COM AS EMENDAS APROVADAS"
    pdf = PDF(f"{casa} · {projeto.get('numero') or ''} · gerado pelo LEGIS em {_data(None)}")
    pdf.texto(pdf.m, pdf.y - 14, casa.upper(), 13, True)
    pdf.texto(pdf.m, pdf.y - 32, titulo_doc, 11, True, pdf.cor)
    pdf.texto(pdf.m, pdf.y - 48, projeto.get("numero") or "", 11, True)
    pdf.y -= 58
    pdf.linha(pdf.m, pdf.y, pdf.L - pdf.m, pdf.y, ESCURO, 1)
    pdf.espaco(6)
    if projeto.get("ementa"):
        pdf.paragrafo(projeto["ementa"], 10, False, CINZA, recuo=180)
        pdf.espaco(8)
    for d in cons["dispositivos"]:
        recuo = {"inciso": 18, "alinea": 36, "item": 54}.get(d["tipo"], 0)
        if d["tipo"] == "artigo":
            pdf.espaco(5)
        pdf.paragrafo(linha(d), 10, d["tipo"] == "agrupador", ESCURO, recuo=recuo)
    aplicadas = [e for e in emendas if e["numero"] in cons["aplicadas"]]
    pdf.secao(1, "Emendas incorporadas")
    if aplicadas:
        pdf.tabela([("Emenda", .14, "e"), ("Tipo", .16, "e"), ("Dispositivo", .4, "e"), ("Autoria", .3, "e")],
                   [[f"nº {e['numero']}", TIPOS[e["tipo"]], e["alvoRef"], e["autor"]] for e in aplicadas])
    else:
        pdf.paragrafo("Nenhuma emenda aprovada.", 9.5, False, CINZA)
    if cons["avisos"]:
        pdf.espaco(8)
        for a in cons["avisos"]:
            pdf.paragrafo("• " + a, 9, False, CINZA)
    return pdf.gerar()
