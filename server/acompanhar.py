"""Cola entre o servidor e os recursos de acompanhar (feeds.py), opinião na pauta e Tribuna Livre (tribuna.py)
e citações/LexML/OAI-PMH (citacoes.py): rotas HTTP, ações da presidência e a vigia dos avisos por e-mail.

O servidor chama instalar(globals()) uma vez; daí em diante get/form/post atendem as rotas deste recurso
(devolvem None quando o caminho não é delas). Fica fora do servidor.py para não misturar com outros recursos.
"""
import ipaddress
import json
import os
import re
import ssl
import threading
import time
import traceback
import urllib.parse
from urllib.parse import urlparse

import citacoes
import feeds
import tribuna

# preenchidos por instalar(): estado, banco e funções do servidor
S = db = trava = Erro = None
_ACOES = []
_HOST_OK = re.compile(r"(?:[A-Za-z0-9-]{1,63}\.)*[A-Za-z0-9-]{1,63}(?::\d{1,5})?|\[[0-9A-Fa-f:.]{2,45}\](?::\d{1,5})?")
URL_PUBLICA = os.environ.get("URL_PUBLICA", "").strip().rstrip("/")   # ex.: https://camara.exemplo.gov.br (links dos e-mails)
correio = acompanhamentos = None
DO_SERVIDOR = ("S", "db", "trava", "Erro", "proj_obter", "proj_todos", "registrar", "confirmar", "PORTA",
               "PROXY_CONFIAVEL", "_de_proxy", "akn", "estatisticas", "agora")


def _presidente(fn):
    _ACOES.append(fn)
    return fn


def instalar(ns: dict):
    """Liga o recurso ao servidor: tabelas, campos novos do estado, ações da presidência e threads de fundo."""
    global correio, acompanhamentos
    globals().update({k: ns[k] for k in DO_SERVIDOR})
    citacoes.criar_tabela(db)
    tribuna.criar_tabelas(db)
    correio = feeds.Correio(os.environ)
    acompanhamentos = feeds.Acompanhamentos(db, correio)
    _acomp_base["url"] = URL_PUBLICA
    S.setdefault("placarCidadao", {"mostrar": False, "itens": {}})   # placar público (só opiniões aprovadas) e telão
    S.setdefault("tribunaAberta", True)                               # recebendo inscrições para a Tribuna Livre
    _placar_atualizar()
    for fn in _ACOES:
        ns["acao"]("presidente")(fn)
    if correio.ativo:
        correio.iniciar()
        threading.Thread(target=_vigiar_acompanhamentos, daemon=True, name="acompanhamentos").start()


_acomp_base = {"url": ""}


def _local_lex() -> str | None:
    return citacoes.localidade(S["casa"].get("uf"), S["casa"].get("municipio"))


def linkar_texto(texto: str) -> tuple[str, list[dict]]:
    return citacoes.linkar(texto, _local_lex(), lambda t, n, a: citacoes.norma_buscar(db, t, n, a))


def _itens_opinaveis() -> list[dict]:
    return [m for m in S["pauta"] if m.get("situacao") in ("pendente", "em_discussao", "adiada")]


def _placar_atualizar():
    ids = [m["id"] for m in S["pauta"]]
    lista = tribuna.opinioes(db, ids)
    itens = {}
    for i in ids:
        c = tribuna.placar([o for o in lista if o["item"] == i])
        if sum(c.values()):
            itens[i] = c
    S["placarCidadao"]["itens"] = itens



@_presidente
def opiniaoModerar(ator, id, situacao):
    try:
        tribuna.opiniao_moderar(db, str(id), situacao)
    except (LookupError, ValueError) as e:
        raise Erro(str(e))
    _placar_atualizar()
    S["cidadaniaRev"] += 1


@_presidente
def placarTelao(ator, mostrar=True):
    S["placarCidadao"]["mostrar"] = bool(mostrar)
    _placar_atualizar()


@_presidente
def tribunaAbrir(ator, abertas=True):
    S["tribunaAberta"] = bool(abertas)


@_presidente
def tribunaModerar(ator, id, situacao):
    try:
        tribuna.inscricao_moderar(db, str(id), situacao)
    except (LookupError, ValueError) as e:
        raise Erro(str(e))
    S["cidadaniaRev"] += 1


@_presidente
def tribunaMover(ator, id, d):
    try:
        tribuna.inscricao_mover(db, str(id), int(d))
    except (LookupError, ValueError) as e:
        raise Erro(str(e))
    S["cidadaniaRev"] += 1


@_presidente
def normaMunicipalSalvar(ator, tipo, numero, data, ementa="", link="", id=None):
    try:
        citacoes.norma_salvar(db, tipo, numero, data, ementa, link, str(id) if id else None)
    except (LookupError, ValueError) as e:
        raise Erro(str(e))
    S["normasRev"] = S.get("normasRev", 0) + 1


@_presidente
def normaMunicipalRemover(ator, id):
    citacoes.norma_remover(db, str(id))
    S["normasRev"] = S.get("normasRev", 0) + 1


def _url_base(h) -> str:
    if URL_PUBLICA:
        return URL_PUBLICA
    host = (h.headers.get("Host") or "").strip()
    if not _HOST_OK.fullmatch(host):
        host = f"localhost:{PORTA}"
    esquema = "https" if isinstance(h.request, ssl.SSLSocket) else "http"
    if PROXY_CONFIAVEL and h.client_address and _de_proxy(h.client_address[0]) and \
            h.headers.get("X-Forwarded-Proto") in ("http", "https"):
        esquema = h.headers["X-Forwarded-Proto"]
    return f"{esquema}://{host}"


def _url_base_email(h) -> str:
    """Links que vão por e-mail: URL_PUBLICA; sem ela, só um Host que é IP, localhost ou nome do TLS_NOMES
    (um Host forjado não pode pôr o link de outro site na mensagem de confirmação)."""
    if URL_PUBLICA:
        return URL_PUBLICA
    base = _url_base(h)
    host = urlparse(base).hostname or ""
    nomes = {n.strip().lower() for n in os.environ.get("TLS_NOMES", "").split(",") if n.strip()} | {"localhost"}
    try:
        ipaddress.ip_address(host)
        return base
    except ValueError:
        if host.lower() in nomes:
            return base
    raise Erro("Configure URL_PUBLICA (endereço público do site) para enviar e-mails.")


def _id_atom() -> str:
    return "urn:legis:" + akn._slug(S["casa"]["nome"])


def _projetos_publicos() -> list[dict]:
    return [p for p in proj_todos() if p["situacao"] != "proposta"]


def _vigiar_acompanhamentos():
    intervalo = max(5, int(os.environ.get("ACOMPANHAR_INTERVALO", "60")))
    while True:
        time.sleep(intervalo)
        try:
            with trava:
                if _acomp_base["url"]:
                    acompanhamentos.novidades(proj_obter, _acomp_base["url"], S["casa"]["nome"])
                acompanhamentos.limpar()
        except Exception:
            traceback.print_exc()



def _registros_oai(base: str) -> list[dict]:
    casa = S["casa"]["nome"]
    local = _local_lex()
    regs = []
    for n in citacoes.normas_listar(db):
        u = citacoes.urn("municipal", n["tipo"], n["numero"] or None, n["ano"], n["data"], local)
        regs.append({"id": f"norma/{n['id']}", "datestamp": n["atualizado"] or n["criado"], "set": "normas",
                     "dc": {"title": citacoes.nome_norma(n), "description": n["ementa"], "publisher": casa,
                            "date": n["data"] or str(n["ano"]), "type": ["Norma jurídica", citacoes.TIPOS_NOMES.get(n["tipo"])],
                            "identifier": [u, n["link"] or None, f"{base}/normas#n-{n['id']}"], "language": "por"}})
    for p in _projetos_publicos():
        regs.append({"id": f"projeto/{p['id']}", "datestamp": p.get("atualizado") or p.get("criado") or 0, "set": "projetos",
                     "dc": {"title": f"{p.get('numero') or 'Projeto'} — {p.get('ementa') or ''}", "creator": p.get("autor"),
                            "subject": [estatisticas.AREAS.get(a, a) for a in p.get("areas") or []],
                            "description": p.get("ementa"), "publisher": casa,
                            "date": time.strftime("%Y-%m-%d", time.localtime((p.get("criado") or 0) / 1000)),
                            "type": "Proposição legislativa", "language": "por", "rights": "CC-BY 4.0",
                            "identifier": [f"{base}/projeto/{p['id']}", f"{base}/dados/akn/projeto/{p['id']}.xml"]}})
    return regs


def _eventos_agenda(base: str, projeto: str | None) -> list[dict]:
    casa = S["casa"]["nome"]
    eventos = []
    for v in S["virtuais"]:
        if v.get("cancelada") or (projeto and not any(i["projeto"] == projeto for i in v["itens"])):
            continue
        itens = "\n".join(f"- {i['numero']}: {i['ementa']}" for i in v["itens"])
        eventos.append({"uid": f"virtual-{v['id']}", "inicio": v["inicio"], "fim": v["fim"], "titulo": f"Sessão virtual: {v['titulo']}",
                        "descricao": f"Votação online da {casa}.\n{itens}", "url": f"{base}/transparencia#secVirtual", "local": casa})
    for (txt,) in db.execute("select json from sessoes order by inicio desc limit 300").fetchall():
        r = json.loads(txt)
        if projeto and not any(m.get("id") == projeto for m in r.get("pauta") or []):
            continue
        pauta = "\n".join(f"- {m.get('numero')}: {m.get('ementa')}" for m in r.get("pauta") or [])
        eventos.append({"uid": f"sessao-{r['id']}", "inicio": r.get("inicio") or 0, "fim": r.get("fim"), "titulo": r.get("titulo") or "Sessão",
                        "descricao": f"Sessão realizada. Ata pública: {base}/sessao/{r['id']}\n{pauta}", "url": f"{base}/sessao/{r['id']}",
                        "local": casa, "dia_inteiro": bool(r.get("semHorario"))})
    return eventos


def _responder(h, corpo: bytes, tipo: str, extras: dict | None = None):
    h._enviar(200, corpo, tipo, {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff", **(extras or {})})
    return True


def _pagina(h, codigo, corpo: bytes):
    h._enviar(codigo, corpo, "text/html; charset=utf-8", {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                                         "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
                                                         "Referrer-Policy": "no-referrer"})
    return True


def get(h, caminho: str):
    """Rotas GET deste recurso; None quando o caminho não é delas."""
    qs = urllib.parse.parse_qs(urlparse(h.path).query)
    if re.fullmatch(r"/projeto/[A-Za-z0-9]+", caminho):
        h._estatico("/projeto.html")
        return True
    if caminho.startswith("/feeds/") and caminho.endswith(".atom"):
        base = _url_base(h)
        with trava:
            casa, idb = S["casa"]["nome"], _id_atom()
            if caminho == "/feeds/projetos.atom":
                entradas = [e for p in _projetos_publicos() for e in feeds.entradas_projeto(p, base, idb)]
                titulo, alt, sub = f"Projetos — {casa}", f"{base}/transparencia#projetos", "Projetos novos e mudanças de situação"
            elif caminho == "/feeds/sessoes.atom":
                sessoes = [json.loads(x[0]) for x in db.execute("select json from sessoes order by fim desc limit 100").fetchall()]
                entradas = [feeds.entradas_sessao(r, base, idb) for r in sessoes]
                titulo, alt, sub = f"Sessões — {casa}", f"{base}/transparencia#sessoes", "Sessões encerradas, com a ata pública"
            elif m := re.fullmatch(r"/feeds/projeto/([A-Za-z0-9]+)\.atom", caminho):
                p = proj_obter(m.group(1))
                if not p or p["situacao"] == "proposta":
                    return h._json(404, {"erro": "Projeto não encontrado."})
                entradas = feeds.entradas_projeto(p, base, idb)
                titulo, alt, sub = f"{p.get('numero') or 'Projeto'} — {casa}", f"{base}/projeto/{p['id']}", p.get("ementa") or ""
            elif m := re.fullmatch(r"/feeds/vereador/([A-Za-z0-9]+)\.atom", caminho):
                pid = m.group(1)
                quem = next((x for x in S["parls"] + S.get("exParls", []) if x["id"] == pid), None)
                if not quem:
                    return h._json(404, {"erro": "Parlamentar não encontrado."})
                sessoes = [json.loads(x[0]) for x in db.execute("select json from sessoes order by fim desc limit 200").fetchall()]
                meus = [p for p in _projetos_publicos() if (p.get("autor") or "").lower() == quem["nome"].lower()]
                entradas = feeds.entradas_vereador(pid, quem["nome"], sessoes, meus, base, idb)
                titulo, alt, sub = f"{quem['nome']} — {casa}", f"{base}/parlamentares/{pid}", "Falas, votos nominais e projetos"
            else:
                return h._json(404, {"erro": "Feed não encontrado."})
        entradas.sort(key=lambda e: -e["atualizado"])
        corpo = feeds.atom(titulo, f"{idb}:feed:{caminho[7:-5].replace('/', ':')}", base + caminho, alt, casa, entradas[:100], sub)
        return _responder(h, corpo, "application/atom+xml; charset=utf-8")
    if caminho == "/agenda.ics":
        base = _url_base(h)
        projeto = (qs.get("projeto") or [""])[0]
        if projeto and not projeto.isalnum():
            return h._json(400, {"erro": "Projeto inválido."})
        with trava:
            eventos = _eventos_agenda(base, projeto or None)
            casa = S["casa"]["nome"]
        dominio = (urlparse(base).hostname or "legis").replace(":", "-")
        return _responder(h, feeds.ics(f"Agenda — {casa}", eventos, dominio), "text/calendar; charset=utf-8",
                          {"Content-Disposition": 'inline; filename="agenda-camara.ics"'})
    if caminho == "/oai":
        base = _url_base(h) + "/oai"
        with trava:
            regs = _registros_oai(base[:-4])
            casa = S["casa"]["nome"]
            email = str((S["rodape"].get("camara") or {}).get("email") or "")
        slug = akn._slug(casa)
        repo_id = "legis." + (slug if slug[:1].isalpha() else "c" + slug)
        if not feeds.EMAIL_RE.fullmatch(email):
            email = os.environ.get("OAI_EMAIL", "legis@localhost.localdomain")
        corpo = citacoes.oai(qs, base, f"{casa} — LEGIS", repo_id, email, regs)
        return _responder(h, corpo, "text/xml; charset=utf-8")
    if m := re.fullmatch(r"/api/projeto-publico/([A-Za-z0-9]+)", caminho):
        with trava:
            p = proj_obter(m.group(1))
            if not p or p["situacao"] == "proposta":
                return h._json(404, {"erro": "Projeto não encontrado."})
            ementa_html, c1 = linkar_texto(p.get("ementa") or "")
            texto_html, c2 = linkar_texto(p["texto"]) if p.get("texto") else (None, [])
            casa = {k: S["casa"].get(k) for k in ("nome", "logoV", "destaque", "tema")}
        return h._json(200, {"projeto": {k: p.get(k) for k in ("id", "numero", "ementa", "autor", "situacao", "historico", "criado",
                                                               "atualizado", "areas", "anexo", "textoUrl", "saplUrl")},
                             "ementaHtml": ementa_html, "textoHtml": texto_html, "citacoes": c1 + c2, "casa": casa,
                             "email": correio.ativo, "feed": f"/feeds/projeto/{p['id']}.atom", "agenda": f"/agenda.ics?projeto={p['id']}"})
    if caminho == "/api/normas-municipais":
        with trava:
            local = _local_lex()
            lista = [n | {"nome": citacoes.nome_norma(n), "urn": citacoes.urn("municipal", n["tipo"], n["numero"] or None, n["ano"], n["data"], local)}
                     for n in citacoes.normas_listar(db)]
            casa = S["casa"]["nome"]
        return h._json(200, {"normas": lista, "tipos": {k: citacoes.TIPOS_NOMES[k] for k in citacoes.TIPOS_MUNICIPAIS},
                             "casa": casa, "localidade": local})
    if caminho == "/api/pauta-cidada":
        with trava:
            ator = h._ator()
            interno = bool(ator and ator["papel"] == "presidente")
            itens = S["pauta"] if interno else _itens_opinaveis()
            lista = tribuna.opinioes(db, [m["id"] for m in itens])
            saida = []
            for m in itens:
                deste = [o for o in lista if o["item"] == m["id"]]
                item = {k: m.get(k) for k in ("id", "numero", "ementa", "autor", "situacao")} | {
                    "placar": tribuna.placar(deste), "comentarios": [tribuna.opiniao_publica(o) for o in deste if o["situacao"] == "aprovado"],
                    "aberto": m in _itens_opinaveis()}
                if interno:
                    item["placarTotal"] = tribuna.placar([o for o in deste if o["situacao"] != "recusado"], False)
                    item["moderacao"] = [tribuna.opiniao_interna(o) for o in deste]
                saida.append(item)
            r = {"itens": saida, "posicoes": tribuna.POSICOES, "titulo": S["casa"]["titulo"], "casa": S["casa"]["nome"],
                 "telao": S["placarCidadao"]["mostrar"]}
        return h._json(200, r)
    if caminho == "/api/tribuna":
        with trava:
            ator = h._ator()
            interno = bool(ator and ator["papel"] == "presidente")
            todas = tribuna.inscricoes(db, S["sessao"]["id"])
            r = {"abertas": S["tribunaAberta"], "titulo": S["casa"]["titulo"], "casa": S["casa"]["nome"],
                 "inscritos": [tribuna.inscricao_publica(i) for i in todas if i["situacao"] == "aprovado"]}
            if interno:
                r["todas"] = [tribuna.inscricao_interna(i) for i in todas]
        return h._json(200, r)
    if caminho == "/api/acompanhar":
        return h._json(200, {"email": correio.ativo})
    if caminho in ("/acompanhar/confirmar", "/acompanhar/sair"):
        t = (qs.get("t") or [""])[0][:100]
        if caminho.endswith("confirmar"):
            return _pagina(h, 200, feeds.pagina("Confirmar acompanhamento", "Clique para confirmar que quer receber, neste e-mail, "
                                                "os avisos sobre o andamento do projeto.", "Confirmar", "/acompanhar/confirmar", t))
        return _pagina(h, 200, feeds.pagina("Cancelar avisos", "Clique para não receber mais e-mails sobre este projeto. "
                                            "O seu e-mail será apagado da lista.", "Cancelar os avisos", f"/acompanhar/sair?t={t}", t))
    return None


def form(h, caminho: str):
    """POST de formulário (sem JSON): confirmação e descadastro, inclusive o one-click do List-Unsubscribe (RFC 8058)."""
    if caminho not in ("/acompanhar/confirmar", "/acompanhar/sair"):
        return None
    n = h._tamanho()
    if n > 4096:
        raise Erro("Requisição grande demais.")
    form = urllib.parse.parse_qs(h.rfile.read(n).decode("utf-8", "replace"))
    t = ((form.get("t") or urllib.parse.parse_qs(urlparse(h.path).query).get("t") or [""])[0])[:100]
    with trava:
        if caminho.endswith("confirmar"):
            ultimo = lambda pid: max([x.get("ts") or 0 for x in (proj_obter(pid) or {}).get("historico") or []] + [agora()])
            ok = acompanhamentos.confirmar(t, ultimo)
            return _pagina(h, 200 if ok else 400, feeds.pagina(
                "Acompanhamento confirmado" if ok else "Link inválido",
                "Pronto! Você vai receber um e-mail quando este projeto tiver novidades. Cada e-mail traz o link para cancelar."
                if ok else "Este link já foi usado, venceu (48 horas) ou não existe. Peça a inscrição de novo na página do projeto."))
        ok = acompanhamentos.sair(t)
    return _pagina(h, 200, feeds.pagina("Avisos cancelados" if ok else "Nada a cancelar",
                                        "Seu e-mail foi apagado da lista deste projeto." if ok else "Esta inscrição já não existia."))


def post(h, caminho: str, corpo: dict):
    if caminho == "/api/acompanhar":
        if not correio.ativo:
            return h._json(404, {"erro": "Avisos por e-mail não estão disponíveis nesta Câmara."})
        if corpo.get("site"):
            return h._json(200, {"ok": True})
        base = _url_base_email(h)
        with trava:
            p = proj_obter(str(corpo.get("projeto") or "")) if str(corpo.get("projeto") or "").isalnum() else None
            if not p or p["situacao"] == "proposta":
                return h._json(404, {"erro": "Projeto não encontrado."})
            try:
                acompanhamentos.inscrever(corpo.get("email"), p, h._ip(), base, S["casa"]["nome"])
            except ValueError as e:
                return h._json(400, {"erro": str(e)})
            except PermissionError as e:
                return h._json(429, {"erro": str(e)})
            _acomp_base["url"] = _acomp_base["url"] or base
        return h._json(200, {"ok": True, "mensagem": "Se o e-mail for válido, chegará um link de confirmação. Só depois de confirmar você recebe os avisos."})
    if m := re.fullmatch(r"/api/pauta/([A-Za-z0-9]+)/opinar", caminho):
        if corpo.get("site"):
            return h._json(200, {"ok": True})
        with trava:
            if not any(x["id"] == m.group(1) for x in _itens_opinaveis()):
                return h._json(400, {"erro": "Este item não está recebendo comentários."})
            try:
                o = tribuna.opinar(db, m.group(1), corpo.get("nome"), corpo.get("email"), corpo.get("bairro"),
                                   corpo.get("posicao"), corpo.get("texto"), h._ip())
            except ValueError as e:
                return h._json(400, {"erro": str(e)})
            except PermissionError as e:
                return h._json(429, {"erro": str(e)})
            registrar("cidadao", "pautaOpinar", {"item": o["item"], "opiniao": o["id"]})
            S["cidadaniaRev"] += 1
            confirmar()
        return h._json(200, {"ok": True, "situacao": "pendente"})
    if caminho == "/api/tribuna":
        if corpo.get("site"):
            return h._json(200, {"ok": True})
        with trava:
            if not S["tribunaAberta"]:
                return h._json(400, {"erro": "As inscrições para a Tribuna Livre estão fechadas."})
            try:
                i = tribuna.inscrever(db, S["sessao"]["id"], corpo.get("nome"), corpo.get("entidade"), corpo.get("tema"),
                                      corpo.get("contato"), h._ip())
            except ValueError as e:
                return h._json(400, {"erro": str(e)})
            except PermissionError as e:
                return h._json(429, {"erro": str(e)})
            registrar("cidadao", "tribunaInscrever", {"inscricao": i["id"]})
            S["cidadaniaRev"] += 1
            confirmar()
        return h._json(200, {"ok": True, "situacao": "pendente"})
    if caminho == "/api/citacoes":
        texto = str(corpo.get("texto") or "")
        if len(texto) > 50_000:
            return h._json(400, {"erro": "Texto grande demais (até 50 mil caracteres)."})
        with trava:
            html_, cits = linkar_texto(texto)
        return h._json(200, {"html": html_, "citacoes": cits})
    if caminho == "/api/normas-municipais/importar":
        with trava:
            ator = h._ator()
            if not ator or ator["papel"] != "presidente":
                return h._json(401, {"erro": "Entre no painel do presidente."})
            try:
                r = citacoes.importar_csv(db, str(corpo.get("csv") or ""))
            except ValueError as e:
                return h._json(400, {"erro": str(e)})
            registrar("presidente", "normasMunicipaisImportar", {"importadas": r["importadas"]})
            S["normasRev"] = S.get("normasRev", 0) + 1
            confirmar()
        return h._json(200, r)
    return None
