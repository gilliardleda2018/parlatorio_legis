#!/usr/bin/env python3
"""Instala o Legis a partir do pacote completo e configura o município no final."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def executar(*comando):
    return subprocess.run(comando, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sem-perguntar", action="store_true", help="deixa a seleção da cidade para o painel ou usa UF e MUNICIPIO")
    parser.add_argument("--perguntar", action="store_true", help="permite ajustar os componentes opcionais antes de instalar")
    opcoes = parser.parse_args()

    if os.geteuid() != 0:
        parser.exit(1, "Execute com sudo: sudo python3 instalar-legis.py\n")
    instalador = Path(__file__).with_name("instalar-legis.sh")
    if not instalador.is_file():
        parser.exit(1, "Extraia o pacote completo. instalar-legis.py e instalar-legis.sh ficam na mesma pasta do .deb.\n")

    argumentos = ["bash", str(instalador)]
    if opcoes.sem_perguntar or not sys.stdin.isatty():
        argumentos.append("--sem-perguntar")
    if opcoes.perguntar:
        argumentos.append("--perguntar")
    try:
        executar(*argumentos)
    except subprocess.CalledProcessError as erro:
        return erro.returncode
    except KeyboardInterrupt:
        print("\nInstalação interrompida. Para retomar a seleção da cidade, use sudo legis configurar-cidade.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
