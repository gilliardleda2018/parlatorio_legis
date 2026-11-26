"""Rascunho da ata por item da pauta, a partir das transcrições, das falas registradas e das votações.

Com IA: um parágrafo por item (pontos discutidos, posições, encaminhamentos, resultado).
Sem IA: resumo objetivo montado com os próprios registros (quem falou, por quanto tempo, resultado).
Sempre é rascunho: a secretaria revisa e marca cada item como revisado antes da ata oficial.
"""
import time

import ia

SISTEMA = ("Você redige atas de sessões de Câmara Municipal no Brasil. Escreva em português formal, terceira "
           "pessoa, pretérito, de forma impessoal e fiel. Use SOMENTE o material fornecido: não invente falas, "
           "números, nomes, votos ou decisões. Um único parágrafo de 3 a 7 frases: quem se pronunciou e o "
           "essencial do que defendeu, encaminhamentos e, se houver, o resultado da votação. Sem títulos, sem "
           "listas, sem comentários sobre o texto.")

VOTOS = {"aprovada": "aprovada", "rejeitada": "rejeitada", "sem_quorum": "sem quórum", "adiada": "adiada",
         "retirada": "retirada", "destacado": "destacada para o plenário"}


def _min(seg) -> str:
    seg = int(seg or 0)
    return f"{seg // 60} min {seg % 60:02d} s" if seg >= 60 else f"{seg} s"


def _hora(ms) -> str:
    return time.strftime("%H:%M", time.localtime((ms or 0) / 1000))


def blocos(r: dict) -> list[dict]:
    """Agrupa falas, transcrições e votações por matéria; o que não tem matéria vai para o Expediente."""
    grav = {}
    for g in r.get("gravacoes") or []:
        t = (g.get("transcricao") or {}).get("texto")
        if t:
            grav.setdefault((g["pid"], g.get("materia")), []).append(t)
    itens = [{"chave": m["id"], "titulo": f"{m.get('numero') or 'Matéria'} — {m.get('ementa', '')}", "materia": m}
             for m in r.get("pauta") or []]
    itens.append({"chave": "expediente", "titulo": "Expediente e pronunciamentos", "materia": None})
    for b in itens:
        mid = b["materia"]["id"] if b["materia"] else None
        b["falas"] = [{"nome": h["nome"], "partido": h["partido"], "fase": h.get("faseNome", ""), "hora": _hora(h["inicio"]),
                       "tempo": _min(h.get("usado")), "texto": " ".join(grav.get((h["pid"], h.get("materia")), []))[:6000]}
                      for h in sorted(r.get("hist") or [], key=lambda h: h["inicio"]) if h.get("materia") == mid]
        b["votacoes"] = [v for v in r.get("votacoes") or [] if mid and v.get("materia") == mid]
    return [b for b in itens if b["falas"] or b["votacoes"]]


def _placar(v: dict) -> str:
    c = v.get("contagem") or {}
    placar = f" ({c.get('sim', 0)} sim, {c.get('nao', 0)} não, {c.get('abst', 0)} abstenções)" if c and v.get("tipo") != "simbolica" else ""
    return f"votação {v.get('tipo', '')}: {VOTOS.get(v.get('resultado'), v.get('resultado') or 'sem resultado')}{placar}"


def extrativo(b: dict) -> str:
    partes = []
    if b["falas"]:
        partes.append("Fizeram uso da palavra: " + "; ".join(f"{f['nome']} ({f['partido']}), às {f['hora']}, por {f['tempo']}"
                                                                for f in b["falas"]) + ".")
    for v in b["votacoes"]:
        partes.append(f"Submetida a {_placar(v)}.")
    return " ".join(partes)


def resumir_bloco(b: dict, casa: str, titulo_sessao: str) -> tuple[str, str]:
    """(texto, motor). Cai no resumo objetivo se a IA estiver desligada ou falhar."""
    if not ia.ligada():
        return extrativo(b), "registros"
    material = [f"# {casa} — {titulo_sessao}", f"Item: {b['titulo']}"]
    for f in b["falas"]:
        material.append(f"- {f['nome']} ({f['partido']}), {f['fase']}, {f['hora']}, {f['tempo']}: "
                        + (f["texto"] or "(sem transcrição)"))
    for v in b["votacoes"]:
        material.append(f"- Resultado: {_placar(v)}")
    try:
        return ia.gerar(SISTEMA, "\n".join(material), 700), ia.descricao()
    except ia.SemIA:
        return extrativo(b), "registros"


def gerar(r: dict, casa: str, ao_avancar=None) -> dict:
    itens = []
    lista = blocos(r)
    for n, b in enumerate(lista, 1):
        texto, motor = resumir_bloco(b, casa, r.get("titulo", ""))
        itens.append({"chave": b["chave"], "titulo": b["titulo"], "texto": texto, "motor": motor, "revisado": False})
        if ao_avancar:
            ao_avancar(n, len(lista))
    return {"gerado": int(time.time() * 1000), "itens": itens}
