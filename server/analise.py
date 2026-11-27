"""Análise prévia de uma proposição, como apoio à Mesa e às comissões (inspirada no programa Ulysses da Câmara).

Duas camadas:
  * regras, sempre disponíveis (sem IA): comissões sugeridas pelas áreas e pelo texto, e alertas jurídicos
    comuns no processo legislativo municipal (vício de iniciativa, renúncia de receita, aumento de despesa,
    competência privativa da União);
  * IA, se ligada: resumo em linguagem simples e observações.

É sempre um RASCUNHO: só vale depois que uma pessoa revisa (como exigem as regras de IA do Senado e da Câmara).
Não substitui o parecer jurídico nem o da comissão.
"""
import re
import time
import unicodedata

import ia

COMISSAO_CCJ = "Constituição, Justiça e Redação"
COMISSAO_FINANCAS = "Finanças, Orçamento e Tributação"
COMISSOES_POR_AREA = {
    "saude": "Saúde e Assistência Social", "assistencia": "Saúde e Assistência Social",
    "educacao": "Educação, Cultura e Esporte", "cultura": "Educação, Cultura e Esporte",
    "infraestrutura": "Obras, Urbanismo, Transporte e Meio Ambiente", "transporte": "Obras, Urbanismo, Transporte e Meio Ambiente",
    "habitacao": "Obras, Urbanismo, Transporte e Meio Ambiente", "meio_ambiente": "Obras, Urbanismo, Transporte e Meio Ambiente",
    "economia": "Desenvolvimento Econômico, Agricultura e Turismo", "seguranca": "Segurança Pública e Defesa Civil",
    "direitos": "Direitos Humanos, Cidadania e Defesa do Consumidor", "administracao": "Administração Pública e Servidores",
}
# (padrão no texto sem acento, comissão)
COMISSOES_POR_TEXTO = [
    (r"\b(saude|hospital|ubs|vacina|medicament)", "Saúde e Assistência Social"),
    (r"\b(escola|ensino|alun|professor|creche|biblioteca|cultura|esporte)", "Educação, Cultura e Esporte"),
    (r"\b(obra|pavimenta|logradouro|transito|transporte|onibus|ambient|arboriza|residuo|lixo|saneamento)",
     "Obras, Urbanismo, Transporte e Meio Ambiente"),
    (r"\b(agricult|feira|turismo|empreend|emprego|comercio)", "Desenvolvimento Econômico, Agricultura e Turismo"),
]
# (padrão, gravidade, título, explicação)
ALERTAS = [
    (r"\bcri(a|ar|acao|ado|ada)\b.{0,40}\b(cargos?|secretarias?|orgaos?|autarquia|fundacao|departamento)\b",
     "alto", "Possível vício de iniciativa: estrutura da administração",
     "Criar cargos, órgãos ou secretarias é de iniciativa privativa do Prefeito (art. 61, §1º, II, da Constituição, por simetria; "
     "veja também a Lei Orgânica). Projeto de vereador nesse tema costuma ser vetado ou declarado inconstitucional."),
    (r"\b(servidor(es)?|remuneracao|vencimentos?|gratificac|plano de carreira|regime juridico|adicional de)\b",
     "alto", "Possível vício de iniciativa: servidores públicos",
     "Regime jurídico, remuneração e carreira dos servidores do Executivo são de iniciativa do Prefeito (art. 61, §1º, II, a e c, da CF, por simetria)."),
    (r"\b(isencao|isenta|anistia|remissao|reducao de aliquota|desconto no (iptu|iss)|beneficio fiscal)\b",
     "medio", "Renúncia de receita",
     "Benefício tributário precisa de estimativa do impacto orçamentário e financeiro e de medidas de compensação "
     "(art. 113 do ADCT e art. 14 da Lei de Responsabilidade Fiscal)."),
    (r"\b(despesas?|custeio|dotac(ao|oes) orcamentaria|correrao por conta|recursos financeiros|repasse|subsidio)\b",
     "medio", "Aumento de despesa",
     "Indique a fonte de custeio e a estimativa de impacto (arts. 16 e 17 da LRF). Lei de vereador que gera despesa sem "
     "mexer na estrutura do Executivo pode ser válida (Tema 917 do STF), mas precisa ser executável."),
    (r"\b(o poder executivo (devera|fica obrigado)|compete a secretaria|cabera a secretaria|o executivo regulamentara)\b",
     "baixo", "Atribuições ao Executivo",
     "Impor tarefas a órgãos do Executivo pode ser visto como interferência na administração (separação de poderes). "
     "Prefira redação autorizativa ou de diretrizes, e confira o Tema 917 do STF."),
    (r"\b(direito civil|direito penal|crime|pena de|direito do trabalho|telecomunica|energia eletrica|codigo de transito|multa de transito|desapropria)\b",
     "alto", "Competência da União",
     "Esses temas são de competência legislativa privativa da União (art. 22 da Constituição). O município legisla sobre "
     "assuntos de interesse local e suplementa a legislação federal e estadual (art. 30)."),
    (r"\b(denomina|denominacao|da nome|nomeia)\b.{0,40}\b(rua|avenida|praca|logradouro|escola|predio|ponte|estrada)\b",
     "info", "Denominação de bem público",
     "Confira as regras da lei municipal de denominação (em geral, homenagem a pessoa falecida, com biografia e sem nome repetido)."),
    (r"\b(dia municipal|semana municipal|mes municipal|calendario oficial|data comemorativa)\b",
     "info", "Data comemorativa", "Inclusão no calendário oficial: matéria de baixo impacto, normalmente de livre iniciativa."),
]

SISTEMA = ("Você ajuda a Mesa de uma Câmara Municipal brasileira a entender uma proposição. Escreva em português simples, "
           "para qualquer cidadão: primeiro um resumo de até 3 frases do que o projeto muda na vida das pessoas; depois, "
           "em até 3 tópicos curtos começando com '- ', pontos que a comissão deve conferir. Não invente fatos, números "
           "nem artigos de lei que não estejam no texto. Não dê opinião política nem recomende aprovar ou rejeitar.")


def _norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def regras(p: dict) -> dict:
    texto = _norm(f"{p.get('ementa', '')}\n{p.get('texto', '')}")
    comissoes = [COMISSAO_CCJ]
    for a in p.get("areas") or []:
        c = COMISSOES_POR_AREA.get(a)
        if c and c not in comissoes:
            comissoes.append(c)
    for padrao, c in COMISSOES_POR_TEXTO:
        if re.search(padrao, texto) and c not in comissoes:
            comissoes.append(c)
    alertas = []
    for padrao, grav, titulo, expl in ALERTAS:
        m = re.search(padrao, texto)
        if m:
            alertas.append({"gravidade": grav, "titulo": titulo, "texto": expl, "trecho": m.group(0)[:80]})
    if any(a["titulo"] in ("Renúncia de receita", "Aumento de despesa") for a in alertas) and COMISSAO_FINANCAS not in comissoes:
        comissoes.insert(1, COMISSAO_FINANCAS)
    ordem = {"alto": 0, "medio": 1, "baixo": 2, "info": 3}
    alertas.sort(key=lambda a: ordem[a["gravidade"]])
    return {"comissoes": comissoes, "alertas": alertas}


def analisar(p: dict) -> dict:
    """Rascunho da análise. Com IA: também o resumo em linguagem simples (cai para as regras se a IA falhar)."""
    r = regras(p)
    saida = {**r, "resumo": None, "observacoes": None, "motor": "regras", "gerado": int(time.time() * 1000),
             "revisada": False, "revisor": None, "aviso": None}
    if ia.ligada():
        pedido = (f"Proposição {p.get('numero', '')}: {p.get('ementa', '')}\nAutoria: {p.get('autor') or 'não informada'}\n\n"
                  f"Texto:\n{(p.get('texto') or '(texto não cadastrado; use só a ementa)')[:6000]}")
        try:
            resposta = ia.gerar(SISTEMA, pedido, 500).strip()
            linhas = resposta.splitlines()
            topicos = [l.strip()[2:].strip() for l in linhas if l.strip().startswith(("- ", "• "))]
            corpo = " ".join(l.strip() for l in linhas if l.strip() and not l.strip().startswith(("- ", "• ")))
            saida.update(resumo=corpo[:900] or None, observacoes=topicos[:4] or None, motor=ia.descricao())
        except ia.SemIA as e:
            saida["aviso"] = f"IA indisponível ({e}); só as regras foram aplicadas."
    return saida
