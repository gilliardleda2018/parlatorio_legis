#!/usr/bin/env python3
"""Cria um banco do Parlatório Legis com os vereadores ELEITOS de um município (dados oficiais).

Uso: python3 scripts/tse_eleitos.py MA "Tuntum" dados/tuntum.db
     PARLATORIO_BANCO=dados/tuntum.db python3 server/servidor.py

Fontes oficiais e públicas:
  * TSE, resultados das Eleições Municipais 2024 (1º turno): nome de urna, partido
    pelo qual foi eleito e foto da candidatura (resultados.tse.jus.br);
  * IBGE, Censo 2022: população residente (usada na nota de relevância social).

Os dados refletem a eleição de 2024. Suplentes que assumiram depois, mudanças de
partido e nomes parlamentares diferentes do nome de urna precisam ser ajustados
no painel (Plenário → Editar) ou importados do SAPL da câmara, se houver.
O banco nasce SEM sessões, votos ou projetos: nada é inventado sobre pessoas reais.
"""
import json
import os
import sys
import unicodedata
import urllib.request
from pathlib import Path

ELEICAO = "619"  # Eleições Municipais 2024, 1º turno
TSE = "https://resultados.tse.jus.br/oficial/ele2024"
AGENTE = "ParlatorioLegis/2 (+https://github.com/gilliardleda2018/parlatorio_legis)"
MINUSCULAS = {"de", "da", "do", "das", "dos", "e", "na", "no", "nas", "nos", "di", "du"}


def baixar(url: str, limite: int = 5_000_000) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(limite)


def sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").upper().strip()


def nome_proprio(nome: str) -> str:
    palavras = nome.lower().split()
    return " ".join(p if (k and p in MINUSCULAS) else p.capitalize() for k, p in enumerate(palavras))


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    uf, municipio, banco = sys.argv[1].lower(), sys.argv[2], Path(sys.argv[3])
    if banco.exists():
        sys.exit(f"{banco} já existe. Escolha outro arquivo (este script cria um banco novo).")

    cfg = json.loads(baixar(f"{TSE}/{ELEICAO}/config/mun-e000{ELEICAO}-cm.json", 20_000_000))
    estado = next((e for e in cfg["abr"] if e["cd"] == uf), None)
    mun = next((m for m in (estado or {}).get("mu", []) if sem_acento(m["nm"]) == sem_acento(municipio)), None)
    if not mun:
        sys.exit(f"Município {municipio}/{uf.upper()} não encontrado no TSE.")
    print(f"TSE: {mun['nm']}/{uf.upper()} (código TSE {mun['cd']}, IBGE {mun['cdi']})")

    res = json.loads(baixar(f"{TSE}/{ELEICAO}/dados/{uf}/{uf}{mun['cd']}-c0013-e000{ELEICAO}-u.json"))
    cargo = res["carg"][0]
    eleitos = []
    for agr in cargo["agr"]:
        for par in agr["par"]:
            for c in par["cand"]:
                if str(c.get("st", "")).lower().startswith("eleito"):
                    eleitos.append({"nome": nome_proprio(c["nmu"]), "partido": par["sg"], "numero": c["n"],
                                    "votos": int(c.get("vap") or 0), "sq": c["sqcand"], "situacao": c["st"]})
    eleitos.sort(key=lambda e: e["nome"])
    print(f"{len(eleitos)} vereadores eleitos (vagas: {cargo.get('nv')}), totalização de {res.get('dg')}")

    populacao = None
    try:
        ibge = json.loads(baixar(f"https://servicodados.ibge.gov.br/api/v3/agregados/4709/periodos/2022/variaveis/93"
                                 f"?localidades=N6%5B{mun['cdi']}%5D"))
        populacao = int(ibge[0]["resultados"][0]["series"][0]["serie"]["2022"])
        print(f"IBGE, Censo 2022: {populacao:,} habitantes".replace(",", "."))
    except Exception as e:  # o IBGE fora do ar não impede a importação
        print(f"População do IBGE indisponível ({e}); ajuste em Projetos → critérios.")

    # cria o banco pelo próprio servidor (estado inicial completo) e troca os parlamentares de exemplo
    os.environ["PARLATORIO_BANCO"] = str(banco)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
    import servidor  # noqa: E402

    S = servidor.S
    S["casa"]["nome"] = f"Câmara Municipal de {mun['nm'].title()}"
    S["casa"]["titulo"] = "Sessão Ordinária"
    S["pauta"], S["parls"], S["_pins"] = [], [], {}
    if populacao:
        S["relevancia"]["populacao"] = populacao
    servidor.db.execute("delete from projetos")
    for k, e in enumerate(eleitos, 1):
        p = {"id": servidor.uid(), "nome": e["nome"], "partido": e["partido"], "lider": False, "fotoV": 0,
             "canal": k, "bloco": "", "tse": {"numero": e["numero"], "votos": e["votos"], "sq": e["sq"], "situacao": e["situacao"]}}
        try:
            foto = baixar(f"{TSE}/{ELEICAO}/fotos/{uf}/{e['sq']}.jpeg", 1_500_000)
            if foto[:2] == b"\xff\xd8":
                servidor.db.execute("insert or replace into imagens (chave, mime, dados) values (?, ?, ?)",
                                    (p["id"], "image/jpeg", foto))
                p["fotoV"] = servidor.agora()
        except Exception as erro:
            print(f"  foto de {e['nome']} indisponível ({erro})")
        S["parls"].append(p)
        S["_pins"][p["id"]] = servidor.seguranca.hash_pin("0000")
        S["partidos"].setdefault(e["partido"], "#5b6b86")
        print(f"  {e['nome']:<28} {e['partido']:<6} nº {e['numero']:<6} {e['votos']:>5} votos  {'foto ✓' if p['fotoV'] else 'sem foto'}")
    servidor.db.commit()
    servidor.salvar()
    print(f"\nBanco criado em {banco}. PIN inicial de todos: 0000 (troque em Plenário → Editar).")


if __name__ == "__main__":
    main()
