#!/usr/bin/env python3
"""Instala o AzuraCast da Câmara e prepara a estação pelo assistente e pela API oficiais."""
from contextlib import closing
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

IMAGEM = "ghcr.io/azuracast/azuracast:0.24.1"
ESTACAO = "radio_da_camara"
DJ = "legis"
VOLUMES = {
    "station_data": "/var/azuracast/stations",
    "backups": "/var/azuracast/backups",
    "db_data": "/var/lib/mysql",
    "www_uploads": "/var/azuracast/storage/uploads",
    "sftpgo_data": "/var/azuracast/storage/sftpgo",
    "acme": "/var/azuracast/storage/acme",
}


def gravar(arquivo, dados):
    temporario = arquivo.with_suffix(".novo")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporario.replace(arquivo)


def abrir_porta():
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def configuracao(pasta):
    arquivo = pasta / "provisionamento.json"
    if arquivo.exists():
        return json.loads(arquivo.read_text(encoding="utf-8"))
    porta_web = abrir_porta()
    porta_dj = abrir_porta()
    while porta_dj == porta_web:
        porta_dj = abrir_porta()
    dados = {
        "web": porta_web,
        "dj": porta_dj,
        "email": os.environ.get("AZURACAST_ADMIN_EMAIL") or "radio-admin@legis.local",
        "senha_admin": secrets.token_urlsafe(24),
        "senha_dj": secrets.token_urlsafe(24),
        "senha_sql": secrets.token_urlsafe(24),
    }
    gravar(arquivo, dados)
    return dados


def especificacao_compose(dados):
    return {
        "name": "legis-radio",
        "services": {"web": {
            "image": IMAGEM,
            "restart": "unless-stopped",
            "ports": [f"127.0.0.1:{dados['web']}:80", f"127.0.0.1:{dados['dj']}:8005"],
            "environment": {"MYSQL_PASSWORD": dados["senha_sql"], "MYSQL_RANDOM_ROOT_PASSWORD": "yes"},
            "volumes": [f"{volume}:{destino}" for volume, destino in VOLUMES.items()],
            "ulimits": {"nofile": {"soft": 65536, "hard": 65536}},
        }},
        "volumes": {volume: {} for volume in VOLUMES},
    }


def props_vue(pagina):
    chamada = re.search(r"window\.vueComponent\('#[^']+',\s*", pagina)
    if not chamada:
        raise RuntimeError("O assistente do AzuraCast retornou um formato inesperado.")
    return json.JSONDecoder().raw_decode(pagina[chamada.end():])[0]


class AzuraCast:
    def __init__(self, porta):
        self.base = f"http://127.0.0.1:{porta}"
        self.navegador = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = None

    def requisitar(self, rota, dados=None, metodo=None, formulario=False):
        headers = {}
        corpo = None
        if dados is not None:
            if formulario:
                corpo = urlencode(dados).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                corpo = json.dumps(dados).encode()
                headers["Content-Type"] = "application/json"
        if self.csrf:
            headers["X-API-CSRF"] = self.csrf
        req = Request(self.base + rota, data=corpo, headers=headers, method=metodo)
        try:
            with self.navegador.open(req, timeout=20) as resposta:
                conteudo = resposta.read().decode("utf-8")
                if resposta.headers.get_content_type() == "application/json" and conteudo:
                    conteudo = json.loads(conteudo)
                return urlparse(resposta.geturl()).path, conteudo
        except HTTPError as erro:
            raise RuntimeError(f"AzuraCast retornou HTTP {erro.code} em {rota}.") from None

    def esperar(self, segundos=420):
        limite = time.monotonic() + segundos
        while time.monotonic() < limite:
            try:
                return self.requisitar("/")
            except (RuntimeError, URLError, TimeoutError, OSError):
                time.sleep(3)
        raise RuntimeError("O AzuraCast não iniciou dentro do prazo. Confira os logs do contêiner.")

    def configurar(self, credenciais):
        rota, pagina = self.esperar()
        login = {"username": credenciais["email"], "password": credenciais["senha_admin"]}
        if rota == "/setup/register":
            login["csrf"] = props_vue(pagina)["componentProps"]["csrf"]
            rota, pagina = self.requisitar(rota, login, formulario=True)
        elif rota == "/login":
            self.requisitar("/login", login, formulario=True)
            rota, pagina = self.requisitar("/")
        self.csrf = props_vue(pagina).get("apiCsrf")
        if not self.csrf:
            raise RuntimeError("O AzuraCast não autenticou o administrador gerado na instalação.")

        _, estacoes = self.requisitar("/api/admin/stations")
        if isinstance(estacoes, dict):
            estacoes = estacoes["rows"]
        estacao = next((estacao for estacao in estacoes if estacao.get("short_name") == ESTACAO), None)
        if not estacao:
            _, estacao = self.requisitar("/api/admin/stations", {
                "name": "Rádio da Câmara", "short_name": ESTACAO, "enable_streamers": True,
                "frontend_type": "icecast", "backend_type": "liquidsoap",
            })
        if rota.startswith("/setup/"):
            self.requisitar("/api/admin/settings/general", {
                "instance_name": "Rádio da Câmara", "base_url": self.base, "prefer_browser_url": True,
                "use_radio_proxy": True,
            }, metodo="PUT")
        id_estacao = int(estacao["id"])
        caminho = f"/api/station/{id_estacao}"
        _, operadores = self.requisitar(caminho + "/streamers")
        if isinstance(operadores, dict):
            operadores = operadores["rows"]
        if not any(operador["streamer_username"] == DJ for operador in operadores):
            self.requisitar(caminho + "/streamers", {
                "streamer_username": DJ, "streamer_password": credenciais["senha_dj"],
                "display_name": "Plenário Legis", "is_active": True,
            })
        self.requisitar(caminho + "/restart", {}, metodo="POST")
        return estacao["short_name"]


def main():
    pasta, configure = Path(sys.argv[1]), Path(sys.argv[2])
    os.umask(0o077)
    pasta.mkdir(mode=0o700, parents=True, exist_ok=True)
    dados = configuracao(pasta)
    arquivo = pasta / "compose.json"
    if not arquivo.exists():
        gravar(arquivo, especificacao_compose(dados))
    comando = ["docker", "compose"] if subprocess.run(
        ["docker", "compose", "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0 else ["docker-compose"]
    subprocess.run([*comando, "-f", str(arquivo), "-p", "legis-radio", "up", "-d"], check=True)
    pronto = pasta / "pronto.json"
    if pronto.exists():
        AzuraCast(dados["web"]).esperar()
    else:
        estacao = AzuraCast(dados["web"]).configurar(dados)
        gravar(pronto, {"estacao": estacao})
    campos = {"LEGIS_AZURACAST_WEB": dados["web"], "LEGIS_AZURACAST_DJ_PORTA": dados["dj"],
              "LEGIS_AZURACAST_DJ_SENHA": dados["senha_dj"]}
    linhas = configure.read_text(encoding="utf-8").splitlines()
    if not all(f"{campo}={valor}" in linhas for campo, valor in campos.items()):
        with configure.open("a", encoding="utf-8") as arquivo_env:
            arquivo_env.writelines(f"{campo}={valor}\n" for campo, valor in campos.items())
    print("AzuraCast configurado. Credenciais iniciais em " + str(pasta / "provisionamento.json"))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.CalledProcessError) as erro:
        sys.exit(f"AzuraCast pendente: {erro}")
