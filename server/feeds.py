"""Acompanhar a Câmara sem precisar entrar no site (inspirado no SPLegis e nos feeds dos parlamentos).

  * Feeds Atom 1.0 (RFC 4287): projetos novos e mudanças de situação, um projeto, um vereador, sessões encerradas.
  * Agenda iCalendar (RFC 5545): sessões virtuais (com início e fim) e sessões já realizadas como eventos passados.
  * E-mail opcional (SMTP_HOST, SMTP_USUARIO, SMTP_SENHA, SMTP_REMETENTE): o cidadão inscreve um e-mail para
    acompanhar um projeto. Confirmação obrigatória por link com token de uso único (double opt-in), link de
    descadastro em todos os e-mails, envio numa thread de fundo com limite por hora. Guardamos só o e-mail, o
    projeto e os dados técnicos da inscrição (tokens e datas); nunca o IP, e o e-mail nunca sai nas APIs públicas.

Tudo só com dados já públicos (sessões encerradas, votos nominais, projetos protocolados).
"""
import hashlib
import re
import secrets
import smtplib
import ssl
import threading
import time
import collections
import queue
import xml.etree.ElementTree as ET
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

ATOM = "http://www.w3.org/2005/Atom"
EVENTOS = {"pauta": "Incluído na pauta", "votacao": "Votação", "proposta": "Proposta registrada", "destaque": "Destaque na sessão virtual"}
SITUACOES = {"aprovada": "aprovada", "rejeitada": "rejeitada", "sem_quorum": "sem quórum", "adiada": "adiada",
             "retirada": "retirada", "pendente": "pendente", "em_discussao": "em discussão", "em_votacao": "em votação",
             "em_tramitacao": "em tramitação", "em_pauta": "em pauta", "proposta": "proposta"}


def agora() -> int:
    return int(time.time() * 1000)


def _iso(ms) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((ms or 0) / 1000))


def _data(ms) -> str:
    return time.strftime("%d/%m/%Y", time.localtime((ms or 0) / 1000))


def _limpo(t) -> str:
    """Tira caracteres de controle que o XML 1.0 não aceita."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]", "", str(t or ""))


# ---------------------------------------------------------------- Atom
def atom(titulo: str, id_feed: str, self_url: str, alt_url: str, autor: str, entradas: list[dict], subtitulo: str = "") -> bytes:
    """entradas: {id, titulo, link, atualizado (ms), resumo, [publicado], [relacionados: [(href, tipo, titulo)]]}"""
    ET.register_namespace("", ATOM)
    a = lambda tag: f"{{{ATOM}}}{tag}"
    feed = ET.Element(a("feed"), {"{http://www.w3.org/XML/1998/namespace}lang": "pt-BR"})
    ET.SubElement(feed, a("id")).text = id_feed
    ET.SubElement(feed, a("title")).text = _limpo(titulo)
    if subtitulo:
        ET.SubElement(feed, a("subtitle")).text = _limpo(subtitulo)
    ET.SubElement(feed, a("updated")).text = _iso(max((e["atualizado"] for e in entradas), default=agora()))
    ET.SubElement(feed, a("link"), {"rel": "self", "type": "application/atom+xml", "href": self_url})
    ET.SubElement(feed, a("link"), {"rel": "alternate", "type": "text/html", "href": alt_url})
    ET.SubElement(ET.SubElement(feed, a("author")), a("name")).text = _limpo(autor)
    ET.SubElement(feed, a("generator"), {"uri": "https://www.aggillintelligence.com.br"}).text = "LEGIS"
    for e in entradas:
        en = ET.SubElement(feed, a("entry"))
        ET.SubElement(en, a("id")).text = e["id"]
        ET.SubElement(en, a("title")).text = _limpo(e["titulo"])
        ET.SubElement(en, a("updated")).text = _iso(e["atualizado"])
        if e.get("publicado"):
            ET.SubElement(en, a("published")).text = _iso(e["publicado"])
        ET.SubElement(en, a("link"), {"rel": "alternate", "type": "text/html", "href": e["link"]})
        for href, tipo, tit in e.get("relacionados") or []:
            ET.SubElement(en, a("link"), {"rel": "related", "type": tipo, "href": href, "title": _limpo(tit)})
        if e.get("resumo"):
            ET.SubElement(en, a("summary"), {"type": "text"}).text = _limpo(e["resumo"])
    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)


def _id(base_id: str, *partes) -> str:
    return base_id + ":" + ":".join(re.sub(r"[^A-Za-z0-9.\-]", "-", str(p)) for p in partes)


def entradas_projeto(p: dict, base: str, base_id: str) -> list[dict]:
    """Uma entrada para o protocolo do projeto e uma para cada evento do histórico."""
    link = f"{base}/projeto/{p['id']}"
    nome = f"{p.get('numero') or 'Projeto'}"
    saida = [{"id": _id(base_id, "projeto", p["id"], "novo"), "titulo": f"Novo projeto: {nome}", "link": link,
              "atualizado": p.get("criado") or 0, "publicado": p.get("criado") or 0,
              "resumo": f"{p.get('ementa') or ''}" + (f" Autoria: {p['autor']}." if p.get("autor") else "")}]
    for k, h in enumerate(p.get("historico") or []):
        ev = EVENTOS.get(h.get("evento"), h.get("evento") or "Atualização")
        sit = h.get("resultado") or h.get("situacao") or ""
        detalhe = f"{ev}{' — ' + SITUACOES.get(sit, sit) if sit else ''}"
        c = h.get("contagem")
        if c:
            detalhe += f" ({c.get('sim', 0)} sim, {c.get('nao', 0)} não, {c.get('abst', 0)} abstenções)"
        saida.append({"id": _id(base_id, "projeto", p["id"], h.get("ts") or 0, k), "titulo": f"{nome}: {detalhe}", "link": link,
                      "atualizado": h.get("ts") or 0,
                      "resumo": f"{detalhe}. Sessão: {h.get('sessao') or '—'}. {p.get('ementa') or ''}"})
    return saida


def entradas_sessao(r: dict, base: str, base_id: str) -> dict:
    votos = "; ".join(f"{v.get('numero')}: {SITUACOES.get(v.get('resultado'), v.get('resultado') or '—')}"
                      for v in r.get("votacoes") or [])
    return {"id": _id(base_id, "sessao", r["id"]), "titulo": r.get("titulo") or "Sessão", "link": f"{base}/sessao/{r['id']}",
            "atualizado": r.get("fim") or r.get("inicio") or 0, "publicado": r.get("inicio") or 0,
            "resumo": f"Sessão de {_data(r.get('inicio'))}: {len(r.get('presenca') or {})} de {len(r.get('parls') or [])} presentes, "
                      f"{len(r.get('hist') or [])} pronunciamento(s), {len(r.get('votacoes') or [])} votação(ões)." + (f" {votos}." if votos else ""),
            "relacionados": [(f"{base}/sessao/{r['id']}", "text/html", "Ata pública: pauta, falas, votações e vídeo"),
                             (f"{base}/dados/akn/sessao/{r['id']}.xml", "application/akn+xml", "Registro em Akoma Ntoso")]}


def entradas_vereador(pid: str, nome: str, sessoes: list[dict], projetos: list[dict], base: str, base_id: str) -> list[dict]:
    saida = []
    for r in sessoes:
        for k, h in enumerate(r.get("hist") or []):
            if h.get("pid") != pid:
                continue
            minutos = round((h.get("usado") or 0) / 60)
            saida.append({"id": _id(base_id, "vereador", pid, "fala", r["id"], h.get("inicio") or k),
                          "titulo": f"{nome} falou em {r.get('titulo') or 'sessão'}", "link": f"{base}/sessao/{r['id']}",
                          "atualizado": h.get("inicio") or r.get("inicio") or 0,
                          "resumo": f"{h.get('faseNome') or 'Pronunciamento'}, {minutos} min, em {_data(h.get('inicio'))}."})
        for v in r.get("votacoes") or []:
            if v.get("tipo") != "nominal" or pid not in (v.get("votos") or {}):
                continue   # só votos nominais (o secreto e o simbólico não identificam quem votou)
            voto = v["votos"][pid]
            voto = voto.get("valor") if isinstance(voto, dict) else voto
            rot = {"sim": "SIM", "nao": "NÃO", "abst": "ABSTENÇÃO"}.get(voto, str(voto))
            saida.append({"id": _id(base_id, "vereador", pid, "voto", r["id"], v.get("materia") or v.get("numero")),
                          "titulo": f"{nome} votou {rot} em {v.get('numero') or 'matéria'}", "link": f"{base}/sessao/{r['id']}",
                          "atualizado": v.get("fim") or v.get("inicio") or r.get("fim") or 0,
                          "resumo": f"{v.get('ementa') or ''} Resultado: {SITUACOES.get(v.get('resultado'), v.get('resultado') or '—')}."})
    for p in projetos:
        saida.append({"id": _id(base_id, "vereador", pid, "projeto", p["id"]), "titulo": f"{nome} apresentou {p.get('numero') or 'projeto'}",
                      "link": f"{base}/projeto/{p['id']}", "atualizado": p.get("criado") or 0, "resumo": p.get("ementa") or ""})
    return saida


# ---------------------------------------------------------------- iCalendar
def ics_texto(t) -> str:
    """Escape de TEXT (RFC 5545, 3.3.11): barra invertida, ponto e vírgula, vírgula e quebras de linha."""
    t = _limpo(t).replace("\r\n", "\n").replace("\r", "\n")
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def dobrar(linha: str) -> str:
    """Dobra em linhas de até 75 octetos (UTF-8), sem cortar caracteres multibyte; continuação começa com espaço."""
    partes, atual, tam = [], "", 0
    for c in linha:
        n = len(c.encode("utf-8"))
        limite = 75 if not partes else 74   # a continuação já gasta 1 octeto com o espaço
        if tam + n > limite:
            partes.append(atual)
            atual, tam = "", 0
        atual += c
        tam += n
    partes.append(atual)
    return "\r\n ".join(partes)


def _ics_data(ms) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime((ms or 0) / 1000))


def ics(nome: str, eventos: list[dict], dominio: str) -> bytes:
    """eventos: {uid, inicio, fim, titulo, descricao, url, [dia_inteiro], [status]}"""
    linhas = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Aggilli Intelligence//LEGIS//PT-BR", "CALSCALE:GREGORIAN",
              "METHOD:PUBLISH", f"X-WR-CALNAME:{ics_texto(nome)}", "X-WR-TIMEZONE:America/Sao_Paulo"]
    carimbo = _ics_data(agora())
    for e in eventos:
        linhas += ["BEGIN:VEVENT", f"UID:{re.sub(r'[^A-Za-z0-9.@-]', '-', e['uid'])}@{dominio}", f"DTSTAMP:{carimbo}"]
        if e.get("dia_inteiro"):
            d = time.strftime("%Y%m%d", time.localtime(e["inicio"] / 1000))
            d2 = time.strftime("%Y%m%d", time.localtime(e["inicio"] / 1000 + 86400))
            linhas += [f"DTSTART;VALUE=DATE:{d}", f"DTEND;VALUE=DATE:{d2}"]
        else:
            linhas += [f"DTSTART:{_ics_data(e['inicio'])}", f"DTEND:{_ics_data(max(e.get('fim') or 0, e['inicio'] + 60_000))}"]
        linhas += [f"SUMMARY:{ics_texto(e['titulo'])}", f"DESCRIPTION:{ics_texto(e.get('descricao') or '')}",
                   f"STATUS:{e.get('status') or 'CONFIRMED'}", "TRANSP:OPAQUE"]
        if e.get("url"):
            linhas.append(f"URL:{e['url']}")
        if e.get("local"):
            linhas.append(f"LOCATION:{ics_texto(e['local'])}")
        linhas.append("END:VEVENT")
    linhas.append("END:VCALENDAR")
    return ("\r\n".join(dobrar(l) for l in linhas) + "\r\n").encode("utf-8")


# ---------------------------------------------------------------- e-mail (opcional)
EMAIL_RE = re.compile(r"[^@\s<>\"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}")
INSCRICOES_POR_HORA = 5       # por endereço de origem (em memória; o IP não é gravado)
CONFIRMACOES_POR_DIA = 3      # por e-mail: ninguém usa a Câmara para encher a caixa de outra pessoa
VALIDADE_CONFIRMACAO = 48 * 3600_000


def _hash(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


class Correio:
    """Fila de envio numa thread de fundo, com limite por hora (SMTP_LIMITE_HORA, padrão 60)."""

    def __init__(self, env: dict):
        self.host = env.get("SMTP_HOST", "").strip()
        self.usuario = env.get("SMTP_USUARIO", "").strip()
        self.senha = env.get("SMTP_SENHA", "")
        self.remetente = env.get("SMTP_REMETENTE", "").strip()
        self.porta = int(env.get("SMTP_PORTA") or 587)
        self.starttls = env.get("SMTP_STARTTLS", "1") != "0"   # 0 só para testes ou relay local sem TLS
        self.limite = max(1, int(env.get("SMTP_LIMITE_HORA") or 60))
        self.ativo = bool(self.host and self.usuario and self.senha and self.remetente)
        self.fila: queue.Queue = queue.Queue(maxsize=2000)
        self.enviados = collections.deque()
        self.erros = 0
        self._thread = None

    def iniciar(self):
        if self.ativo and not self._thread:
            self._thread = threading.Thread(target=self._laco, daemon=True, name="correio")
            self._thread.start()

    def enviar(self, para: str, assunto: str, texto: str, sair: str | None = None) -> bool:
        if not self.ativo:
            return False
        try:
            self.fila.put_nowait((para, assunto, texto, sair))
            return True
        except queue.Full:
            return False

    def _laco(self):
        while True:
            item = self.fila.get()
            while True:   # limite por hora: espera a janela abrir
                hora = time.time() - 3600
                while self.enviados and self.enviados[0] < hora:
                    self.enviados.popleft()
                if len(self.enviados) < self.limite:
                    break
                time.sleep(min(60, max(1, self.enviados[0] - hora)))
            self.enviados.append(time.time())
            try:
                self._mandar(*item)
            except Exception as e:   # nunca derruba a thread; o motivo vai para o log (sem o conteúdo)
                self.erros += 1
                print(f"correio: falha ao enviar ({e.__class__.__name__}: {e})", flush=True)

    def _mandar(self, para, assunto, texto, sair):
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = self.remetente, para, assunto
        m["Date"], m["Message-ID"] = formatdate(localtime=True), make_msgid(domain=self.remetente.rsplit("@", 1)[-1].strip(">"))
        if sair:
            m["List-Unsubscribe"] = f"<{sair}>"
            m["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        m.set_content(texto)
        with smtplib.SMTP(self.host, self.porta, timeout=30) as s:
            s.ehlo()
            if self.starttls:
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
            s.login(self.usuario, self.senha)
            s.send_message(m)


class Acompanhamentos:
    """Inscrições por e-mail para acompanhar um projeto (tabela acompanhamentos)."""

    def __init__(self, db, correio: Correio):
        self.db, self.correio = db, correio
        self._por_ip: dict[str, collections.deque] = {}
        db.execute("create table if not exists acompanhamentos (id text primary key, email text, projeto text, "
                   "confirmacao text, sair text, confirmado integer, criado integer, visto integer)")
        db.commit()

    def limpar(self):
        self.db.execute("delete from acompanhamentos where confirmado = 0 and criado < ?", (agora() - VALIDADE_CONFIRMACAO,))
        self.db.commit()

    def inscrever(self, email: str, projeto: dict, ip: str, base: str, casa: str) -> None:
        """Sempre responde igual (não revela se o e-mail já estava inscrito)."""
        email = str(email or "").strip().lower()[:254]
        if not EMAIL_RE.fullmatch(email):
            raise ValueError("E-mail inválido.")
        fila = self._por_ip.setdefault(hashlib.sha256(ip.encode()).hexdigest()[:16], collections.deque())
        while fila and fila[0] < time.time() - 3600:
            fila.popleft()
        if len(fila) >= INSCRICOES_POR_HORA:
            raise PermissionError("Muitas inscrições seguidas deste endereço. Tente mais tarde.")
        fila.append(time.time())
        self.limpar()
        ja = self.db.execute("select confirmado from acompanhamentos where email = ? and projeto = ?", (email, projeto["id"])).fetchone()
        if ja and ja[0]:
            return
        recentes = self.db.execute("select count(*) from acompanhamentos where email = ? and criado > ?",
                                   (email, agora() - 86_400_000)).fetchone()[0]
        if recentes >= CONFIRMACOES_POR_DIA:
            return
        token = secrets.token_urlsafe(24)
        sair = secrets.token_urlsafe(24)
        self.db.execute("delete from acompanhamentos where email = ? and projeto = ? and confirmado = 0", (email, projeto["id"]))
        self.db.execute("insert into acompanhamentos values (?, ?, ?, ?, ?, 0, ?, ?)",
                        (secrets.token_hex(6), email, projeto["id"], _hash(token), sair, agora(), 0))
        self.db.commit()
        self.correio.enviar(email, f"Confirme: acompanhar {projeto.get('numero') or 'projeto'} — {casa}",
                            f"Olá!\n\nRecebemos um pedido para avisar este e-mail sobre o andamento de {projeto.get('numero') or 'um projeto'}"
                            f" ({projeto.get('ementa') or ''}) na {casa}.\n\nPara confirmar, abra o link abaixo (vale por 48 horas, uma única vez):\n"
                            f"{base}/acompanhar/confirmar?t={token}\n\nSe não foi você, ignore esta mensagem: nada será enviado.\n\n"
                            f"Usamos o e-mail só para estes avisos (LGPD, art. 7º, I: consentimento). Cancele quando quiser: {base}/acompanhar/sair?t={sair}\n",
                            f"{base}/acompanhar/sair?t={sair}")

    def confirmar(self, token: str, ultimo_evento) -> dict | None:
        """Token de uso único: depois de usado (ou vencido) não vale mais."""
        if not token or len(token) > 100:
            return None
        r = self.db.execute("select id, projeto, criado from acompanhamentos where confirmacao = ? and confirmado = 0",
                            (_hash(token),)).fetchone()
        if not r or agora() - r[2] > VALIDADE_CONFIRMACAO:
            return None
        self.db.execute("update acompanhamentos set confirmado = 1, confirmacao = NULL, visto = ? where id = ?",
                        (ultimo_evento(r[1]), r[0]))
        self.db.commit()
        return {"projeto": r[1]}

    def sair(self, token: str) -> bool:
        if not token or len(token) > 100:
            return False
        n = self.db.execute("delete from acompanhamentos where sair = ?", (token,)).rowcount
        self.db.commit()
        return n > 0

    def contar(self, projeto: str) -> int:
        return self.db.execute("select count(*) from acompanhamentos where projeto = ? and confirmado = 1", (projeto,)).fetchone()[0]

    def novidades(self, proj_obter, base: str, casa: str) -> int:
        """Manda um e-mail a cada inscrito quando o histórico do projeto ganhou eventos novos. Devolve quantos."""
        n = 0
        for iid, email, pid, sair, visto in self.db.execute(
                "select id, email, projeto, sair, visto from acompanhamentos where confirmado = 1").fetchall():
            p = proj_obter(pid)
            if not p or p.get("situacao") == "proposta":
                continue
            novos = [h for h in p.get("historico") or [] if (h.get("ts") or 0) > (visto or 0)]
            if not novos:
                continue
            linhas = []
            for h in novos:
                sit = h.get("resultado") or h.get("situacao") or ""
                linhas.append(f"- {_data(h.get('ts'))}: {EVENTOS.get(h.get('evento'), h.get('evento'))}"
                              f"{' — ' + SITUACOES.get(sit, sit) if sit else ''} ({h.get('sessao') or '—'})")
            self.correio.enviar(email, f"{p.get('numero') or 'Projeto'}: novidades na {casa}",
                                f"{p.get('numero')} — {p.get('ementa')}\nSituação atual: {SITUACOES.get(p.get('situacao'), p.get('situacao'))}\n\n"
                                + "\n".join(linhas) + f"\n\nDetalhes: {base}/projeto/{pid}\n\n"
                                f"Você recebe este aviso porque confirmou o acompanhamento deste projeto.\nPara não receber mais: {base}/acompanhar/sair?t={sair}\n",
                                f"{base}/acompanhar/sair?t={sair}")
            self.db.execute("update acompanhamentos set visto = ? where id = ?", (max(h.get("ts") or 0 for h in novos), iid))
            n += 1
        self.db.commit()
        return n


def pagina(titulo: str, mensagem: str, botao: str = "", acao: str = "", token: str = "") -> bytes:
    """Página simples (sem script) de confirmação/descadastro. O botão faz POST: robôs que só abrem links não confirmam."""
    from html import escape
    form = (f'<form method="post" action="{escape(acao)}"><input type="hidden" name="t" value="{escape(token)}">'
            f'<button class="botao principal">{escape(botao)}</button></form>') if botao else ""
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(titulo)}</title><link rel="stylesheet" href="/aggilli.css"></head>
<body><main style="max-width:560px;margin:48px auto;padding:0 16px"><section class="cartao"><h2>{escape(titulo)}</h2>
<p>{escape(mensagem)}</p>{form}<p><a href="/participe">Voltar ao site da Câmara</a></p></section></main></body></html>""".encode()
