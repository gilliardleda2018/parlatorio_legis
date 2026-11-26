"""Integração com a mesa de som.

O servidor calcula, a cada mudança, quais canais devem estar abertos
(orador com a palavra, aparte, microfone liberado pelo presidente) e
quais devem estar fechados (tempo esgotado, microfone cortado, plenário
congelado). Este módulo compara com o último estado enviado e manda só
as diferenças para o equipamento, numa thread própria para nunca travar
o plenário se a mesa não responder.

Drivers:
  * simulado: só registra no painel (para treinar e testar sem equipamento)
  * osc: mensagens OSC por UDP (Behringer X32/M32, X Air, WING, Midas e outras)
  * yamaha: protocolo de texto da Yamaha por TCP, porta 49280 (TF, DM3, DM7, QL, CL, Rivage)
  * allenheath_qu: MIDI por TCP, mudo por Note On (Allen & Heath Qu, dLive, Avantis)
  * allenheath_sq: MIDI por TCP, mudo por NRPN (Allen & Heath SQ)
  * soundcraft: WebSocket das mesas Soundcraft Ui (Ui12, Ui16, Ui24R)
  * http: POST JSON para um endereço (ponte para sistemas de conferência,
    Node-RED, Bitfocus Companion, controladores próprios)
  * http_modelo: uma URL por canal, com {canal}, {acao} (on/off) e {valor} (1/0):
    módulos de relé (Shelly, ESP32), centrais de conferência com API simples

Em todos, "aberto" é o canal ligado (sem mudo). Os protocolos de Yamaha, Allen & Heath e
Soundcraft seguem a documentação dos fabricantes e devem ser validados no equipamento.

A cada 5 s o estado completo é reenviado (opcional), para corrigir a mesa
se alguém mexer nela à mão.
"""
import base64
import json
import os
import queue
import socket
import struct
import threading
import time
import urllib.parse
import urllib.request

# Modelos que o painel oferece: cada um escolhe o driver e preenche porta/endereço/valores.
PRESETS = {
    "x32": {"nome": "Behringer X32 / Midas M32", "driver": "osc", "porta": 10023, "endereco": "/ch/{canal:02d}/mix/on",
            "valorAberto": 1, "valorFechado": 0, "tipoValor": "i"},
    "xair": {"nome": "Behringer X Air (XR12/16/18) / Midas MR", "driver": "osc", "porta": 10024,
             "endereco": "/ch/{canal:02d}/mix/on", "valorAberto": 1, "valorFechado": 0, "tipoValor": "i"},
    "wing": {"nome": "Behringer WING", "driver": "osc", "porta": 2223, "endereco": "/ch/{canal}/mute",
             "valorAberto": 0, "valorFechado": 1, "tipoValor": "i"},
    "yamaha": {"nome": "Yamaha TF / DM3 / DM7 / QL / CL / Rivage", "driver": "yamaha", "porta": 49280},
    "allenheath_qu": {"nome": "Allen & Heath Qu / dLive / Avantis", "driver": "allenheath_qu", "porta": 51325},
    "allenheath_sq": {"nome": "Allen & Heath SQ", "driver": "allenheath_sq", "porta": 51325},
    "soundcraft": {"nome": "Soundcraft Ui12 / Ui16 / Ui24R", "driver": "soundcraft", "porta": 80},
    "osc": {"nome": "Outra mesa digital com OSC", "driver": "osc", "porta": 10023, "endereco": "/ch/{canal:02d}/mix/on",
            "valorAberto": 1, "valorFechado": 0, "tipoValor": "i"},
    "http": {"nome": "Ponte HTTP (Companion, Node-RED, sistema de conferência)", "driver": "http"},
    "http_modelo": {"nome": "Relés ou central com URL por canal (Shelly, ESP32)", "driver": "http_modelo"},
}
DRIVERS = ("simulado", "osc", "yamaha", "allenheath_qu", "allenheath_sq", "soundcraft", "http", "http_modelo")

CFG_PADRAO = {
    "driver": "simulado", "modelo": "", "host": "192.168.0.50", "porta": 10023, "endereco": "/ch/{canal:02d}/mix/on",
    "valorAberto": 1, "valorFechado": 0, "tipoValor": "i", "url": "", "metodo": "GET", "midiCanal": 1,
    "canalTribuna": 0, "reenviar": True,
}
_CHAVES_CONEXAO = ("driver", "host", "porta", "endereco", "url", "metodo", "midiCanal", "tipoValor", "valorAberto", "valorFechado")


def osc_mensagem(endereco: str, valor, tipo: str) -> bytes:
    def texto(s: str) -> bytes:
        b = s.encode() + b"\0"
        return b + b"\0" * (-len(b) % 4)
    if tipo == "f":
        arg = struct.pack(">f", float(valor))
    else:
        tipo, arg = "i", struct.pack(">i", int(valor))
    return texto(endereco) + texto("," + tipo) + arg


class Mesa:
    def __init__(self, aviso):
        self.aviso = aviso          # chamado quando o status muda (para atualizar as telas)
        self.cfg = dict(CFG_PADRAO)
        self.desejado: dict[int, bool] = {}
        self.enviado: dict[int, bool] = {}
        self.status = {"ok": True, "erro": None, "ultimo": None, "log": []}
        self.fila: queue.Queue = queue.Queue()
        threading.Thread(target=self._laco, daemon=True).start()

    # chamados pelo servidor (com a trava do estado; aqui só enfileira)
    def atualizar(self, cfg: dict, canais: dict[int, bool]):
        self.fila.put(("estado", dict(cfg), dict(canais)))

    def testar(self, cfg: dict, canal: int, aberto: bool):
        self.fila.put(("teste", dict(cfg), {int(canal): bool(aberto)}))

    def publico(self) -> dict:
        return {**self.status, "canais": {str(k): v for k, v in sorted(self.enviado.items())}}

    # thread da mesa
    def _laco(self):
        ultimo_reenvio = 0.0
        while True:
            try:
                item = self.fila.get(timeout=1)
            except queue.Empty:
                item = None
            teste = None
            while item:  # junta tudo o que chegou e fica só com o estado mais recente
                if item[0] == "teste":
                    teste = item
                else:
                    _, cfg, canais = item
                    if any(cfg.get(k) != self.cfg.get(k) for k in _CHAVES_CONEXAO):
                        self.enviado = {}
                    self.cfg, self.desejado = cfg, canais
                try:
                    item = self.fila.get_nowait()
                except queue.Empty:
                    item = None
            if teste:
                self._enviar(teste[1], teste[2], "teste")
            reenviar = self.cfg.get("reenviar") and self.desejado and time.time() - ultimo_reenvio > 5
            mudancas = {c: a for c, a in self.desejado.items() if self.enviado.get(c) != a}
            if mudancas:
                self._enviar(self.cfg, mudancas)
            elif reenviar:
                ultimo_reenvio = time.time()
                self._enviar(self.cfg, self.desejado, "reenvio")

    def _enviar(self, cfg: dict, canais: dict[int, bool], motivo: str = ""):
        driver = cfg.get("driver", "simulado")
        antes = (self.status["ok"], self.status["erro"])
        try:
            if driver != "simulado":
                ENVIOS[driver](cfg, canais, motivo)
            if motivo != "teste":
                self.enviado.update(canais)
            self.status.update(ok=True, erro=None, ultimo=int(time.time() * 1000))
            if motivo != "reenvio":
                hora = time.strftime("%H:%M:%S")
                for canal, aberto in canais.items():
                    self.status["log"].insert(0, f"{hora} canal {canal} {'ABERTO' if aberto else 'fechado'}{' (teste)' if motivo == 'teste' else ''}")
                del self.status["log"][12:]
        except Exception as e:  # rede fora, host errado, ponte desligada
            self.status.update(ok=False, erro=f"{type(e).__name__}: {e}"[:200])
        if motivo != "reenvio" or (self.status["ok"], self.status["erro"]) != antes:
            try:
                self.aviso()
            except Exception:  # avisar as telas nunca pode derrubar o controle dos microfones
                pass


# ---------------------------------------------------------------- protocolos
def _osc(cfg, canais, motivo):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        destino = (cfg["host"], int(cfg["porta"]))
        for canal, aberto in canais.items():
            valor = cfg["valorAberto"] if aberto else cfg["valorFechado"]
            s.sendto(osc_mensagem(cfg["endereco"].format(canal=canal), valor, cfg.get("tipoValor", "i")), destino)


def _tcp(cfg, dados: bytes, ler=False) -> bytes:
    with socket.create_connection((cfg["host"], int(cfg["porta"])), timeout=2) as s:
        s.sendall(dados)
        if not ler:
            return b""
        s.settimeout(0.6)
        resposta = b""
        try:
            while len(resposta) < 65536:
                parte = s.recv(4096)
                if not parte:
                    break
                resposta += parte
        except socket.timeout:
            pass
        return resposta


def _yamaha(cfg, canais, motivo):
    """Protocolo de controle remoto da Yamaha (TCP 49280): liga/desliga o botão ON do canal de entrada."""
    linhas = "".join(f"set MIXER:Current/InCh/Fader/On {c - 1} 0 {1 if a else 0}\n" for c, a in canais.items())
    resposta = _tcp(cfg, linhas.encode(), ler=True).decode(errors="replace")
    erros = [l for l in resposta.splitlines() if l.startswith("ERROR")]
    if erros:
        raise RuntimeError("a mesa Yamaha respondeu " + erros[0][:120])


def _midi_canal(cfg) -> int:
    return max(0, min(15, int(cfg.get("midiCanal") or 1) - 1))


def _allenheath_qu(cfg, canais, motivo):
    """Qu / dLive / Avantis: mudo do canal de entrada por Note On (velocidade > 0x40 liga o mudo)."""
    n = 0x90 | _midi_canal(cfg)
    dados = b"".join(bytes([n, c - 1, 0x3F if a else 0x7F, n, c - 1, 0x00]) for c, a in canais.items())
    _tcp(cfg, dados)


def _allenheath_sq(cfg, canais, motivo):
    """SQ: mudo do canal de entrada por NRPN (MSB 0x00, LSB = canal - 1, valor 1 = mudo)."""
    b = 0xB0 | _midi_canal(cfg)
    dados = b"".join(bytes([b, 0x63, 0x00, b, 0x62, c - 1, b, 0x06, 0x00, b, 0x26, 0 if a else 1]) for c, a in canais.items())
    _tcp(cfg, dados)


def _ws_quadro(texto: str) -> bytes:
    """Quadro de texto WebSocket mascarado (cliente → servidor)."""
    dados = texto.encode()
    mascara = os.urandom(4)
    n = len(dados)
    cab = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
    return cab + mascara + bytes(x ^ mascara[i % 4] for i, x in enumerate(dados))


def _soundcraft(cfg, canais, motivo):
    """Soundcraft Ui: WebSocket na porta 80, comando SETD^i.<canal-1>.mute^<1|0>."""
    host, porta = cfg["host"], int(cfg.get("porta") or 80)
    with socket.create_connection((host, porta), timeout=2) as s:
        chave = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET / HTTP/1.1\r\nHost: {host}:{porta}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {chave}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resposta = b""
        while b"\r\n\r\n" not in resposta and len(resposta) < 8192:
            parte = s.recv(1024)
            if not parte:
                break
            resposta += parte
        if b" 101 " not in resposta.split(b"\r\n", 1)[0]:
            raise RuntimeError("a mesa Soundcraft não aceitou a conexão WebSocket")
        for c, a in canais.items():
            s.sendall(_ws_quadro(f"3:::SETD^i.{c - 1}.mute^{0 if a else 1}"))


def _http(cfg, canais, motivo):
    if not cfg.get("url"):
        raise ValueError("Informe o endereço HTTP da ponte.")
    corpo = json.dumps({"canais": [{"canal": c, "aberto": a} for c, a in canais.items()], "motivo": motivo}).encode()
    req = urllib.request.Request(cfg["url"], corpo, {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=2).read()


def montar_url(modelo: str, canal: int, aberto: bool) -> str:
    return (modelo.replace("{canal}", str(canal)).replace("{acao}", "on" if aberto else "off")
            .replace("{valor}", "1" if aberto else "0").replace("{aberto}", "true" if aberto else "false"))


def _http_modelo(cfg, canais, motivo):
    if "{canal}" not in (cfg.get("url") or ""):
        raise ValueError("A URL precisa conter {canal}, por exemplo http://192.168.0.60/relay/{canal}?turn={acao}.")
    metodo = "POST" if str(cfg.get("metodo")).upper() == "POST" else "GET"
    for c, a in canais.items():
        req = urllib.request.Request(montar_url(cfg["url"], c, a), b"" if metodo == "POST" else None, method=metodo)
        urllib.request.urlopen(req, timeout=2).read()


ENVIOS = {"osc": _osc, "yamaha": _yamaha, "allenheath_qu": _allenheath_qu, "allenheath_sq": _allenheath_sq,
          "soundcraft": _soundcraft, "http": _http, "http_modelo": _http_modelo}
