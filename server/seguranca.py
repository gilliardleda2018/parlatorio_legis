"""Segurança de acesso: PIN com hash, bloqueio por tentativas e regras de PIN fraco.

* PINs nunca ficam em texto: PBKDF2-SHA256 com sal individual (`pbkdf2$iteracoes$sal$hash`).
* Depois de 5 tentativas erradas para a mesma conta (ou 20 do mesmo endereço),
  novas tentativas ficam bloqueadas por 15 minutos.
* PINs óbvios (0000, 1234, 1111, 9876…) são recusados e, se ainda estiverem em uso,
  o sistema obriga a troca no primeiro acesso.
"""
import hashlib
import secrets
import threading
import time

ITERACOES = 200_000
LIMITE_CONTA = 5
LIMITE_ENDERECO = 20
JANELA = 15 * 60  # segundos


def hash_pin(pin: str) -> str:
    sal = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", str(pin).encode(), bytes.fromhex(sal), ITERACOES).hex()
    return f"pbkdf2${ITERACOES}${sal}${h}"


def e_hash(valor) -> bool:
    return isinstance(valor, str) and valor.startswith("pbkdf2$")


def verificar_pin(pin: str, guardado: str) -> bool:
    if not guardado:
        return False
    if not e_hash(guardado):  # compatibilidade: valor antigo em texto (é convertido ao carregar)
        return secrets.compare_digest(str(pin), str(guardado))
    _, it, sal, h = guardado.split("$")
    calc = hashlib.pbkdf2_hmac("sha256", str(pin).encode(), bytes.fromhex(sal), int(it)).hex()
    return secrets.compare_digest(calc, h)


def pin_fraco(pin: str) -> bool:
    p = str(pin)
    if len(set(p)) == 1:
        return True
    sobe = "01234567890123456789"
    desce = sobe[::-1]
    return p in sobe or p in desce or p in ("1212", "2121", "1122", "6969", "2580", "1004", "0007")


def validar_novo_pin(pin: str, minimo: int = 4) -> str | None:
    """Devolve a mensagem de erro, ou None se o PIN serve."""
    p = str(pin or "")
    if not p.isdigit() or not minimo <= len(p) <= 8:
        return f"O PIN deve ter de {minimo} a 8 números."
    if pin_fraco(p):
        return "PIN muito fácil de adivinhar (repetido ou em sequência). Escolha outro."
    return None


class Bloqueio:
    """Conta falhas de login por conta e por endereço IP (só em memória)."""

    def __init__(self):
        self._falhas: dict[str, list[float]] = {}
        self._trava = threading.Lock()

    def _recentes(self, chave: str) -> list[float]:
        agora = time.time()
        lista = [t for t in self._falhas.get(chave, []) if agora - t < JANELA]
        self._falhas[chave] = lista
        return lista

    def espera(self, conta: str, ip: str) -> int:
        """Segundos até poder tentar de novo (0 = liberado)."""
        with self._trava:
            for chave, limite in ((f"c:{conta}", LIMITE_CONTA), (f"i:{ip}", LIMITE_ENDERECO)):
                lista = self._recentes(chave)
                if len(lista) >= limite:
                    return int(JANELA - (time.time() - lista[-limite])) + 1
            return 0

    def falhou(self, conta: str, ip: str):
        with self._trava:
            agora = time.time()
            for chave in (f"c:{conta}", f"i:{ip}"):
                self._recentes(chave).append(agora)

    def acertou(self, conta: str):
        with self._trava:
            self._falhas.pop(f"c:{conta}", None)
