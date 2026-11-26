"""Modelo de linguagem do LEGIS: resumos de ata, respostas sobre discursos e o assistente do cidadão.

Escolha pela variável IA:
* ``ollama`` (recomendado no on-premise): modelo aberto rodando no próprio servidor (Docker
  ``ollama/ollama``), sem custo e sem dados saindo da Câmara. OLLAMA_URL (padrão
  http://ollama:11434) e IA_MODELO (padrão qwen2.5:3b).
* ``claude``: API da Anthropic pelo SDK oficial (``pip install anthropic``), modelo
  IA_MODELO (padrão claude-opus-5-5). Credenciais do ambiente (ANTHROPIC_API_KEY ou perfil do
  ``ant auth login``). Os textos saem do servidor da Câmara.
* ``falso``: respostas determinísticas, para os testes.
* ``0`` ou vazio: desligada. Cada recurso tem um modo simples sem IA.
"""
import json
import os
import urllib.request

PROVEDOR = os.environ.get("IA", "").strip().lower()
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/")
MODELO = os.environ.get("IA_MODELO", "")


class SemIA(RuntimeError):
    pass


def ligada() -> bool:
    return PROVEDOR in ("ollama", "claude", "falso")


def descricao() -> str:
    if PROVEDOR == "ollama":
        return f"local (Ollama, {MODELO or 'qwen2.5:3b'})"
    if PROVEDOR == "claude":
        return f"Claude ({MODELO or 'claude-opus-5-5'}) — os textos saem do servidor"
    if PROVEDOR == "falso":
        return "simulada (testes)"
    return "desligada"


def gerar(sistema: str, pedido: str, max_tokens: int = 2000) -> str:
    """Uma pergunta, uma resposta em texto. Erros viram SemIA (o recurso cai para o modo simples)."""
    if PROVEDOR == "ollama":
        return _ollama(sistema, pedido, max_tokens)
    if PROVEDOR == "claude":
        return _claude(sistema, pedido, max_tokens)
    if PROVEDOR == "falso":
        return _falso(sistema, pedido)
    raise SemIA("IA desligada neste servidor.")


def _ollama(sistema: str, pedido: str, max_tokens: int) -> str:
    corpo = {"model": MODELO or "qwen2.5:3b", "stream": False,
             "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": pedido}],
             "options": {"num_ctx": 16384, "num_predict": max_tokens, "temperature": 0.2}}
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", json.dumps(corpo).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            resposta = json.loads(r.read())
    except Exception as e:  # serviço fora do ar, modelo não baixado
        raise SemIA(f"O modelo local não respondeu: {e}"[:300])
    texto = (resposta.get("message") or {}).get("content", "").strip()
    if not texto:
        raise SemIA("O modelo local devolveu resposta vazia.")
    return texto


def _claude(sistema: str, pedido: str, max_tokens: int) -> str:
    try:
        import anthropic  # SDK oficial; só é necessário com IA=claude
    except ImportError:
        raise SemIA("Instale o SDK da Anthropic no servidor (pip install anthropic).")
    client = anthropic.Anthropic()
    try:
        # streaming: entradas longas (transcrições de uma sessão inteira) não estouram o tempo da requisição
        with client.beta.messages.stream(
            model=MODELO or "claude-opus-5-5",
            max_tokens=max(max_tokens, 4000),
            system=sistema,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": pedido}],
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.APIConnectionError:
        raise SemIA("Sem conexão com a API da Anthropic.")
    except anthropic.RateLimitError:
        raise SemIA("Limite de uso da API atingido; tente de novo em instantes.")
    except anthropic.APIStatusError as e:
        raise SemIA(f"A API da Anthropic recusou o pedido ({e.status_code}).")
    if msg.stop_reason == "refusal":
        raise SemIA("O modelo recusou este pedido.")
    texto = "".join(b.text for b in msg.content if b.type == "text").strip()
    if not texto:
        raise SemIA("Resposta vazia do modelo.")
    return texto


def _falso(sistema: str, pedido: str) -> str:
    primeira = next((l.strip() for l in pedido.splitlines() if l.strip() and not l.startswith("#")), "")
    return f"Resumo automático: {primeira[:160]}"
