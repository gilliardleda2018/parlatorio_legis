"""Passkey / WebAuthn: confirmação do voto e da presença com a biometria (ou o bloqueio de tela) do próprio celular.

Inspirado no Infoleg da Câmara dos Deputados (voto pelo aplicativo com identificação biométrica) e no ponto
biométrico das câmaras municipais. Tudo com a biblioteca padrão do Python:

* decodificador CBOR mínimo (mapas, arrays, bytes, texto, inteiros, simples) para o attestationObject e a chave COSE;
* verificação de assinatura em Python puro: ECDSA P-256 (COSE -7, ES256) e RSA PKCS#1 v1.5 (COSE -257, RS256).
  Escolhemos a aritmética em Python em vez de chamar o `openssl` por subprocess: verificar é só matemática com a
  chave PÚBLICA (não há segredo a vazar por tempo de execução), não cria arquivos temporários, não depende da
  versão/saída do openssl instalado e custa poucos milissegundos por voto;
* desafios (challenges) aleatórios de uso único, válidos por 2 minutos, guardados só em memória por conta;
* attestation "none": o aparelho não é identificado pelo fabricante; a confiança vem do cadastro feito pelo próprio
  vereador logado, que confirma o PIN para cadastrar (e para remover) uma passkey.

O rpId é o nome do host atual. WebAuthn não aceita endereço IP como rpId e exige contexto seguro (HTTPS, ou
http://localhost): pelo IP a tela explica que é preciso abrir pelo nome (https://<maquina>.local:8443 ou o domínio).
Só para testes automáticos, PASSKEY_ORIGEM e PASSKEY_RP fixam a origem e o rpId esperados (ex.: http://127.0.0.1:8090);
em produção, deixe-as vazias: vale o host HTTPS do pedido.
"""
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import struct
import threading
import time
from urllib.parse import urlparse

VALIDADE_DESAFIO = 120  # segundos
MAX_DESAFIOS_CONTA = 5
MAX_PASSKEYS_CONTA = 10
MODOS = ("nao", "opcional", "exigir")  # desligado | passkey ou PIN | passkey obrigatória (para quem cadastrou)
ACOES_PLENARIO = {"votar", "presenca", "presencaRemota"}
ALG_ES256, ALG_RS256 = -7, -257


class ErroPasskey(Exception):
    pass


# ---------------------------------------------------------------- base64url
def b64u(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


def de_b64u(texto) -> bytes:
    if not isinstance(texto, str) or len(texto) > 64_000 or not re.fullmatch(r"[A-Za-z0-9_\-+/=]*", texto):
        raise ErroPasskey("Dado em base64 inválido.")
    t = texto.replace("+", "-").replace("/", "_").rstrip("=")
    try:
        return base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))
    except ValueError:
        raise ErroPasskey("Dado em base64 inválido.")


# ---------------------------------------------------------------- CBOR mínimo (RFC 8949)
def _cbor(d: bytes, i: int, prof: int):
    if prof > 16:
        raise ErroPasskey("CBOR aninhado demais.")
    if i >= len(d):
        raise ErroPasskey("CBOR truncado.")
    ini = d[i]
    maior, info = ini >> 5, ini & 31
    i += 1
    if maior == 7:
        if info == 20:
            return False, i
        if info == 21:
            return True, i
        if info in (22, 23):
            return None, i
        tam = {25: 2, 26: 4, 27: 8}.get(info)
        if not tam or i + tam > len(d):
            raise ErroPasskey("CBOR: valor simples não suportado.")
        bruto = d[i:i + tam]
        if tam == 2:  # meia precisão
            return struct.unpack(">e", bruto)[0], i + tam
        return struct.unpack(">f" if tam == 4 else ">d", bruto)[0], i + tam
    if info < 24:
        arg = info
    elif info in (24, 25, 26, 27):
        tam = 1 << (info - 24)
        if i + tam > len(d):
            raise ErroPasskey("CBOR truncado.")
        arg = int.from_bytes(d[i:i + tam], "big")
        i += tam
    else:
        raise ErroPasskey("CBOR: tamanho indefinido não suportado.")
    if maior == 0:
        return arg, i
    if maior == 1:
        return -1 - arg, i
    if maior in (2, 3):
        if i + arg > len(d):
            raise ErroPasskey("CBOR truncado.")
        bruto = d[i:i + arg]
        if maior == 2:
            return bytes(bruto), i + arg
        try:
            return bruto.decode("utf-8"), i + arg
        except UnicodeDecodeError:
            raise ErroPasskey("CBOR: texto inválido.")
    if maior == 4:
        if arg > len(d):
            raise ErroPasskey("CBOR truncado.")
        lista = []
        for _ in range(arg):
            v, i = _cbor(d, i, prof + 1)
            lista.append(v)
        return lista, i
    if maior == 5:
        if arg > len(d):
            raise ErroPasskey("CBOR truncado.")
        mapa = {}
        for _ in range(arg):
            k, i = _cbor(d, i, prof + 1)
            if isinstance(k, (list, dict)):
                raise ErroPasskey("CBOR: chave de mapa inválida.")
            v, i = _cbor(d, i, prof + 1)
            mapa[k] = v
        return mapa, i
    raise ErroPasskey("CBOR: tipo não suportado.")  # 6 (tags)


def cbor_ler(d: bytes, i: int = 0):
    """Decodifica um item a partir de `i`; devolve (valor, posição seguinte)."""
    return _cbor(bytes(d), i, 0)


def cbor_decodificar(d: bytes):
    """Decodifica exatamente um item (sobra de bytes é erro)."""
    v, i = cbor_ler(d)
    if i != len(d):
        raise ErroPasskey("CBOR com bytes sobrando.")
    return v


# ---------------------------------------------------------------- ECDSA P-256 (Python puro, só verificação)
P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
     0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)


def na_curva(x: int, y: int) -> bool:
    return 0 <= x < P and 0 <= y < P and (y * y - (x * x * x - 3 * x + B)) % P == 0


def _dobrar(p):  # coordenadas jacobianas, a = -3
    if p is None:
        return None
    x, y, z = p
    if y == 0:
        return None
    delta = z * z % P
    gama = y * y % P
    beta = x * gama % P
    alfa = 3 * (x - delta) * (x + delta) % P
    x3 = (alfa * alfa - 8 * beta) % P
    z3 = ((y + z) ** 2 - gama - delta) % P
    y3 = (alfa * (4 * beta - x3) - 8 * gama * gama) % P
    return x3, y3, z3


def _somar(p, q):
    if p is None:
        return q
    if q is None:
        return p
    x1, y1, z1 = p
    x2, y2, z2 = q
    z1z1, z2z2 = z1 * z1 % P, z2 * z2 % P
    u1, u2 = x1 * z2z2 % P, x2 * z1z1 % P
    s1, s2 = y1 * z2 * z2z2 % P, y2 * z1 * z1z1 % P
    if u1 == u2:
        return _dobrar(p) if s1 == s2 else None
    h, r = (u2 - u1) % P, (s2 - s1) % P
    hh = h * h % P
    hhh = h * hh % P
    x3 = (r * r - hhh - 2 * u1 * hh) % P
    y3 = (r * (u1 * hh - x3) - s1 * hhh) % P
    return x3, y3, h * z1 * z2 % P


def _shamir(u1: int, u2: int, q: tuple):
    """u1·G + u2·Q (truque de Shamir), resultado em coordenadas afins ou None (ponto no infinito)."""
    gj, qj = (G[0], G[1], 1), (q[0], q[1], 1)
    gq = _somar(gj, qj)
    acc = None
    for bit in range(max(u1.bit_length(), u2.bit_length()) - 1, -1, -1):
        acc = _dobrar(acc)
        a, b = (u1 >> bit) & 1, (u2 >> bit) & 1
        if a and b:
            acc = _somar(acc, gq)
        elif a:
            acc = _somar(acc, gj)
        elif b:
            acc = _somar(acc, qj)
    if acc is None or acc[2] == 0:
        return None
    zi = pow(acc[2], -1, P)
    return acc[0] * zi * zi % P, acc[1] * zi * zi * zi % P


def _der_int(d: bytes, i: int):
    if i + 2 > len(d) or d[i] != 0x02:
        raise ErroPasskey("Assinatura DER inválida.")
    tam = d[i + 1]
    if tam == 0 or tam > 33 or i + 2 + tam > len(d):
        raise ErroPasskey("Assinatura DER inválida.")
    return int.from_bytes(d[i + 2:i + 2 + tam], "big"), i + 2 + tam


def der_para_rs(sig: bytes) -> tuple[int, int]:
    if len(sig) < 8 or sig[0] != 0x30 or sig[1] != len(sig) - 2:
        raise ErroPasskey("Assinatura DER inválida.")
    r, i = _der_int(sig, 2)
    s, i = _der_int(sig, i)
    if i != len(sig):
        raise ErroPasskey("Assinatura DER inválida.")
    return r, s


def ecdsa_p256_verificar(x: int, y: int, assinatura_der: bytes, mensagem: bytes) -> bool:
    try:
        r, s = der_para_rs(assinatura_der)
    except ErroPasskey:
        return False
    if not (1 <= r < N and 1 <= s < N) or not na_curva(x, y):
        return False
    e = int.from_bytes(hashlib.sha256(mensagem).digest(), "big")
    w = pow(s, -1, N)
    ponto = _shamir(e * w % N, r * w % N, (x, y))
    return ponto is not None and ponto[0] % N == r


# ---------------------------------------------------------------- RSA PKCS#1 v1.5 + SHA-256
_DIGEST_INFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")


def rsa_verificar(n: int, e: int, assinatura: bytes, mensagem: bytes) -> bool:
    k = (n.bit_length() + 7) // 8
    if n.bit_length() < 2048 or e < 3 or e % 2 == 0 or len(assinatura) != k:
        return False
    s = int.from_bytes(assinatura, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = _DIGEST_INFO_SHA256 + hashlib.sha256(mensagem).digest()
    esperado = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return secrets.compare_digest(em, esperado)


# ---------------------------------------------------------------- COSE e authenticatorData
def cose_para_chave(cose) -> dict:
    """Chave pública COSE (mapa CBOR) → dicionário guardável em JSON."""
    if not isinstance(cose, dict):
        raise ErroPasskey("Chave pública inválida.")
    kty, alg = cose.get(1), cose.get(3)
    if kty == 2 and alg == ALG_ES256:
        x, y = cose.get(-2), cose.get(-3)
        if cose.get(-1) != 1 or not isinstance(x, bytes) or not isinstance(y, bytes) or len(x) != 32 or len(y) != 32:
            raise ErroPasskey("Chave EC inválida (só P-256).")
        xi, yi = int.from_bytes(x, "big"), int.from_bytes(y, "big")
        if not na_curva(xi, yi):
            raise ErroPasskey("Ponto fora da curva P-256.")
        return {"alg": ALG_ES256, "x": x.hex(), "y": y.hex()}
    if kty == 3 and alg == ALG_RS256:
        n, e = cose.get(-1), cose.get(-2)
        if not isinstance(n, bytes) or not isinstance(e, bytes) or len(n) < 256 or len(n) > 1024 or len(e) > 8:
            raise ErroPasskey("Chave RSA inválida (mínimo 2048 bits).")
        return {"alg": ALG_RS256, "n": n.hex(), "e": int.from_bytes(e, "big")}
    raise ErroPasskey("Algoritmo da passkey não suportado (use ES256 ou RS256).")


def verificar_assinatura(chave: dict, assinatura: bytes, dados: bytes) -> bool:
    if chave.get("alg") == ALG_ES256:
        return ecdsa_p256_verificar(int(chave["x"], 16), int(chave["y"], 16), assinatura, dados)
    if chave.get("alg") == ALG_RS256:
        return rsa_verificar(int(chave["n"], 16), int(chave["e"]), assinatura, dados)
    return False


FLAG_UP, FLAG_UV, FLAG_AT, FLAG_ED = 0x01, 0x04, 0x40, 0x80


def ler_auth_data(ad: bytes) -> dict:
    if len(ad) < 37:
        raise ErroPasskey("authenticatorData curto demais.")
    flags = ad[32]
    r = {"rpIdHash": ad[:32], "flags": flags, "up": bool(flags & FLAG_UP), "uv": bool(flags & FLAG_UV),
         "contador": int.from_bytes(ad[33:37], "big"), "credId": None, "cose": None}
    i = 37
    if flags & FLAG_AT:
        if len(ad) < i + 18:
            raise ErroPasskey("Dados da credencial truncados.")
        tam = int.from_bytes(ad[i + 16:i + 18], "big")
        i += 18
        if tam == 0 or tam > 1023 or len(ad) < i + tam:
            raise ErroPasskey("Identificador da credencial inválido.")
        r["credId"] = ad[i:i + tam]
        r["cose"], i = cbor_ler(ad, i + tam)
    if flags & FLAG_ED:
        _, i = cbor_ler(ad, i)
    if i != len(ad):
        raise ErroPasskey("authenticatorData com bytes sobrando.")
    return r


# ---------------------------------------------------------------- origem e rpId
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?(?::\d{1,5})?")


def e_ip(nome: str) -> bool:
    try:
        ipaddress.ip_address(nome.strip("[]"))
        return True
    except ValueError:
        return False


def origem_esperada(host_header: str, https: bool) -> tuple[str, str]:
    """(origem, rpId) esperados para o pedido. Sem condições (IP, http), ErroPasskey com a explicação."""
    fixa = os.environ.get("PASSKEY_ORIGEM", "").strip().rstrip("/")
    if fixa:  # só para testes automáticos (ver o topo do arquivo)
        return fixa, os.environ.get("PASSKEY_RP", "").strip() or (urlparse(fixa).hostname or "")
    host = (host_header or "").strip().lower()
    if host.startswith("[") or e_ip(host.rsplit(":", 1)[0] if host.count(":") == 1 else host):
        raise ErroPasskey("A passkey não funciona pelo endereço IP: abra o LEGIS pelo nome "
                          "(ex.: https://<maquina>.local:8443 ou o domínio da Câmara).")
    if not _HOST.fullmatch(host) or ".." in host:
        raise ErroPasskey("Endereço do servidor inválido para passkey.")
    nome = host.split(":")[0]
    if not https and nome != "localhost":
        raise ErroPasskey("A passkey só funciona pelo endereço seguro https:// do LEGIS.")
    return f"{'https' if https else 'http'}://{host}", nome


# ---------------------------------------------------------------- desafios de uso único
class Desafios:
    def __init__(self):
        self._d: dict[str, dict[str, tuple[float, str]]] = {}
        self._trava = threading.Lock()

    def gerar(self, conta: str, tipo: str) -> bytes:
        bruto = secrets.token_bytes(32)
        agora = time.time()
        with self._trava:
            if len(self._d) > 10_000:
                self._d = {c: v for c, v in self._d.items() if any(e > agora for e, _ in v.values())}
            meus = {k: v for k, v in self._d.get(conta, {}).items() if v[0] > agora}
            while len(meus) >= MAX_DESAFIOS_CONTA:
                meus.pop(min(meus, key=lambda k: meus[k][0]))
            meus[b64u(bruto)] = (agora + VALIDADE_DESAFIO, tipo)
            self._d[conta] = meus
        return bruto

    def consumir(self, conta: str, tipo: str, desafio: str) -> bool:
        """Retira o desafio (uso único, mesmo se a verificação falhar depois) e diz se era válido."""
        with self._trava:
            item = self._d.get(conta, {}).pop(desafio, None)
        return bool(item) and item[1] == tipo and item[0] > time.time()

    def esquecer(self, conta: str):
        with self._trava:
            self._d.pop(conta, None)


# ---------------------------------------------------------------- passkeys guardadas
def _id_usuario(pid: str) -> bytes:
    return hashlib.sha256(("legis-parlamentar:" + pid).encode()).digest()[:16]


def _client_data(bruto: bytes, tipo: str, origem: str) -> dict:
    try:
        cd = json.loads(bruto)
    except (ValueError, UnicodeDecodeError):
        raise ErroPasskey("clientDataJSON inválido.")
    if not isinstance(cd, dict) or cd.get("type") != tipo:
        raise ErroPasskey("Tipo de operação incorreto.")
    if cd.get("origin") != origem:
        raise ErroPasskey("Origem não confere com o endereço do LEGIS.")
    if cd.get("crossOrigin") is True:
        raise ErroPasskey("Operação vinda de outro site.")
    if not isinstance(cd.get("challenge"), str):
        raise ErroPasskey("Desafio ausente.")
    return cd


class Passkeys:
    """Cadastro e verificação. `ctx` traz o que vem do servidor: estado (S), trava, db, registrar, confirmar, bloqueio,
    verificar_pin(pid, pin) e nome(pid)."""

    def __init__(self, db, estado, trava, registrar, confirmar, bloqueio, verificar_pin, nome, acoes_pin):
        self.db, self.S, self.trava = db, estado, trava
        self.registrar, self.confirmar, self.bloqueio = registrar, confirmar, bloqueio
        self.verificar_pin, self.nome = verificar_pin, nome
        self.acoes = set(acoes_pin) | ACOES_PLENARIO
        self.desafios = Desafios()
        self.exige_uv = os.environ.get("PASSKEY_UV", "1") != "0"
        with trava:
            db.execute("create table if not exists passkeys (id text primary key, pid text not null, json text not null)")
            db.commit()

    # ----- banco (sempre dentro da trava)
    def _da_conta(self, pid: str) -> list[dict]:
        return [json.loads(x[0]) for x in self.db.execute("select json from passkeys where pid = ? order by rowid", (pid,))]

    def _obter(self, cid: str) -> dict | None:
        linha = self.db.execute("select json from passkeys where id = ?", (cid,)).fetchone()
        return json.loads(linha[0]) if linha else None

    def _gravar(self, c: dict):
        self.db.execute("insert or replace into passkeys (id, pid, json) values (?, ?, ?)", (c["id"], c["pid"], json.dumps(c)))

    def modo(self) -> str:
        m = (self.S.get("cfg") or {}).get("passkeyVoto", "nao")
        return m if m in MODOS else "nao"

    def tem(self, pid: str) -> bool:
        with self.trava:
            return self.db.execute("select 1 from passkeys where pid = ? limit 1", (pid,)).fetchone() is not None

    @staticmethod
    def publica(c: dict) -> dict:
        return {"id": c["id"], "nome": c.get("nome", ""), "criado": c.get("criado"), "usado": c.get("usado"),
                "alg": "ES256" if c["chave"]["alg"] == ALG_ES256 else "RS256"}

    # ----- cadastro
    def opcoes_cadastro(self, pid: str, rp: str) -> dict:
        with self.trava:
            existentes = self._da_conta(pid)
            nome = self.nome(pid)
            casa = str((self.S.get("casa") or {}).get("nome") or "LEGIS")
        if len(existentes) >= MAX_PASSKEYS_CONTA:
            raise ErroPasskey(f"Limite de {MAX_PASSKEYS_CONTA} passkeys por vereador. Remova uma antiga.")
        desafio = self.desafios.gerar(f"parlamentar:{pid}", "cadastro")
        return {"challenge": b64u(desafio), "rp": {"id": rp, "name": f"LEGIS — {casa}"[:64]},
                "user": {"id": b64u(_id_usuario(pid)), "name": nome[:64], "displayName": nome[:64]},
                "pubKeyCredParams": [{"type": "public-key", "alg": ALG_ES256}, {"type": "public-key", "alg": ALG_RS256}],
                "authenticatorSelection": {"residentKey": "preferred", "userVerification": "required" if self.exige_uv else "preferred"},
                "attestation": "none", "timeout": VALIDADE_DESAFIO * 1000,
                "excludeCredentials": [{"type": "public-key", "id": c["id"]} for c in existentes]}

    def cadastrar(self, pid: str, credencial: dict, nome_aparelho: str, origem: str, rp: str) -> dict:
        if not isinstance(credencial, dict):
            raise ErroPasskey("Credencial ausente.")
        cd_bruto = de_b64u(credencial.get("clientDataJSON"))
        cd = _client_data(cd_bruto, "webauthn.create", origem)
        if not self.desafios.consumir(f"parlamentar:{pid}", "cadastro", cd["challenge"]):
            raise ErroPasskey("Desafio expirado ou já usado. Tente de novo.")
        att = cbor_decodificar(de_b64u(credencial.get("attestationObject")))
        if not isinstance(att, dict) or not isinstance(att.get("authData"), bytes) or not isinstance(att.get("fmt"), str):
            raise ErroPasskey("attestationObject inválido.")
        # attestation "none" (pedida): o attStmt não é conferido; a confiança vem do vereador logado com o PIN
        ad = ler_auth_data(att["authData"])
        if not secrets.compare_digest(ad["rpIdHash"], hashlib.sha256(rp.encode()).digest()):
            raise ErroPasskey("A passkey foi criada para outro endereço.")
        if not ad["up"] or (self.exige_uv and not ad["uv"]):
            raise ErroPasskey("O celular não confirmou a biometria ou o bloqueio de tela.")
        if not ad["credId"]:
            raise ErroPasskey("Credencial sem identificador.")
        cid = b64u(ad["credId"])
        if credencial.get("id") not in (None, cid):
            raise ErroPasskey("Identificador da credencial não confere.")
        chave = cose_para_chave(ad["cose"])
        nome_aparelho = re.sub(r"[\x00-\x1f<>]", "", str(nome_aparelho or "")).strip()[:60] or "Celular"
        c = {"id": cid, "pid": pid, "nome": nome_aparelho, "chave": chave, "rp": rp, "contador": ad["contador"],
             "criado": int(time.time() * 1000), "usado": None}
        with self.trava:
            if self._obter(cid):
                raise ErroPasskey("Esta passkey já está cadastrada.")
            if len(self._da_conta(pid)) >= MAX_PASSKEYS_CONTA:
                raise ErroPasskey("Limite de passkeys atingido.")
            self._gravar(c)
            self.registrar(pid, "passkeyCadastrar", {"aparelho": nome_aparelho, "alg": self.publica(c)["alg"]})
            self.confirmar()
        return self.publica(c)

    # ----- confirmação (asserção)
    def opcoes_confirmacao(self, pid: str, rp: str) -> dict:
        with self.trava:
            minhas = [c for c in self._da_conta(pid) if c.get("rp") == rp]
        if not minhas:
            raise ErroPasskey("Nenhuma passkey cadastrada neste endereço.")
        desafio = self.desafios.gerar(f"parlamentar:{pid}", "confirmar")
        return {"challenge": b64u(desafio), "rpId": rp, "timeout": VALIDADE_DESAFIO * 1000,
                "userVerification": "required" if self.exige_uv else "preferred",
                "allowCredentials": [{"type": "public-key", "id": c["id"]} for c in minhas]}

    def verificar(self, pid: str, assercao, origem: str, rp: str) -> dict:
        if not isinstance(assercao, dict):
            raise ErroPasskey("Confirmação ausente.")
        cid = assercao.get("id")
        if not isinstance(cid, str) or len(cid) > 2000:
            raise ErroPasskey("Passkey inválida.")
        cd_bruto = de_b64u(assercao.get("clientDataJSON"))
        ad_bruto = de_b64u(assercao.get("authenticatorData"))
        assinatura = de_b64u(assercao.get("signature"))
        cd = _client_data(cd_bruto, "webauthn.get", origem)
        if not self.desafios.consumir(f"parlamentar:{pid}", "confirmar", cd["challenge"]):
            raise ErroPasskey("Desafio expirado ou já usado.")
        with self.trava:
            c = self._obter(cid)
        if not c or c["pid"] != pid:
            raise ErroPasskey("Passkey não cadastrada para este vereador.")
        if c.get("rp") != rp:
            raise ErroPasskey("Passkey cadastrada em outro endereço.")
        ad = ler_auth_data(ad_bruto)
        if not secrets.compare_digest(ad["rpIdHash"], hashlib.sha256(rp.encode()).digest()):
            raise ErroPasskey("rpId não confere.")
        if not ad["up"]:
            raise ErroPasskey("Presença do usuário não confirmada.")
        if self.exige_uv and not ad["uv"]:
            raise ErroPasskey("Biometria (ou bloqueio de tela) não confirmada.")
        if not verificar_assinatura(c["chave"], assinatura, ad_bruto + hashlib.sha256(cd_bruto).digest()):
            raise ErroPasskey("Assinatura inválida.")
        with self.trava:
            c = self._obter(cid)  # relê dentro da trava: dois pedidos em paralelo não passam com o mesmo contador
            if not c:
                raise ErroPasskey("Passkey removida.")
            if (ad["contador"] or c["contador"]) and ad["contador"] <= c["contador"]:
                raise ErroPasskey("Contador da passkey voltou (possível cópia). Fale com a secretaria.")
            c["contador"], c["usado"] = ad["contador"], int(time.time() * 1000)
            self._gravar(c)
            self.db.commit()
        return c

    def confirmar_acao(self, nome: str, args: dict, ator, ip: str, origem_rp) -> tuple | bool | None:
        """Gancho do /api/acao. Tira `passkey` dos args. None: segue o fluxo normal (PIN onde houver);
        True: passkey válida (dispensa o PIN); tupla (código HTTP, corpo): recusa."""
        dado = args.pop("passkey", None)
        if nome not in self.acoes or not ator or ator.get("papel") != "parlamentar":
            return None
        pid = ator["pid"]
        with self.trava:
            modo = self.modo()
        if modo == "nao" or not self.tem(pid):
            return None
        if dado is None:
            if modo == "exigir":
                return 403, {"erro": "Confirme com a passkey (biometria) deste celular.", "codigo": "passkey"}
            return None
        conta = f"parlamentar:{pid}"
        espera, marca = self.bloqueio.tentar(conta, ip)
        if espera:
            return 429, {"erro": "Muitas tentativas erradas. Aguarde alguns minutos."}
        try:
            if isinstance(origem_rp, ErroPasskey):
                raise origem_rp
            self.verificar(pid, dado, *origem_rp)
        except ErroPasskey as e:
            with self.trava:
                self.registrar(pid, "passkeyRecusada", {"acao": nome, "motivo": str(e)})
                self.db.commit()
            return 403, {"erro": f"Passkey não confirmada: {e} Nada foi registrado.", "codigo": "passkey"}
        self.bloqueio.acertou(conta, ip, marca)
        return True

    # ----- rotas /api/passkey/*
    def _conferir_pin(self, pid: str, pin, ip: str):
        conta = f"parlamentar:{pid}"
        espera, marca = self.bloqueio.tentar(conta, ip)
        if espera:
            return 429, {"erro": "Muitas tentativas erradas. Aguarde alguns minutos."}
        if not self.verificar_pin(pid, str(pin or "")):
            return 403, {"erro": "PIN incorreto.", "codigo": "pin"}
        self.bloqueio.acertou(conta, ip, marca)
        return None

    def rota(self, metodo: str, caminho: str, corpo: dict | None, ator, ip: str, origem_rp):
        """Devolve (código HTTP, corpo) ou None se o caminho não for daqui. `origem_rp`: tupla ou ErroPasskey."""
        corpo = corpo if isinstance(corpo, dict) else {}
        if not ator:
            return 401, {"erro": "Entre novamente."}
        papel, pid = ator["papel"], ator.get("pid")
        disponivel = not isinstance(origem_rp, ErroPasskey)
        try:
            if metodo == "GET" and caminho == "/api/passkey/estado":
                with self.trava:
                    modo = self.modo()
                    if papel == "presidente":
                        contas = {}
                        for (p, n) in self.db.execute("select pid, count(*) from passkeys group by pid"):
                            contas[p] = n
                        return 200, {"modo": modo, "contas": contas, "modos": list(MODOS)}
                    minhas = [self.publica(c) for c in self._da_conta(pid)]
                return 200, {"modo": modo, "disponivel": disponivel, "motivo": "" if disponivel else str(origem_rp),
                             "rp": origem_rp[1] if disponivel else "", "minhas": minhas, "exigeUV": self.exige_uv}
            if metodo != "POST":
                return None
            if caminho == "/api/passkey/modo":
                if papel != "presidente":
                    return 403, {"erro": "Só a presidência muda esta opção."}
                modo = corpo.get("modo")
                if modo not in MODOS:
                    return 400, {"erro": "Opção inválida."}
                with self.trava:
                    self.S["cfg"]["passkeyVoto"] = modo
                    self.registrar("presidente", "passkeyModo", {"modo": modo})
                    self.confirmar()
                return 200, {"ok": True}
            if caminho == "/api/passkey/revogar":
                if papel != "presidente":
                    return 403, {"erro": "Só a presidência revoga passkeys."}
                alvo = corpo.get("pid")
                if not isinstance(alvo, str) or len(alvo) > 32:
                    return 400, {"erro": "Vereador inválido."}
                with self.trava:
                    n = self.db.execute("delete from passkeys where pid = ?", (alvo,)).rowcount
                    self.registrar("presidente", "passkeyRevogar", {"pid": alvo, "quantas": n})
                    self.confirmar()
                self.desafios.esquecer(f"parlamentar:{alvo}")
                return 200, {"ok": True, "removidas": n}
            if papel != "parlamentar":
                return 403, {"erro": "Só o vereador cadastra a própria passkey."}
            if caminho == "/api/passkey/desafio":
                if not disponivel:
                    return 400, {"erro": str(origem_rp)}
                tipo = corpo.get("tipo")
                if tipo == "cadastro":
                    return 200, {"publicKey": self.opcoes_cadastro(pid, origem_rp[1])}
                if tipo == "confirmar":
                    return 200, {"publicKey": self.opcoes_confirmacao(pid, origem_rp[1])}
                return 400, {"erro": "Tipo de desafio inválido."}
            if caminho == "/api/passkey/cadastrar":
                if not disponivel:
                    return 400, {"erro": str(origem_rp)}
                recusa = self._conferir_pin(pid, corpo.get("pin"), ip)  # sessão roubada não cadastra passkey própria
                if recusa:
                    return recusa
                return 200, {"ok": True, "passkey": self.cadastrar(pid, corpo.get("credencial"), corpo.get("nome"), *origem_rp)}
            if caminho == "/api/passkey/remover":
                cid = corpo.get("id")
                if not isinstance(cid, str) or len(cid) > 2000:
                    return 400, {"erro": "Passkey inválida."}
                recusa = self._conferir_pin(pid, corpo.get("pin"), ip)
                if recusa:
                    return recusa
                with self.trava:
                    c = self._obter(cid)
                    if not c or c["pid"] != pid:
                        return 404, {"erro": "Passkey não encontrada."}
                    self.db.execute("delete from passkeys where id = ?", (cid,))
                    self.registrar(pid, "passkeyRemover", {"aparelho": c.get("nome", "")})
                    self.confirmar()
                return 200, {"ok": True}
            return None
        except ErroPasskey as e:
            return 400, {"erro": str(e)}
