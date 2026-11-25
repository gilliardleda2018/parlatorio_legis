"""Recomenda boas práticas (boas_praticas.json) para cada parlamentar.

Nota de 0 a 100, sempre com os motivos que a explicam:

  afinidade (45%)   o que o parlamentar já mostrou que defende:
                    - áreas marcadas no perfil (peso 3), dos seus projetos (1),
                      das práticas que salvou (2) e menos as que descartou (−1);
                    - semelhança de TEXTO (TF-IDF) entre a prática e as ementas
                      dos seus projetos, os temas que escreveu no perfil e as
                      práticas que salvou — pega temas iguais com áreas diferentes.
  evidência (25%)   forte 1,0 · moderada 0,7 · limitada 0,4
  lacuna (15%)      áreas com poucos projetos ativos na cidade sobem
  colegas (15%)     práticas salvas ou propostas por outros parlamentares sobem

  penalidade        −15 se já existe projeto parecido na cidade (em tramitação
                    ou aprovado), mostrando qual é.

Práticas descartadas vão para o fim e a tela as esconde.
Sem nenhum sinal do parlamentar: evidência 50%, lacuna 30%, colegas 20%.
"""
import json
from collections import Counter
from pathlib import Path

from texto import Indice

ARQUIVO = Path(__file__).resolve().parent / "boas_praticas.json"
PESO_EVIDENCIA = {"forte": 1.0, "moderada": 0.7, "limitada": 0.4}
INATIVOS = ("rejeitada", "retirada", "arquivado")
ATIVOS_PARECIDOS = ("em_tramitacao", "em_pauta", "aprovada")
LIMIAR_TEXTO = 0.35      # cosseno a partir do qual a afinidade de texto é máxima
LIMIAR_PARECIDO = 0.30   # cosseno a partir do qual um projeto da cidade conta como "parecido"


def catalogo() -> list[dict]:
    return json.loads(ARQUIVO.read_text(encoding="utf-8"))["praticas"]


def _texto_pratica(p: dict) -> str:
    return f"{p['titulo']} {p['titulo']} {p['resumo']} {p['ementaSugerida']}"


def _texto_projeto(p: dict) -> str:
    return f"{p.get('ementa', '')} {p.get('justificativa', '')}"


def recomendar(nome: str, perfil: dict, feedback: dict, feedback_todos: dict, projetos: list[dict],
               areas_nomes: dict, pid: str) -> list[dict]:
    """perfil: {areas, temas}; feedback: {pratica: 'salvo'|'descartado'} deste parlamentar;
    feedback_todos: {pid: {pratica: valor}} de todos (para o sinal dos colegas)."""
    praticas = catalogo()
    por_id = {p["id"]: p for p in praticas}
    interesses = perfil.get("areas") or []
    temas = (perfil.get("temas") or "").strip()
    meus = [p for p in projetos if p.get("autor") == nome]

    indice = Indice([_texto_pratica(p) for p in praticas] + [_texto_projeto(p) for p in projetos])
    vet_pratica = indice.vetores[:len(praticas)]
    vet_projeto = indice.vetores[len(praticas):]

    # ---- sinais do parlamentar
    peso = Counter()
    for a in interesses:
        peso[a] += 3
    for p in meus:
        for a in p.get("areas") or []:
            peso[a] += 1
    for prid, valor in feedback.items():
        for a in por_id.get(prid, {}).get("areas", []):
            peso[a] += 2 if valor == "salvo" else -1
    maximo = max([v for v in peso.values() if v > 0], default=0)

    fontes_texto = []  # (vetor, rótulo do motivo, id da prática de origem)
    for k, p in enumerate(projetos):
        if p.get("autor") == nome and p.get("ementa"):
            fontes_texto.append((vet_projeto[k], f"seu {p['numero']}", None))
    if temas:
        fontes_texto.append((indice.vetor(temas), "os temas que você defende", None))
    for prid, valor in feedback.items():
        if valor == "salvo" and prid in por_id:
            fontes_texto.append((vet_pratica[praticas.index(por_id[prid])], f"“{por_id[prid]['titulo']}”, que você salvou", prid))
    tem_sinal = bool(maximo or fontes_texto)

    # ---- sinais da cidade e dos colegas
    ativos = Counter(a for p in projetos if p.get("situacao") not in INATIVOS for a in p.get("areas") or [])
    minhas_origens = {p.get("origem") for p in meus if p.get("origem")}
    colegas = Counter()
    for outro, fb in feedback_todos.items():
        if outro != pid:
            for prid, valor in fb.items():
                if valor == "salvo":
                    colegas[prid] += 1
    for p in projetos:
        if p.get("origem") and p.get("autor") != nome:
            colegas[p["origem"]] += 2

    saida = []
    for k, item in enumerate(praticas):
        areas, vp = item["areas"], vet_pratica[k]
        motivos = []

        # afinidade por área
        af_area = max((max(peso[a], 0) for a in areas), default=0) / maximo if maximo else 0.0
        comuns = [areas_nomes.get(a, a) for a in areas if peso[a] > 0]
        # afinidade por texto
        melhor, rotulo = 0.0, None
        for v, r, origem in fontes_texto:
            if origem == item["id"]:
                continue  # não compara a prática salva com ela mesma
            c = indice.cosseno(vp, v)
            if c > melhor:
                melhor, rotulo = c, r
        af_texto = min(1.0, melhor / LIMIAR_TEXTO)
        afinidade = max(af_area, af_texto) if fontes_texto and maximo else (af_texto if fontes_texto else af_area)
        if af_texto >= 0.5 and rotulo:
            motivos.append(f"Parecido com {rotulo}")
        if comuns:
            motivos.append(f"Combina com suas áreas: {', '.join(comuns)}")

        evidencia = PESO_EVIDENCIA.get(item["evidencia"], 0.4)
        lacuna = 1 - min(1.0, min((ativos[a] for a in areas), default=0) / 3)
        area_lacuna = min(areas, key=lambda a: ativos[a]) if areas else None
        if area_lacuna and ativos[area_lacuna] <= 1:
            motivos.append(f"Poucos projetos ativos na cidade em {areas_nomes.get(area_lacuna, area_lacuna)}")
        popular = min(1.0, colegas[item["id"]] / 5)
        if colegas[item["id"]]:
            motivos.append("Colegas também se interessaram")

        # já existe algo parecido na cidade?
        parecido, sim_max = None, 0.0
        for j, p in enumerate(projetos):
            if p.get("situacao") in ATIVOS_PARECIDOS:
                c = indice.cosseno(vp, vet_projeto[j])
                if c > sim_max:
                    sim_max, parecido = c, p
        penalidade = 0
        if parecido and sim_max >= LIMIAR_PARECIDO:
            penalidade = 15
            parecido = {"numero": parecido["numero"], "situacao": parecido["situacao"], "semelhanca": round(sim_max, 2)}
        else:
            parecido = None

        motivos.append(f"Evidência {item['evidencia']}")
        if item["custo"] == "baixo":
            motivos.append("Custo baixo para o município")

        if tem_sinal:
            base = 0.45 * afinidade + 0.25 * evidencia + 0.15 * lacuna + 0.15 * popular
        else:
            base = 0.5 * evidencia + 0.3 * lacuna + 0.2 * popular
        nota = max(0, min(100, round(100 * base) - penalidade))
        situacao = "proposto_por_voce" if item["id"] in minhas_origens else (
            "proposto_por_outro" if any(p.get("origem") == item["id"] and p.get("autor") != nome for p in projetos) else None)
        saida.append({**item, "nota": nota, "motivos": motivos, "situacao": situacao, "parecido": parecido,
                      "feedback": feedback.get(item["id"]), "colegas": colegas[item["id"]],
                      "partes": {"afinidade": round(afinidade, 2), "afinidadeTexto": round(af_texto, 2),
                                 "evidencia": evidencia, "lacuna": round(lacuna, 2), "colegas": round(popular, 2),
                                 "penalidade": penalidade}})
    ordem = {None: 0, "salvo": 0, "descartado": 2}
    saida.sort(key=lambda x: (ordem[x["feedback"]] + (1 if x["situacao"] == "proposto_por_voce" else 0), -x["nota"]))
    return saida
