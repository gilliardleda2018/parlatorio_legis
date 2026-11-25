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
BLOCOS = {"situacao": "Situação", "oposicao": "Oposição", "independente": "Independente", "": "Sem bloco"}
# índice de atuação: mede QUANTIDADE de atividade registrada, não a qualidade do mandato
PESOS_ATUACAO = {"presenca": 25, "debates": 25, "producao": 25, "votacoes": 25}


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
             interno: bool = False, blocos: dict | None = None) -> dict:
    """sessoes: retratos arquivados (mais antigo primeiro); projetos: banco de projetos."""
    nomes = {p["id"]: {"nome": p["nome"], "partido": p["partido"]} for s in sessoes for p in s["parls"]}
    nomes.update({p["id"]: {"nome": p["nome"], "partido": p["partido"], "fotoV": p.get("fotoV", 0)} for p in parls_atuais})
    por_parl = {pid: {"id": pid, **info, "membro": False, "sessoes": 0, "presencas": 0, "falas": 0, "tempoFala": 0,
                      "votos": 0, "sim": 0, "nao": 0, "abst": 0, "autoria": 0, "autoriaAprovada": 0,
                      "comMaioria": 0, "nominais": 0, "ocorrencias": 0, "nominaisPossiveis": 0}
                for pid, info in nomes.items()}
    for p in parls_atuais:
        por_parl[p["id"]]["membro"] = True
    por_partido: dict[str, dict] = {}
    por_mes: dict[str, dict] = {}
    lista_sessoes, ultimas_votacoes = [], []
    total_seg = total_pres = total_membros = 0
    votacoes = aprovadas = rejeitadas = 0

    for s in sessoes:
        dur = 0 if s.get("semHorario") or s.get("virtual") else max(0, (s["fim"] - s["inicio"]) // 1000)
        total_seg += dur
        total_pres += len(s["presenca"])
        total_membros += len(s["parls"])
        lista_sessoes.append({"id": s["id"], "titulo": s["titulo"], "inicio": s["inicio"], "duracao": dur,
                              "semHorario": bool(s.get("semHorario") or s.get("virtual")), "virtual": bool(s.get("virtual")),
                              "fonte": s.get("fonte"),
                              "presentes": len(s["presenca"]), "membros": len(s["parls"]), "falas": len(s["hist"]),
                              "votacoes": len(s["votacoes"]), "pauta": len(s.get("pauta") or [])})
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
            if v.get("tipo") == "nominal":
                for p in s["parls"]:
                    por_parl[p["id"]]["nominaisPossiveis"] += 1
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
                                       "vulneraveis", "urgencia", "fonte", "situacao", "historico", "criado", "saplUrl", "textoUrl",
                                       *(("origem", "justificativa", "instrumento") if interno else ()))}
        item["relevancia"] = r
        lista_proj.append(item)
    lista_proj.sort(key=lambda x: (x["relevancia"] is None, -(x["relevancia"] or {}).get("nota", 0), x["numero"]))

    blocos = blocos or {}
    parlamentares = []
    for a in por_parl.values():
        if not a["sessoes"] and not a["membro"]:
            continue
        a["presencaPct"] = round(100 * a["presencas"] / a["sessoes"]) if a["sessoes"] else None
        a["alinhamentoPct"] = round(100 * a["comMaioria"] / a["nominais"]) if a["nominais"] else None
        a["bloco"] = blocos.get(a["id"], "")
        if not interno:
            for k in ("ocorrencias", "comMaioria", "nominais", "alinhamentoPct"):
                a.pop(k, None)
        parlamentares.append(a)
    parlamentares.sort(key=lambda a: a["nome"])
    pesos_atuacao = _atuacao(parlamentares)
    situacao_oposicao = _blocos(parlamentares, ultimas_votacoes, interno)

    return {
        "geradoEm": int(time.time() * 1000), "interno": interno, "populacao": populacao, "pesos": pesos,
        "areas": AREAS, "vulneraveisNomes": VULNERAVEIS,
        "abrangenciaNomes": {k: v[0] for k, v in ABRANGENCIA.items()}, "urgenciaNomes": {k: v[0] for k, v in URGENCIA.items()},
        "totais": {
            "sessoes": len(sessoes), "horas": round(total_seg / 3600, 1) if total_seg else None,
            "materiasEmPauta": sum(len(s.get("pauta") or []) for s in sessoes),
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
        "blocosNomes": BLOCOS, "pesosAtuacao": pesos_atuacao,
        "maisAtuantes": [{"id": a["id"], "nome": a["nome"], "partido": a["partido"], "bloco": a["bloco"], "membro": a["membro"],
                          **a["atuacao"]}
                         for a in sorted((a for a in parlamentares if a.get("atuacao")), key=lambda a: -a["atuacao"]["nota"])],
        **situacao_oposicao,
    }


def _atuacao(parlamentares: list[dict]) -> dict:
    """Índice 0–100: presença, falas por sessão, projetos (aprovados valem 3) e participação nas votações nominais.
    Componente que a fonte não registra para ninguém (ex.: sessões importadas sem falas nem votos nominais)
    sai do cálculo e o peso é redistribuído; devolve os pesos efetivamente usados."""
    ativos = [a for a in parlamentares if a["sessoes"]]
    falas = {a["id"]: a["falas"] / max(a["presencas"], 1) for a in ativos}
    producao = {a["id"]: a["autoria"] + 2 * a["autoriaAprovada"] for a in ativos}
    max_falas = max(falas.values(), default=0)
    max_prod = max(producao.values(), default=0)
    medidos = {"presenca": bool(ativos), "debates": max_falas > 0, "producao": max_prod > 0,
               "votacoes": any(a["nominaisPossiveis"] for a in ativos)}
    soma = sum(PESOS_ATUACAO[k] for k, ok in medidos.items() if ok) or 1
    pesos = {k: round(100 * PESOS_ATUACAO[k] / soma, 1) if ok else 0 for k, ok in medidos.items()}
    for a in parlamentares:
        if not a["sessoes"]:
            a["atuacao"] = None
            continue
        comp = {"presenca": (a["presencaPct"] or 0) / 100, "debates": falas[a["id"]] / max_falas if max_falas else 0.0,
                "producao": producao[a["id"]] / max_prod if max_prod else 0.0,
                "votacoes": min(1.0, a["votos"] / a["nominaisPossiveis"]) if a["nominaisPossiveis"] else 0.0}
        partes = {k: round(pesos[k] * v, 1) for k, v in comp.items()}
        a["atuacao"] = {"nota": round(sum(partes.values())), "partes": partes}
    return pesos


def _blocos(parlamentares: list[dict], votacoes: list[dict], interno: bool) -> dict:
    """Situação × oposição: números por bloco, coesão, votações que dividiram os blocos e quem venceu."""
    bloco_de = {a["id"]: a["bloco"] for a in parlamentares}
    stats = {b: {"bloco": b, "nome": nome, "membros": 0, "presencaSoma": 0, "presencaN": 0, "falas": 0, "tempoFala": 0,
                 "autoria": 0, "autoriaAprovada": 0, "coesaoSoma": 0.0, "coesaoN": 0}
             for b, nome in BLOCOS.items()}
    for a in parlamentares:
        if not a["membro"]:
            continue
        st = stats[a["bloco"] if a["bloco"] in stats else ""]
        st["membros"] += 1
        st["falas"] += a["falas"]
        st["tempoFala"] += a["tempoFala"]
        st["autoria"] += a["autoria"]
        st["autoriaAprovada"] += a["autoriaAprovada"]
        if a["presencaPct"] is not None:
            st["presencaSoma"] += a["presencaPct"]
            st["presencaN"] += 1
    com_situacao: dict[str, list[int]] = {}
    divididas, vitorias = [], {"situacao": 0, "oposicao": 0}
    for v in votacoes:
        if not v["votos"]:
            continue
        contagem: dict[str, dict] = {}
        for x in v["votos"]:
            c = contagem.setdefault(bloco_de.get(x["pid"], ""), {"sim": 0, "nao": 0, "abst": 0})
            c[x["voto"]] = c.get(x["voto"], 0) + 1
        posicao = {}
        for b, c in contagem.items():
            decididos = c["sim"] + c["nao"]
            posicao[b] = "sim" if c["sim"] > c["nao"] else "nao" if c["nao"] > c["sim"] else None
            if decididos and b in stats:
                stats[b]["coesaoSoma"] += max(c["sim"], c["nao"]) / decididos
                stats[b]["coesaoN"] += 1
        sit = posicao.get("situacao")
        if sit:
            for x in v["votos"]:
                if x["voto"] in ("sim", "nao"):
                    par = com_situacao.setdefault(x["pid"], [0, 0])
                    par[0] += x["voto"] == sit
                    par[1] += 1
        opo = posicao.get("oposicao")
        if sit and opo and sit != opo and v["resultado"] in ("aprovada", "rejeitada"):
            venceu = "situacao" if (v["resultado"] == "aprovada") == (sit == "sim") else "oposicao"
            vitorias[venceu] += 1
            divididas.append({"numero": v["numero"], "ementa": v["ementa"], "data": v["data"], "resultado": v["resultado"],
                              "venceu": venceu, "placar": contagem, "posicao": {"situacao": sit, "oposicao": opo}})
    for a in parlamentares:
        par = com_situacao.get(a["id"])
        a["comSituacaoPct"] = round(100 * par[0] / par[1]) if par and par[1] else None
        if interno and par and par[1] >= 3:  # sugestão para quem classifica os blocos (só na visão interna)
            pct = par[0] / par[1]
            a["blocoSugerido"] = "situacao" if pct >= 0.7 else "oposicao" if pct <= 0.3 else "independente"
    lista = []
    for st in stats.values():
        if not st["membros"]:
            continue
        lista.append({"bloco": st["bloco"], "nome": st["nome"], "membros": st["membros"],
                      "presencaMedia": round(st["presencaSoma"] / st["presencaN"]) if st["presencaN"] else None,
                      "falas": st["falas"], "tempoFala": st["tempoFala"], "autoria": st["autoria"],
                      "autoriaAprovada": st["autoriaAprovada"],
                      "coesao": round(100 * st["coesaoSoma"] / st["coesaoN"]) if st["coesaoN"] else None})
    return {"blocos": lista, "votacoesDivididas": list(reversed(divididas))[:20], "vitoriasBlocos": vitorias,
            "temBlocos": any(b["bloco"] in ("situacao", "oposicao") for b in lista)}
