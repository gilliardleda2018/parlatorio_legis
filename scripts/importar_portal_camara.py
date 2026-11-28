#!/usr/bin/env python3
"""Importa vereadores e matérias do portal legislativo da câmara (plataforma legislativomunicipal.com).

Uso: python3 scripts/importar_portal_camara.py https://www.cmtuntum.legislativomunicipal.com dados/tuntum.db

Lê as páginas públicas do portal (sem login) e grava no banco do Parlatório Legis:
  * vereadores: nome civil, cargo na Mesa, partido atual e os números oficiais do portal
    (matérias e presença). Quem já existe no banco (ex.: importado do TSE) é reconhecido
    pelo partido e pelos nomes; suplentes e ex-vereadores entram como ex-membros;
  * matérias de autoria de cada vereador (requerimentos, projetos de lei, resoluções,
    indicações, moções): tipo, número, data, ementa, fase e situação, com link para o portal.

As áreas temáticas são sugeridas por palavras-chave da ementa e ficam marcadas como
classificação automática. Beneficiados, abrangência e urgência NÃO existem na fonte e
ficam em branco para a secretaria preencher. Pare o servidor antes de rodar.
"""
import html
import http.cookiejar
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path

AGENTE = "ParlatorioLegis/2 (+https://github.com/gilliardleda2018/parlatorio_legis)"
PAUSA = 0.7
TIPOS = [("REQUERIMENTO", "REQ"), ("PROJETO DE LEI COMPLEMENTAR", "PLC"), ("PROJETO DE LEI", "PL"),
         ("PROJETO DE RESOLU", "PR"), ("PROJETO DE DECRETO", "PDL"), ("INDICA", "IND"), ("MO", "MOC"),
         ("EMENDA", "EMD"), ("VETO", "VET"), ("MEDIDA PROVIS", "MP")]
IGNORAR = ("ATA", "OFICIO", "PARECER")  # itens de pauta que não são proposições (não vão para o banco de projetos)
CONHECIDOS = r"(REQUERIMENTO[\w\s-]*|PROJETO DE[\w\s-]*|INDICA[\wÇÃ]*[\w\s-]*|MO[CÇ][AÃ]O[\w\s-]*|EMENDA[\w\s-]*|VETO[\w\s-]*|MEDIDA PROVIS[\wÓ]*|PARECER[\w\s-]*|OF[IÍ]CIO[\w\s-]*|(?:VOTA[CÇ][AÃ]O DA )?ATA)"


def tipo_limpo(titulo: str) -> str:
    """Remove texto da página que grudou antes do tipo (ex.: "ORDEM DO DIA VOTAÇÃO DA ATA: 5/2026")."""
    tipo, sep, num = titulo.partition(":")
    achados = re.findall(CONHECIDOS, tipo.upper())
    return (achados[-1].strip() if achados else tipo.strip()) + sep + num


def ignorar(titulo: str) -> bool:
    tipo = sem_acento(tipo_limpo(titulo).partition(":")[0]).upper()
    return any(tipo.startswith(x) or tipo.endswith(" " + x) or tipo == x for x in IGNORAR)
SITUACOES = {"aprovad": "aprovada", "rejeitad": "rejeitada", "arquivad": "arquivado", "retirad": "retirada",
             "tramita": "em_tramitacao", "promulg": "aprovada", "sancion": "aprovada"}
# palavras-chave → área (sugestão; a secretaria confirma na ficha social)
AREAS = [
    ("saude", r"\b(saude|ubs|hospital|posto de saude|medic|agentes comunitarios|endemias|vacina|farmacia|ambulancia)"),
    ("educacao", r"\b(escola|ensino|educa|creche|professor|aluno|biblioteca|transporte escolar)"),
    ("infraestrutura", r"\b(pavimenta|asfalt|estrada|vicinal|sarjeta|calcament|ponte|bueiro|iluminacao|drenagem|recupera.{0,12}(via|estrada|rua)|terraplanagem|meio-fio)"),
    ("transporte", r"\b(quebra-mola|redutor de velocidade|transito|sinaliza|onibus|transporte)"),
    ("meio_ambiente", r"\b(poco artesiano|agua potavel|abastecimento de agua|saneamento|caema|esgoto|lixo|residuo|arboriza|ambiental)"),
    ("cultura", r"\b(campo de futebol|esporte|quadra|cultura|lazer|praca|festival|carate|artes marciais|ginasio)"),
    ("assistencia", r"\b(assistencia social|cras|creas|cesta basica|vulnerab|idoso|pessoa com deficiencia|crianca)"),
    ("seguranca", r"\b(seguranca|policia|guarda|violencia|videomonitora)"),
    ("habitacao", r"\b(habitac|moradia|regulariza.{0,10}fundiaria|loteamento)"),
    ("economia", r"\b(emprego|feira|agricultor|produtor|comercio|empreend)"),
    ("administracao", r"\b(titulo de cidada|cidadao honorario|cidada honoraria|denomina|homenagem|utilidade publica|regimento)"),
]


_navegador = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def baixar(url: str) -> str:
    """O portal guarda o filtro da listagem em cookie: a paginação só funciona na mesma sessão."""
    time.sleep(PAUSA)
    req = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    with _navegador.open(req, timeout=40) as r:
        return r.read(6_000_000).decode("utf-8", errors="replace")


def texto(fragmento: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragmento))).strip()


def sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").lower()


def frase(t: str) -> str:
    """EMENTA EM MAIÚSCULAS → Ementa em maiúsculas (só a primeira letra; mantém o resto se já vier misto)."""
    t = t.strip()
    return t[:1].upper() + t[1:].lower() if t and t == t.upper() else t


def _data(d: str) -> tuple[int, int, int]:
    dia, mes, ano = d.split("/")
    return int(ano), int(mes), int(dia)


def _mandatos_novos(corpo: str, hoje: tuple) -> dict | None:
    """Layout novo do portal (ex.: Barra do Corda): tabela "Informações dos mandatos" (cargo, vínculo, legislatura e
    período) e "Filiação partidária" (período e sigla). Em exercício = um mandato cujo período cobre hoje: suplente
    que já saiu tem data de fim, e o mesmo vereador aparece de novo em cada legislatura antiga."""
    a = corpo.find("Informações dos mandatos")
    if a < 0:
        return None
    b = corpo.find("Filiação partidária", a)
    trecho = corpo[a:b if b > 0 else a + 4000]
    linhas = re.findall(r"([A-ZÇÃÁÉÍÓÚÂÊÔ()º°ª \-]+?)\s+\d+ª \([^)]*\) Legislatura \((\d{4}) - (\d{4})\)\s+"
                        r"(\d{2}/\d{2}/\d{4})(?:\s+à\s+(\d{2}/\d{2}/\d{4}))?", trecho)
    atual = next((cargo for cargo, _, _, ini, fim in linhas
                  if _data(ini) <= hoje and (not fim or _data(fim) >= hoje)), None)
    cargo = ""
    if atual:
        for chave, nome in (("VICE", "Vice-Presidente"), ("PRESIDENTE", "Presidente"), ("SECRET", "Secretário")):
            if chave in atual.upper():
                cargo = nome
                break
    partido = ""
    if b > 0:
        filiacoes = re.findall(r"(\d{2}/\d{2}/\d{4}) à (\d{2}/\d{2}/\d{4})\s+(\S+(?: do B)?)", corpo[b:b + 1500])
        vigente = [f for f in filiacoes if _data(f[0]) <= hoje <= _data(f[1])]
        if vigente or filiacoes:
            partido = (vigente or filiacoes)[-1][2]   # sigla como o partido escreve (ex.: "PT do B")
    return {"emExercicio": atual is not None, "cargo": cargo, "partido": partido}


def ler_vereador(base: str, vid: int) -> dict | None:
    t = baixar(f"{base}/vereadores/{vid}")
    corpo = texto(re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S))
    m = re.search(r"Detalhe (.+?) Partido:\s*(\S*)", corpo)
    if not m:
        return None
    og = re.search(r'og:description" content="[^:]*:\s*([^"]+)"', t)
    civil = " ".join(html.unescape(og.group(1)).split()) if og else ""
    trecho = m.group(1)
    # o nome civil pode vir em maiúsculas no og e misto na página: procura sem diferenciar caixa nem espaços
    achado = re.search(r"\s+".join(map(re.escape, civil.split())), trecho, re.I) if civil else None
    curto = trecho[:achado.start()].strip() if achado and achado.start() > 0 else trecho.split("  ")[0].strip()
    if achado:
        civil = achado.group(0)   # a grafia da página (Aurean de Lima Barbalho), não a do og (AUREAN DE LIMA BARBALHO)
    resto = trecho[achado.end():].strip() if achado else trecho
    curto = re.sub(r"\s*(Vereador\(A\)|Presidente|Vice-Presidente|\d[ºª] Secretário)\s*-?\s*$", "", curto).strip() or civil
    # layout antigo (ex.: Tuntum): "Vereador(A) - Vereador Em Exercício Partido: PSB"
    cargo = re.search(r"(Presidente|Vice-Presidente|\d[ºª] Secretário|Vereador\(A\))\s*-\s*([^|]*?)$", resto)
    partido = m.group(2) if m.group(2) and m.group(2) not in ("Produção", "Comissões", "Mandatos", "Filiação") else ""
    em_exercicio = bool(cargo and cargo.group(2).strip())
    cargo_atual = cargo.group(1).replace("Vereador(A)", "").strip() if cargo else ""
    novo = _mandatos_novos(corpo, time.localtime()[:3])
    if novo is not None:   # layout novo (ex.: Barra do Corda): a tabela de mandatos decide; o resto completa
        em_exercicio = novo["emExercicio"]
        cargo_atual = novo["cargo"] or (cargo_atual if em_exercicio else "")
        partido = novo["partido"] or partido
    presenca = re.search(r"Presente em (\d+) sessões, representando ([\d,]+)%", corpo)
    materias = re.search(r"Quantidade de (\d+) matérias", corpo)
    return {"portalId": vid, "curto": curto, "civil": civil or curto, "partido": partido,
            "cargo": cargo_atual, "emExercicio": em_exercicio, "url": f"{base}/vereadores/{vid}",
            "presencas": int(presenca.group(1)) if presenca else None,
            "presencaPct": float(presenca.group(2).replace(",", ".")) if presenca else None,
            "materias": int(materias.group(1)) if materias else None}


def ler_materias(base: str, vid: int) -> list[dict]:
    vistas, saida, pagina = set(), [], 0
    while pagina < 30:
        t = baixar(f"{base}/materias?origem=V-{vid}" + (f"&pagina={pagina}" if pagina else ""))
        novos = 0
        for bloco in t.split("card-list-novo view-desktop")[1:]:
            mid = re.search(r'href="materias/(\d+)"', bloco)
            titulo = re.search(r"<strong[^>]*>\s*([^<]+?)\s*</strong>", bloco)
            if not mid or not titulo or mid.group(1) in vistas or ignorar(texto(titulo.group(1))):
                continue
            vistas.add(mid.group(1))
            novos += 1
            data = re.search(r"bi-calendar[^>]*></i>\s*([\d/]+)", bloco)
            ementa = re.search(r'card-text-novo[^>]*>(.*?)</p>', bloco, re.S)
            campo = lambda nome: (re.search(rf"<strong>\s*{nome}s?:\s*</strong>(.*?)</p>", bloco, re.S) or [None, ""])[1]
            saida.append({"portalId": int(mid.group(1)), "titulo": texto(titulo.group(1)),
                          "data": data.group(1) if data else "", "ementa": texto(ementa.group(1)) if ementa else "",
                          "fase": texto(campo("Fase")), "situacao": texto(campo("Situação")),
                          "autores": texto(campo("Autor")), "url": f"{base}/materias/{mid.group(1)}"})
        if not novos:
            break
        pagina += 1
    return saida


def ler_sessoes(base: str) -> list[dict]:
    """Sessões ENCERRADAS do portal: chamada nominal (presença) e matérias da pauta com a situação."""
    t = baixar(f"{base}/sessoes")
    ids = sorted({int(x) for x in re.findall(r'sessoes/(\d+)', t)})
    saida = []
    for sid in ids:
        pagina = baixar(f"{base}/sessoes/{sid}")
        corpo = texto(re.sub(r"<script.*?</script>|<style.*?</style>", " ", pagina, flags=re.S))
        cab = re.search(r"([A-ZÁÉÍÓÚÂÊÔÃÕÇ]+):\s*(\d+)/(\d{4}) Informações da sessão Data:\s*([\d/]+) Status:\s*(\w+)", corpo)
        if not cab or cab.group(5).upper() != "ENCERRADA":
            continue
        chamada = re.findall(r"Nome:\s*(.+?)\s+Cargo:\s*(.+?)\s+Chamada:\s*(PRESENTE|AUSENTE|JUSTIFICAD\w*)", corpo)
        itens = []
        for bloco in re.split(r'(?=<a[^>]+href="/\.\./materias/\d+")', pagina)[1:]:
            mid = re.search(r'materias/(\d+)', bloco)
            if mid and int(mid.group(1)) not in {i["portalId"] for i in itens}:
                itens.append({"portalId": int(mid.group(1))})
        # texto de cada matéria da pauta (mesma ordem dos links)
        cards = re.findall(r"((?:[A-ZÁÉÍÓÚÂÊÔÃÕÇ\- ]+) Nº ?[\d/]+) ([\d/]{10}) (.+?) Situação:\s*(.+?) Autor:\s*(.+?) Acessar", corpo)
        for item, c in zip(itens, cards):
            item.update(titulo=tipo_limpo(c[0].replace(" Nº", ":").strip()), data=c[1], ementa=c[2].strip(), situacao=c[3].strip(),
                        autores=c[4].strip(), url=f"{base}/materias/{item['portalId']}")
        saida.append({"portalId": sid, "tipo": cab.group(1).title(), "numero": int(cab.group(2)), "ano": int(cab.group(3)),
                      "data": cab.group(4), "chamada": [{"civil": n.strip(), "cargo": c.strip(), "presente": p == "PRESENTE"}
                                                        for n, c, p in chamada],
                      "itens": [i for i in itens if "titulo" in i], "url": f"{base}/sessoes/{sid}"})
        print(f"  sessão {cab.group(2)}/{cab.group(3)} ({cab.group(4)}): {sum(1 for x in chamada if x[2] == 'PRESENTE')}/{len(chamada)} presentes, {len(saida[-1]['itens'])} matérias")
    return saida


def numero(titulo: str) -> str:
    tipo, _, num = tipo_limpo(titulo).partition(":")
    sigla = next((s for chave, s in TIPOS if sem_acento(tipo).upper().startswith(sem_acento(chave).upper())), "MAT")
    return f"{sigla} {num.strip()}"


def situacao(s: str) -> str:
    s = sem_acento(s)
    return next((v for k, v in SITUACOES.items() if k in s), "em_tramitacao")


def areas(ementa: str) -> list[str]:
    e = sem_acento(ementa)
    return [a for a, padrao in AREAS if re.search(padrao, e)][:3]


def casar(portal: list[dict], existentes: list[dict]) -> dict[int, str]:
    """Liga cada vereador do portal a um parlamentar já cadastrado (partido igual + nomes em comum)."""
    stop = {"de", "da", "do", "das", "dos", "e", "dr", "silva"}
    tok = lambda n: {w for w in re.findall(r"[a-z]+", sem_acento(n)) if w not in stop and len(w) > 2}
    pares = []
    for v in portal:
        for p in existentes:
            if v["partido"] and p["partido"] and v["partido"] != p["partido"]:
                continue
            comum = tok(v["civil"] + " " + v["curto"]) & tok(p["nome"])
            pares.append((len(comum), v["portalId"], p["id"]))
    pares.sort(reverse=True)
    ligados, usados_v, usados_p = {}, set(), set()
    for n, vid, pid in pares:
        if n and vid not in usados_v and pid not in usados_p:
            ligados[vid], usados_v, usados_p = pid, usados_v | {vid}, usados_p | {pid}
    # sobrou um só de cada partido? liga (ex.: apelido sem nada em comum com o nome civil)
    for v in portal:
        if v["portalId"] in ligados or not v["emExercicio"]:
            continue
        cand = [p for p in existentes if p["id"] not in usados_p and p["partido"] == v["partido"]]
        if len(cand) == 1:
            ligados[v["portalId"]] = cand[0]["id"]
            usados_p.add(cand[0]["id"])
    return ligados


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    base, banco = sys.argv[1].rstrip("/"), Path(sys.argv[2])
    if not banco.exists():
        sys.exit(f"{banco} não existe. Crie antes (ex.: scripts/tse_eleitos.py).")

    lista = baixar(f"{base}/vereadores")
    ids = sorted({int(x) for x in re.findall(r'href="/vereadores/(\d+)"', lista)})
    print(f"Portal: {len(ids)} vereadores listados")
    vereadores = [v for v in (ler_vereador(base, i) for i in ids) if v]
    for v in vereadores:
        v["lista"] = ler_materias(base, v["portalId"])
        print(f"  {v['curto']:<22} {v['partido'] or '—':<6} {'em exercício' if v['emExercicio'] else 'ex/suplente':<12} "
              f"{len(v['lista']):>3} matérias  {v['cargo']}")

    os.environ["PARLATORIO_BANCO"] = str(banco)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
    import servidor  # noqa: E402
    S = servidor.S

    # a mesma pessoa pode ter um cadastro por legislatura (ex.: Barra do Corda): vira uma pessoa só, em exercício se
    # algum cadastro estiver; as matérias de todos os cadastros ficam com ela
    grupos: dict[str, list[dict]] = {}
    for v in vereadores:
        grupos.setdefault(" ".join(sem_acento(v["civil"]).split()), []).append(v)
    pessoas, outros_de = [], {}
    for grupo in grupos.values():
        principal = next((v for v in grupo if v["emExercicio"]), max(grupo, key=lambda v: v["portalId"]))
        pessoas.append(principal)
        for v in grupo:
            if v is not principal:
                outros_de[v["portalId"]] = principal["portalId"]
                if not principal.get("partido") and v.get("partido") and not principal["emExercicio"]:
                    principal["partido"] = v["partido"]
    if outros_de:
        print(f"Portal: {len(outros_de)} cadastro(s) repetido(s) da mesma pessoa (outras legislaturas) juntado(s)")

    ligados = casar([v for v in pessoas if v["emExercicio"]], S["parls"])
    ex = {p["portalId"]: p for p in S.setdefault("exParls", [])}
    nome_de: dict[int, str] = {}
    for v in pessoas:
        info = {"nomeCivil": v["civil"], "cargoMesa": v["cargo"] if v["cargo"] not in ("", "Vereador") else "",
                "portal": {k: v[k] for k in ("url", "presencas", "presencaPct", "materias")} | {"lidoEm": servidor.agora()}}
        pid = ligados.get(v["portalId"])
        if pid:
            p = next(x for x in S["parls"] if x["id"] == pid)
            p.update(info, **({"partido": v["partido"]} if v["partido"] else {}))
        elif v["emExercicio"]:
            p = {"id": servidor.uid(), "nome": v["curto"], "partido": v["partido"], "lider": False, "fotoV": 0, "canal": 0,
                 "bloco": "", **info}
            S["parls"].append(p)
            servidor.definir_pin_inicial(S, p["id"])   # PIN inicial próprio e aleatório (nunca 0000)
            print(f"  + novo em exercício: {v['curto']}")
        else:
            p = ex.get(v["portalId"]) or {"id": servidor.uid(), "fotoV": 0, "portalId": v["portalId"]}
            p.update({"nome": v["curto"], "partido": v["partido"], "suplente": True, **info})
            ex[v["portalId"]] = p
        p["portalId"] = v["portalId"]
        nome_de[v["portalId"]] = p["nome"]
    for outro, principal in outros_de.items():
        nome_de[outro] = nome_de[principal]
        ex.pop(outro, None)   # importação antiga pode ter deixado o cadastro repetido como ex-vereador
    S["exParls"] = list(ex.values())
    S["casa"]["portal"] = base   # a Câmara passa a saber de onde vêm os dados (legis importar-portal atualiza)
    if not S["rodape"]["camara"].get("site"):
        S["rodape"]["camara"]["site"] = base

    for p in servidor.proj_todos():
        if p.get("portalId") and ignorar(f"{p.get('tipoNome', '')}: x"):
            servidor.db.execute("delete from projetos where id = ?", (p["id"],))
    servidor.db.commit()
    novos = atualizados = 0
    existentes = {p.get("portalId"): p for p in servidor.proj_todos() if p.get("portalId")}
    for v in vereadores:
        for m in v["lista"]:
            p = existentes.get(m["portalId"])
            if p and p.get("autor") and p["autor"] != nome_de[v["portalId"]]:
                p.setdefault("coautores", [])
                if nome_de[v["portalId"]] not in p["coautores"]:
                    p["coautores"].append(nome_de[v["portalId"]])
                servidor.proj_salvar(p)
                continue  # coautoria: a matéria já entrou pelo primeiro autor
            if p:
                atualizados += 1
            else:
                novos += 1
                sugeridas = areas(m["ementa"])
                p = servidor.proj_novo(areas=sugeridas, areasAuto=bool(sugeridas))
                try:
                    d, mes, ano = m["data"].split("/")
                    p["criado"] = int(time.mktime((int(ano), int(mes), int(d), 12, 0, 0, 0, 0, -1)) * 1000)
                except ValueError:
                    pass
            existentes[m["portalId"]] = p
            p.update(numero=numero(m["titulo"]), ementa=m["ementa"], autor=nome_de[v["portalId"]],
                     situacao=situacao(m["situacao"]), fase=m["fase"], portalId=m["portalId"], portalUrl=m["url"],
                     tipoNome=tipo_limpo(m["titulo"]).partition(":")[0].strip().title(), dataApresentacao=m["data"],
                     fonte=p.get("fonte") or "")
            if not any(h.get("evento") == "apresentacao" for h in p["historico"]):
                p["historico"].insert(0, {"ts": p["criado"], "sessao": "—", "evento": "apresentacao", "situacao": p["situacao"]})
            servidor.proj_salvar(p)
    # ---- sessões reais: presença pela chamada e pauta com a situação de cada matéria
    print("\nSessões encerradas:")
    sessoes = ler_sessoes(base)
    por_civil = {}
    for p in S["parls"] + S["exParls"]:
        por_civil[sem_acento(p.get("nomeCivil") or p["nome"])] = p
    # Vereador em exercício que a Câmara não cadastrou no portal (ex.: Barra do Corda) aparece na chamada pelo nome
    # civil: liga ao nome de urna do TSE pelos nomes em comum (um par por vez, do mais parecido ao menos), guarda o
    # nome civil e passa a contar a presença dele
    stop = {"de", "da", "do", "das", "dos", "e", "dr", "silva", "junior", "filho", "neto"}
    tok = lambda n: {w for w in re.findall(r"[a-z]+", sem_acento(n)) if w not in stop and len(w) > 2}
    faltam = {c["civil"] for se in sessoes for c in se["chamada"] if sem_acento(c["civil"]) not in por_civil}
    livres = [p for p in S["parls"] if not p.get("portalId") and not p.get("nomeCivil")]
    pares = sorted(((len(tok(civil) & tok(p["nome"])), civil, p["id"]) for civil in faltam for p in livres), reverse=True)
    usados_c, usados_p = set(), set()
    for n, civil, pid in pares:
        if not n or civil in usados_c or pid in usados_p:
            continue
        empate = [x for x in pares if x[0] == n and (x[1] == civil) != (x[2] == pid) and (x[1] == civil or x[2] == pid)
                  and x[1] not in usados_c and x[2] not in usados_p]
        if empate:
            continue   # dois candidatos igualmente parecidos: não adivinha
        p = next(x for x in S["parls"] if x["id"] == pid)
        p["nomeCivil"] = civil
        por_civil[sem_acento(civil)] = p
        usados_c.add(civil)
        usados_p.add(pid)
        print(f"  chamada: {civil} = {p['nome']} (nome de urna do TSE)")
    projetos = {p.get("portalId"): p for p in servidor.proj_todos() if p.get("portalId")}
    for se in sessoes:
        d, mes, ano = se["data"].split("/")
        ts = int(time.mktime((int(ano), int(mes), int(d), 19, 0, 0, 0, 0, -1)) * 1000)
        titulo = f"{se['numero']}ª Sessão {se['tipo']} de {se['ano']}"
        membros, presenca = [], {}
        for c in se["chamada"]:
            p = por_civil.get(sem_acento(c["civil"]))
            if not p:
                print(f"    (sem cadastro: {c['civil']})")
                continue
            membros.append({k: p.get(k) for k in ("id", "nome", "partido", "lider")})
            if c["presente"]:
                presenca[p["id"]] = ts
        pauta = []
        for it in se["itens"]:
            if ignorar(it["titulo"]):
                continue
            p = projetos.get(it["portalId"])
            if not p:  # matéria do Executivo ou de outro autor que não estava no banco
                p = servidor.proj_novo(numero=numero(it["titulo"]), ementa=it["ementa"], autor=it["autores"],
                                       situacao=situacao(it["situacao"]), portalId=it["portalId"], portalUrl=it["url"],
                                       tipoNome=it["titulo"].partition(":")[0].strip().title(), dataApresentacao=it["data"],
                                       areas=areas(it["ementa"]))
                p["areasAuto"] = bool(p["areas"])
                projetos[it["portalId"]] = p
            elif "fase" not in p:  # veio só da pauta (ex.: Executivo): mantém número e tipo atualizados
                p.update(numero=numero(it["titulo"]), tipoNome=it["titulo"].partition(":")[0].strip().title())
            if not any(h.get("sessao") == titulo for h in p["historico"]):
                p["historico"].append({"ts": ts, "sessao": titulo, "evento": "pauta", "situacao": situacao(it["situacao"])})
            servidor.proj_salvar(p)
            pauta.append({"id": p["id"], "numero": p["numero"], "ementa": p["ementa"], "autor": p["autor"],
                          "situacao": situacao(it["situacao"])})
        r = {"id": f"portal{se['portalId']}", "titulo": titulo, "casa": S["casa"]["nome"], "inicio": ts, "fim": ts,
             "semHorario": True, "fonte": se["url"], "parls": membros, "presenca": presenca, "pauta": pauta,
             "hist": [], "votacoes": [], "ocorrencias": [], "gravacoes": []}
        resumo = {"falas": 0, "votacoes": 0, "presentes": len(presenca), "membros": len(membros), "gravacoes": 0,
                  "ocorrencias": 0, "pauta": len(pauta)}
        servidor.db.execute("insert or replace into sessoes (id, titulo, inicio, fim, resumo, json) values (?, ?, ?, ?, ?, ?)",
                            (r["id"], titulo, ts, ts, json.dumps(resumo), json.dumps(r, ensure_ascii=False)))
    servidor.db.commit()
    print(f"{len(sessoes)} sessões encerradas arquivadas (presença e pauta reais; o portal não publica votos nominais nem falas).")

    S["projetosRev"] += 1
    servidor.salvar()
    print(f"\nMatérias: {novos} nova(s), {atualizados} atualizada(s). Vereadores ligados: {len(ligados)}; ex-membros/suplentes: {len(S['exParls'])}.")
    sem_par = [v["curto"] for v in pessoas if v["emExercicio"] and v["portalId"] not in ligados]
    if sem_par:
        print("Atenção, entraram como novos (confira se não são duplicados):", ", ".join(sem_par))


if __name__ == "__main__":
    main()
