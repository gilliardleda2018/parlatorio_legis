"""Perguntas sobre a Câmara, respondidas a partir dos próprios dados do LEGIS.

Busca os trechos mais parecidos com a pergunta (TF-IDF, sem bibliotecas externas) entre discursos
publicados, projetos, parlamentares, sessões e contatos. Com IA ligada, o modelo redige a resposta
usando só esses trechos e citando-os ([1], [2]...). Sem IA, devolve os trechos encontrados.
"""
import re
import time

import ia
from texto import Indice

SISTEMA = ("Você é o assistente de transparência de uma Câmara Municipal brasileira. Responda em português "
           "simples, em no máximo 6 frases, usando SOMENTE as fontes numeradas fornecidas. Cite as fontes "
           "entre colchetes, como [1] ou [2][3]. Se as fontes não respondem à pergunta, diga claramente que não "
           "encontrou essa informação nos registros da Câmara. Não invente nomes, números, datas ou votos. Não "
           "dê opinião política nem recomende voto.")


def _pedacos(texto: str, tamanho: int = 90) -> list[str]:
    palavras = texto.split()
    return [" ".join(palavras[i:i + tamanho]) for i in range(0, len(palavras), tamanho - 15)] or [""]


def _data(ms) -> str:
    return time.strftime("%d/%m/%Y", time.localtime((ms or 0) / 1000)) if ms else ""


def montar_documentos(discursos: list[dict], projetos: list[dict], parlamentares: list[dict],
                      sessoes: list[dict], casa: str, rodape: dict | None = None) -> list[dict]:
    docs = []
    for d in discursos:  # só discursos publicados chegam aqui
        cab = f"Discurso de {d['nome']} ({d['partido']}) em {d.get('sessao') or 'sessão'} de {_data(d['inicio'])}"
        for parte in _pedacos(d["texto"]):
            docs.append({"tipo": "discurso", "titulo": cab, "texto": f"{cab}: {parte}", "link": d.get("link", "")})
    for p in projetos:
        texto = (f"{p.get('numero') or 'Projeto'}: {p.get('ementa', '')}. Autoria: {p.get('autor') or 'não informada'}. "
                 f"Situação: {p.get('situacao', '')}. " + (p.get("texto") or "")[:1500])
        docs.append({"tipo": "projeto", "titulo": p.get("numero") or "Projeto", "texto": texto,
                     "link": f"/participe#{p['id']}" if p.get("consulta") else "/transparencia#projetos"})
    for p in parlamentares:
        texto = (f"Vereador(a) {p['nome']}, partido {p.get('partido', '')}"
                 + (f", bloco {p['bloco']}" if p.get("bloco") else "")
                 + (f". {p['resumo']}" if p.get("resumo") else "."))
        docs.append({"tipo": "parlamentar", "titulo": p["nome"], "texto": texto, "link": f"/parlamentares/{p['id']}"})
    for s in sessoes:
        texto = (f"{s.get('titulo', 'Sessão')} de {_data(s.get('inicio'))}: {s.get('presentes', 0)} presentes, "
                 f"{s.get('falas', 0)} pronunciamentos, {s.get('votacoes', 0)} votações. {s.get('detalhe', '')}")
        docs.append({"tipo": "sessao", "titulo": f"{s.get('titulo', 'Sessão')} ({_data(s.get('inicio'))})", "texto": texto,
                     "link": f"/sessao/{s['id']}" if s.get("id") else "/transparencia#sessoes"})
    c = (rodape or {}).get("camara") or {}
    contato = "; ".join(f"{k}: {v}" for k, v in c.items() if v)
    if contato:
        docs.append({"tipo": "contato", "titulo": f"Contatos da {casa}", "texto": f"Contatos e endereço da {casa}: {contato}",
                     "link": "/"})
    return docs


class Base:
    """Índice reaproveitado enquanto os dados não mudam (a chave muda quando algo é publicado)."""

    def __init__(self):
        self.chave, self.docs, self.indice = None, [], None

    def atualizar(self, chave, gerar_docs):
        if chave != self.chave or self.indice is None:
            self.docs = gerar_docs()
            self.indice = Indice([d["texto"] for d in self.docs])
            self.chave = chave

    def buscar(self, pergunta: str, k: int = 6, tipos: set | None = None) -> list[dict]:
        if not self.docs:
            return []
        v = self.indice.vetor(pergunta)
        notas = sorted(((self.indice.cosseno(v, dv), i) for i, dv in enumerate(self.indice.vetores)
                        if not tipos or self.docs[i]["tipo"] in tipos), reverse=True)
        return [{**self.docs[i], "nota": round(n, 3)} for n, i in notas[:k] if n > 0.04]


def responder(base: Base, pergunta: str, tipos: set | None = None, historico: list | None = None) -> dict:
    pergunta = re.sub(r"\s+", " ", pergunta).strip()[:400]
    fontes = base.buscar(pergunta, tipos=tipos)
    saida = {"pergunta": pergunta, "fontes": [{k: f[k] for k in ("tipo", "titulo", "texto", "link")} for f in fontes],
             "resposta": None, "modo": "trechos", "ia": ia.descricao()}
    if not fontes:
        saida["resposta"] = "Não encontrei esse assunto nos registros publicados da Câmara."
        return saida
    if not ia.ligada():
        return saida
    numeradas = "\n".join(f"[{n}] {f['texto'][:700]}" for n, f in enumerate(fontes, 1))
    conversa = "".join(f"{'Cidadão' if m.get('de') == 'eu' else 'Assistente'}: {str(m.get('texto'))[:300]}\n"
                       for m in (historico or [])[-4:])
    pedido = (f"Fontes:\n{numeradas}\n\n" + (f"Conversa anterior:\n{conversa}\n" if conversa else "")
              + f"Pergunta do cidadão: {pergunta}")
    try:
        saida["resposta"], saida["modo"] = ia.gerar(SISTEMA, pedido, 600), "ia"
    except ia.SemIA as e:
        saida["aviso"] = str(e)
    return saida
