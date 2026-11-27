"""Assistente do Regimento Interno e da Lei Orgânica (inspirado no Ulysses Chat da Câmara dos Deputados).

A Mesa cadastra as normas da Câmara (texto colado, .txt ou PDF). Cada artigo vira um trecho com o
título, o capítulo e a seção em que está; a pergunta busca os artigos mais parecidos (TF-IDF). Com IA,
o modelo responde usando só esses artigos e citando-os; sem IA, devolve os artigos encontrados.
"""
import json
import re
import secrets
import shutil
import subprocess
import time

import ia
from texto import Indice

SISTEMA = ("Você ajuda a Mesa Diretora e os vereadores de uma Câmara Municipal brasileira a aplicar as normas da Casa. "
           "Responda em português claro, em até 6 frases, usando SOMENTE os artigos numerados fornecidos e citando-os "
           "entre colchetes, como [1] ou [2][3], com o nome da norma e o número do artigo. Se os artigos não respondem, "
           "diga que não encontrou a regra e sugira consultar a Procuradoria ou a Secretaria. Não invente artigos, prazos "
           "nem quóruns.")
CABECALHO = re.compile(r"^\s*(T[ÍI]TULO|CAP[ÍI]TULO|SE[ÇC][ÃA]O|SUBSE[ÇC][ÃA]O)\s+[IVXLC\d]+.*$", re.I | re.M)


def criar_tabela(db):
    db.execute("create table if not exists normas (id text primary key, nome text, criado integer, texto text)")
    db.commit()


def listar(db) -> list[dict]:
    return [{"id": i, "nome": n, "criado": c, "artigos": len(dispositivos(t, n)), "caracteres": len(t)}
            for i, n, c, t in db.execute("select id, nome, criado, texto from normas order by criado")]


def pdf_para_texto(dados: bytes) -> str:
    if not shutil.which("pdftotext"):
        raise ValueError("O servidor não lê PDF (instale poppler-utils) — cole o texto ou envie um .txt.")
    r = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", "-", "-"], input=dados, capture_output=True, timeout=60)
    if r.returncode != 0:
        raise ValueError("Não consegui ler este PDF (talvez seja uma imagem escaneada).")
    return r.stdout.decode("utf-8", "replace")


def salvar(db, nome: str, texto: str) -> dict:
    nome = " ".join(str(nome or "").split())[:120]
    texto = str(texto or "").replace("\r", "")
    texto = re.sub(r"[ \t]+", " ", texto).strip()
    if len(nome) < 3:
        raise ValueError("Dê um nome à norma (ex.: Regimento Interno).")
    if len(texto) < 200 or not re.search(r"Art\.?\s*\d", texto):
        raise ValueError("O texto precisa ter os artigos (Art. 1º, Art. 2º…). Para PDF escaneado, cole o texto.")
    if len(texto) > 3_000_000:
        raise ValueError("Texto grande demais.")
    nid = secrets.token_hex(5)
    db.execute("insert into normas (id, nome, criado, texto) values (?, ?, ?, ?)", (nid, nome, int(time.time() * 1000), texto))
    db.commit()
    return {"id": nid, "nome": nome, "artigos": len(dispositivos(texto, nome))}


def remover(db, nid: str):
    db.execute("delete from normas where id = ?", (nid,))
    db.commit()


def dispositivos(texto: str, nome: str) -> list[dict]:
    """Um trecho por artigo (com parágrafos e incisos), com o caminho Título > Capítulo > Seção."""
    saida, contexto = [], {}
    partes = re.split(r"(?m)^(?=\s*Art\.?\s*\d+)", texto)
    for parte in partes:
        for m in CABECALHO.finditer(parte):
            nivel = m.group(1).upper().replace("Í", "I").replace("Ç", "C").replace("Ã", "A")
            contexto[nivel] = " ".join(m.group(0).split())[:90]
            for menor in {"TITULO": ("CAPITULO", "SECAO", "SUBSECAO"), "CAPITULO": ("SECAO", "SUBSECAO"),
                          "SECAO": ("SUBSECAO",)}.get(nivel, ()):
                contexto.pop(menor, None)
        m = re.match(r"\s*Art\.?\s*(\d+)", parte)
        if not m:
            continue
        corpo = CABECALHO.sub("", parte).strip()
        caminho = " > ".join(contexto[k] for k in ("TITULO", "CAPITULO", "SECAO", "SUBSECAO") if k in contexto)
        saida.append({"norma": nome, "artigo": m.group(1), "caminho": caminho, "texto": corpo[:2500]})
    return saida


class Base:
    def __init__(self):
        self.chave, self.itens, self.indice = None, [], None

    def atualizar(self, db):
        chave = db.execute("select count(*), coalesce(max(criado), 0) from normas").fetchone()
        if chave != self.chave:
            self.itens = [d for (nome, texto) in db.execute("select nome, texto from normas order by criado")
                          for d in dispositivos(texto, nome)]
            self.indice = Indice([f"{d['caminho']} {d['texto']}" for d in self.itens]) if self.itens else None
            self.chave = chave

    def buscar(self, pergunta: str, k: int = 5) -> list[dict]:
        if not self.indice:
            return []
        v = self.indice.vetor(pergunta)
        notas = sorted(((self.indice.cosseno(v, dv), i) for i, dv in enumerate(self.indice.vetores)), reverse=True)
        return [self.itens[i] | {"nota": round(n, 3)} for n, i in notas[:k] if n > 0.03]


def perguntar(base: Base, pergunta: str) -> dict:
    """Chame base.atualizar(db) antes (com a trava do banco); aqui só busca e, com IA, redige (pode demorar)."""
    pergunta = " ".join(str(pergunta or "").split())[:400]
    if len(pergunta) < 5:
        raise ValueError("Escreva a pergunta.")
    if not base.itens:
        return {"pergunta": pergunta, "fontes": [], "resposta": "Nenhuma norma cadastrada. Cadastre o Regimento Interno e a "
                "Lei Orgânica em Configurações.", "modo": "vazio", "ia": ia.descricao()}
    fontes = base.buscar(pergunta)
    saida = {"pergunta": pergunta, "fontes": [{k: f[k] for k in ("norma", "artigo", "caminho", "texto")} for f in fontes],
             "resposta": None, "modo": "trechos", "ia": ia.descricao()}
    if not fontes:
        saida["resposta"] = "Não encontrei artigos sobre isso nas normas cadastradas."
        return saida
    if not ia.ligada():
        return saida
    numeradas = "\n\n".join(f"[{n}] {f['norma']}, art. {f['artigo']}{' (' + f['caminho'] + ')' if f['caminho'] else ''}:\n{f['texto'][:1500]}"
                            for n, f in enumerate(fontes, 1))
    try:
        saida["resposta"] = ia.gerar(SISTEMA, f"Artigos:\n{numeradas}\n\nPergunta: {pergunta}", 600)
        saida["modo"] = "ia"
    except ia.SemIA as e:
        saida["aviso"] = str(e)
    return saida
