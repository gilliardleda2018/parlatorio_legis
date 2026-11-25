"""Semelhança entre textos em português, sem bibliotecas externas.

Normaliza (minúsculas, sem acento), descarta palavras vazias, reduz cada
palavra a um radical simples, expande alguns sinônimos do vocabulário
legislativo municipal e compara textos por TF-IDF e cosseno.
"""
import math
import re
import unicodedata
from collections import Counter

PALAVRAS_VAZIAS = set("""
a ao aos as ate com como da das de dela dele deles do dos e ela elas ele eles em entre era essa esse esta este eu foi
for ha isso isto ja la lhe mais mas me mesmo meu minha muito na nao nas nem no nos nossa nosso num numa o os ou para
pela pelas pelo pelos por qual quando que quem se sem ser seu sua suas seus so sob sobre sua tambem te tem tendo ter
um uma umas uns vai vez voce sao foram sera serao cada outro outra outros outras todo toda todos todas onde mesma
ainda apos assim bem desde deve devem dispoe institui cria dispõe fica ficam municipal municipio municipios
programa politica lei projeto poder executivo indica ambito publico publica publicos publicas forma meio meios
pessoa pessoas situacao cidade cidades populacao garantir acesso
""".split())

# aproxima termos que aparecem com palavras diferentes nas ementas
SINONIMOS = {
    "horta": "agricultura", "hortas": "agricultura", "pomar": "agricultura", "agroecologica": "agricultura",
    "onibus": "transporte", "coletivo": "transporte", "passe": "tarifa", "passagem": "tarifa", "gratuidade": "tarifa",
    "bicicleta": "ciclovia", "ciclofaixa": "ciclovia", "ciclofaixas": "ciclovia", "ciclistas": "ciclovia",
    "pedestres": "pedestre", "calcada": "pedestre", "calcadas": "pedestre",
    "creche": "infancia", "creches": "infancia", "bebe": "infancia", "gestante": "infancia", "gestantes": "infancia",
    "alfabetizar": "alfabetizacao", "leitura": "alfabetizacao",
    "fome": "alimentar", "alimentos": "alimentar", "alimentacao": "alimentar", "restaurante": "alimentar",
    "moradores": "moradia", "habitacao": "moradia", "habitacional": "moradia",
    "rua": "rua", "sem-teto": "rua", "albergue": "rua", "abrigo": "rua",
    "violencia": "violencia", "feminicidio": "violencia", "protetivas": "violencia",
    "transito": "viaria", "acidentes": "viaria", "velocidade": "viaria",
    "arborizacao": "verde", "arvores": "verde", "pracas": "verde", "parque": "verde", "parques": "verde",
    "imposto": "tributo", "iptu": "tributo", "desconto": "tributo",
    "renda": "renda", "transferencia": "renda", "beneficio": "renda",
}

SUFIXOS = ("amentos", "imentos", "amento", "imento", "mente", "acoes", "icoes", "idades", "idade", "acao", "icao",
           "ismos", "ismo", "istas", "ista", "ivos", "ivas", "ivo", "iva", "icos", "icas", "ico", "ica", "oes", "aes",
           "ais", "eis", "ores", "or", "es", "os", "as", "s")


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def radical(p: str) -> str:
    for suf in SUFIXOS:
        if p.endswith(suf) and len(p) - len(suf) >= 4:
            return p[: -len(suf)]
    return p


def termos(texto: str) -> list[str]:
    saida = []
    for p in re.findall(r"[a-z\-]+", _sem_acento(str(texto or "").lower())):
        p = p.strip("-")
        if len(p) < 3 or p in PALAVRAS_VAZIAS:
            continue
        saida.append(radical(SINONIMOS.get(p, p)))
    return saida


class Indice:
    """TF-IDF sobre um conjunto de documentos; compara qualquer texto com eles."""

    def __init__(self, documentos: list[str]):
        contagens = [Counter(termos(d)) for d in documentos]
        n = len(contagens) or 1
        df = Counter(t for c in contagens for t in c)
        self.idf = {t: math.log((1 + n) / (1 + k)) + 1 for t, k in df.items()}
        self.vetores = [self.vetor_de(c) for c in contagens]

    def vetor_de(self, contagem: Counter) -> dict[str, float]:
        v = {t: (1 + math.log(f)) * self.idf.get(t, 1.0) for t, f in contagem.items()}
        norma = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norma for t, x in v.items()}

    def vetor(self, texto: str) -> dict[str, float]:
        return self.vetor_de(Counter(termos(texto)))

    @staticmethod
    def cosseno(a: dict, b: dict) -> float:
        if len(a) > len(b):
            a, b = b, a
        return sum(x * b.get(t, 0.0) for t, x in a.items())

    def termos_comuns(self, a: dict, b: dict, n: int = 3) -> list[str]:
        comuns = sorted(((a[t] * b[t], t) for t in a.keys() & b.keys()), reverse=True)
        return [t for _, t in comuns[:n]]
