"""Dados oficiais e públicos de um município para configurar a Câmara.

  * TSE, resultados das Eleições Municipais 2024 (1º turno): vereadores eleitos com
    nome de urna, partido pelo qual foram eleitos e foto da candidatura;
  * IBGE, Censo 2022: população residente.

Os dados refletem a eleição de 2024. Suplentes que assumiram depois, mudanças de
partido e nomes parlamentares diferentes do nome de urna são ajustados no painel.
Nada é inventado sobre pessoas reais: só o que o TSE e o IBGE publicam.
"""
import json
import os
import unicodedata
import urllib.request

ELEICAO = "619"  # Eleições Municipais 2024, 1º turno
TSE = os.environ.get("LEGIS_TSE_URL", "https://resultados.tse.jus.br/oficial/ele2024")   # trocáveis nos testes
IBGE = os.environ.get("LEGIS_IBGE_URL", "https://servicodados.ibge.gov.br/api")
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


def buscar(uf: str, municipio: str, log=print, fotos: bool = True) -> dict:
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

    return {"uf": uf.upper(), "municipio": nome_proprio(mun["nm"]), "ibge": mun["cdi"], "tse": mun["cd"],
            "populacao": populacao, "eleitos": eleitos}
