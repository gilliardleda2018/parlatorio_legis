"""Dados oficiais e públicos de um município para configurar a Câmara.

  * TSE, resultados das Eleições Municipais 2024 (1º turno): vereadores eleitos com
    nome de urna, partido pelo qual foram eleitos e foto da candidatura;
  * IBGE, Censo 2022: população residente;
  * Wikidata e Wikimedia Commons: o brasão do município (pelo código do IBGE), só quando a imagem é de
    domínio público ou CC0. Brasão enviado pela Câmara sempre vale mais que o automático.

Os dados refletem a eleição de 2024. Suplentes que assumiram depois, mudanças de
partido e nomes parlamentares diferentes do nome de urna são ajustados no painel.
Nada é inventado sobre pessoas reais: só o que o TSE e o IBGE publicam.
"""
import base64
import html
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request

ELEICAO = "619"  # Eleições Municipais 2024, 1º turno
TSE = os.environ.get("LEGIS_TSE_URL", "https://resultados.tse.jus.br/oficial/ele2024")   # trocáveis nos testes
IBGE = os.environ.get("LEGIS_IBGE_URL", "https://servicodados.ibge.gov.br/api")
WIKIDATA = os.environ.get("LEGIS_WIKIDATA_URL", "https://query.wikidata.org/sparql")
COMMONS = os.environ.get("LEGIS_COMMONS_URL", "https://commons.wikimedia.org/w/api.php")
LIMITE_BRASAO = 1_500_000
# Só entra sozinho o brasão que pode ser usado sem crédito ao autor (o LEGIS mostra o brasão em telas e atas)
LICENCAS_LIVRES = re.compile(r"public domain|domínio público|\bpd\b|cc0|cc-zero", re.I)
AGENTE = "ParlatorioLegis/2 (+https://github.com/gilliardleda2018/parlatorio_legis)"
MINUSCULAS = {"de", "da", "do", "das", "dos", "e", "na", "no", "nas", "nos", "di", "du"}
UFS = {"ac", "al", "ap", "am", "ba", "ce", "df", "es", "go", "ma", "mt", "ms", "mg", "pa", "pb", "pr",
       "pe", "pi", "rj", "rn", "rs", "ro", "rr", "sc", "sp", "se", "to"}


def baixar(url: str, limite: int = 5_000_000) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(limite)


def sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").upper().strip()


def nome_proprio(nome: str) -> str:
    palavras = nome.lower().split()
    return " ".join(p if (k and p in MINUSCULAS) else p.capitalize() for k, p in enumerate(palavras))


def _tipo_imagem(b: bytes) -> str | None:
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:2] == b"\xff\xd8":
        return "image/jpeg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return None


def brasao(ibge: str, log=print) -> dict | None:
    """Brasão do município no Wikidata (propriedade P94, achada pelo código IBGE na P1585) e o arquivo no Wikimedia
    Commons: PNG, JPEG ou WebP de até 1,5 MB (SVG vem na miniatura PNG de 512 px). Só aceita domínio público ou CC0.
    Nunca levanta erro: sem brasão, a Câmara envia o dela depois (Configurações)."""
    ibge = str(ibge or "")
    if not re.fullmatch(r"\d{7}", ibge):
        return None
    try:
        consulta = f'SELECT ?b WHERE {{ ?m wdt:P1585 "{ibge}" ; wdt:P94 ?b }} LIMIT 1'
        r = json.loads(baixar(f"{WIKIDATA}?{urllib.parse.urlencode({'query': consulta, 'format': 'json'})}", 200_000))
        achados = r["results"]["bindings"]
        if not achados:
            log("Brasão: o Wikidata não tem o brasão deste município; envie o da Câmara em Configurações.")
            return None
        arquivo = urllib.parse.unquote(achados[0]["b"]["value"].rsplit("/", 1)[1])
        info = json.loads(baixar(f"{COMMONS}?" + urllib.parse.urlencode(
            {"action": "query", "titles": f"File:{arquivo}", "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
             "iiurlwidth": 512, "format": "json"}), 200_000))
        dados = next(iter(info["query"]["pages"].values()))["imageinfo"][0]
        meta = dados.get("extmetadata") or {}
        licenca = html.unescape(re.sub(r"<[^>]+>", "", str((meta.get("LicenseShortName") or {}).get("value") or ""))).strip()
        if not LICENCAS_LIVRES.search(licenca):
            log(f"Brasão: {arquivo} no Wikimedia Commons tem licença '{licenca or 'desconhecida'}', que pede crédito ao "
                "autor; não entra sozinho. Envie o brasão da Câmara em Configurações.")
            return None
        url = dados.get("thumburl") or dados["url"]
        if urllib.parse.urlparse(url).scheme not in ("https", "http"):
            return None
        imagem = baixar(url, LIMITE_BRASAO + 1)
        tipo = _tipo_imagem(imagem)
        if not tipo or len(imagem) > LIMITE_BRASAO:
            log(f"Brasão: {arquivo} não é PNG, JPEG ou WebP de até 1,5 MB; envie o da Câmara em Configurações.")
            return None
        log(f"Brasão: {arquivo} (Wikimedia Commons, {licenca}, {len(imagem) // 1024} KB)")
        return {"mime": tipo, "dados": imagem, "fonte": f"https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(arquivo)}",
                "licenca": licenca}
    except Exception as e:  # Wikidata ou Commons fora do ar não impedem a configuração
        log(f"Brasão automático indisponível ({e}); envie o da Câmara em Configurações.")
        return None


def buscar(uf: str, municipio: str, log=print, fotos: bool = True, com_brasao: bool = True) -> dict:
    """Busca o município no TSE (eleitos e fotos) e no IBGE (população). Levanta ValueError se não achar."""
    uf = str(uf or "").lower().strip()
    if uf not in UFS:
        raise ValueError("Escolha o estado.")
    municipio = str(municipio or "").strip()
    if not municipio:
        raise ValueError("Escreva o nome do município.")

    cfg = json.loads(baixar(f"{TSE}/{ELEICAO}/config/mun-e000{ELEICAO}-cm.json", 20_000_000))
    estado = next((e for e in cfg["abr"] if e["cd"] == uf), None)
    mun = next((m for m in (estado or {}).get("mu", []) if sem_acento(m["nm"]) == sem_acento(municipio)), None)
    if not mun:
        raise ValueError(f"Município {municipio}/{uf.upper()} não encontrado no TSE. Confira a grafia.")
    log(f"TSE: {mun['nm']}/{uf.upper()} (código TSE {mun['cd']}, IBGE {mun['cdi']})")

    res = json.loads(baixar(f"{TSE}/{ELEICAO}/dados/{uf}/{uf}{mun['cd']}-c0013-e000{ELEICAO}-u.json"))
    cargo = res["carg"][0]
    eleitos = []
    for agr in cargo["agr"]:
        for par in agr["par"]:
            for c in par["cand"]:
                if str(c.get("st", "")).lower().startswith("eleito"):
                    eleitos.append({"nome": nome_proprio(c["nmu"]), "partido": par["sg"], "numero": c["n"],
                                    "votos": int(c.get("vap") or 0), "sq": c["sqcand"], "situacao": c["st"], "foto": None})
    eleitos.sort(key=lambda e: e["nome"])
    if not eleitos:
        raise ValueError("O TSE não lista vereadores eleitos para este município.")
    log(f"{len(eleitos)} vereadores eleitos (vagas: {cargo.get('nv')}), totalização de {res.get('dg')}")

    populacao = None
    try:
        ibge = json.loads(baixar(f"{IBGE}/v3/agregados/4709/periodos/2022/variaveis/93?localidades=N6%5B{mun['cdi']}%5D"))
        populacao = int(ibge[0]["resultados"][0]["series"][0]["serie"]["2022"])
        log(f"IBGE, Censo 2022: {populacao:,} habitantes".replace(f"{populacao:,}", f"{populacao:,}".replace(",", ".")))
    except Exception as e:  # o IBGE fora do ar não impede a configuração
        log(f"População do IBGE indisponível ({e}); ajuste em Projetos → critérios.")

    if fotos:
        for e in eleitos:
            try:
                foto = baixar(f"{TSE}/{ELEICAO}/fotos/{uf}/{e['sq']}.jpeg", 1_500_000)
                if foto[:2] == b"\xff\xd8":
                    e["foto"] = foto
            except Exception as erro:
                log(f"  foto de {e['nome']} indisponível ({erro})")

    saida = {"uf": uf.upper(), "municipio": nome_proprio(mun["nm"]), "ibge": mun["cdi"], "tse": mun["cd"],
             "populacao": populacao, "eleitos": eleitos}
    if com_brasao:
        logo = brasao(mun["cdi"], log)
        if logo:
            saida["logo"] = logo
    return saida


def para_json(dados: dict) -> str:
    """Dados da cidade (com fotos e brasão) num JSON que vai dentro do pacote da Câmara."""
    d = dict(dados)
    d["eleitos"] = [{**e, "foto": base64.b64encode(e["foto"]).decode() if e.get("foto") else None} for e in dados["eleitos"]]
    if dados.get("logo"):
        d["logo"] = {**dados["logo"], "dados": base64.b64encode(dados["logo"]["dados"]).decode()}   # com fonte e licença
    return json.dumps(d, ensure_ascii=False)


def de_json(texto: str) -> dict:
    d = json.loads(texto)
    d["eleitos"] = [{**e, "foto": base64.b64decode(e["foto"]) if e.get("foto") else None} for e in d["eleitos"]]
    if d.get("logo"):
        d["logo"] = {**d["logo"], "dados": base64.b64decode(d["logo"]["dados"])}
    return d
