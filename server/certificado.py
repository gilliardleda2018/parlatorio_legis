"""Primeiro acesso de cada aparelho: instala a autoridade certificadora da Câmara e abre o LEGIS sem aviso.

O LEGIS da Câmara usa uma autoridade própria (tls.py), porque endereços da rede local (IP, NOME.local) não recebem
certificado público. A página http://IP/certificado reconhece o aparelho e oferece o jeito mais curto de confiar nela:

- Windows: um arquivo ``.cmd`` que instala a autoridade só para o usuário (sem administrador; o Windows pede um "Sim")
  e abre o LEGIS. Chrome e Edge usam esse repositório; o Firefox também, nas versões atuais.
- iPhone, iPad e Mac: um perfil ``.mobileconfig`` (no iPhone, depois ligar a confiança em Ajustes).
- Android, Linux e outros: o arquivo ``.crt`` com o caminho certo de cada um.

A página testa se o navegador já confia no endereço https:// e, quando confia, mostra o botão para abrir o LEGIS.
"""
import base64
import json
import html
import ipaddress
import re
import uuid

_HOST = re.compile(r"[A-Za-z0-9.-]{1,253}")


def host_seguro(host_header: str) -> str:
    """O nome ou IP do cabeçalho Host, sem porta; vazio se for estranho (nunca vai para a página sem conferir)."""
    host = (host_header or "").strip()
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
        try:
            return "[" + str(ipaddress.IPv6Address(host)) + "]"
        except ValueError:
            return ""
    host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return host if _HOST.fullmatch(host) and ".." not in host else ""


def endereco_https(host: str, porta: int) -> str:
    return f"https://{host}/" if porta == 443 else f"https://{host}:{porta}/"


def pem_para_der(pem: bytes) -> bytes:
    corpo = b"".join(l.strip() for l in pem.splitlines() if l and not l.startswith(b"-----"))
    return base64.b64decode(corpo)


def windows_cmd(pem: bytes, url: str) -> bytes:
    """Script .cmd com a autoridade embutida: instala no repositório do usuário (certutil -user) e abre o LEGIS."""
    linhas = ["@echo off", "chcp 65001 >nul", "title LEGIS - instalar o certificado da Camara",
              'set "CRT=%TEMP%\\legis-camara-ca.crt"']
    for i, l in enumerate(l.decode("ascii").strip() for l in pem.splitlines() if l.strip()):
        linhas.append(f'{">" if i == 0 else ">>"} "%CRT%" echo {l}')
    linhas += ["echo.", "echo Instalando o certificado da Camara (o Windows vai perguntar: responda Sim)...",
               'certutil -user -addstore Root "%CRT%" >nul',
               "if errorlevel 1 (echo Nao foi possivel instalar. Tente de novo e responda Sim. & pause & exit /b 1)",
               'del "%CRT%" >nul 2>&1',
               "echo Pronto. Abrindo o LEGIS...",
               f'start "" "{url}"', "exit /b 0"]
    return ("\r\n".join(linhas) + "\r\n").encode("ascii")


def mobileconfig(pem: bytes, nome: str) -> bytes:
    """Perfil da Apple (iPhone, iPad, Mac) com a autoridade da Câmara."""
    der = base64.b64encode(pem_para_der(pem)).decode()
    ident = "br.com.aggilli.legis." + re.sub(r"[^a-z0-9]+", "-", nome.lower()).strip("-")
    nome = html.escape(nome)
    u1, u2 = str(uuid.uuid5(uuid.NAMESPACE_URL, ident + ".ca")).upper(), str(uuid.uuid5(uuid.NAMESPACE_URL, ident)).upper()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
 <key>PayloadContent</key><array><dict>
  <key>PayloadCertificateFileName</key><string>legis-camara-ca.cer</string>
  <key>PayloadContent</key><data>{der}</data>
  <key>PayloadDescription</key><string>Autoridade certificadora do LEGIS desta Câmara</string>
  <key>PayloadDisplayName</key><string>LEGIS — {nome}</string>
  <key>PayloadIdentifier</key><string>{ident}.ca</string>
  <key>PayloadType</key><string>com.apple.security.root</string>
  <key>PayloadUUID</key><string>{u1}</string>
  <key>PayloadVersion</key><integer>1</integer>
 </dict></array>
 <key>PayloadDescription</key><string>Permite abrir o LEGIS da Câmara pelo endereço da rede sem aviso de segurança.</string>
 <key>PayloadDisplayName</key><string>LEGIS — {nome}</string>
 <key>PayloadIdentifier</key><string>{ident}</string>
 <key>PayloadRemovalDisallowed</key><false/>
 <key>PayloadType</key><string>Configuration</string>
 <key>PayloadUUID</key><string>{u2}</string>
 <key>PayloadVersion</key><integer>1</integer>
</dict></plist>
""".encode()


def pagina(url: str, nome: str) -> bytes:
    """Página do primeiro acesso. `url` é o endereço https:// do LEGIS no mesmo host que o aparelho usou."""
    u, n = html.escape(url, quote=True), html.escape(nome)
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>LEGIS — primeiro acesso</title>
<style>
body{{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#f3f6f9;color:#15324b;margin:0;padding:24px}}
main{{max-width:640px;margin:auto;background:#fff;border-radius:16px;padding:24px 28px;box-shadow:0 4px 18px rgba(0,0,0,.08)}}
h1{{font-size:1.4rem;margin:0 0 4px}} .sub{{color:#5a768d;margin:0 0 18px}}
.passo{{display:none}} .passo.ativo{{display:block}}
.botao{{display:inline-block;background:#0f6b6f;color:#fff;text-decoration:none;padding:12px 20px;border-radius:12px;font-weight:600;margin:6px 0}}
.botao.sec{{background:#fff;color:#15324b;border:1px solid #b9d3df}}
.ok{{background:#e6f6ee;border:1px solid #9ad3b5;border-radius:12px;padding:14px;display:none}}
ol li{{margin:6px 0}} small{{color:#5a768d}} details{{margin-top:18px}}
</style></head><body><main>
<h1>LEGIS — {n}</h1><p class="sub">Primeiro acesso deste aparelho: instale o certificado da Câmara uma única vez.</p>
<div class="ok" id="ok"><strong>Este aparelho já confia no LEGIS.</strong><br>
<a class="botao" href="{u}">Abrir o LEGIS</a><br><small>{u}</small></div>
<div id="passos">
<section class="passo" data-so="windows"><h2>Windows</h2><ol>
<li><a class="botao" href="/certificado/windows.cmd" download="instalar-certificado-legis.cmd">Baixar o instalador</a></li>
<li>Abra o arquivo baixado. Se aparecer "O Windows protegeu o computador", clique em <b>Mais informações → Executar assim mesmo</b>.</li>
<li>Responda <b>Sim</b> à pergunta do Windows. O LEGIS abre sozinho.</li></ol></section>
<section class="passo" data-so="apple"><h2>iPhone, iPad ou Mac</h2><ol>
<li><a class="botao" href="/certificado/legis.mobileconfig">Baixar o perfil</a> (no iPhone, abra pelo Safari)</li>
<li>iPhone/iPad: <b>Ajustes → Perfil Transferido → Instalar</b>. Depois <b>Ajustes → Geral → Sobre → Ajustes de Confiança de Certificados</b> e ligue "LEGIS".</li>
<li>Mac: abra o perfil e instale em <b>Ajustes do Sistema → Privacidade e Segurança → Perfis</b>.</li>
<li>Volte a esta página e toque em <b>Abrir o LEGIS</b>.</li></ol></section>
<section class="passo" data-so="android"><h2>Android</h2><ol>
<li><a class="botao" href="/certificado.crt" download="legis-camara.crt">Baixar o certificado</a></li>
<li>Abra <b>Configurações → Segurança → Mais configurações de segurança → Criptografia e credenciais → Instalar certificado → Certificado de CA</b> (o caminho muda um pouco por marca; procure por "certificado" nas Configurações).</li>
<li>Escolha o arquivo <b>legis-camara.crt</b> e confirme.</li>
<li>Volte a esta página e toque em <b>Abrir o LEGIS</b>.</li></ol></section>
<section class="passo" data-so="linux"><h2>Linux</h2><ol>
<li><a class="botao" href="/certificado.crt" download="legis-camara.crt">Baixar o certificado</a></li>
<li>No terminal: <code>sudo cp ~/Downloads/legis-camara.crt /usr/local/share/ca-certificates/ &amp;&amp; sudo update-ca-certificates</code></li>
<li>Chrome/Chromium: <b>Configurações → Privacidade e segurança → Segurança → Gerenciar certificados → Autoridades → Importar</b>. Firefox: <b>Configurações → Privacidade → Ver certificados → Autoridades → Importar</b> (marque "confiar para sites").</li></ol></section>
</div>
<p><a class="botao sec" href="{u}" id="abrir">Abrir o LEGIS</a> <small id="estado">Conferindo se este aparelho já confia…</small></p>
<details><summary>Outro aparelho ou sistema</summary><p>
<a href="#" data-mostra="windows">Windows</a> · <a href="#" data-mostra="apple">iPhone/iPad/Mac</a> ·
<a href="#" data-mostra="android">Android</a> · <a href="#" data-mostra="linux">Linux</a> ·
<a href="/certificado.crt" download="legis-camara.crt">só o arquivo .crt</a></p></details>
</main><script>
const ua = navigator.userAgent, url = {json.dumps(url)};
const so = /Windows/i.test(ua) ? "windows" : /iPhone|iPad|Macintosh|Mac OS/i.test(ua) ? "apple" : /Android/i.test(ua) ? "android" : "linux";
function mostra(s) {{ document.querySelectorAll(".passo").forEach(p => p.classList.toggle("ativo", p.dataset.so === s)); }}
mostra(so);
document.querySelectorAll("[data-mostra]").forEach(a => a.onclick = e => {{ e.preventDefault(); mostra(a.dataset.mostra); }});
function testa() {{
  fetch(url + "certificado/ok", {{mode: "no-cors", cache: "no-store"}})
    .then(() => {{ document.getElementById("ok").style.display = "block"; document.getElementById("passos").style.display = "none";
                  document.getElementById("estado").textContent = "Certificado instalado."; }})
    .catch(() => {{ document.getElementById("estado").textContent = "Ainda não instalado neste aparelho."; setTimeout(testa, 4000); }});
}}
testa();
</script></body></html>""".encode()
