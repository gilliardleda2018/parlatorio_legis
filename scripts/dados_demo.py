#!/usr/bin/env python3
"""Cria dados de DEMONSTRAÇÃO (fictícios) para apresentar os dashboards.

Uso: python3 scripts/dados_demo.py dados/demo.db
     PARLATORIO_BANCO=dados/demo.db python3 server/servidor.py

Gera um banco de projetos com ficha social e oito sessões arquivadas nos
últimos meses. Recusa-se a escrever num banco que já tenha sessões.
Não use em produção: os números são inventados.
"""
import json
import random
import sqlite3
import sys
import time
from pathlib import Path

if len(sys.argv) != 2:
    sys.exit(__doc__)
banco = Path(sys.argv[1])
random.seed(7)

PARLS = [("Ana Beatriz Moura", "PSD"), ("Carlos Eduardo Lima", "PT"), ("Fernanda Rocha", "MDB"), ("João Batista Neto", "PL"),
         ("Juliana Prado", "PSB"), ("Marcos Vinícius Teles", "UNIÃO"), ("Patrícia Albuquerque", "PSDB"), ("Ricardo Menezes", "PP"),
         ("Sandra Oliveira", "REPUBLICANOS"), ("Tiago Fontes", "PDT"), ("Vera Lúcia Campos", "PSOL"), ("Wagner Siqueira", "PODE"),
         ("Helena Castro", "REDE"), ("Otávio Brandão", "NOVO"), ("Luana Ferraz", "PCdoB")]
FONTE = "Estimativa de demonstração (dados fictícios)"
PROJETOS = [
    ("PL 112/2026", "Institui o programa municipal de hortas comunitárias em escolas e praças.", 2, ["meio_ambiente", "educacao"], 8000, "regional", ["criancas", "baixa_renda"], "media"),
    ("PL 118/2026", "Dispõe sobre a transparência dos contratos de transporte público.", 9, ["transporte", "administracao"], 62000, "municipal", [], "media"),
    ("PL 121/2026", "Cria a tarifa social de transporte para estudantes e pessoas de baixa renda.", 1, ["transporte", "assistencia"], 21000, "municipal", ["baixa_renda", "criancas"], "alta"),
    ("PL 124/2026", "Amplia o horário das unidades básicas de saúde até as 22h.", 4, ["saude"], 45000, "municipal", ["idosos", "baixa_renda"], "alta"),
    ("PL 127/2026", "Institui o programa de acolhimento de pessoas em situação de rua.", 10, ["assistencia", "direitos"], 650, "municipal", ["pop_rua"], "alta"),
    ("PL 130/2026", "Obriga calçadas acessíveis em novas obras públicas.", 6, ["infraestrutura", "direitos"], 9000, "municipal", ["pcd", "idosos"], "media"),
    ("PL 133/2026", "Cria o banco de alimentos municipal.", 14, ["assistencia"], 12000, "municipal", ["baixa_renda", "criancas"], "alta"),
    ("PL 136/2026", "Programa de reforço escolar no contraturno.", 0, ["educacao"], 7500, "municipal", ["criancas"], "media"),
    ("PL 139/2026", "Revitalização da praça central do bairro Jardim Esperança.", 3, ["cultura", "infraestrutura"], 4200, "local", [], "baixa"),
    ("PL 142/2026", "Iluminação pública em LED nas vias de acesso aos bairros periféricos.", 7, ["seguranca", "infraestrutura"], 30000, "regional", ["mulheres"], "media"),
    ("PL 145/2026", "Incentivo fiscal para primeiro emprego de jovens.", 13, ["economia"], 3000, "municipal", [], "media"),
    ("PL 148/2026", "Casa de acolhimento para mulheres vítimas de violência.", 12, ["direitos", "seguranca"], 400, "municipal", ["mulheres"], "alta"),
    ("PL 151/2026", "Denomina rua no loteamento Vila Nova.", 11, ["administracao"], 300, "local", [], "baixa"),
    ("PL 154/2026", "Programa de regularização fundiária de assentamentos urbanos.", 5, ["habitacao"], 5600, "regional", ["baixa_renda"], "media"),
    ("REQ 45/2026", "Requer informações sobre a manutenção das escolas municipais.", 10, ["educacao", "administracao"], 0, "", [], ""),
    ("PL 157/2026", "Ciclofaixas de lazer aos domingos nas avenidas principais.", 8, ["transporte", "cultura"], 15000, "regional", [], "baixa"),
]

agora = int(time.time() * 1000)
db = sqlite3.connect(banco)
db.executescript("""
create table if not exists estado (id integer primary key check (id = 1), json text not null);
create table if not exists sessoes (id text primary key, titulo text, inicio integer, fim integer, resumo text, json text);
create table if not exists gravacoes (id text primary key, sessao text, criado integer, json text);
create table if not exists projetos (id text primary key, atualizado integer, json text);
""")
if db.execute("select count(*) from sessoes").fetchone()[0]:
    sys.exit("Este banco já tem sessões arquivadas; escolha outro arquivo.")

hexid = lambda: "%08x" % random.getrandbits(32)
# blocos FICTÍCIOS desta câmara de demonstração
BLOCO = {"PSD": "situacao", "MDB": "situacao", "PL": "situacao", "UNIÃO": "situacao", "PP": "situacao", "REPUBLICANOS": "situacao",
         "PSDB": "situacao", "PT": "oposicao", "PSOL": "oposicao", "PCdoB": "oposicao", "PDT": "oposicao", "REDE": "oposicao",
         "PSB": "oposicao", "NOVO": "independente", "PODE": "independente"}
parls = [{"id": hexid(), "nome": n, "partido": p, "lider": False, "fotoV": 0, "canal": k + 1, "bloco": BLOCO[p]}
         for k, (n, p) in enumerate(PARLS)]
projetos = []
for k, (numero, ementa, autor, areas, benef, abr, vuln, urg) in enumerate(PROJETOS):
    projetos.append({"id": hexid(), "numero": numero, "ementa": ementa, "autor": PARLS[autor][0], "areas": areas,
                     "beneficiados": benef, "abrangencia": abr, "vulneraveis": vuln, "urgencia": urg,
                     "fonte": FONTE if benef else "", "situacao": "em_tramitacao", "criado": agora - (200 - k) * 86400000,
                     "historico": []})

FASES = [("Pequeno Expediente", 300), ("Grande Expediente", 900), ("Ordem do Dia — Discussão", 600), ("Explicação Pessoal", 300)]
fila_votar = [p for p in projetos if p["numero"] != "REQ 45/2026"]
random.shuffle(fila_votar)
for n in range(8):
    inicio = agora - (170 - n * 21) * 86400000 + 19 * 3600000
    fim = inicio + random.randint(110, 190) * 60000
    titulo = f"{37 + n}ª Sessão Ordinária"
    presentes = random.sample(parls, random.randint(9, 15))
    presenca = {p["id"]: inicio + random.randint(-10, 25) * 60000 for p in presentes}
    hist, t = [], inicio + 15 * 60000
    for _ in range(random.randint(5, 10)):
        p = random.choice(presentes)
        fase, prev = random.choice(FASES)
        usado = min(prev + 60, max(40, int(random.gauss(prev * 0.8, prev * 0.25))))
        hist.insert(0, {"id": hexid(), "pid": p["id"], "nome": p["nome"], "partido": p["partido"], "fase": "x", "faseNome": fase,
                        "materia": None, "previsto": prev, "bonus": 0, "extra": 0, "usado": usado, "inicio": t, "fim": t + usado * 1000,
                        "apartes": [], "cedentes": []})
        t += usado * 1000 + 60000
    pauta, votacoes = [], []
    for proj in [fila_votar.pop() for _ in range(random.choice([0, 1, 1, 2])) if fila_votar]:
        votos, cont = {}, {"sim": 0, "nao": 0, "abst": 0}
        governo = random.random() < 0.8  # posição da situação: a favor na maioria das vezes
        for p in presentes:
            if p["bloco"] == "situacao":
                segue = random.random() < 0.88
                quer = governo if segue else not governo
            elif p["bloco"] == "oposicao":
                segue = random.random() < 0.75
                quer = (not governo) if segue else governo
            else:
                quer = random.random() < 0.55
            x = "sim" if quer else random.choice(["nao", "nao", "nao", "abst"])
            votos[p["id"]] = x
            cont[x] += 1
        res = "aprovada" if cont["sim"] > cont["nao"] else "rejeitada"
        vfim = t + random.randint(5, 20) * 60000
        votacoes.insert(0, {"id": hexid(), "materia": proj["id"], "numero": proj["numero"], "ementa": proj["ementa"], "tipo": "nominal",
                            "quorum": "simples", "aberta": False, "inicio": vfim - 300000, "fim": vfim, "votos": votos,
                            "votantes": list(votos), "contagem": cont, "resultado": res, "membros": 15, "presentes": len(presentes)})
        pauta.append({"id": proj["id"], "numero": proj["numero"], "ementa": proj["ementa"], "autor": proj["autor"], "situacao": res})
        proj["situacao"] = res
        proj["historico"] += [{"ts": vfim, "sessao": titulo, "evento": "votacao", "tipo": "nominal", "resultado": res, "contagem": cont},
                              {"ts": fim, "sessao": titulo, "evento": "pauta", "situacao": res}]
    ocorr = []
    if random.random() < 0.5:
        p = random.choice(presentes)
        ocorr.append({"id": hexid(), "ts": inicio + 3600000, "tipo": random.choice(["tempo_esgotado", "corte"]), "pid": p["id"],
                      "nome": p["nome"], "motivo": "Excedeu o tempo regimental", "materia": None})
    r = {"id": hexid() + hexid(), "titulo": titulo, "casa": "Câmara Municipal", "inicio": inicio, "fim": fim,
         "parls": [{k: p[k] for k in ("id", "nome", "partido", "lider")} for p in parls], "presenca": presenca, "pauta": pauta,
         "hist": hist, "votacoes": votacoes, "ocorrencias": ocorr, "gravacoes": []}
    resumo = {"falas": len(hist), "votacoes": len(votacoes), "presentes": len(presenca), "membros": 15, "gravacoes": 0, "ocorrencias": len(ocorr)}
    db.execute("insert into sessoes values (?, ?, ?, ?, ?, ?)", (r["id"], titulo, inicio, fim, json.dumps(resumo), json.dumps(r, ensure_ascii=False)))

for p in projetos:
    p["atualizado"] = agora
    db.execute("insert or replace into projetos values (?, ?, ?)", (p["id"], agora, json.dumps(p, ensure_ascii=False)))
db.commit()
db.close()

# estado inicial do servidor com os mesmos parlamentares das sessões (PIN 0000)
import os  # noqa: E402
os.environ["PARLATORIO_BANCO"] = str(banco)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import servidor  # noqa: E402  (cria o estado inicial neste banco)
servidor.S["parls"] = parls
servidor.S["_pins"] = {p["id"]: servidor.seguranca.hash_pin("0000") for p in parls}
servidor.S["casa"]["titulo"] = "45ª Sessão Ordinária"
servidor.S["pauta"] = []
servidor.salvar()
print(f"Demonstração criada em {banco}: {len(projetos)} projetos e 8 sessões arquivadas.")
