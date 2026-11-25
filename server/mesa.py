"""Integração com a mesa de som.

O servidor calcula, a cada mudança, quais canais devem estar abertos
(orador com a palavra, aparte, microfone liberado pelo presidente) e
quais devem estar fechados (tempo esgotado, microfone cortado, plenário
congelado). Este módulo compara com o último estado enviado e manda só
as diferenças para o equipamento, numa thread própria para nunca travar
o plenário se a mesa não responder.

Drivers:
  * simulado: só registra no painel (para treinar e testar sem equipamento)
  * osc: mensagens OSC por UDP (Behringer X32/M32, X Air, e outras mesas digitais)
  * http: POST JSON para um endereço (ponte para sistemas de conferência,
    Node-RED, Companion, controladores próprios)

A cada 5 s o estado completo é reenviado (opcional), para corrigir a mesa
se alguém mexer nela à mão.
"""
import json
import queue
import socket
import struct
import threading
import time
import urllib.request

PRESETS = {
    "x32": {"porta": 10023, "endereco": "/ch/{canal:02d}/mix/on", "valorAberto": 1, "valorFechado": 0, "tipoValor": "i"},
    "xair": {"porta": 10024, "endereco": "/ch/{canal:02d}/mix/on", "valorAberto": 1, "valorFechado": 0, "tipoValor": "i"},
}

CFG_PADRAO = {
    "driver": "simulado", "host": "192.168.0.50", "porta": 10023, "endereco": "/ch/{canal:02d}/mix/on",
    "valorAberto": 1, "valorFechado": 0, "tipoValor": "i", "url": "", "canalTribuna": 0, "reenviar": True,
}


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
                    if any(cfg.get(k) != self.cfg.get(k) for k in ("driver", "host", "porta", "endereco", "url", "tipoValor", "valorAberto", "valorFechado")):
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
            if driver == "osc":
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    destino = (cfg["host"], int(cfg["porta"]))
                    for canal, aberto in canais.items():
                        valor = cfg["valorAberto"] if aberto else cfg["valorFechado"]
                        s.sendto(osc_mensagem(cfg["endereco"].format(canal=canal), valor, cfg.get("tipoValor", "i")), destino)
            elif driver == "http":
                if not cfg.get("url"):
                    raise ValueError("Informe o endereço HTTP da ponte.")
                corpo = json.dumps({"canais": [{"canal": c, "aberto": a} for c, a in canais.items()], "motivo": motivo}).encode()
                req = urllib.request.Request(cfg["url"], corpo, {"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=2).read()
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
            self.aviso()
