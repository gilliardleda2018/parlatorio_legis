"""Drivers da mesa de som contra equipamentos simulados (TCP, WebSocket, HTTP e OSC)."""
import socket
import struct
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import mesa  # noqa: E402

from apoio import TesteComServidor  # noqa: E402


class ServidorTCP:
    """Aceita conexões e guarda tudo o que chega; responde com `resposta` (se houver)."""

    def __init__(self, resposta=b"", websocket=False):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.porta = self.sock.getsockname()[1]
        self.recebido = b""
        self.resposta, self.websocket = resposta, websocket
        self.pronto = threading.Event()
        threading.Thread(target=self._servir, daemon=True).start()

    def _servir(self):
        c, _ = self.sock.accept()
        with c:
            c.settimeout(2)
            if self.websocket:
                cab = b""
                while b"\r\n\r\n" not in cab:
                    cab += c.recv(1024)
                c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
            try:
                while True:
                    parte = c.recv(4096)
                    if not parte:
                        break
                    self.recebido += parte
                    if self.resposta:
                        c.sendall(self.resposta)
            except socket.timeout:
                pass
        self.pronto.set()

    def fechar(self):
        self.sock.close()


def quadros_ws(dados: bytes) -> list[str]:
    """Decodifica quadros de texto mascarados enviados pelo cliente."""
    textos, i = [], 0
    while i < len(dados):
        n = dados[i + 1] & 0x7F
        i += 2
        if n == 126:
            n = struct.unpack(">H", dados[i:i + 2])[0]
            i += 2
        mascara = dados[i:i + 4]
        i += 4
        textos.append(bytes(x ^ mascara[k % 4] for k, x in enumerate(dados[i:i + n])).decode())
        i += n
    return textos


class Drivers(unittest.TestCase):
    def cfg(self, driver, porta, **extra):
        return {**mesa.CFG_PADRAO, "driver": driver, "host": "127.0.0.1", "porta": porta, **extra}

    def test_yamaha_liga_e_desliga_o_canal(self):
        srv = ServidorTCP(resposta=b"OK set MIXER:Current/InCh/Fader/On 2 0 1\n")
        mesa.ENVIOS["yamaha"](self.cfg("yamaha", srv.porta), {3: True, 5: False}, "")
        srv.pronto.wait(3)
        self.assertEqual(srv.recebido.decode().splitlines(),
                         ["set MIXER:Current/InCh/Fader/On 2 0 1", "set MIXER:Current/InCh/Fader/On 4 0 0"])
        srv.fechar()

    def test_yamaha_erro_vira_excecao(self):
        srv = ServidorTCP(resposta=b"ERROR set InvalidArgument\n")
        with self.assertRaises(RuntimeError):
            mesa.ENVIOS["yamaha"](self.cfg("yamaha", srv.porta), {1: True}, "")
        srv.fechar()

    def test_allenheath_qu_note_on(self):
        srv = ServidorTCP()
        mesa.ENVIOS["allenheath_qu"](self.cfg("allenheath_qu", srv.porta, midiCanal=2), {1: False, 4: True}, "")
        srv.pronto.wait(3)
        self.assertEqual(srv.recebido, bytes([0x91, 0, 0x7F, 0x91, 0, 0, 0x91, 3, 0x3F, 0x91, 3, 0]))
        srv.fechar()

    def test_allenheath_sq_nrpn(self):
        srv = ServidorTCP()
        mesa.ENVIOS["allenheath_sq"](self.cfg("allenheath_sq", srv.porta), {10: False}, "")
        srv.pronto.wait(3)
        self.assertEqual(srv.recebido, bytes([0xB0, 0x63, 0, 0xB0, 0x62, 9, 0xB0, 0x06, 0, 0xB0, 0x26, 1]))
        srv.fechar()

    def test_soundcraft_websocket(self):
        srv = ServidorTCP(websocket=True)
        mesa.ENVIOS["soundcraft"](self.cfg("soundcraft", srv.porta), {1: True, 2: False}, "")
        srv.pronto.wait(3)
        self.assertEqual(quadros_ws(srv.recebido), ["3:::SETD^i.0.mute^0", "3:::SETD^i.1.mute^1"])
        srv.fechar()

    def test_osc_wing_usa_mudo(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        s.settimeout(2)
        p = mesa.PRESETS["wing"]
        cfg = self.cfg("osc", s.getsockname()[1], endereco=p["endereco"], valorAberto=p["valorAberto"],
                       valorFechado=p["valorFechado"], tipoValor="i")
        mesa.ENVIOS["osc"](cfg, {7: True}, "")
        d, _ = s.recvfrom(1024)
        self.assertEqual((d[:d.index(b"\0")], struct.unpack(">i", d[-4:])[0]), (b"/ch/7/mute", 0))
        s.close()

    def test_http_modelo_uma_url_por_canal(self):
        pedidos = []

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                pedidos.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/relay/{{canal}}?turn={{acao}}&v={{valor}}"
        mesa.ENVIOS["http_modelo"](self.cfg("http_modelo", 0, url=url), {2: True, 3: False}, "")
        srv.shutdown()
        srv.server_close()
        self.assertEqual(pedidos, ["/relay/2?turn=on&v=1", "/relay/3?turn=off&v=0"])

    def test_todos_os_modelos_tem_driver(self):
        for nome, p in mesa.PRESETS.items():
            self.assertIn(p["driver"], mesa.DRIVERS, nome)
            self.assertTrue(p["driver"] in mesa.ENVIOS or p["driver"] == "simulado", nome)


class ConfiguracaoNoPainel(TesteComServidor):
    def test_salvar_valida_cada_modelo(self):
        s, P = self.s, self.P
        self.assertEqual(s.acao(P, "mesaSalvar", cfg={"driver": "yamaha", "modelo": "yamaha", "host": "192.168.0.80",
                                                       "porta": 49280})["ok"], True)
        self.assertEqual(s.estado()["mesa"]["driver"], "yamaha")
        self.erro(s.acao(P, "mesaSalvar", cfg={"driver": "fantasma"}), 400)
        self.erro(s.acao(P, "mesaSalvar", cfg={"driver": "http_modelo", "url": "http://10.0.0.5/relay"}), 400, "{canal}")
        self.erro(s.acao(P, "mesaSalvar", cfg={"driver": "http", "url": "file:///etc/passwd"}), 400)
        self.erro(s.acao(P, "mesaSalvar", cfg={"driver": "allenheath_sq", "host": "10.0.0.9", "midiCanal": 17}), 400)
        self.assertIn("wing", s.estado()["mesaPresets"])
        s.acao(P, "mesaSalvar", cfg={"driver": "simulado", "modelo": ""})


if __name__ == "__main__":
    unittest.main()
