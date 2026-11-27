"""Passkey / WebAuthn: autenticador simulado (chave gerada pelo openssl) contra o servidor e o módulo passkey.py.

A assinatura é feita pelo `openssl` (implementação independente) e conferida pela verificação em Python puro do
servidor: se as duas concordam, a matemática do ECDSA P-256 e do RSA está certa.
"""
import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from apoio import RAIZ, Servidor, TesteComServidor

sys.path.insert(0, str(RAIZ / "server"))
import passkey  # noqa: E402

ORIGEM, RP = "https://legis.teste:8443", "legis.teste"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def de_b64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------- CBOR (só para montar os dados do teste)
def _cab(maior: int, n: int) -> bytes:
    if n < 24:
        return bytes([maior << 5 | n])
    for info, tam in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if n < 1 << (8 * tam):
            return bytes([maior << 5 | info]) + n.to_bytes(tam, "big")
    raise ValueError(n)


def cbor(v) -> bytes:
    if v is None:
        return b"\xf6"
    if isinstance(v, bool):
        return b"\xf5" if v else b"\xf4"
    if isinstance(v, int):
        return _cab(0, v) if v >= 0 else _cab(1, -1 - v)
    if isinstance(v, bytes):
        return _cab(2, len(v)) + v
    if isinstance(v, str):
        e = v.encode()
        return _cab(3, len(e)) + e
    if isinstance(v, list):
        return _cab(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return _cab(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(v)


class Autenticador:
    """Simula o celular: chave guardada num arquivo temporário, assinatura pelo openssl."""

    def __init__(self, rsa=False):
        self.dir = tempfile.TemporaryDirectory(prefix="legis-passkey-")
        self.chave = Path(self.dir.name) / "chave.pem"
        self.rsa = rsa
        if rsa:
            subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                            "-out", str(self.chave)], check=True, capture_output=True)
            mod = subprocess.run(["openssl", "rsa", "-in", str(self.chave), "-noout", "-modulus"],
                                 check=True, capture_output=True, text=True).stdout.strip().split("=", 1)[1]
            self.cose = {1: 3, 3: -257, -1: bytes.fromhex(mod), -2: (65537).to_bytes(3, "big")}
        else:
            subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(self.chave)],
                           check=True, capture_output=True)
            der = subprocess.run(["openssl", "ec", "-in", str(self.chave), "-pubout", "-outform", "DER"],
                                 check=True, capture_output=True).stdout
            ponto = der[-65:]
            assert ponto[0] == 4
            self.cose = {1: 2, 3: -7, -1: 1, -2: ponto[1:33], -3: ponto[33:]}
        self.cred_id = os.urandom(32)
        self.contador = 0

    def fechar(self):
        self.dir.cleanup()

    def assinar(self, dados: bytes) -> bytes:
        return subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(self.chave)], input=dados,
                              check=True, capture_output=True).stdout

    @staticmethod
    def client_data(tipo, desafio, origem=ORIGEM) -> bytes:
        return json.dumps({"type": tipo, "challenge": desafio, "origin": origem, "crossOrigin": False}).encode()

    def criar(self, opcoes: dict, origem=ORIGEM, rp=RP, flags=0x45) -> dict:
        auth = (hashlib.sha256(rp.encode()).digest() + bytes([flags]) + self.contador.to_bytes(4, "big")
                + bytes(16) + len(self.cred_id).to_bytes(2, "big") + self.cred_id + cbor(self.cose))
        att = cbor({"fmt": "none", "attStmt": {}, "authData": auth})
        return {"id": b64u(self.cred_id), "clientDataJSON": b64u(self.client_data("webauthn.create", opcoes["challenge"], origem)),
                "attestationObject": b64u(att)}

    def obter(self, opcoes: dict, origem=ORIGEM, rp=RP, flags=0x05, contador=None, estragar=False) -> dict:
        self.contador = self.contador + 1 if contador is None else contador
        auth = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + self.contador.to_bytes(4, "big")
        cd = self.client_data("webauthn.get", opcoes["challenge"], origem)
        dados = auth + hashlib.sha256(cd).digest()
        sig = self.assinar(dados + b"x" if estragar else dados)
        return {"id": b64u(self.cred_id), "clientDataJSON": b64u(cd), "authenticatorData": b64u(auth), "signature": b64u(sig)}


# ---------------------------------------------------------------- módulo (sem servidor)
class Modulo(unittest.TestCase):
    def test_cbor(self):
        v = {1: 2, -1: b"\x00\xff", "fmt": "none", "lista": [1, -300, True, None], "grande": 2 ** 40}
        self.assertEqual(passkey.cbor_decodificar(cbor(v)), {**v, "lista": [1, -300, True, None]})
        with self.assertRaises(passkey.ErroPasskey):
            passkey.cbor_decodificar(cbor(v) + b"\x00")       # sobra
        with self.assertRaises(passkey.ErroPasskey):
            passkey.cbor_decodificar(cbor(b"abcdef")[:-2])     # truncado
        with self.assertRaises(passkey.ErroPasskey):
            passkey.cbor_decodificar(b"\x9f\x01\xff")          # tamanho indefinido
        aninhado = b"\x81" * 40 + b"\x01"
        with self.assertRaises(passkey.ErroPasskey):
            passkey.cbor_decodificar(aninhado)

    def test_ecdsa_p256_confere_com_openssl(self):
        a = Autenticador()
        try:
            chave = passkey.cose_para_chave(passkey.cbor_decodificar(cbor(a.cose)))
            for msg in (b"", b"voto sim", os.urandom(300)):
                sig = a.assinar(msg)
                self.assertTrue(passkey.verificar_assinatura(chave, sig, msg))
                self.assertFalse(passkey.verificar_assinatura(chave, sig, msg + b"!"))
            ruim = bytearray(a.assinar(b"m"))
            ruim[-1] ^= 1
            self.assertFalse(passkey.verificar_assinatura(chave, bytes(ruim), b"m"))
            self.assertFalse(passkey.verificar_assinatura(chave, b"\x30\x00", b"m"))
        finally:
            a.fechar()
        with self.assertRaises(passkey.ErroPasskey):  # ponto fora da curva
            passkey.cose_para_chave({1: 2, 3: -7, -1: 1, -2: b"\x01" * 32, -3: b"\x02" * 32})

    def test_rsa_confere_com_openssl(self):
        a = Autenticador(rsa=True)
        try:
            chave = passkey.cose_para_chave(a.cose)
            sig = a.assinar(b"presenca")
            self.assertTrue(passkey.verificar_assinatura(chave, sig, b"presenca"))
            self.assertFalse(passkey.verificar_assinatura(chave, sig, b"presenca."))
        finally:
            a.fechar()

    def test_origem_esperada(self):
        guardado = {k: os.environ.pop(k, None) for k in ("PASSKEY_ORIGEM", "PASSKEY_RP")}
        try:
            for host, https in (("192.168.0.10:8443", True), ("[::1]:8443", True), ("camara.local:8090", False)):
                with self.assertRaises(passkey.ErroPasskey):
                    passkey.origem_esperada(host, https)
            self.assertEqual(passkey.origem_esperada("Camara.local:8443", True), ("https://camara.local:8443", "camara.local"))
            self.assertEqual(passkey.origem_esperada("legis.camara.sp.gov.br", True),
                             ("https://legis.camara.sp.gov.br", "legis.camara.sp.gov.br"))
            self.assertEqual(passkey.origem_esperada("localhost:8090", False), ("http://localhost:8090", "localhost"))
            os.environ["PASSKEY_ORIGEM"] = "http://127.0.0.1:9999"
            self.assertEqual(passkey.origem_esperada("qualquer", False), ("http://127.0.0.1:9999", "127.0.0.1"))
        finally:
            for k, v in guardado.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def test_desafio_uso_unico_e_validade(self):
        d = passkey.Desafios()
        c = b64u(d.gerar("conta", "confirmar"))
        self.assertFalse(d.consumir("outra", "confirmar", c))
        self.assertTrue(d.consumir("conta", "confirmar", c))
        self.assertFalse(d.consumir("conta", "confirmar", c))
        c = b64u(d.gerar("conta", "cadastro"))
        self.assertFalse(d.consumir("conta", "confirmar", c))  # tipo errado também gasta o desafio
        velho = passkey.VALIDADE_DESAFIO
        passkey.VALIDADE_DESAFIO = -1
        try:
            c = b64u(d.gerar("conta", "confirmar"))
        finally:
            passkey.VALIDADE_DESAFIO = velho
        self.assertFalse(d.consumir("conta", "confirmar", c))


# ---------------------------------------------------------------- servidor
class PasskeyNoServidor(TesteComServidor):
    @classmethod
    def setUpClass(cls):
        cls.servidor = Servidor(PASSKEY_ORIGEM=ORIGEM, PASSKEY_RP=RP)
        cls.s = cls.servidor
        cls.P = cls.s.entrar("presidente")
        cls.ids = [p["id"] for p in cls.s.estado()["parls"]]
        cls.autenticadores = []

    @classmethod
    def tearDownClass(cls):
        for a in cls.autenticadores:
            a.fechar()
        super().tearDownClass()

    def _autenticador(self, **k):
        a = Autenticador(**k)
        self.autenticadores.append(a)
        return a

    def _desafio(self, t, tipo):
        r = self.s.req("/api/passkey/desafio", {"tipo": tipo}, t)
        self.assertIn("publicKey", r, r)
        return r["publicKey"]

    def _cadastrar(self, t, pid, a, nome="Meu celular"):
        op = self._desafio(t, "cadastro")
        self.assertEqual(op["rp"]["id"], RP)
        self.assertEqual(op["attestation"], "none")
        r = self.s.req("/api/passkey/cadastrar", {"credencial": a.criar(op), "nome": nome, "pin": self.s.pins[("parlamentar", pid)]}, t)
        self.assertTrue(r.get("ok"), r)
        return r["passkey"]

    def _assercao(self, t, a, **k):
        return a.obter(self._desafio(t, "confirmar"), **k)

    def _banco(self, sql):
        with sqlite3.connect(self.s.dir.name + "/teste.db") as banco:
            linhas = banco.execute(sql).fetchall()
        banco.close()
        return linhas

    def _modo(self, modo):
        self.assertTrue(self.s.req("/api/passkey/modo", {"modo": modo}, self.P).get("ok"))

    def test_1_cadastro_voto_e_recusas(self):
        s, P, ids = self.s, self.P, self.ids
        pid = ids[0]
        t = s.entrar("parlamentar", pid)
        e = s.req("/api/passkey/estado", token=t)
        self.assertEqual((e["disponivel"], e["modo"], e["minhas"]), (True, "nao", []))
        a = self._autenticador()
        # PIN errado não cadastra
        op = self._desafio(t, "cadastro")
        cred = a.criar(op)
        self.erro(s.req("/api/passkey/cadastrar", {"credencial": cred, "pin": "000000"}, t), 403, "PIN")
        self.assertTrue(s.req("/api/passkey/cadastrar", {"credencial": cred, "nome": "Galaxy", "pin": s.pins[("parlamentar", pid)]}, t).get("ok"))
        # o mesmo attestation de novo: desafio já usado
        self.erro(s.req("/api/passkey/cadastrar", {"credencial": cred, "pin": s.pins[("parlamentar", pid)]}, t), 400, "Desafio")
        e = s.req("/api/passkey/estado", token=t)
        self.assertEqual([x["nome"] for x in e["minhas"]], ["Galaxy"])
        # só a presidência muda a opção
        self.erro(s.req("/api/passkey/modo", {"modo": "exigir"}, t), 403)
        self._modo("exigir")
        self.assertEqual(s.estado()["cfg"]["passkeyVoto"], "exigir")
        # presença sem passkey: recusada; com passkey: aceita
        self.erro(s.acao(t, "presenca"), 403, "passkey")
        valida = self._assercao(t, a)
        self.ok(s.acao(t, "presenca", passkey=valida))
        pauta = s.estado()["pauta"]
        self.ok(s.acao(P, "votacaoAbrir", materia_id=pauta[0]["id"], tipo="nominal", quorum="simples"))
        self.erro(s.acao(t, "votar", valor="sim"), 403, "passkey")
        self.erro(s.acao(t, "votar", valor="sim", passkey=valida), 403, "Desafio")          # challenge reutilizado
        self.erro(s.acao(t, "votar", valor="sim", passkey=self._assercao(t, a, estragar=True)), 403, "Assinatura")
        self.erro(s.acao(t, "votar", valor="sim", passkey=self._assercao(t, a, origem="https://golpe.exemplo")), 403, "Origem")
        self.ok(s.acao(t, "presenca", passkey=self._assercao(t, a, contador=20)))  # acerto zera o bloqueio
        self.erro(s.acao(t, "votar", valor="sim", passkey=self._assercao(t, a, contador=1)), 403, "Contador")
        self.erro(s.acao(t, "votar", valor="sim", passkey=self._assercao(t, a, flags=0x01)), 403, "Biometria")  # sem UV
        self.assertNotIn(pid, s.estado()["votacao"].get("votos", {}))
        self.ok(s.acao(t, "votar", valor="sim", passkey=self._assercao(t, a, contador=50)))
        # vereador sem passkey cadastrada continua votando como antes
        t2 = s.entrar("parlamentar", ids[1])
        self.ok(s.acao(t2, "presenca"))
        self.ok(s.acao(t2, "votar", valor="nao"))
        # remoção (pede o PIN)
        self.erro(s.req("/api/passkey/remover", {"id": e["minhas"][0]["id"], "pin": "000000"}, t), 403)
        self.erro(s.req("/api/passkey/remover", {"id": e["minhas"][0]["id"], "pin": s.pins[("parlamentar", ids[1])]}, t2), 404)  # a de outro vereador
        self.assertTrue(s.req("/api/passkey/remover", {"id": e["minhas"][0]["id"], "pin": s.pins[("parlamentar", pid)]}, t).get("ok"))
        self.assertEqual(s.req("/api/passkey/estado", token=t)["minhas"], [])
        self.ok(s.acao(P, "votacaoEncerrar"))
        self.ok(s.acao(P, "votacaoLimpar"))
        self._modo("nao")
        # auditoria: eventos registrados, sem dados da credencial
        acoes = [a for (a,) in self._banco("select acao from eventos where acao like 'passkey%'")]
        for esperado in ("passkeyCadastrar", "passkeyModo", "passkeyRecusada", "passkeyRemover"):
            self.assertIn(esperado, acoes)
        for (args,) in self._banco("select args from eventos"):
            for proibido in ("signature", "clientDataJSON", "authenticatorData", "attestationObject"):
                self.assertNotIn(proibido, args)

    def test_2_virtual_passkey_ou_pin_e_revogacao(self):
        s, P, ids = self.s, self.P, self.ids
        pid = ids[2]
        t = s.entrar("parlamentar", pid)
        a, b = self._autenticador(), self._autenticador(rsa=True)
        self._cadastrar(t, pid, a, "Celular")
        self._cadastrar(t, pid, b, "Tablet")  # RS256
        agora = int(time.time() * 1000)
        self.ok(s.acao(P, "virtualCriar", titulo="Virtual passkey", inicio=agora + 2500, fim=agora + 86400000))
        v = s.estado()["virtuais"][-1]
        for m in s.estado()["pauta"][:3]:
            self.ok(s.acao(P, "virtualItem", vid=v["id"], projetoId=m["id"]))
        time.sleep(3)
        itens = [i["id"] for i in s.estado()["virtuais"][-1]["itens"]]
        pin = s.pins[("parlamentar", pid)]
        self._modo("opcional")  # passkey OU PIN
        self.ok(s.acao(t, "virtualVotar", vid=v["id"], itemId=itens[0], valor="sim", passkey=self._assercao(t, b)))
        self.ok(s.acao(t, "virtualVotar", vid=v["id"], itemId=itens[1], valor="nao", pin=pin))
        self.erro(s.acao(t, "virtualVotar", vid=v["id"], itemId=itens[2], valor="sim"), 403, "PIN")
        self._modo("exigir")
        self.erro(s.acao(t, "virtualVotar", vid=v["id"], itemId=itens[2], valor="sim", pin=pin), 403, "passkey")
        self.ok(s.acao(t, "virtualDestaque", vid=v["id"], itemId=itens[2], passkey=self._assercao(t, a)))
        # modo desligado: a passkey sozinha não substitui o PIN
        self._modo("nao")
        self.erro(s.acao(t, "virtualVotar", vid=v["id"], itemId=itens[2], valor="sim", passkey=self._assercao(t, a)), 403, "PIN")
        # presidência vê quem tem passkey e revoga todas (perda do celular)
        e = s.req("/api/passkey/estado", token=P)
        self.assertEqual(e["contas"].get(pid), 2)
        self.erro(s.req("/api/passkey/revogar", {"pid": pid}, t), 403)
        r = s.req("/api/passkey/revogar", {"pid": pid}, P)
        self.assertEqual(r.get("removidas"), 2, r)
        self.assertNotIn(pid, s.req("/api/passkey/estado", token=P)["contas"])
        self.assertIn(("passkeyRevogar",), self._banco("select acao from eventos where acao = 'passkeyRevogar'"))

    def test_3_cadastro_recusado(self):
        s, ids = self.s, self.ids
        pid = ids[3]
        t = s.entrar("parlamentar", pid)
        pin = s.pins[("parlamentar", pid)]
        a = self._autenticador()
        for k, erro in (({"origem": "https://outro.site"}, "Origem"), ({"rp": "outro.site"}, "outro endereço"),
                        ({"flags": 0x41}, "biometria")):
            r = s.req("/api/passkey/cadastrar", {"credencial": a.criar(self._desafio(t, "cadastro"), **k), "pin": pin}, t)
            self.erro(r, 400, erro)
        # presidente não cadastra; sem login, nada
        self.erro(s.req("/api/passkey/desafio", {"tipo": "cadastro"}, self.P), 403)
        self.erro(s.req("/api/passkey/estado"), 401)
        # sem passkey cadastrada não há desafio de confirmação
        self.erro(s.req("/api/passkey/desafio", {"tipo": "confirmar"}, t), 400, "Nenhuma")


class PasskeyPeloIP(TesteComServidor):
    """Sem PASSKEY_ORIGEM, pelo IP (como num celular que abriu https://192.168.x.x): indisponível, com explicação."""

    def test_ip_indisponivel(self):
        t = self.s.entrar("parlamentar", self.ids[0])
        e = self.s.req("/api/passkey/estado", token=t)
        self.assertFalse(e["disponivel"])
        self.assertIn("IP", e["motivo"])
        self.erro(self.s.req("/api/passkey/desafio", {"tipo": "cadastro"}, t), 400, "IP")


if __name__ == "__main__":
    unittest.main()
