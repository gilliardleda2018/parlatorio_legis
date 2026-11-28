"""Modelo de cidade: um arquivo camara.json descreve tudo o que muda de uma Câmara para outra.

O mesmo LEGIS serve qualquer município do Brasil. O que é de cada Câmara fica num arquivo só
(cidades/<nome>/camara.json, ver cidades/LEIA-ME.md), usado em três lugares:

* na geração do pacote da Câmara (packaging/construir-cidade.sh --modelo …), que embute o modelo no .deb;
* na instalação direta (instalar-legis.sh --modelo …), sem gerar pacote nenhum;
* na primeira partida do servidor (LEGIS_MODELO=…), que aplica as preferências ao banco novo.

O modelo é aplicado UMA vez por banco: depois disso vale o que a Câmara mudar no painel. Para aplicar de novo:
sudo legis modelo --reaplicar.

Sem dependências: o gerador de pacotes importa este módulo sem subir o servidor.
"""
import copy
import hashlib
import json
import re
import unicodedata
from pathlib import Path

VERSAO_MODELO = 1
UFS = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI",
       "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"}
BITRATES = (64, 96, 128, 160, 192)
CONTATOS = ("endereco", "telefone", "whatsapp", "email", "site", "instagram", "facebook", "youtube", "linkedin")
FASES = ("pe", "ge", "cl", "od", "ev", "ep", "tl")

# Tudo o que uma Câmara pode personalizar, com o padrão de quem não disser nada.
PADRAO = {
    "versaoModelo": VERSAO_MODELO,
    "uf": "",
    "municipio": "",
    "nome": "",                 # nome curto: https://<nome>.local na rede; padrão: o município sem acento
    "camara": "",               # padrão: Câmara Municipal de <município>
    "brasao": "",               # arquivo ao lado do camara.json (PNG, JPEG ou WebP, até 1,5 MB)
    "congelada": False,         # true: novas versões do LEGIS não regeneram o pacote desta Câmara sozinhas
    "visual": {"destaque": "#1f8a4c", "tema": "escuro"},
    "contatos": {k: "" for k in CONTATOS},
    "radio": {"ligada": True, "nome": "", "bitrate": 128},          # nome padrão: <Câmara> — Rádio
    "plenarioVirtual": {"ligado": True, "videoconferencia": "propria"},  # propria | publica | nenhuma
    "certificado": {"nomesExtras": []},   # domínios ou IPs a mais no certificado HTTPS (ex.: camara.cidade.ma.leg.br)
    "youtube": {"link": ""},
    "portal": {"url": ""},   # portal legislativomunicipal.com da Câmara (em branco: descoberto pelo nome do município)
    "recursos": {"transcricao": "auto", "ia": "auto"},              # auto | sim | nao
    "regimento": {
        "tempos": {"alerta": 60, "aparte": 60, "ordem": 180},       # segundos
        "fases": {},                                                # id da fase -> {"min": …, "totalMin": …}
    },
}


class ModeloInvalido(ValueError):
    pass


def sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", sem_acento(texto).lower()).strip("-")


def _nome_seguro(rotulo: str, valor, limite: int, obrigatorio=True) -> str:
    """Município e Câmara entram em scripts que rodam como root: só letras (com acento), dígitos, espaço e .-'"""
    valor = " ".join(str(valor or "").split())
    if not valor and not obrigatorio:
        return ""
    if not valor or len(valor) > limite or not all(c.isalnum() or c in " .-'" for c in valor):
        raise ModeloInvalido(f"{rotulo} inválido: use só letras, números, espaço e . - ' (até {limite} caracteres): {valor!r}")
    return valor


def _url(rotulo: str, valor, padrao=r"https://[A-Za-z0-9./?=_@%&:+~#-]+") -> str:
    valor = str(valor or "").strip()
    if valor and not re.fullmatch(padrao, valor, re.ASCII):
        raise ModeloInvalido(f"{rotulo} inválido (deve começar com https:// e não ter espaços nem ; $ ` aspas): {valor!r}")
    return valor


def _mesclar(padrao: dict, dado: dict, caminho="") -> dict:
    """Completa o modelo com os padrões e recusa chaves desconhecidas (erro de digitação vira aviso, não silêncio)."""
    saida = copy.deepcopy(padrao)
    for k, v in dado.items():
        if k.startswith("_"):            # "_comentario" e afins: anotações de quem preencheu
            continue
        if k not in padrao:
            raise ModeloInvalido(f"Campo desconhecido no modelo: {caminho}{k}")
        if isinstance(padrao[k], dict) and padrao[k] and k != "fases":
            if not isinstance(v, dict):
                raise ModeloInvalido(f"{caminho}{k} deve ser um objeto {{…}}")
            saida[k] = _mesclar(padrao[k], v, f"{caminho}{k}.")
        else:
            saida[k] = v
    return saida


def validar(dado: dict) -> dict:
    """Devolve o modelo completo e conferido. Levanta ModeloInvalido com uma mensagem para quem preencheu."""
    if not isinstance(dado, dict):
        raise ModeloInvalido("O modelo deve ser um objeto JSON {…}.")
    m = _mesclar(PADRAO, dado)
    if int(m["versaoModelo"]) > VERSAO_MODELO:
        raise ModeloInvalido(f"Modelo da versão {m['versaoModelo']}: atualize o LEGIS (esta versão lê até a {VERSAO_MODELO}).")
    m["versaoModelo"] = VERSAO_MODELO
    m["uf"] = str(m["uf"] or "").strip().upper()
    if m["uf"] not in UFS:
        raise ModeloInvalido(f"Estado inválido: {m['uf']!r} (use a sigla, ex.: MA).")
    m["municipio"] = _nome_seguro("Município", m["municipio"], 80)
    m["camara"] = _nome_seguro("Nome da Câmara", m["camara"], 120, obrigatorio=False) or f"Câmara Municipal de {m['municipio']}"
    m["nome"] = slug(str(m["nome"] or "") or m["municipio"])
    if not re.fullmatch(r"[a-z0-9-]{1,40}", m["nome"]):
        raise ModeloInvalido("Nome curto inválido (letras minúsculas, números e -, até 40).")
    m["brasao"] = str(m["brasao"] or "").strip()
    if m["brasao"] and (Path(m["brasao"]).name != m["brasao"] or m["brasao"].startswith(".")):
        raise ModeloInvalido("brasao: informe só o nome do arquivo que está ao lado do camara.json.")
    m["congelada"] = bool(m["congelada"])

    v = m["visual"]
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(v["destaque"])):
        raise ModeloInvalido("visual.destaque: use uma cor como #1f8a4c.")
    if v["tema"] not in ("escuro", "claro"):
        raise ModeloInvalido("visual.tema: escuro ou claro.")

    for k in CONTATOS:
        valor = " ".join(str(m["contatos"][k] or "").split())[:200]
        if k in ("site", "instagram", "facebook", "youtube", "linkedin") and valor:
            valor = _url(f"contatos.{k}", valor if valor.startswith("http") else "https://" + valor.lstrip("/@"))
        if k == "email" and valor and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", valor):
            raise ModeloInvalido("contatos.email inválido.")
        m["contatos"][k] = valor

    r = m["radio"]
    r["ligada"] = bool(r["ligada"])
    r["nome"] = " ".join(str(r["nome"] or "").split())[:120] or f"{m['camara']} — Rádio"
    if int(r["bitrate"]) not in BITRATES:
        raise ModeloInvalido(f"radio.bitrate: use um destes valores {BITRATES}.")
    r["bitrate"] = int(r["bitrate"])

    pv = m["plenarioVirtual"]
    pv["ligado"] = bool(pv["ligado"])
    if pv["videoconferencia"] not in ("propria", "publica", "nenhuma"):
        raise ModeloInvalido("plenarioVirtual.videoconferencia: propria (Jitsi da Câmara), publica (meet.jit.si) ou nenhuma.")

    nomes = m["certificado"]["nomesExtras"]
    if not isinstance(nomes, list) or len(nomes) > 20:
        raise ModeloInvalido("certificado.nomesExtras: lista de até 20 nomes ou IPs.")
    m["certificado"]["nomesExtras"] = []
    for n in nomes:
        n = str(n).strip().lower()
        if not re.fullmatch(r"[a-z0-9.:-]{1,253}", n) or ".." in n:
            raise ModeloInvalido(f"certificado.nomesExtras: nome inválido {n!r}.")
        m["certificado"]["nomesExtras"].append(n)

    m["youtube"]["link"] = _url("youtube.link", m["youtube"]["link"])
    m["portal"]["url"] = str(m["portal"]["url"] or "").strip().rstrip("/")
    if m["portal"]["url"] and not re.fullmatch(r"https://(www\.)?[a-z0-9-]{2,60}\.legislativomunicipal\.com", m["portal"]["url"]):
        raise ModeloInvalido("portal.url: só a plataforma legislativomunicipal.com (ex.: https://www.cmtuntum.legislativomunicipal.com).")
    for k in ("transcricao", "ia"):
        if m["recursos"][k] not in ("auto", "sim", "nao"):
            raise ModeloInvalido(f"recursos.{k}: auto, sim ou nao.")

    t = m["regimento"]["tempos"]
    for k, (minimo, maximo) in {"alerta": (5, 600), "aparte": (10, 900), "ordem": (30, 1800)}.items():
        if not isinstance(t[k], int) or not minimo <= t[k] <= maximo:
            raise ModeloInvalido(f"regimento.tempos.{k}: número de segundos entre {minimo} e {maximo}.")
    fases = m["regimento"]["fases"]
    if not isinstance(fases, dict):
        raise ModeloInvalido("regimento.fases deve ser um objeto {\"pe\": {\"min\": 5, \"totalMin\": 60}, …}.")
    for fid, f in fases.items():
        if fid not in FASES or not isinstance(f, dict) or set(f) - {"min", "totalMin"}:
            raise ModeloInvalido(f"regimento.fases.{fid}: fases válidas {FASES}, cada uma com min e totalMin.")
        for k, valor in f.items():
            if not isinstance(valor, int) or not 0 <= valor <= 600:
                raise ModeloInvalido(f"regimento.fases.{fid}.{k}: minutos entre 0 e 600.")
    return m


def ler(caminho) -> dict:
    caminho = Path(caminho)
    try:
        dado = json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ModeloInvalido(f"{caminho}: JSON inválido na linha {e.lineno}: {e.msg}")
    return validar(dado)


def impressao(m: dict) -> str:
    """Identifica a versão do modelo (muda quando o conteúdo muda)."""
    return hashlib.sha256(json.dumps(m, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def sim(valor: str, padrao: bool) -> bool:
    return padrao if valor == "auto" else valor == "sim"


def aplicar(S: dict, m: dict, agora_ms: int) -> list[str]:
    """Aplica as preferências do modelo ao estado do servidor (chamar com a trava). Devolve o que mudou."""
    feito = []
    casa = S["casa"]
    casa.update(nome=m["camara"], destaque=m["visual"]["destaque"], tema=m["visual"]["tema"])
    feito.append(f"Câmara: {m['camara']}")
    contatos = {k: v for k, v in m["contatos"].items() if v}
    if contatos:
        S["rodape"]["camara"].update(contatos)
        feito.append("contatos do rodapé")
    if m["radio"]["ligada"]:
        S["transmissao"]["nome"] = m["radio"]["nome"]
        S["_transmissao"]["bitrate"] = m["radio"]["bitrate"]
        feito.append(f"rádio: {m['radio']['nome']} ({m['radio']['bitrate']} kbps)")
    pv = m["plenarioVirtual"]
    if pv["ligado"] and pv["videoconferencia"] != "nenhuma":
        S["remoto"]["ativo"] = True
        if not S["_remoto"].get("sala"):
            import secrets
            S["_remoto"]["sala"] = "parlatorio-" + secrets.token_urlsafe(12).replace("_", "").replace("-", "")
        if pv["videoconferencia"] == "publica":
            S["remoto"]["servidor"] = "https://meet.jit.si"
        feito.append("plenário virtual e sessão remota prontos")
    if m["youtube"]["link"]:
        S["youtube"]["link"] = m["youtube"]["link"]
        feito.append("link do YouTube")
    if m["portal"]["url"]:
        S["casa"]["portal"] = m["portal"]["url"]
        if not S["rodape"]["camara"].get("site"):
            S["rodape"]["camara"]["site"] = m["portal"]["url"]
        feito.append("portal legislativo")
    S["cfg"].update(m["regimento"]["tempos"])
    for f in S["fases"]:
        f.update(m["regimento"]["fases"].get(f["id"], {}))
    S["_modelo"] = {"nome": m["nome"], "impressao": impressao(m), "aplicado": agora_ms}
    return feito
