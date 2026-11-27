"""HTTPS na rede local.

Cria (uma vez) uma autoridade certificadora própria da casa legislativa e um
certificado do servidor válido para localhost, o nome da máquina e os IPs da
rede. Basta instalar o certificado da autoridade (ca.crt, servido em
/certificado) em cada computador, tablet e celular para o navegador confiar
no endereço https:// sem avisos.

Em servidor com domínio público, prefira um proxy com Let's Encrypt (veja o
README) e rode o Parlatório só em HTTP atrás dele.
"""
import ipaddress
import os
import shutil
import socket
import subprocess
from pathlib import Path


def ips_locais() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # descobre o IP usado para sair para a rede (não envia nada)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    if shutil.which("hostname"):
        try:
            saida = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3).stdout
            for ip in saida.split():
                try:
                    if ipaddress.ip_address(ip).version == 4:
                        ips.add(ip)
                except ValueError:
                    pass
        except (OSError, subprocess.SubprocessError):
            pass
    return sorted(ips)


def nomes_certificado() -> tuple[list[str], list[str]]:
    dns = {"localhost", socket.gethostname(), socket.gethostname() + ".local"}
    ips = set(ips_locais())
    for extra in filter(None, (x.strip() for x in os.environ.get("TLS_NOMES", "").split(","))):
        try:
            ipaddress.ip_address(extra)
            ips.add(extra)
        except ValueError:
            dns.add(extra)
    return sorted(dns), sorted(ips)


# A autoridade local vai para a lista de confiança dos celulares e PCs: ela só pode assinar nomes da rede local
# (e os de TLS_NOMES), nunca um site da internet. Vale para autoridades novas; as já instaladas seguem como estão.
REDES_LOCAIS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "100.64.0.0/10",
                "::1/128", "fc00::/7", "fe80::/10")
DOMINIOS_LOCAIS = ("localhost", "local", "lan", "home", "internal", "intranet", "home.arpa", "ts.net")


def restricoes_ca() -> str:
    dns, ips = nomes_certificado()
    dominios = sorted(set(DOMINIOS_LOCAIS) | {d.lstrip("*.") for d in dns})
    redes = set(REDES_LOCAIS)
    for ip in ips:
        endereco = ipaddress.ip_address(ip)
        if not any(endereco in ipaddress.ip_network(r) for r in REDES_LOCAIS):
            redes.add(f"{ip}/{endereco.max_prefixlen}")
    partes = [f"permitted;DNS:{d}" for d in dominios]
    for r in sorted(redes):
        rede = ipaddress.ip_network(r)
        partes.append(f"permitted;IP:{rede.network_address}/{rede.netmask}")
    return "nameConstraints=critical," + ",".join(partes)


def _openssl(*args: str):
    subprocess.run(["openssl", *args], check=True, capture_output=True, timeout=60)


def nomes_atuais(crt: Path) -> set[str]:
    """Nomes e IPs que o certificado cobre, como 'DNS:x' e 'IP:1.2.3.4'."""
    saida = set()
    for parte in nomes_no_certificado(crt).replace("\n", ",").split(","):
        parte = parte.strip()
        if parte.startswith("DNS:"):
            saida.add(parte)
        elif parte.startswith("IP Address:"):
            saida.add("IP:" + parte.split(":", 1)[1].strip())
    return saida


def precisa_renovar(crt: Path, dias: int = 30) -> bool:
    """O certificado do servidor não cobre o nome ou um IP atual da máquina, ou vence em menos de `dias`.
    Renovar mantém a autoridade (ca.crt): os aparelhos que já confiam continuam confiando."""
    if not crt.exists():
        return True
    dns, ips = nomes_certificado()
    queridos = {f"DNS:{d}" for d in dns} | {f"IP:{i}" for i in ips}
    if not queridos <= nomes_atuais(crt):
        return True
    try:
        r = subprocess.run(["openssl", "x509", "-in", str(crt), "-noout", "-checkend", str(dias * 86400)],
                           capture_output=True, timeout=10)
        return r.returncode != 0
    except (OSError, subprocess.SubprocessError):
        return False


def gerar(pasta: Path, renovar: bool = False) -> tuple[Path, Path]:
    """Cria ca.crt/ca.key (se faltarem) e servidor.crt/servidor.key, e renova o do servidor quando ele não cobre mais
    o nome ou um IP atual da máquina (IP novo pelo DHCP, nome trocado) ou está para vencer. Retorna (crt, key)."""
    if not shutil.which("openssl"):
        raise RuntimeError("openssl não encontrado; instale-o ou informe TLS_CERT e TLS_KEY.")
    pasta.mkdir(parents=True, exist_ok=True)
    ca_crt, ca_key = pasta / "ca.crt", pasta / "ca.key"
    crt, key = pasta / "servidor.crt", pasta / "servidor.key"
    if not ca_crt.exists() or not ca_key.exists():
        _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "3650",
                 "-keyout", str(ca_key), "-out", str(ca_crt),
                 "-subj", "/CN=Parlatorio Legis - autoridade local",
                 "-addext", "basicConstraints=critical,CA:TRUE",
                 "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                 "-addext", restricoes_ca())
        os.chmod(ca_key, 0o600)
    if renovar or not crt.exists() or not key.exists() or precisa_renovar(crt):
        dns, ips = nomes_certificado()
        san = ",".join([f"DNS:{d}" for d in dns] + [f"IP:{i}" for i in ips])
        csr, ext = pasta / "servidor.csr", pasta / "servidor.ext"
        ext.write_text(f"subjectAltName={san}\nbasicConstraints=CA:FALSE\n"
                       "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n")
        _openssl("req", "-newkey", "rsa:2048", "-nodes", "-sha256", "-keyout", str(key), "-out", str(csr),
                 "-subj", "/CN=Parlatorio Legis")
        # 825 dias é o máximo aceito pelos aparelhos Apple
        _openssl("x509", "-req", "-sha256", "-days", "825", "-in", str(csr), "-CA", str(ca_crt), "-CAkey", str(ca_key),
                 "-CAcreateserial", "-out", str(crt), "-extfile", str(ext))
        os.chmod(key, 0o600)
        csr.unlink(missing_ok=True)
        ext.unlink(missing_ok=True)
    return crt, key


def nomes_no_certificado(crt: Path) -> str:
    try:
        r = subprocess.run(["openssl", "x509", "-in", str(crt), "-noout", "-ext", "subjectAltName"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.split("\n", 1)[-1].strip()
    except (OSError, subprocess.SubprocessError):
        return ""
