#!/usr/bin/env python3
"""Painel dos pacotes de instalação do LEGIS (só para quem gera e entrega os pacotes).

    python3 packaging/painel.py            abre http://127.0.0.1:8099 no navegador

- Câmaras: cria o pacote de uma Câmara (estado, município, nome, brasão, link do YouTube), mostra quais
  estão desatualizados em relação ao código atual, regenera um ou todos, baixa os arquivos e copia o
  comando de instalação.
- Versão geral: gera o pacote completo e publica a versão no GitHub (com confirmação).
- Instalar num servidor: cada Câmara tem um comando de uma linha para rodar no servidor dela, que baixa o
  pacote direto deste computador pela rede (porta 8098, com um código por Câmara) e instala sem perguntas.

A configuração de cada Câmara fica em dados/camaras/<nome>/ (fora do Git); os pacotes, em dist/.
Neste computador (http://127.0.0.1:8099) abre direto. Online (PAINEL_PUBLICO=servidor.ts.net:10000, publicado
pelo Tailscale Funnel), pede a chave de acesso guardada em dados/painel/chave e abre uma sessão de 12 horas; as
instalações (/i/… e /p/…) continuam liberadas só com o código de cada Câmara.
"""
import base64
import hashlib
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

RAIZ = Path(__file__).resolve().parent.parent
CAMARAS = Path(os.environ.get("PAINEL_CAMARAS", RAIZ / "dados" / "camaras"))
DIST = Path(os.environ.get("PAINEL_DIST", RAIZ / "dist"))
DIST_CIDADES = DIST / "cidades"
PORTA = int(os.environ.get("PAINEL_PORTA", "8099"))
PORTA_REDE = int(os.environ.get("PAINEL_PORTA_REDE", "8098"))   # só entrega pacotes, com o código de cada Câmara
TOKEN = secrets.token_urlsafe(24)
PUBLICO = os.environ.get("PAINEL_PUBLICO", "").strip().lower()     # ex.: server.taila400aa.ts.net:10000
ARQ_CHAVE = Path(os.environ.get("PAINEL_CHAVE_ARQ", RAIZ / "dados" / "painel" / "chave"))
SESSOES: dict[str, dict] = {}            # cookie -> {"token", "expira"}
TENTATIVAS: dict[str, list] = {}         # ip -> horários de chaves erradas
SESSAO_H = 12


def chave_acesso() -> str:
    """Chave de acesso do painel online: criada na primeira vez, só legível pelo dono do arquivo."""
    if not ARQ_CHAVE.exists():
        ARQ_CHAVE.parent.mkdir(parents=True, exist_ok=True)
        ARQ_CHAVE.write_text(secrets.token_urlsafe(18) + "\n")
        ARQ_CHAVE.chmod(0o600)
    return ARQ_CHAVE.read_text().strip()
UFS = ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI",
       "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"]
TIPOS_BRASAO = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
LINK_YOUTUBE = re.compile(r"https://[A-Za-z0-9./?=_@%&:+~#-]+", re.ASCII)   # nada de ; $( ` aspas ou espaço


def nome_seguro(valor: str) -> bool:
    """Município e Câmara vão para scripts que rodam como root: só letras (com acento), dígitos, espaço e .-'"""
    return all(c.isalnum() or c in " .-'" for c in valor)


def slug(t: str) -> str:
    t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:40]


def git(*args) -> str:
    r = subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, text=True)
    return r.stdout.strip()


def versao_atual() -> dict:
    return {"commit": git("rev-parse", "--short", "HEAD"), "titulo": git("log", "-1", "--format=%s"),
            "pendente": bool(git("status", "--porcelain", "--untracked-files=no"))}


def arquivos(pasta: Path) -> list[dict]:
    if not pasta.is_dir():
        return []
    return [{"nome": f.name, "bytes": f.stat().st_size, "url": "/arquivos/" + f.relative_to(DIST).as_posix(),
             "data": int(f.stat().st_mtime * 1000)} for f in sorted(pasta.iterdir()) if f.is_file()]


def commit_do_pacote(nome: str) -> str | None:
    m = re.search(r"\+([0-9a-f]{7,})_all\.deb$", nome)
    return m.group(1) if m else None


# ---------------------------------------------------------------- tarefas (uma de cada vez: os scripts dividem dist/)
class Tarefas:
    def __init__(self):
        self.lista: list[dict] = []
        self.fila: queue.Queue = queue.Queue()
        self.trava = threading.Lock()
        threading.Thread(target=self._trabalhar, daemon=True).start()

    def nova(self, titulo: str, cmd: list[str], env: dict | None = None, depois=None) -> dict:
        t = {"id": secrets.token_hex(4), "titulo": titulo, "estado": "na fila", "log": [], "criada": int(time.time() * 1000),
             "inicio": None, "fim": None}
        with self.trava:
            self.lista.insert(0, t)
            del self.lista[30:]
        self.fila.put((t, cmd, env or {}, depois))
        return t

    def publico(self, t: dict, linhas: int = 400) -> dict:
        return {**{k: v for k, v in t.items() if k != "log"}, "log": t["log"][-linhas:]}

    def _trabalhar(self):
        while True:
            t, cmd, env, depois = self.fila.get()
            t["estado"], t["inicio"] = "rodando", int(time.time() * 1000)
            try:
                proc = subprocess.Popen(cmd, cwd=RAIZ, env={**os.environ, **env}, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1)
                for linha in proc.stdout:
                    t["log"].append(re.sub(r"\x1b\[[0-9;]*m", "", linha.rstrip("\n")))
                    del t["log"][:-3000]
                codigo = proc.wait()
            except OSError as e:
                t["log"].append(f"Erro: {e}")
                codigo = 1
            t["estado"], t["fim"] = ("concluída" if codigo == 0 else "erro"), int(time.time() * 1000)
            if codigo == 0 and depois:
                try:
                    depois()
                except Exception as e:  # o pacote foi gerado; só o registro extra falhou
                    t["log"].append(f"Aviso: {e}")


TAREFAS = Tarefas()


def enderecos() -> list[dict]:
    """Endereços de onde outro servidor baixa: internet (painel publicado), rede local e Tailscale."""
    r = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True)
    saida = [{"url": f"https://{PUBLICO}", "rede": "internet (de qualquer lugar)"}] if PUBLICO else []
    for linha in r.stdout.splitlines():
        partes = linha.split()
        if len(partes) < 4:
            continue
        iface, ip = partes[1], partes[3].split("/")[0]
        if iface == "lo" or iface.startswith(("docker", "br-", "veth", "virbr")):
            continue
        saida.append({"url": f"http://{ip}:{PORTA_REDE}",
                      "rede": "Tailscale" if iface.startswith("tailscale") or ip.startswith("100.") else f"rede local ({iface})"})
    return saida[:1] + sorted(saida[1:], key=lambda e: e["rede"].startswith("Tailscale")) if PUBLICO else \
        sorted(saida, key=lambda e: e["rede"].startswith("Tailscale"))


# ---------------------------------------------------------------- Câmaras
def ler_camara(nome: str) -> dict | None:
    arq = CAMARAS / nome / "camara.json"
    if not re.fullmatch(r"[a-z0-9-]{1,40}", nome) or not arq.is_file():
        return None
    c = json.loads(arq.read_text(encoding="utf-8"))
    if not c.get("codigo"):   # código da instalação pela rede (Câmaras criadas antes desta função)
        c["codigo"] = secrets.token_urlsafe(9)
        arq.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    c["nome"] = nome
    return c


def camaras() -> list[dict]:
    atual = versao_atual()["commit"]
    saida = []
    for pasta in sorted(CAMARAS.glob("*/camara.json")):
        c = ler_camara(pasta.parent.name)
        if not c:
            continue
        arqs = arquivos(DIST_CIDADES / c["nome"])
        deb = next((a for a in arqs if a["nome"].endswith("_all.deb")), None)
        c["arquivos"] = arqs
        c["pacote"] = deb and {"nome": deb["nome"], "commit": commit_do_pacote(deb["nome"]), "data": deb["data"]}
        c["atualizado"] = bool(deb and commit_do_pacote(deb["nome"]) == atual)
        c["temBrasao"] = bool(c.get("brasao") and (CAMARAS / c["nome"] / c["brasao"]).is_file())
        saida.append(c)
    return saida


def gerar_camara(c: dict) -> dict:
    cmd = ["bash", "packaging/construir-cidade.sh", c["uf"], c["municipio"], "--nome", c["nome"]]
    if c.get("camara"):
        cmd += ["--camara", c["camara"]]
    if c.get("brasao"):
        cmd += ["--brasao", str(CAMARAS / c["nome"] / c["brasao"])]
    if c.get("youtube"):
        cmd += ["--youtube-link", c["youtube"]]

    def registrar():
        c2 = ler_camara(c["nome"])
        if c2:
            c2["gerado"] = int(time.time() * 1000)
            c2.pop("nome", None)
            (CAMARAS / c["nome"] / "camara.json").write_text(json.dumps(c2, ensure_ascii=False, indent=2), encoding="utf-8")
    return TAREFAS.nova(f"Pacote de {c.get('camara') or c['municipio']}", cmd, {"DIST_CIDADES": str(DIST_CIDADES)}, registrar)


def salvar_camara(dados: dict) -> dict:
    uf = str(dados.get("uf") or "").upper()
    municipio = " ".join(str(dados.get("municipio") or "").split())[:80]
    if uf not in UFS:
        raise ValueError("Escolha o estado.")
    if not municipio:
        raise ValueError("Escreva o município.")
    if not nome_seguro(municipio):
        raise ValueError("Município: use só letras, números, espaço e . - '")
    nome = slug(str(dados.get("nome") or "") or municipio)
    if not nome:
        raise ValueError("Nome curto inválido.")
    camara = " ".join(str(dados.get("camara") or "").split())[:120]
    if not nome_seguro(camara):
        raise ValueError("Nome da Câmara: use só letras, números, espaço e . - '")
    youtube = str(dados.get("youtube") or "").strip()
    if youtube and (len(youtube) > 300 or not LINK_YOUTUBE.fullmatch(youtube)):
        raise ValueError("O link do YouTube deve começar com https:// e não pode ter espaços, aspas, ; $ ou `")
    pasta = CAMARAS / nome
    existente = ler_camara(nome)
    if existente and not dados.get("editar"):
        raise ValueError(f"Já existe uma Câmara com o nome curto “{nome}”.")
    pasta.mkdir(parents=True, exist_ok=True)
    c = {"uf": uf, "municipio": municipio, "camara": camara, "youtube": youtube,
         "brasao": (existente or {}).get("brasao"), "criado": (existente or {}).get("criado") or int(time.time() * 1000),
         "codigo": (existente or {}).get("codigo") or secrets.token_urlsafe(9)}
    url = str(dados.get("brasao") or "")
    if url.startswith("data:"):
        cab, _, b64 = url.partition(",")
        mime = cab[5:].split(";")[0]
        if mime not in TIPOS_BRASAO:
            raise ValueError("Brasão: use PNG, JPEG ou WebP.")
        try:
            img = base64.b64decode(b64, validate=True)
        except ValueError:
            raise ValueError("Brasão inválido.")
        if len(img) > 1_500_000:
            raise ValueError("Brasão grande demais (máximo 1,5 MB).")
        for antigo in pasta.glob("brasao.*"):
            antigo.unlink()
        c["brasao"] = f"brasao.{TIPOS_BRASAO[mime]}"
        (pasta / c["brasao"]).write_bytes(img)
    elif url == "remover":
        for antigo in pasta.glob("brasao.*"):
            antigo.unlink()
        c["brasao"] = None
    (pasta / "camara.json").write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**c, "nome": nome}


def remover_camara(nome: str):
    if not ler_camara(nome):
        raise ValueError("Câmara não encontrada.")
    shutil.rmtree(CAMARAS / nome)
    shutil.rmtree(DIST_CIDADES / nome, ignore_errors=True)


# ---------------------------------------------------------------- versão geral
def geral() -> dict:
    arqs = [a for a in arquivos(DIST) if re.match(r"(legis_.*_all\.deb(\.sha256)?|legis-completo-.*\.tar\.gz|instalar-legis\.sh)$", a["nome"])]
    deb = next((a for a in arqs if a["nome"].endswith("_all.deb")), None)
    return {"arquivos": arqs, "commit": deb and commit_do_pacote(deb["nome"]), "gh": bool(shutil.which("gh"))}


ULTIMA_RELEASE = {"valor": None, "quando": 0}


def ultima_release() -> dict | None:
    if time.time() - ULTIMA_RELEASE["quando"] < 120:
        return ULTIMA_RELEASE["valor"]
    valor = None
    if shutil.which("gh"):
        r = subprocess.run(["gh", "release", "view", "--json", "tagName,name,url,publishedAt"], cwd=RAIZ,
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            valor = json.loads(r.stdout)
    ULTIMA_RELEASE.update(valor=valor, quando=time.time())
    return valor


# ---------------------------------------------------------------- HTTP
class Painel(BaseHTTPRequestHandler):
    server_version = "LegisPainel"

    def log_message(self, *a):
        pass

    def _json(self, codigo: int, obj):
        dados = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(dados)

    def _arquivo(self, caminho: Path, tipo: str | None = None, baixar: bool = False):
        if not caminho.is_file():
            return self._json(404, {"erro": "Arquivo não encontrado."})
        self.send_response(200)
        self.send_header("Content-Type", tipo or mimetypes.guess_type(caminho.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(caminho.stat().st_size))
        if baixar:
            self.send_header("Content-Disposition", f'attachment; filename="{caminho.name}"')
        self.end_headers()
        with open(caminho, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def _host_local(self) -> bool:
        """Pedido feito neste computador: veio do loopback, endereçado a 127.0.0.1/localhost (barra outro site via
        DNS apontado para cá) e sem cabeçalhos de proxy. Pelo Tailscale Funnel a conexão também chega do loopback
        e o Host é o que o cliente mandar; o que denuncia o proxy são estes cabeçalhos, que ele sempre põe."""
        if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return False
        if any(self.headers.get(c) is not None for c in
               ("X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "Forwarded", "Tailscale-Funnel-Request")):
            return False
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("127.0.0.1", "localhost", "::1")

    def _host_publico(self) -> bool:
        return bool(PUBLICO) and (self.headers.get("Host") or "").lower() in (PUBLICO, PUBLICO.split(":")[0])

    def _sessao(self) -> dict | None:
        """Online: a sessão do cookie, se ainda válida. Local: sempre (token da execução)."""
        if self._host_local():
            return {"token": TOKEN}
        for parte in (self.headers.get("Cookie") or "").split(";"):
            nome, _, valor = parte.strip().partition("=")
            s = SESSOES.get(valor) if nome == "painel" else None
            if s and s["expira"] > time.time():
                return s
        return None

    def _autorizado(self) -> bool:
        s = self._sessao()
        return bool(s) and secrets.compare_digest(self.headers.get("X-Painel", ""), s["token"])

    def _ip_cliente(self) -> str:
        """Endereço de quem tenta entrar. Atrás do Funnel, o último item do X-Forwarded-For é o que o próprio
        Funnel acrescentou; os anteriores vêm do cliente e podem ser forjados para fugir do limite de tentativas."""
        xff = self.headers.get("X-Forwarded-For")
        if xff and self.client_address[0] in ("127.0.0.1", "::1"):
            return xff.split(",")[-1].strip()
        return self.client_address[0]

    def _entrar(self):
        """POST /entrar {chave}: abre a sessão online (limite: 5 chaves erradas a cada 15 minutos por endereço)."""
        ip, agora = self._ip_cliente(), time.time()
        erros = [t for t in TENTATIVAS.get(ip, []) if agora - t < 900]
        if len(erros) >= 5:
            return self._json(429, {"erro": "Muitas tentativas. Aguarde 15 minutos."})
        try:
            chave = str(json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 2000)) or b"{}").get("chave", ""))
        except ValueError:
            chave = ""
        if not secrets.compare_digest(chave.strip().encode(), chave_acesso().encode()):
            TENTATIVAS[ip] = erros + [agora]
            time.sleep(1)
            return self._json(403, {"erro": "Chave de acesso incorreta."})
        TENTATIVAS.pop(ip, None)
        for k in [k for k, s in SESSOES.items() if s["expira"] < agora]:
            SESSOES.pop(k)
        sid = secrets.token_urlsafe(32)
        SESSOES[sid] = {"token": secrets.token_urlsafe(24), "expira": agora + SESSAO_H * 3600}
        corpo = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Set-Cookie", f"painel={sid}; Path=/; Max-Age={SESSAO_H * 3600}; HttpOnly; Secure; SameSite=Strict")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        caminho = unquote(urlparse(self.path).path)
        if caminho.startswith(("/i/", "/p/")) and (self._host_publico() or self._host_local()):
            return distribuir(self, caminho, "https" if self._host_publico() else "http")
        if not self._host_local() and not self._host_publico():
            return self._json(403, {"erro": "Endereço não autorizado."})
        if caminho == "/logo.png":
            return self._arquivo(RAIZ / "public" / "marca" / "legis-claro.png", "image/png")
        if caminho == "/logo-escuro.png":
            return self._arquivo(RAIZ / "public" / "marca" / "legis.png", "image/png")
        sessao = self._sessao()
        if caminho == "/" and not sessao:
            pagina = LOGIN.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(pagina)))
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self'")
            self.end_headers()
            return self.wfile.write(pagina)
        if not sessao:
            return self._json(401, {"erro": "Entre com a chave de acesso."})
        if caminho == "/":
            pagina = PAGINA.replace("@TOKEN@", sessao["token"]).replace("@ONLINE@", "0" if self._host_local() else "1").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(pagina)))
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' https://servicodados.ibge.gov.br")
            self.end_headers()
            return self.wfile.write(pagina)
        if caminho == "/logo.png":
            return self._arquivo(RAIZ / "public" / "marca" / "legis-claro.png", "image/png")
        if caminho == "/logo-escuro.png":
            return self._arquivo(RAIZ / "public" / "marca" / "legis.png", "image/png")
        if caminho.startswith("/arquivos/"):
            alvo = (DIST / caminho.removeprefix("/arquivos/")).resolve()
            if DIST.resolve() not in alvo.parents:
                return self._json(404, {"erro": "Arquivo não encontrado."})
            return self._arquivo(alvo, baixar=True)
        m = re.fullmatch(r"/brasao/([a-z0-9-]{1,40})", caminho)
        if m:
            c = ler_camara(m.group(1))
            return self._arquivo(CAMARAS / m.group(1) / c["brasao"]) if c and c.get("brasao") else self._json(404, {})
        if not self._autorizado():
            return self._json(403, {"erro": "Recarregue o painel."})
        if caminho == "/api/estado":
            return self._json(200, {"versao": versao_atual(), "geral": geral(), "release": ultima_release(),
                                    "camaras": camaras(), "tarefas": [TAREFAS.publico(t, 0) for t in TAREFAS.lista],
                                    "rede": {"porta": PORTA_REDE, "enderecos": enderecos()}})
        m = re.fullmatch(r"/api/tarefas/([0-9a-f]{8})", caminho)
        if m:
            t = next((t for t in TAREFAS.lista if t["id"] == m.group(1)), None)
            return self._json(200, TAREFAS.publico(t)) if t else self._json(404, {"erro": "Tarefa não encontrada."})
        return self._json(404, {"erro": "Não encontrado."})

    def do_POST(self):
        caminho = urlparse(self.path).path
        if not self._host_local() and not self._host_publico():
            return self._json(403, {"erro": "Endereço não autorizado."})
        if caminho == "/entrar" and self._host_publico():
            return self._entrar()
        if not self._autorizado():
            return self._json(403, {"erro": "Sessão expirada: recarregue o painel."})
        if caminho == "/sair":
            for parte in (self.headers.get("Cookie") or "").split(";"):
                nome, _, valor = parte.strip().partition("=")
                if nome == "painel":
                    SESSOES.pop(valor, None)
            return self._json(200, {"ok": True})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 3_000_000:
                return self._json(413, {"erro": "Envio grande demais."})
            corpo = json.loads(self.rfile.read(n) or b"{}")
            if caminho == "/api/camaras":
                c = salvar_camara(corpo)
                return self._json(200, {"camara": c, "tarefa": gerar_camara(c) if corpo.get("gerar", True) else None})
            m = re.fullmatch(r"/api/camaras/([a-z0-9-]{1,40})/(gerar|remover)", caminho)
            if m:
                if m.group(2) == "remover":
                    remover_camara(m.group(1))
                    return self._json(200, {"ok": True})
                c = ler_camara(m.group(1))
                if not c:
                    return self._json(404, {"erro": "Câmara não encontrada."})
                return self._json(200, {"tarefa": gerar_camara(c)})
            if caminho == "/api/camaras/gerar-todas":
                return self._json(200, {"tarefas": [gerar_camara(c) for c in camaras() if not c["atualizado"] or corpo.get("todas")]})
            if caminho == "/api/geral/gerar":
                return self._json(200, {"tarefa": TAREFAS.nova("Pacote geral", ["bash", "packaging/construir-pacote.sh"])})
            if caminho == "/api/geral/publicar":
                titulo = " ".join(str(corpo.get("titulo") or "").split())[:120]
                if len(titulo) < 4:
                    return self._json(400, {"erro": "Escreva o título da novidade."})
                if versao_atual()["pendente"]:
                    return self._json(409, {"erro": "Há mudanças sem commit no código: faça o commit antes de publicar."})
                ULTIMA_RELEASE["quando"] = 0
                return self._json(200, {"tarefa": TAREFAS.nova(f"Publicar: {titulo}", ["bash", "packaging/publicar-versao.sh", titulo])})
            return self._json(404, {"erro": "Não encontrado."})
        except ValueError as e:
            return self._json(400, {"erro": str(e)})


class Distribuicao(BaseHTTPRequestHandler):
    """Na rede: entrega o instalador e o pacote de uma Câmara a quem tem o código dela. Nada mais."""
    server_version = "LegisPacotes"

    def log_message(self, formato, *args):
        print(f"rede: {self.address_string()} {formato % args}", flush=True)

    def do_GET(self):
        return distribuir(self, urlparse(self.path).path, "http")


def distribuir(h, caminho: str, esquema: str):
    """GET /i/<nome>/<código> (script de instalação) e /p/<nome>/<código>/<arquivo> (pacote, .sha256, instalador)."""
    def nada():
        h.send_response(404)
        h.send_header("Content-Length", "0")
        h.end_headers()

    def camara(nome, codigo):
        c = ler_camara(nome)
        return c if c and secrets.compare_digest(str(c.get("codigo", "")), codigo) else None

    m = re.fullmatch(r"/i/([a-z0-9-]{1,40})/([A-Za-z0-9_-]{8,40})", caminho)
    if m:
        c = camara(*m.groups())
        deb = next((a["nome"] for a in arquivos(DIST_CIDADES / m.group(1)) if a["nome"].endswith("_all.deb")), None) if c else None
        if not c or not deb:
            return nada()
        base = f"{esquema}://{h.headers.get('Host')}/p/{c['nome']}/{c['codigo']}"
        if not re.fullmatch(r"https?://[A-Za-z0-9.:\[\]-]+/p/[a-z0-9-]+/[A-Za-z0-9_-]+", base):
            return nada()
        if not re.fullmatch(r"legis-[a-z0-9-]+_[A-Za-z0-9.+~-]+_all\.deb", deb):
            return nada()
        def sha256(arq: Path) -> str:
            h = hashlib.sha256()
            with open(arq, "rb") as f:
                while bloco := f.read(1 << 20):
                    h.update(bloco)
            return h.hexdigest()
        # nome da Câmara só com caracteres seguros (Câmaras antigas podem ter outros); o script roda como root
        rotulo = "".join(ch for ch in (c.get("camara") or c["municipio"]) if ch.isalnum() or ch in " .-'")
        corpo = SCRIPT_REDE.replace("@BASE@", base).replace("@DEB@", deb).replace("@CAMARA@", rotulo).replace(
            "@SHA_DEB@", sha256(DIST_CIDADES / c["nome"] / deb)).replace(
            "@SHA_INSTALADOR@", sha256(RAIZ / "packaging" / "instalar-legis.sh")).encode()
        h.send_response(200)
        h.send_header("Content-Type", "text/x-shellscript; charset=utf-8")
        h.send_header("Content-Length", str(len(corpo)))
        h.end_headers()
        return h.wfile.write(corpo)
    m = re.fullmatch(r"/p/([a-z0-9-]{1,40})/([A-Za-z0-9_-]{8,40})/([A-Za-z0-9._+-]{1,120})", unquote(caminho))
    if m and camara(m.group(1), m.group(2)):
        nome = m.group(3)
        alvo = RAIZ / "packaging" / "instalar-legis.sh" if nome == "instalar-legis.sh" else DIST_CIDADES / m.group(1) / nome
        if alvo.is_file() and (nome == "instalar-legis.sh" or re.fullmatch(r"legis-[a-z0-9-]+_.+_all\.deb(\.sha256)?", nome)):
            h.send_response(200)
            h.send_header("Content-Type", "application/octet-stream")
            h.send_header("Content-Length", str(alvo.stat().st_size))
            h.end_headers()
            with open(alvo, "rb") as f:
                shutil.copyfileobj(f, h.wfile)
            return
    return nada()


SCRIPT_REDE = r"""#!/bin/bash
# Instala o LEGIS de @CAMARA@ a partir do painel dos pacotes, sem perguntas.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Rode como administrador:  curl -fsSL <endereço> | sudo bash"; exit 1; }
baixar() { curl -fsSL -o "$2" "$1" 2>/dev/null || wget -qO "$2" "$1"; }
T=$(mktemp -d)
echo "== LEGIS de @CAMARA@: baixando o pacote do painel..."
baixar "@BASE@/@DEB@" "$T/@DEB@"
echo "@SHA_DEB@  $T/@DEB@" | sha256sum -c - >/dev/null || { echo "O pacote baixado não confere (SHA-256)."; exit 1; }
echo "   pacote conferido (SHA-256)"
baixar "@BASE@/instalar-legis.sh" "$T/instalar-legis.sh"
echo "@SHA_INSTALADOR@  $T/instalar-legis.sh" | sha256sum -c - >/dev/null || { echo "O instalador baixado não confere (SHA-256)."; exit 1; }
DEB="$T/@DEB@" bash "$T/instalar-legis.sh"
"""


LOGIN = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LEGIS — Pacotes de instalação</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:linear-gradient(135deg,#14532d,#1f8a4c);font:16px system-ui,sans-serif;color:#16211b}
form{background:#fff;border-radius:16px;padding:28px 26px;width:min(380px,90vw);display:grid;gap:12px;box-shadow:0 20px 60px rgba(0,0,0,.3)}
img{height:46px;justify-self:start}h1{font-size:18px;margin:4px 0 0}p{margin:0;color:#5b6b62;font-size:14px}
input{font:inherit;padding:10px 12px;border:1px solid #dce5df;border-radius:10px}button{font:inherit;font-weight:600;padding:10px;border:0;border-radius:10px;background:#1f8a4c;color:#fff;cursor:pointer}
#erro{color:#b42318;font-size:14px;min-height:1.2em}</style></head><body>
<form onsubmit="event.preventDefault(); entrar()"><img src="/logo-escuro.png" alt="LEGIS"><h1>Pacotes de instalação</h1>
<p>Digite a chave de acesso do painel.</p>
<input id="chave" type="password" autocomplete="current-password" placeholder="Chave de acesso" required autofocus>
<button>Entrar</button><div id="erro" role="alert"></div></form>
<script>
async function entrar() {
  const r = await fetch('/entrar', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chave: document.getElementById('chave').value }) });
  if (r.ok) return location.reload();
  const j = await r.json().catch(() => ({}));
  document.getElementById('erro').textContent = j.erro || 'Não foi possível entrar.';
}
</script></body></html>
"""


PAGINA = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LEGIS — Pacotes de instalação</title>
<style>
:root{--verde:#1f8a4c;--verde-esc:#14532d;--fundo:#f3f6f4;--cartao:#fff;--texto:#16211b;--suave:#5b6b62;--borda:#dce5df;--perigo:#b42318;--ambar:#a15c07}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--fundo);color:var(--texto)}
header{background:linear-gradient(90deg,var(--verde-esc),var(--verde));color:#fff;padding:14px 28px;display:flex;align-items:center;gap:18px}
header img{height:44px}header h1{font-size:18px;margin:0;font-weight:650}header .esp{flex:1}header small{opacity:.85}
main{max-width:1180px;margin:24px auto;padding:0 20px;display:grid;gap:20px;grid-template-columns:1fr 380px;align-items:start}
@media(max-width:980px){main{grid-template-columns:1fr}}
.cartao{background:var(--cartao);border:1px solid var(--borda);border-radius:14px;padding:18px 20px}
h2{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--suave);margin:0 0 12px;display:flex;align-items:center;gap:10px}
h2 .esp{flex:1}
.botao{border:1px solid var(--borda);background:#fff;color:var(--texto);border-radius:9px;padding:7px 13px;font:inherit;font-size:14px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:6px}
.botao:hover{border-color:var(--verde)}.botao.principal{background:var(--verde);border-color:var(--verde);color:#fff}.botao.principal:hover{background:var(--verde-esc)}
.botao.perigo{color:var(--perigo)}.botao.pq{padding:4px 9px;font-size:13px}.botao:disabled{opacity:.5;cursor:default}
table{width:100%;border-collapse:collapse}th{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--suave);text-align:left;padding:6px 8px;border-bottom:1px solid var(--borda)}
td{padding:10px 8px;border-bottom:1px solid var(--borda);vertical-align:top}tr:last-child td{border-bottom:0}
.brasao{width:40px;height:40px;object-fit:contain;border-radius:8px;background:#eef3f0}
.chip{display:inline-block;font-size:12px;font-weight:600;padding:2px 9px;border-radius:99px;background:#eef3f0;color:var(--suave)}
.chip.ok{background:#dcf3e5;color:#166534}.chip.velho{background:#fdf0d9;color:var(--ambar)}.chip.erro{background:#fde4e1;color:var(--perigo)}.chip.rodando{background:#e0ecff;color:#1d4ed8}
.suave{color:var(--suave);font-size:13px}.acoes{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.arqs a{display:block;font-size:13px;color:var(--verde-esc)}code{font-size:12.5px;background:#eef3f0;padding:2px 6px;border-radius:6px;word-break:break-all}
.log{background:#0f1a14;color:#d3e8da;font:12px/1.45 ui-monospace,Menlo,monospace;border-radius:10px;padding:10px 12px;max-height:340px;overflow:auto;white-space:pre-wrap;margin-top:10px}
.tarefa{padding:8px 0;border-bottom:1px solid var(--borda);cursor:pointer}.tarefa:last-child{border:0}.tarefa b{font-weight:600}
dialog{border:0;border-radius:14px;padding:0;width:min(560px,94vw);box-shadow:0 20px 60px rgba(0,0,0,.25)}dialog::backdrop{background:rgba(10,20,14,.45)}
dialog form{padding:20px 22px;display:grid;gap:12px}dialog h3{margin:0 0 4px}
label{display:grid;gap:4px;font-size:13px;color:var(--suave)}input,select{font:inherit;padding:8px 10px;border:1px solid var(--borda);border-radius:9px;color:var(--texto);background:#fff}
input:focus,select:focus{outline:2px solid #b7e0c6;border-color:var(--verde)}.linha{display:grid;grid-template-columns:110px 1fr;gap:10px}
.rodape{display:flex;justify-content:flex-end;gap:8px;margin-top:4px}.vazio{color:var(--suave);padding:18px 0;text-align:center}
#aviso{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#14211a;color:#fff;padding:10px 16px;border-radius:10px;display:none}
</style></head><body>
<header><img src="/logo.png" alt="LEGIS"><div><h1>Pacotes de instalação</h1><small id="versao">…</small></div><span class="esp"></span>
  <button class="botao" onclick="abrirNova()" style="background:#fff">＋ Nova Câmara</button>
  <button class="botao" id="btSair" onclick="sairPainel()" style="background:transparent;color:#fff;border-color:rgba(255,255,255,.5);display:none">Sair</button></header>
<main>
  <section class="cartao">
    <h2>Câmaras<span class="esp"></span><button class="botao pq" id="btTodas" onclick="gerarTodas()">Atualizar desatualizados</button></h2>
    <table><thead><tr><th></th><th>Câmara</th><th>Pacote</th><th>Arquivos e instalação</th></tr></thead><tbody id="camaras"></tbody></table>
  </section>
  <aside style="display:grid;gap:20px">
    <section class="cartao">
      <h2>Versão geral (para todas as Câmaras)</h2>
      <p class="suave" style="margin:0 0 8px">O pacote sem cidade, que pergunta a cidade no painel do LEGIS. Os pacotes de cada Câmara ficam à esquerda e não vão para o GitHub.</p>
      <div id="geral" class="suave">…</div>
      <div class="acoes"><button class="botao" onclick="gerarGeral()">Gerar pacote geral</button><button class="botao principal" id="btPublicar" onclick="publicar()">Publicar versão geral no GitHub…</button></div>
    </section>
    <section class="cartao"><h2>Tarefas</h2><div id="tarefas" class="suave">Nenhuma ainda.</div><div id="log" class="log" hidden></div></section>
  </aside>
</main>
<dialog id="dlg"><form method="dialog" onsubmit="return false">
  <h3 id="dlgTitulo">Nova Câmara</h3>
  <div class="linha"><label>Estado<select id="fUf" required></select></label>
    <label>Município<input id="fMun" list="lista" autocomplete="off" placeholder="Comece a digitar" required><datalist id="lista"></datalist></label></div>
  <label>Nome da Câmara<input id="fCamara" placeholder="Câmara Municipal de …"></label>
  <label>Nome curto (endereço https://NOME.local)<input id="fNome" placeholder="gerado a partir do município"></label>
  <label>Link público do YouTube ao vivo (opcional)<input id="fYt" placeholder="https://www.youtube.com/@suacamara/live"></label>
  <label>Brasão (PNG, JPEG ou WebP, até 1,5 MB)<input id="fBrasao" type="file" accept="image/png,image/jpeg,image/webp"></label>
  <p class="suave" style="margin:0">Os vereadores eleitos (com fotos) e a população vêm do TSE e do IBGE ao gerar o pacote. A chave do YouTube não entra no pacote: passe-a na instalação (<code>YOUTUBE_CHAVE=…</code>) ou cole no painel do LEGIS.</p>
  <div class="rodape"><button class="botao" value="cancelar" onclick="dlg.close()">Cancelar</button><button class="botao principal" id="fOk" onclick="salvar()">Gerar pacote</button></div>
</form></dialog>
<div id="aviso" role="status"></div>
<script>
const TOKEN = '@TOKEN@', ONLINE = '@ONLINE@' === '1', $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tam = b => b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB';
const quando = ms => ms ? new Date(ms).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) : '';
let E = null, editando = null, tarefaAberta = null;
function avisar(t) { const a = $('aviso'); a.textContent = t; a.style.display = 'block'; clearTimeout(a._t); a._t = setTimeout(() => a.style.display = 'none', 3500); }
async function api(caminho, corpo) {
  const r = await fetch(caminho, corpo === undefined ? { headers: { 'X-Painel': TOKEN } }
    : { method: 'POST', headers: { 'X-Painel': TOKEN, 'Content-Type': 'application/json' }, body: JSON.stringify(corpo) });
  const j = await r.json().catch(() => ({}));
  if (r.status === 401 || (r.status === 403 && ONLINE)) { location.reload(); throw new Error('Sessão expirada.'); }
  if (!r.ok) throw new Error(j.erro || 'Falhou.');
  return j;
}
async function atualizar() {
  try { E = await api('/api/estado'); } catch (e) { return; }
  const v = E.versao;
  $('versao').textContent = `Código atual: ${v.commit} — ${v.titulo}${v.pendente ? ' (há mudanças sem commit)' : ''}`;
  $('camaras').innerHTML = E.camaras.length ? E.camaras.map(c => {
    const deb = c.arquivos.find(a => a.nome.endsWith('_all.deb')), tar = c.arquivos.find(a => a.nome.endsWith('.tar.gz'));
    const est = !c.pacote ? '<span class="chip">sem pacote</span>' : c.atualizado ? '<span class="chip ok">atualizado</span>' : `<span class="chip velho">versão ${esc(c.pacote.commit)}</span>`;
    return `<tr><td>${c.temBrasao ? `<img class="brasao" src="/brasao/${esc(c.nome)}?v=${c.criado}" alt="">` : '<div class="brasao"></div>'}</td>
      <td><b>${esc(c.camara || 'Câmara Municipal de ' + c.municipio)}</b><div class="suave">${esc(c.municipio)}/${esc(c.uf)} · ${esc(c.nome)}.local</div>
        <div class="acoes"><button class="botao pq" onclick="gerar('${esc(c.nome)}')">Gerar de novo</button><button class="botao pq" onclick="editar('${esc(c.nome)}')">Editar</button><button class="botao pq perigo" onclick="remover('${esc(c.nome)}')">Remover</button></div></td>
      <td>${est}<div class="suave">${c.pacote ? quando(c.pacote.data) : ''}</div></td>
      <td class="arqs">${deb ? `<a href="${esc(deb.url)}">⬇ ${esc(deb.nome)} <span class="suave">(${tam(deb.bytes)})</span></a>` : ''}${tar ? `<a href="${esc(tar.url)}">⬇ ${esc(tar.nome)}</a>` : ''}
        ${deb ? comandoRede(c) : '<span class="suave">—</span>'}</td></tr>`;
  }).join('') : '<tr><td colspan="4" class="vazio">Nenhuma Câmara ainda. Clique em <b>＋ Nova Câmara</b>.</td></tr>';
  $('btTodas').disabled = !E.camaras.some(c => !c.atualizado);
  const g = E.geral, r = E.release;
  $('geral').innerHTML = (g.arquivos.length ? g.arquivos.map(a => `<a href="${esc(a.url)}" style="display:block;color:var(--verde-esc)">⬇ ${esc(a.nome)} <span class="suave">(${tam(a.bytes)})</span></a>`).join('') : 'Nenhum pacote geral gerado.')
    + `<div style="margin-top:8px">${g.commit ? (g.commit === v.commit ? '<span class="chip ok">atualizado</span>' : `<span class="chip velho">versão ${esc(g.commit)}</span>`) : ''}</div>`
    + (r ? `<div style="margin-top:8px">Publicada: <a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.tagName)}</a><br>${esc(r.name)}</div>` : '')
    + (g.gh ? '' : '<div>Para publicar, instale e autentique o <code>gh</code>.</div>');
  $('btPublicar').disabled = !g.gh;
  $('tarefas').innerHTML = E.tarefas.length ? E.tarefas.map(t => `<div class="tarefa" onclick="verTarefa('${t.id}')"><span class="chip ${t.estado === 'concluída' ? 'ok' : t.estado === 'erro' ? 'erro' : t.estado === 'rodando' ? 'rodando' : ''}">${esc(t.estado)}</span> <b>${esc(t.titulo)}</b><div class="suave">${quando(t.criada)}</div></div>`).join('') : 'Nenhuma ainda.';
  if (tarefaAberta) verTarefa(tarefaAberta, true);
}
async function verTarefa(id, silencioso) {
  tarefaAberta = id;
  const t = await api('/api/tarefas/' + id).catch(() => null); if (!t) return;
  const l = $('log'), fim = l.scrollTop + l.clientHeight >= l.scrollHeight - 20;
  l.hidden = false; l.textContent = `${t.titulo} — ${t.estado}\n\n` + t.log.join('\n');
  if (fim || !silencioso) l.scrollTop = l.scrollHeight;
}
function comandoRede(c) {
  const ends = (E.rede || {}).enderecos || [];
  if (!ends.length) return '<div class="suave">Sem rede: copie o .deb e rode <code>sudo apt install ./arquivo.deb</code> no servidor.</div>';
  return '<div class="suave" style="margin-top:6px"><b>Instalar no servidor da Câmara</b> (rode lá, como administrador):</div>' + ends.map(e => {
    const cmd = `curl -fsSL ${e.url}/i/${c.nome}/${c.codigo} | sudo bash`;
    return `<div style="margin-top:4px"><code>${esc(cmd)}</code> <button class="botao pq" onclick="copiar('${esc(cmd)}')">copiar</button> <span class="suave">${esc(e.rede)}</span></div>`;
  }).join('') + '<div class="suave" style="margin-top:4px">O código no endereço é só desta Câmara. Ou leve o .deb e rode <code>sudo apt install ./arquivo.deb</code>.</div>';
}
function copiar(t) { navigator.clipboard.writeText(t).then(() => avisar('Copiado.')); }
const UFS = ['AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG','PA','PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO'];
$('fUf').innerHTML = '<option value="">—</option>' + UFS.map(u => `<option>${u}</option>`).join('');
$('fUf').onchange = async () => {
  $('lista').innerHTML = ''; if (!$('fUf').value) return;
  try {
    const l = await (await fetch(`https://servicodados.ibge.gov.br/api/v1/localidades/estados/${$('fUf').value}/municipios?orderBy=nome`)).json();
    $('lista').innerHTML = l.map(m => `<option value="${esc(m.nome)}">`).join('');
  } catch { }
};
function abrirNova() { editando = null; $('dlgTitulo').textContent = 'Nova Câmara'; for (const k of ['fUf','fMun','fCamara','fNome','fYt','fBrasao']) $(k).value = ''; $('fNome').disabled = $('fUf').disabled = $('fMun').disabled = false; $('dlg').showModal(); $('fUf').focus(); }
function editar(nome) {
  const c = E.camaras.find(x => x.nome === nome); editando = nome;
  $('dlgTitulo').textContent = 'Editar ' + (c.camara || c.municipio);
  $('fUf').value = c.uf; $('fMun').value = c.municipio; $('fCamara').value = c.camara || ''; $('fNome').value = c.nome; $('fYt').value = c.youtube || ''; $('fBrasao').value = '';
  $('fNome').disabled = true; $('dlg').showModal();
}
const lerArquivo = f => new Promise((ok, erro) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = erro; r.readAsDataURL(f); });
async function salvar() {
  const f = $('fBrasao').files[0];
  const corpo = { uf: $('fUf').value, municipio: $('fMun').value, camara: $('fCamara').value, nome: editando || $('fNome').value, youtube: $('fYt').value, editar: !!editando };
  if (f) corpo.brasao = await lerArquivo(f);
  $('fOk').disabled = true;
  try { const r = await api('/api/camaras', corpo); $('dlg').close(); avisar('Gerando o pacote…'); verTarefa(r.tarefa.id); atualizar(); }
  catch (e) { avisar(e.message); } finally { $('fOk').disabled = false; }
}
async function gerar(nome) { const r = await api(`/api/camaras/${nome}/gerar`, {}).catch(e => avisar(e.message)); if (r) { verTarefa(r.tarefa.id); atualizar(); } }
async function remover(nome) {
  if (!confirm(`Remover a Câmara "${nome}" deste painel? A configuração e os pacotes gerados dela serão apagados deste computador.`)) return;
  await api(`/api/camaras/${nome}/remover`, {}).then(() => { avisar('Removida.'); atualizar(); }).catch(e => avisar(e.message));
}
async function gerarTodas() { const r = await api('/api/camaras/gerar-todas', {}).catch(e => avisar(e.message)); if (r) { avisar(`${r.tarefas.length} pacote(s) na fila.`); if (r.tarefas[0]) verTarefa(r.tarefas[0].id); atualizar(); } }
async function gerarGeral() { const r = await api('/api/geral/gerar', {}).catch(e => avisar(e.message)); if (r) { verTarefa(r.tarefa.id); atualizar(); } }
async function publicar() {
  const titulo = prompt('Publicar a VERSÃO GERAL (sem cidade) no GitHub, como a mais recente e pública.\nOs pacotes das Câmaras NÃO são publicados.\n\nTítulo da novidade (ex.: "Correções na rádio"):'); if (!titulo) return;
  if (/^legis[-_]/i.test(titulo.trim()) || E.camaras.some(c => titulo.toLowerCase().includes(c.municipio.toLowerCase()))) {
    if (!confirm('O título parece o nome de uma Câmara. Este botão publica só a versão geral; para instalar uma Câmara, use o comando "Instalar no servidor da Câmara" dela. Publicar mesmo assim?')) return;
  }
  if (!confirm(`Publicar no GitHub, como versão mais recente e pública:\n\n"${titulo}"\n\nRoda os testes antes; se falharem, nada é publicado.`)) return;
  const r = await api('/api/geral/publicar', { titulo }).catch(e => avisar(e.message)); if (r) { verTarefa(r.tarefa.id); atualizar(); }
}
if (ONLINE) $('btSair').style.display = '';
async function sairPainel() { await api('/sair', {}).catch(() => {}); location.reload(); }
atualizar(); setInterval(atualizar, 3000);
</script></body></html>
"""


def main():
    CAMARAS.mkdir(parents=True, exist_ok=True)
    if PUBLICO:
        chave_acesso()
        print(f"Painel online: https://{PUBLICO}  (chave de acesso em {ARQ_CHAVE})", flush=True)
    servidor = ThreadingHTTPServer(("127.0.0.1", PORTA), Painel)
    try:
        rede = ThreadingHTTPServer(("0.0.0.0", PORTA_REDE), Distribuicao)
        threading.Thread(target=rede.serve_forever, daemon=True).start()
        print(f"Instalação pela rede: porta {PORTA_REDE} (só com o código de cada Câmara)", flush=True)
    except OSError as e:
        print(f"Instalação pela rede indisponível (porta {PORTA_REDE}: {e})", flush=True)
    url = f"http://127.0.0.1:{PORTA}/"
    print(f"Painel dos pacotes do LEGIS: {url}  (Ctrl+C para sair)", flush=True)
    if "--sem-navegador" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
