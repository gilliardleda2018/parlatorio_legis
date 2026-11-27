"""Lê o camara.json de uma release camara-* e imprime as variáveis para o bash (UF, MUN, NOME, CAM, YT)."""
import json
import shlex
import sys

c = json.load(open(sys.argv[1], encoding="utf-8"))
for var, chave in (("UF", "uf"), ("MUN", "municipio"), ("NOME", "nome"), ("CAM", "camara"), ("YT", "youtube")):
    print(f"{var}={shlex.quote(str(c.get(chave) or ''))}")
