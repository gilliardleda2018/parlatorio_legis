"""Recomenda boas práticas (boas_praticas.json) para cada parlamentar.

Pontuação explicável, de 0 a 100:
  * afinidade (50%): áreas de interesse declaradas pelo parlamentar (peso 3)
    e áreas dos projetos de sua autoria (peso 1 cada);
  * evidência (30%): forte 1,0 · moderada 0,7 · limitada 0,4;
  * lacuna na cidade (20%): áreas com poucos projetos ativos no banco ganham prioridade.
Sem perfil nem projetos, vale só evidência (60%) e lacuna (40%).
"""
import json
from collections import Counter
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parent / "boas_praticas.json"
PESO_EVIDENCIA = {"forte": 1.0, "moderada": 0.7, "limitada": 0.4}
INATIVOS = ("rejeitada", "retirada", "arquivado")


def catalogo() -> list[dict]:
    return json.loads(ARQUIVO.read_text(encoding="utf-8"))["praticas"]


def recomendar(nome: str, interesses: list[str], projetos: list[dict], areas_nomes: dict) -> list[dict]:
    meus = [p for p in projetos if p.get("autor") == nome]
    peso = Counter()
    for a in interesses:
        peso[a] += 3
    for p in meus:
        for a in p.get("areas") or []:
            peso[a] += 1
    maximo = max(peso.values(), default=0)
    ativos = Counter(a for p in projetos if p.get("situacao") not in INATIVOS for a in p.get("areas") or [])
    minhas_origens = {p.get("origem") for p in meus if p.get("origem")}
    outras_origens = {p.get("origem") for p in projetos if p.get("origem") and p.get("autor") != nome}

    saida = []
    for item in catalogo():
        areas = item["areas"]
        afinidade = max((peso[a] for a in areas), default=0) / maximo if maximo else 0.0
        evidencia = PESO_EVIDENCIA.get(item["evidencia"], 0.4)
        menor_cobertura = min((ativos[a] for a in areas), default=0)
        lacuna = 1 - min(1.0, menor_cobertura / 3)
        nota = (0.5 * afinidade + 0.3 * evidencia + 0.2 * lacuna) if maximo else (0.6 * evidencia + 0.4 * lacuna)
        motivos = []
        comuns = [areas_nomes.get(a, a) for a in areas if peso[a]]
        if comuns:
            origem = "seus interesses" if any(a in interesses for a in areas) else "seus projetos"
            motivos.append(f"Combina com {origem}: {', '.join(comuns)}")
        area_lacuna = min(areas, key=lambda a: ativos[a]) if areas else None
        if area_lacuna and ativos[area_lacuna] <= 1:
            motivos.append(f"Poucos projetos ativos na cidade em {areas_nomes.get(area_lacuna, area_lacuna)}")
        motivos.append(f"Evidência {item['evidencia']}")
        if item["custo"] == "baixo":
            motivos.append("Custo baixo para o município")
        situacao = "proposto_por_voce" if item["id"] in minhas_origens else "proposto_por_outro" if item["id"] in outras_origens else None
        saida.append({**item, "nota": round(100 * nota), "motivos": motivos, "situacao": situacao,
                      "partes": {"afinidade": round(afinidade, 2), "evidencia": evidencia, "lacuna": round(lacuna, 2)}})
    saida.sort(key=lambda x: (x["situacao"] == "proposto_por_voce", -x["nota"]))
    return saida
