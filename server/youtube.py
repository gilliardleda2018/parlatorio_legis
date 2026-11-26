"""Transmissão da sessão para o YouTube ao vivo (RTMP ou RTMPS).

O navegador da mesa já manda a sessão em partes de 1 s (as mesmas da rádio). Duas imagens:

  * câmera: as partes trazem vídeo (WebM VP8) e áudio. O LEGIS põe por cima uma faixa com o
    nome da Câmara, o orador (nome, partido e fase) e o cronômetro;
  * tela automática (sem câmera): as partes trazem só o áudio. O LEGIS monta a imagem com o nome
    da Câmara, a foto, o nome e o partido do orador, a fase, o cronômetro e a votação em curso.

Os textos ficam em arquivos que o ffmpeg relê a cada quadro (drawtext reload=1). A tela automática
é desenhada por um ffmpeg rápido a cada mudança e mandada ao ffmpeg da transmissão por um
segundo canal (image2pipe), porque o ffmpeg não relê uma imagem fixa que muda no disco.
"""
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

URL_PADRAO = "rtmps://a.rtmps.youtube.com:443/live2"
LARG, ALT = 1280, 720
QPS_TELA = 2          # quadros por segundo da tela automática entregues ao ffmpeg (ele repete até 30)
FILA_MAXIMA = 40      # ~40 s de mídia parada: a internet não está dando conta
FONTES = {False: ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf"],
          True: ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"]}
VERDE_ESCURO, VERDE, SUAVE, VERMELHO = "0x0d2418", "0x1f8a4c", "0xa7c4b3", "0xd32f2f"


def fonte(negrito: bool = False) -> str:
    for f in FONTES[negrito]:
        if os.path.exists(f):
            return f"fontfile={f}:"
    return ""  # sem a fonte DejaVu: o ffmpeg usa a padrão do fontconfig


def validar_url(url: str) -> str:
    url = str(url or "").strip().rstrip("/") or URL_PADRAO
    if not re.fullmatch(r"rtmps?://[A-Za-z0-9.-]{1,253}(:\d{1,5})?(/[A-Za-z0-9._~-]{1,100}){0,4}", url):
        raise ValueError("Endereço do servidor inválido (ex.: rtmps://a.rtmps.youtube.com:443/live2).")
    return url


def validar_chave(chave: str) -> str:
    chave = str(chave or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,120}", chave):
        raise ValueError("Chave de transmissão inválida: copie a “chave de stream” do YouTube Studio.")
    return chave


def _cortar(texto: str, n: int) -> str:
    texto = " ".join(str(texto or "").split())
    return texto if len(texto) <= n else texto[: n - 1].rstrip() + "…"


def _mmss(ms: int) -> str:
    s = abs(int(ms)) // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def textos(q: dict) -> dict:
    """O que vai escrito na imagem, a partir do retrato da sessão montado pelo servidor."""
    o, v = q.get("orador"), q.get("votacao")
    t = {"casa": _cortar(q.get("casa"), 48), "titulo": _cortar(q.get("titulo"), 60),
         "rodape": "LEGIS · Sistema de Gestão Parlamentar", "nome": "", "partido": "", "fase": "", "linha2": "",
         "tempo": "", "excedido": "", "estado": "", "detalhe": ""}
    if o:
        t["nome"] = _cortar(o["nome"], 30)
        t["partido"] = o.get("partido") or ""
        t["fase"] = _cortar(o.get("fase"), 50)
        t["linha2"] = " · ".join(x for x in (t["partido"], t["fase"]) if x)
        restante = int(o.get("restante") or 0)
        t["tempo" if restante >= 0 else "excedido"] = ("+" if restante < 0 else "") + _mmss(restante)
    elif v:
        t["estado"] = _cortar(f"Em votação: {v.get('numero') or ''}", 44)
        t["detalhe"] = _cortar(v.get("ementa"), 70)
    else:
        t["estado"] = "Sessão ao vivo"
        t["detalhe"] = _cortar(q.get("fase") or "Aguardando o próximo orador", 70)
    return t


def _escrever(pasta: Path, nome: str, valor: str):
    tmp = pasta / f".{nome}.tmp"
    tmp.write_text(valor, encoding="utf-8")
    os.replace(tmp, pasta / f"{nome}.txt")   # troca atômica: o ffmpeg nunca lê um arquivo pela metade


def _dt(pasta: Path, nome: str, x: str, y: str, tamanho: int, cor: str = "white", negrito: bool = False, extra: str = "") -> str:
    caminho = str(pasta / f"{nome}.txt").replace("\\", "/").replace(":", r"\:")
    return f"drawtext={fonte(negrito)}textfile='{caminho}':reload=1:x={x}:y={y}:fontsize={tamanho}:fontcolor={cor}{extra}"


def _cronometro(pasta: Path, x: str, y: str, tamanho: int) -> str:
    """Tempo restante (branco) ou excedido (vermelho): um dos dois arquivos está sempre vazio."""
    return ",".join([_dt(pasta, "tempo", x, y, tamanho, negrito=True), _dt(pasta, "excedido", x, y, tamanho, "0xff6b6b", negrito=True)])


def desenhar_tela(pasta: Path, ffmpeg: str, foto: bytes | None, logo: bytes | None, com_orador: bool) -> bytes:
    """Um quadro JPEG 1280×720 da tela automática (sem o cronômetro, que o ffmpeg da transmissão desenha)."""
    entradas, n = ["-f", "lavfi", "-i", f"color=c={VERDE_ESCURO}:s={LARG}x{ALT}"], 1
    grafo = [f"[0:v]drawbox=x=0:y=0:w=iw:h=96:color={VERDE}:t=fill"]
    x_casa = 40
    if logo:
        (pasta / "logo.img").write_bytes(logo)
        entradas += ["-i", str(pasta / "logo.img")]
        grafo[-1] += "[b0];"
        grafo.append(f"[{n}:v]scale=-1:72[lg];[b0][lg]overlay=40:12:format=auto")
        n += 1
        x_casa = 140
    grafo[-1] += "," + ",".join([
        _dt(pasta, "casa", str(x_casa), "28", 36, negrito=True),
        f"drawtext={fonte(True)}text='AO VIVO':x=w-tw-40:y=30:fontsize=28:fontcolor=white:box=1:boxcolor={VERMELHO}:boxborderw=10",
        _dt(pasta, "titulo", "40", "124", 30, SUAVE),
        _dt(pasta, "rodape", "w-tw-40", "h-50", 22, SUAVE),
    ])
    if com_orador:
        x = 60
        if foto:
            (pasta / "foto.img").write_bytes(foto)
            entradas += ["-i", str(pasta / "foto.img")]
            grafo[-1] += "[b1];"
            grafo.append(f"[{n}:v]scale=320:400:force_original_aspect_ratio=increase,crop=320:400[ft];"
                         f"[b1][ft]overlay=60:200:format=auto")
            x = 430
        grafo[-1] += "," + ",".join([_dt(pasta, "nome", str(x), "215", 60, negrito=True),
                                     _dt(pasta, "partido", str(x), "300", 38, SUAVE, negrito=True),
                                     _dt(pasta, "fase", str(x), "356", 30, "0xd9e6dd")])
    else:
        grafo[-1] += "," + ",".join([_dt(pasta, "estado", "(w-tw)/2", "300", 56, negrito=True),
                                     _dt(pasta, "detalhe", "(w-tw)/2", "390", 30, "0xd9e6dd")])
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", *entradas, "-filter_complex", "".join(grafo),
           "-frames:v", "1", "-q:v", "3", "-f", "image2", "-c:v", "mjpeg", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, timeout=20)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(r.stderr.decode(errors="replace").strip()[-300:] or "falha ao desenhar a tela")
    return r.stdout


class YouTube:
    def __init__(self, ao_mudar, quadro, imagem):
        self.ao_mudar = ao_mudar   # avisa as telas quando liga, cai ou desliga
        self.quadro = quadro       # () -> retrato da sessão (dict) para os textos e a tela
        self.imagem = imagem       # (chave) -> bytes da foto/brasão ou None
        self.proc: subprocess.Popen | None = None
        self.fila: queue.Queue = queue.Queue()
        self.modo = None
        self.desde = None
        self.erro = None
        self.pasta: Path | None = None
        self._geracao = 0
        self._trava = threading.Lock()
        self._jpeg = b""

    @property
    def ativa(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> dict:
        return {"ativa": self.ativa, "modo": self.modo if self.ativa else None, "desde": self.desde if self.ativa else None,
                "erro": self.erro}

    # ---- liga
    def ligar(self, url: str, chave: str, camera: bool):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("O servidor precisa do ffmpeg para transmitir (apt install ffmpeg).")
        destino = f"{validar_url(url)}/{validar_chave(chave)}"
        self.desligar(avisar=False)
        pasta = Path(tempfile.mkdtemp(prefix="legis-youtube-"))
        self._atualizar_textos(pasta, textos(self.quadro()))
        log = open(pasta / "ffmpeg.log", "wb")
        saida = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "30", "-g", "60", "-keyint_min", "60",
                 "-sc_threshold", "0", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", "-f", "flv", destino]
        if camera:
            faixa = ",".join([
                f"drawbox=x=0:y=0:w=iw:h=56:color={VERDE_ESCURO}@0.78:t=fill",
                _dt(pasta, "casa", "24", "15", 26, negrito=True),
                f"drawtext={fonte(True)}text='AO VIVO':x=w-tw-24:y=14:fontsize=24:fontcolor=white:box=1:boxcolor={VERMELHO}:boxborderw=8",
                f"drawbox=x=40:y=ih-140:w=iw-80:h=110:color={VERDE_ESCURO}@0.82:t=fill",
                _dt(pasta, "nome", "64", "h-128", 40, negrito=True),
                _dt(pasta, "linha2", "64", "h-76", 26, SUAVE, negrito=True),
                _dt(pasta, "estado", "64", "h-122", 36, negrito=True),
                _dt(pasta, "detalhe", "64", "h-74", 24, "0xd9e6dd"),
                _cronometro(pasta, "w-tw-70", "h-122", 64),
            ])
            grafo = (f"[0:v]scale={LARG}:{ALT}:force_original_aspect_ratio=decrease,pad={LARG}:{ALT}:(ow-iw)/2:(oh-ih)/2,"
                     f"fps=30,format=yuv420p,{faixa}[v]")
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-thread_queue_size", "1024", "-i", "pipe:0",
                   "-filter_complex", grafo, "-map", "[v]", "-map", "0:a", "-b:v", "2500k", "-maxrate", "2500k",
                   "-bufsize", "5000k", *saida]
            pass_fds, video_w = (), None
        else:
            self._jpeg = self._desenhar(pasta, ffmpeg, self.quadro())
            video_r, video_w = os.pipe()
            grafo = (f"[0:v]scale={LARG}:{ALT},fps=30,format=yuv420p,"
                     + _cronometro(pasta, "430", "440", 130) + "[v]")
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
                   "-thread_queue_size", "64", "-f", "image2pipe", "-framerate", str(QPS_TELA), "-c:v", "mjpeg", "-i", f"pipe:{video_r}",
                   "-thread_queue_size", "1024", "-i", "pipe:0",
                   "-filter_complex", grafo, "-map", "[v]", "-map", "1:a", "-tune", "stillimage", "-b:v", "1500k",
                   "-maxrate", "1500k", "-bufsize", "3000k", *saida]
            pass_fds = (video_r,)
        with self._trava:
            self._geracao += 1
            geracao = self._geracao
            self.fila = queue.Queue()
            self.pasta, self.modo = pasta, "camera" if camera else "tela"
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log, pass_fds=pass_fds)
            self.desde, self.erro = int(time.time() * 1000), None
        log.close()
        if video_w is not None:
            os.close(pass_fds[0])
            threading.Thread(target=self._alimentar_tela, args=(video_w, geracao), daemon=True).start()
        threading.Thread(target=self._escrever_partes, args=(self.proc, self.fila, geracao), daemon=True).start()
        threading.Thread(target=self._atualizar, args=(pasta, ffmpeg, geracao), daemon=True).start()
        threading.Thread(target=self._vigiar, args=(self.proc, pasta, geracao), daemon=True).start()

    def enviar(self, dados: bytes):
        if not self.ativa:
            return
        if self.fila.qsize() > FILA_MAXIMA:
            raise RuntimeError("O YouTube está atrasado: a internet do servidor não está dando conta do vídeo.")
        self.fila.put(dados)

    def desligar(self, avisar=True):
        with self._trava:
            proc, pasta = self.proc, self.pasta
            self.proc = self.pasta = None
            self._geracao += 1
            self.fila.put(None)
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if pasta:
            shutil.rmtree(pasta, ignore_errors=True)
        if avisar:
            self.ao_mudar()

    # ---- por dentro
    def _escrever_partes(self, proc, fila, geracao):
        while True:
            dados = fila.get()
            if dados is None or geracao != self._geracao:
                return
            try:
                proc.stdin.write(dados)
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                return

    def _alimentar_tela(self, fd, geracao):
        """Entrega a tela automática ao ffmpeg em ritmo de tempo real (QPS_TELA quadros por segundo)."""
        proximo = time.monotonic()
        try:
            with os.fdopen(fd, "wb", buffering=0) as canal:
                while geracao == self._geracao:
                    canal.write(self._jpeg)
                    proximo += 1 / QPS_TELA
                    time.sleep(max(0.0, proximo - time.monotonic()))
        except (BrokenPipeError, OSError, ValueError):
            pass

    def _desenhar(self, pasta: Path, ffmpeg: str, q: dict) -> bytes:
        o = q.get("orador")
        foto = self.imagem(o["id"]) if o and o.get("fotoV") else None
        logo = self.imagem("logo") if q.get("logoV") else None
        return desenhar_tela(pasta, ffmpeg, foto, logo, bool(o))

    def _atualizar_textos(self, pasta: Path, t: dict):
        for nome, valor in t.items():
            _escrever(pasta, nome, valor)

    def _atualizar(self, pasta: Path, ffmpeg: str, geracao):
        """A cada meio segundo: textos e cronômetro; a tela automática é redesenhada quando muda o que ela mostra."""
        anterior_t, anterior_tela = None, None
        while geracao == self._geracao:
            try:
                q = self.quadro()
                t = textos(q)
                if t != anterior_t:
                    self._atualizar_textos(pasta, t)
                    anterior_t = t
                if self.modo == "tela":
                    o = q.get("orador")
                    chave_tela = (t["casa"], t["titulo"], t["nome"], t["partido"], t["fase"], t["estado"], t["detalhe"],
                                  q.get("logoV"), o and o.get("fotoV"), o and o.get("id"))
                    if chave_tela != anterior_tela:
                        if anterior_tela is not None:
                            self._jpeg = self._desenhar(pasta, ffmpeg, q)
                        anterior_tela = chave_tela
            except Exception as e:  # a transmissão continua com a última imagem boa
                print("youtube: não atualizei a imagem:", e, flush=True)
            time.sleep(0.5)

    def _vigiar(self, proc, pasta: Path, geracao):
        proc.wait()
        if geracao != self._geracao:   # desligada de propósito
            return
        try:
            saida = (pasta / "ffmpeg.log").read_text(errors="replace").strip()
        except OSError:
            saida = ""
        with self._trava:
            self.proc = None
            self.erro = traduzir_erro(saida)
            self._geracao += 1
            self.pasta = None
        shutil.rmtree(pasta, ignore_errors=True)
        self.ao_mudar()


def traduzir_erro(saida: str) -> str:
    """Última linha do ffmpeg → mensagem para o painel (sem nunca mostrar a chave)."""
    s = saida.lower()
    if "connection refused" in s or "cannot open connection" in s or "network is unreachable" in s:
        return "Não consegui conectar ao YouTube: verifique a internet do servidor."
    if "input/output error" in s or "broken pipe" in s or "end of file" in s or "connection reset" in s:
        return "O YouTube recusou ou encerrou a transmissão: confira a chave e se a live está liberada no YouTube Studio."
    if "invalid data" in s or "error while decoding" in s or "could not find codec" in s:
        return "O vídeo ou o áudio enviado pelo navegador não pôde ser lido."
    ultima = re.sub(r"rtmps?://\S+", "rtmp://…", saida.splitlines()[-1]) if saida else ""
    return ("A transmissão para o YouTube parou: " + ultima[:160]) if ultima else "A transmissão para o YouTube parou."
