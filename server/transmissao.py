"""Transmissão ao vivo da sessão para um servidor de rádio (Icecast ou AzuraCast).

O navegador da mesa capta a saída da mesa de som e manda o áudio em partes de 1 s (WebM/Ogg Opus).
Um processo ffmpeg converte para MP3 e este módulo publica o MP3 no servidor de rádio pelo
protocolo "source" do Icecast (o mesmo de programas como BUTT e Mixxx), aceito pelo Icecast e
pelo Liquidsoap do AzuraCast. No AzuraCast, crie uma conta de Streamer/DJ: porta 8005,
montagem "/", usuário e senha do DJ.
"""
import base64
import queue
import shutil
import socket
import ssl
import subprocess
import threading
import time

PRESETS = {
    "local": {"nome": "A rádio puxa do Parlatório (AzuraCast na mesma rede)", "porta": 0, "mount": "", "usuario": ""},
    "azuracast": {"nome": "AzuraCast (Streamer/DJ ao vivo)", "porta": 8005, "mount": "/", "usuario": ""},
    "icecast": {"nome": "Icecast", "porta": 8000, "mount": "/sessao", "usuario": "source"},
}
BITRATES = (64, 96, 128, 160, 192)
MAX_OUVINTES = 20  # quem puxa direto de /aovivo: a rádio e alguns conferentes; o público ouve pela rádio
FILA_MAXIMA = 40  # ~40 s de áudio parado na fila: a rede ou o servidor de rádio não está acompanhando


def _cabecalho(texto: str) -> str:
    return " ".join(str(texto).split())[:200]  # sem quebras de linha: não deixa injetar cabeçalhos


def conectar(cfg: dict, titulo: str, timeout: float = 8) -> socket.socket:
    """Abre a conexão source e confere a resposta. Erros viram RuntimeError com mensagem para o painel."""
    host, porta = cfg["host"], int(cfg["porta"])
    mount = "/" + (cfg.get("mount") or "/").lstrip("/")
    cred = base64.b64encode(f"{cfg.get('usuario') or 'source'}:{cfg.get('senha') or ''}".encode()).decode()
    try:
        s = socket.create_connection((host.strip("[]"), porta), timeout=timeout)
        if cfg.get("tls"):
            s = ssl.create_default_context().wrap_socket(s, server_hostname=host.strip("[]"))
        pedido = (f"SOURCE {mount} HTTP/1.0\r\nHost: {host}:{porta}\r\nAuthorization: Basic {cred}\r\n"
                  f"User-Agent: ParlatorioLegis\r\nContent-Type: audio/mpeg\r\n"
                  f"Ice-Name: {_cabecalho(cfg.get('nome') or titulo or 'Sessão ao vivo')}\r\n"
                  f"Ice-Description: {_cabecalho(titulo or 'Transmissão da sessão')}\r\nIce-Public: 0\r\n"
                  f"Ice-Audio-Info: bitrate={int(cfg.get('bitrate') or 128)}\r\n\r\n")
        s.sendall(pedido.encode("utf-8"))
        resposta = b""
        while b"\n" not in resposta and len(resposta) < 4096:
            parte = s.recv(1024)
            if not parte:
                break
            resposta += parte
    except ssl.SSLError:
        raise RuntimeError("Falha no TLS com o servidor de rádio (desligue o TLS ou confira o certificado).")
    except socket.gaierror:
        raise RuntimeError("Não encontrei o servidor de rádio: confira o endereço.")
    except (ConnectionRefusedError, TimeoutError, socket.timeout, OSError):
        raise RuntimeError("Não foi possível conectar ao servidor de rádio (endereço, porta ou firewall).")
    linha = resposta.split(b"\n", 1)[0].decode(errors="replace").strip()
    codigo = linha.split()[1] if len(linha.split()) > 1 else ""
    if codigo.startswith("2") or codigo == "100":
        s.settimeout(30)
        return s
    s.close()
    if codigo == "401":
        raise RuntimeError("O servidor de rádio recusou usuário ou senha.")
    if codigo in ("403", "409"):
        raise RuntimeError("A montagem já está em uso ou não aceita esta conexão.")
    raise RuntimeError(f"O servidor de rádio respondeu: {linha[:120] or 'nada'}.")


class Transmissor:
    def __init__(self, ao_mudar):
        self.ao_mudar = ao_mudar  # chamado quando a transmissão cai sozinha
        self.proc: subprocess.Popen | None = None
        self.sock: socket.socket | None = None
        self.fila: queue.Queue = queue.Queue()
        self.desde = None
        self.bytes = 0
        self.erro = None
        self.conectando = False
        self._trava = threading.Lock()
        self._geracao = 0
        self.ouvintes: set[queue.Queue] = set()

    @property
    def ativa(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> dict:
        return {"ativa": self.ativa, "conectando": self.conectando, "desde": self.desde, "bytes": self.bytes, "erro": self.erro}

    def ligar_em_segundo_plano(self, cfg: dict, titulo: str = ""):
        """A conexão pode demorar alguns segundos: não segura a trava do plenário enquanto isso."""
        if self.conectando:
            raise RuntimeError("Já estou conectando ao servidor de rádio.")
        self.conectando, self.erro = True, None
        cfg = dict(cfg)

        def rodar():
            try:
                self.ligar(cfg, titulo)
            except RuntimeError as e:
                self.erro = str(e)
            finally:
                self.conectando = False
                self.ao_mudar()
        threading.Thread(target=rodar, daemon=True).start()

    def ligar(self, cfg: dict, titulo: str = ""):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("O servidor precisa do ffmpeg para transmitir (apt install ffmpeg).")
        local = cfg.get("preset") == "local"
        if not local and (not cfg.get("host") or not cfg.get("senha")):
            raise RuntimeError("Configure o servidor de rádio (endereço e senha) antes de transmitir.")
        self.desligar(avisar=False)
        sock = None
        if not local:
            try:
                sock = conectar(cfg, titulo)
            except RuntimeError as e:
                self.erro = str(e)
                raise
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-fflags", "+nobuffer", "-i", "pipe:0", "-vn",
               "-ac", "2", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", f"{int(cfg.get('bitrate') or 128)}k",
               "-flush_packets", "1", "-f", "mp3", "pipe:1"]
        with self._trava:
            self._geracao += 1
            geracao = self._geracao
            self.fila = queue.Queue()
            self.sock = sock
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.desde, self.bytes, self.erro = int(time.time() * 1000), 0, None
        threading.Thread(target=self._escrever, args=(self.proc, self.fila, geracao), daemon=True).start()
        threading.Thread(target=self._publicar, args=(self.proc, sock, geracao), daemon=True).start()

    def enviar(self, dados: bytes):
        if not self.ativa:
            raise RuntimeError(self.erro or "A transmissão não está ligada.")
        if self.fila.qsize() > FILA_MAXIMA:
            raise RuntimeError("A transmissão está atrasada: verifique a internet do servidor.")
        self.fila.put(dados)

    def desligar(self, avisar=True):
        with self._trava:
            proc, sock = self.proc, self.sock
            self.proc = self.sock = None
            self._geracao += 1
            self.fila.put(None)
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        self._fechar_ouvintes()
        if avisar:
            self.ao_mudar()

    def _escrever(self, proc, fila, geracao):
        """Partes do navegador → entrada do ffmpeg."""
        while True:
            dados = fila.get()
            if dados is None or geracao != self._geracao:
                return
            try:
                proc.stdin.write(dados)
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                return

    def _publicar(self, proc, sock, geracao):
        """MP3 do ffmpeg → servidor de rádio. Se qualquer lado cair, registra o motivo e avisa as telas."""
        erro = None
        try:
            while True:
                bloco = proc.stdout.read1(16384)
                if not bloco:
                    break
                if sock:
                    sock.sendall(bloco)
                self._distribuir(bloco)
                self.bytes += len(bloco)
        except (OSError, ValueError):
            erro = "A conexão com o servidor de rádio caiu."
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        saida = proc.stderr.read().decode(errors="replace").strip()
        for f in (proc.stdout, proc.stderr):
            f.close()
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        if geracao != self._geracao:  # desligada de propósito ou substituída
            return
        with self._trava:
            self.proc = self.sock = None
            self.erro = erro or ("O áudio enviado pelo navegador não pôde ser lido: " + saida.splitlines()[-1][:150]
                                 if saida else "A transmissão parou.")
        self._fechar_ouvintes()
        self.ao_mudar()

    # ---- quem puxa o MP3 direto do Parlatório (GET /aovivo)
    def _distribuir(self, bloco: bytes):
        for q in list(self.ouvintes):
            try:
                q.put_nowait(bloco)
            except queue.Full:  # ouvinte lento demais: desliga para não acumular memória
                self.ouvintes.discard(q)
                q.put(None)

    def _fechar_ouvintes(self):
        for q in list(self.ouvintes):
            self.ouvintes.discard(q)
            try:
                q.put_nowait(None)
            except queue.Full:
                pass

    def ouvir(self):
        """Gerador de blocos MP3 enquanto a transmissão estiver no ar; None se não estiver."""
        if not self.ativa:
            return None
        if len(self.ouvintes) >= MAX_OUVINTES:
            raise RuntimeError("Limite de conexões diretas atingido: ouça pela rádio.")
        q: queue.Queue = queue.Queue(maxsize=400)
        self.ouvintes.add(q)

        def gerar():
            try:
                while True:
                    try:
                        bloco = q.get(timeout=15)
                    except queue.Empty:
                        if not self.ativa:
                            return
                        continue
                    if bloco is None:
                        return
                    yield bloco
            finally:
                self.ouvintes.discard(q)
        return gerar()
