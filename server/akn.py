"""Exportação em Akoma Ntoso 3.0 (OASIS LegalDocML), o padrão internacional de documentos legislativos
usado pelo Senado italiano, pelo Parlamento Europeu e, com adaptações, pelo Senado brasileiro (LexML).

Gera o projeto de lei (bill) com seus artigos e o debate de uma sessão (debate) com os pronunciamentos
publicados e as votações. Só entram dados públicos.
"""
import re
import time
import unicodedata
from xml.sax.saxutils import escape, quoteattr

NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _slug(t: str) -> str:
    t = "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-") or "camara"


def _data(ms) -> str:
    return time.strftime("%Y-%m-%d", time.localtime((ms or time.time() * 1000) / 1000))


def _meta(pais: str, tipo: str, numero: str, data: str, casa_slug: str) -> str:
    work = f"/akn/{pais}/{tipo}/{casa_slug}/{data}/{numero}"
    return f"""  <meta>
    <identification source="#legis">
      <FRBRWork><FRBRthis value={quoteattr(work + "/!main")}/><FRBRuri value={quoteattr(work)}/><FRBRdate date="{data}" name="criação"/><FRBRauthor href="#camara"/><FRBRcountry value="{pais}"/></FRBRWork>
      <FRBRExpression><FRBRthis value={quoteattr(work + "/por@/!main")}/><FRBRuri value={quoteattr(work + "/por@")}/><FRBRdate date="{data}" name="versão"/><FRBRauthor href="#camara"/><FRBRlanguage language="por"/></FRBRExpression>
      <FRBRManifestation><FRBRthis value={quoteattr(work + "/por@/!main.xml")}/><FRBRuri value={quoteattr(work + "/por@.akn")}/><FRBRdate date="{_data(None)}" name="geração"/><FRBRauthor href="#legis"/></FRBRManifestation>
    </identification>
    <references source="#legis">
      <TLCOrganization eId="camara" href="/ontology/organization/{casa_slug}" showAs={quoteattr(casa_slug)}/>
      <TLCOrganization eId="legis" href="/ontology/organization/legis" showAs="LEGIS — Sistema de Gestão Parlamentar"/>
    </references>
  </meta>"""


def projeto(p: dict, artigos: list[dict], casa: str, pais: str = "br") -> str:
    numero = _slug(p.get("numero") or p["id"])
    corpo = []
    for a in artigos:
        if a["chave"] == "inicio":
            continue
        n = re.sub(r"\D", "", a["rotulo"]) or "0"
        conteudo = re.sub(r"^Art\.?\s*\d+[ºo°]?\.?\s*[-–—]?\s*", "", a["texto"]).strip()
        paragrafos = "".join(f"<p>{escape(l.strip())}</p>" for l in conteudo.split("\n") if l.strip())
        corpo.append(f'      <article eId="art_{n}"><num>{escape(a["rotulo"])}</num><content>{paragrafos}</content></article>')
    preambulo = next((a["texto"] for a in artigos if a["chave"] == "inicio"), "")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{NS}">
<bill name={quoteattr(p.get("numero") or "projeto")}>
{_meta(pais, "bill", numero, _data(p.get("criado")), _slug(casa))}
  <preface>
    <p class="title"><docTitle>{escape(p.get("numero") or "")}</docTitle></p>
    <p class="ementa">{escape(p.get("ementa") or "")}</p>
    <p class="autoria"><docProponent>{escape(p.get("autor") or "")}</docProponent></p>
    <p class="situacao"><docStatus>{escape(p.get("situacao") or "")}</docStatus></p>
  </preface>
  {f"<preamble><p>{escape(preambulo)}</p></preamble>" if preambulo else ""}
  <body>
{chr(10).join(corpo) if corpo else '      <article eId="art_1"><num>Art. 1º</num><content><p>Texto integral não cadastrado.</p></content></article>'}
  </body>
</bill>
</akomaNtoso>
"""


def debate(r: dict, discursos: list[dict], casa: str, pais: str = "br") -> str:
    data = _data(r.get("inicio"))
    pessoas = {p["id"]: p for p in r.get("parls", [])}
    refs = "".join(f'<TLCPerson eId="p_{pid}" href="/ontology/person/{pid}" showAs={quoteattr(p["nome"])}/>'
                   for pid, p in pessoas.items())
    falas = []
    for d in discursos:
        falas.append(f'      <speech by="#p_{d["pid"]}"><from>{escape(d["nome"])} ({escape(d["partido"])})</from>'
                     + "".join(f"<p>{escape(par.strip())}</p>" for par in d["texto"].split("\n") if par.strip())
                     + "</speech>")
    votos = []
    for v in r.get("votacoes", []):
        c = v.get("contagem") or {}
        votos.append(f'      <debateSection name="votacao"><heading>{escape(v.get("numero") or "")} — {escape(v.get("ementa") or "")}</heading>'
                     f'<p>Votação {escape(v.get("tipo") or "")}: {escape(v.get("resultado") or "")}'
                     + (f' (sim {c.get("sim", 0)}, não {c.get("nao", 0)}, abstenção {c.get("abst", 0)})' if c else "") + "</p></debateSection>")
    meta = _meta(pais, "debate", _slug(r.get("titulo") or r["id"]), data, _slug(casa)).replace(
        "</references>", f"{refs}</references>")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{NS}">
<debate name={quoteattr(r.get("titulo") or "sessao")}>
{meta}
  <preface><p><docTitle>{escape(casa)} — {escape(r.get("titulo") or "")}</docTitle> <docDate date="{data}">{data}</docDate></p></preface>
  <debateBody>
    <debateSection name="pronunciamentos"><heading>Pronunciamentos</heading>
{chr(10).join(falas) if falas else "      <p>Nenhum pronunciamento com transcrição publicada.</p>"}
    </debateSection>
{chr(10).join(votos)}
  </debateBody>
</debate>
</akomaNtoso>
"""


_AKN_TAG = {"artigo": ("article", "art"), "paragrafo": ("paragraph", "para"), "inciso": ("point", "inc"),
            "alinea": ("point", "ali"), "item": ("point", "item")}


def projeto_final(p: dict, dispositivos: list[dict], emendas: list[dict], casa: str, pais: str = "br") -> str:
    """Texto consolidado (redação final) em estrutura hierárquica: article > paragraph > point, com eIds no
    padrão AKN (art_1__para_2__inc_3) e as emendas como metadados em <proprietary>."""
    from emendas import prefixo  # import tardio: emendas.py não depende deste módulo
    filhos: dict = {}
    for d in dispositivos:
        filhos.setdefault(d.get("pai"), []).append(d)

    def no(d, base, recuo) -> str:
        if d["tipo"] in ("preambulo", "agrupador"):
            return ""
        tag, curto = _AKN_TAG[d["tipo"]]
        n = "unico" if d["tipo"] == "paragrafo" and d["num"] == 0 else str(d["num"])
        meu = f"{base}__{curto}_{n}" if base else f"{curto}_{n}"
        texto = "".join(f"<p>{escape(l.strip())}</p>" for l in d["texto"].split("\n") if l.strip()) or "<p/>"
        sub = filhos.get(d["id"], [])
        dentro = (f"<intro>{texto}</intro>" + "".join(no(f, meu, recuo + 2) for f in sub)) if sub else f"<content>{texto}</content>"
        return f'\n{" " * recuo}<{tag} eId="{meu}"><num>{escape(prefixo(d))}</num>{dentro}</{tag}>'

    corpo = []
    for d in dispositivos:
        if d.get("pai"):
            continue
        if d["tipo"] == "agrupador":
            corpo.append(f'\n      <hcontainer eId={quoteattr(d["id"])} name="agrupador"><heading>{escape(d["texto"].split(chr(10))[0])}</heading>'
                         f'<content><p>{escape(d["texto"])}</p></content></hcontainer>')
        elif d["tipo"] != "preambulo":
            corpo.append(no(d, "", 6))
    preambulo = next((d["texto"] for d in dispositivos if d["tipo"] == "preambulo"), "")
    meta_em = "".join(
        f'<legis:emenda numero="{int(e["numero"])}" tipo={quoteattr(e["tipo"])} situacao={quoteattr(e["situacao"])} '
        f'alvo={quoteattr(e.get("alvo") or "")} referencia={quoteattr(e.get("alvoRef") or "")} autor={quoteattr(e.get("autor") or "")} '
        f'data="{_data(e.get("criado"))}"><legis:texto>{escape(e.get("texto") or "")}</legis:texto>'
        f'<legis:justificativa>{escape(e.get("justificativa") or "")}</legis:justificativa></legis:emenda>'
        for e in emendas)
    meta = _meta(pais, "bill", _slug(p.get("numero") or p["id"]) + "-redacao-final", _data(p.get("criado")), _slug(casa)).replace(
        "  </meta>", f'    <proprietary source="#legis"><legis:emendas xmlns:legis="urn:legis:emendas">{meta_em}</legis:emendas></proprietary>\n  </meta>')
    situacao = "redação final" if p.get("situacao") == "aprovada" else "texto consolidado com as emendas aprovadas"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{NS}">
<bill name={quoteattr(p.get("numero") or "projeto")} contains="singleVersion">
{meta}
  <preface>
    <p class="title"><docTitle>{escape(p.get("numero") or "")}</docTitle></p>
    <p class="ementa">{escape(p.get("ementa") or "")}</p>
    <p class="situacao"><docStatus>{situacao}</docStatus></p>
  </preface>
  {f"<preamble><p>{escape(preambulo)}</p></preamble>" if preambulo else ""}
  <body>{"".join(corpo) or '<article eId="art_1"><num>Art. 1º</num><content><p/></content></article>'}
  </body>
</bill>
</akomaNtoso>
"""
