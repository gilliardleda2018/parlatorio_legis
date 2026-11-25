"""Estatísticas das sessões arquivadas e relevância social dos projetos.

A nota de relevância é um apoio à priorização, não um juízo objetivo: vem da
ficha social preenchida pela secretaria (estimativas com fonte) e de pesos que
a própria casa ajusta. Cada nota sai com a decomposição, para ser explicável.
"""
import math
import time

AREAS = {
    "saude": "Saúde", "educacao": "Educação", "assistencia": "Assistência social", "seguranca": "Segurança",
    "transporte": "Transporte e mobilidade", "habitacao": "Habitação", "meio_ambiente": "Meio ambiente",
    "infraestrutura": "Infraestrutura urbana", "economia": "Economia e emprego", "cultura": "Cultura, esporte e lazer",
    "direitos": "Direitos e cidadania", "administracao": "Administração pública", "outros": "Outros",
}
VULNERAVEIS = {
    "criancas": "Crianças e adolescentes", "idosos": "Pessoas idosas", "pcd": "Pessoas com deficiência",
    "baixa_renda": "Baixa renda", "mulheres": "Mulheres", "pop_rua": "População em situação de rua",
    "povos": "Povos e comunidades tradicionais",
}
ABRANGENCIA = {"local": ("Bairro ou comunidade", 1 / 3), "regional": ("Região do município", 2 / 3),
               "municipal": ("Todo o município", 1.0)}
URGENCIA = {"baixa": ("Baixa", 0.2), "media": ("Média", 0.6), "alta": ("Alta", 1.0)}
PESOS_PADRAO = {"beneficiados": 40, "abrangencia": 20, "vulneraveis": 25, "urgencia": 15}


def relevancia(p: dict, populacao: int, pesos: dict) -> dict | None:
    """Nota 0–100 e a contribuição de cada critério. None se não há ficha social."""
    if not p.get("beneficiados") and not p.get("abrangencia"):
        return None
    pop = max(int(populacao or 0), 1000)
    comp = {
        "beneficiados": min(1.0, math.log10(int(p.get("beneficiados") or 0) + 1) / math.log10(pop + 1)),
        "abrangencia": ABRANGENCIA.get(p.get("abrangencia"), ("", 0))[1],
        "vulneraveis": min(1.0, len(p.get("vulneraveis") or []) / 3),
        "urgencia": URGENCIA.get(p.get("urgencia"), ("", 0))[1],
    }
    total_pesos = sum(max(0, float(pesos.get(k, 0))) for k in comp) or 1
    partes = {k: round(100 * max(0, float(pesos.get(k, 0))) * v / total_pesos, 1) for k, v in comp.items()}
    return {"nota": round(sum(partes.values())), "partes": partes}


def _mes(ms: int) -> str:
    return time.strftime("%Y-%m", time.localtime(ms / 1000))


def calcular(sessoes: list[dict], projetos: list[dict], parls_atuais: list[dict], populacao: int, pesos: dict,
             interno: bool = False) -> dict:
    """sessoes: retratos arquivados (mais antigo primeiro); projetos: banco de projetos."""
    nomes = {p["id"]: {"nome": p["nome"], "partido": p["partido"]} for s in sessoes for p in s["parls"]}
    nomes.update({p["id"]: {"nome": p["nome"], "partido": p["partido"]} for p in parls_atuais})
    por_parl = {pid: {"id": pid, **info, "membro": False, "sessoes": 0, "presencas": 0, "falas": 0, "tempoFala": 0,
                      "votos": 0, "sim": 0, "nao": 0, "abst": 0, "autoria": 0, "autoriaAprovada": 0,
                      "comMaioria": 0, "nominais": 0, "ocorrencias": 0}
                for pid, info in nomes.items()}
    for p in parls_atuais:
        por_parl[p["id"]]["membro"] = True
    por_partido: dict[str, dict] = {}
    por_mes: dict[str, dict] = {}
    lista_sessoes, ultimas_votacoes = [], []
    total_seg = total_pres = total_membros = 0
    votacoes = aprovadas = rejeitadas = 0

    for s in sessoes:
        dur = max(0, (s["fim"] - s["inicio"]) // 1000)
        total_seg += dur
        total_pres += len(s["presenca"])
        total_membros += len(s["parls"])
        lista_sessoes.append({"id": s["id"], "titulo": s["titulo"], "inicio": s["inicio"], "duracao": dur,
                              "presentes": len(s["presenca"]), "membros": len(s["parls"]), "falas": len(s["hist"]),
                              "votacoes": len(s["votacoes"])})
        for p in s["parls"]:
            por_parl[p["id"]]["sessoes"] += 1
            if p["id"] in s["presenca"]:
                por_parl[p["id"]]["presencas"] += 1
        for h in s["hist"]:
            alvo = por_parl.get(h["pid"])
            if alvo:
                alvo["falas"] += 1
                alvo["tempoFala"] += h["usado"]
            pp = por_partido.setdefault(h["partido"], {"partido": h["partido"], "falas": 0, "tempoFala": 0})
            pp["falas"] += 1
            pp["tempoFala"] += h["usado"]
        for o in s.get("ocorrencias", []):
            if o.get("pid") in por_parl and o["tipo"] in ("corte", "congelar", "tempo_esgotado"):
                por_parl[o["pid"]]["ocorrencias"] += 1
        for v in s["votacoes"]:
            votacoes += 1
            mes = por_mes.setdefault(_mes(v.get("fim") or s["fim"]), {"aprovada": 0, "rejeitada": 0, "outras": 0})
            if v["resultado"] == "aprovada":
                aprovadas += 1
                mes["aprovada"] += 1
            elif v["resultado"] == "rejeitada":
                rejeitadas += 1
                mes["rejeitada"] += 1
            else:
                mes["outras"] += 1
            maioria = "sim" if v["resultado"] == "aprovada" else "nao" if v["resultado"] == "rejeitada" else None
            for pid, voto in (v.get("votos") or {}).items():
                alvo = por_parl.get(pid)
                if not alvo:
                    continue
                alvo["votos"] += 1
                alvo[voto] = alvo.get(voto, 0) + 1
                if maioria and voto in ("sim", "nao"):
                    alvo["nominais"] += 1
                    alvo["comMaioria"] += voto == maioria
            ultimas_votacoes.append({
                "numero": v["numero"], "ementa": v["ementa"], "data": v.get("fim") or s["fim"], "sessao": s["titulo"],
                "tipo": v["tipo"], "quorum": v["quorum"], "contagem": v.get("contagem"), "resultado": v["resultado"],
                "votos": [{"pid": pid, "nome": nomes.get(pid, {}).get("nome", "?"), "partido": nomes.get(pid, {}).get("partido", ""),
                           "voto": x} for pid, x in (v.get("votos") or {}).items()],
            })

    # autoria (o campo autor do projeto é o nome do parlamentar)
    por_nome = {info["nome"]: pid for pid, info in nomes.items()}
    lista_proj = []
    por_area: dict[str, dict] = {}
    beneficiados_aprovados = 0
    for p in projetos:
        r = relevancia(p, populacao, pesos)
        aprovado = p.get("situacao") == "aprovada"
        pid_autor = por_nome.get(p.get("autor") or "")
        if pid_autor:
            por_parl[pid_autor]["autoria"] += 1
            por_parl[pid_autor]["autoriaAprovada"] += aprovado
        if aprovado:
            beneficiados_aprovados += int(p.get("beneficiados") or 0)
        for a in (p.get("areas") or ["outros"]):
            pa = por_area.setdefault(a, {"area": a, "nome": AREAS.get(a, a), "projetos": 0, "aprovados": 0,
                                         "beneficiadosAprovados": 0, "beneficiadosTramitacao": 0})
            pa["projetos"] += 1
            if aprovado:
                pa["aprovados"] += 1
                pa["beneficiadosAprovados"] += int(p.get("beneficiados") or 0)
            elif p.get("situacao") in ("em_tramitacao", "em_pauta", "adiada", "pendente", "em_discussao"):
                pa["beneficiadosTramitacao"] += int(p.get("beneficiados") or 0)
        item = {k: p.get(k) for k in ("id", "numero", "ementa", "autor", "areas", "beneficiados", "abrangencia",
                                       "vulneraveis", "urgencia", "fonte", "situacao", "historico", "criado",
                                       *(("origem", "justificativa", "instrumento") if interno else ()))}
        item["relevancia"] = r
        lista_proj.append(item)
    lista_proj.sort(key=lambda x: (x["relevancia"] is None, -(x["relevancia"] or {}).get("nota", 0), x["numero"]))

    parlamentares = []
    for a in por_parl.values():
        if not a["sessoes"] and not a["membro"]:
            continue
        a["presencaPct"] = round(100 * a["presencas"] / a["sessoes"]) if a["sessoes"] else None
        a["alinhamentoPct"] = round(100 * a["comMaioria"] / a["nominais"]) if a["nominais"] else None
        if not interno:
            for k in ("ocorrencias", "comMaioria", "nominais", "alinhamentoPct"):
                a.pop(k, None)
        parlamentares.append(a)
    parlamentares.sort(key=lambda a: a["nome"])

    return {
        "geradoEm": int(time.time() * 1000), "interno": interno, "populacao": populacao, "pesos": pesos,
        "areas": AREAS, "vulneraveisNomes": VULNERAVEIS,
        "abrangenciaNomes": {k: v[0] for k, v in ABRANGENCIA.items()}, "urgenciaNomes": {k: v[0] for k, v in URGENCIA.items()},
        "totais": {
            "sessoes": len(sessoes), "horas": round(total_seg / 3600, 1),
            "presencaMedia": round(100 * total_pres / total_membros) if total_membros else None,
            "votacoes": votacoes, "aprovadas": aprovadas, "rejeitadas": rejeitadas,
            "projetos": len(projetos), "beneficiadosAprovados": beneficiados_aprovados,
            "falas": sum(len(s["hist"]) for s in sessoes),
        },
        "sessoes": list(reversed(lista_sessoes)),
        "projetos": lista_proj,
        "porArea": sorted(por_area.values(), key=lambda a: -(a["beneficiadosAprovados"] + a["beneficiadosTramitacao"])),
        "porMes": [{"mes": m, **v} for m, v in sorted(por_mes.items())][-12:],
        "porPartido": sorted(por_partido.values(), key=lambda p: -p["tempoFala"]),
        "parlamentares": parlamentares,
        "votacoes": list(reversed(ultimas_votacoes))[:30],
    }
