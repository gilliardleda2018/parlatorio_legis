"""Lê o modelo de uma cidade (cidades/<nome>/camara.json) para os scripts de empacotamento.

  python3 packaging/modelo.py validar cidades/tuntum/camara.json
  python3 packaging/modelo.py vars cidades/tuntum/camara.json        # variáveis para o bash (eval/.)
  python3 packaging/modelo.py normalizar cidades/x/camara.json destino.json
  python3 packaging/modelo.py congelada tuntum                       # sai 0 se cidades/tuntum estiver congelada

As variáveis saem com shlex.quote: podem ir para "eval" ou "." sem risco de executar nada.
"""
import json
import shlex
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "server"))
import modelo_cidade  # noqa: E402


def _bool(v: bool) -> str:
    return "true" if v else "false"


def variaveis(caminho: Path) -> dict:
    m = modelo_cidade.ler(caminho)
    brasao = ""
    if m["brasao"]:
        arq = (caminho.parent / m["brasao"]).resolve()
        if not arq.is_file():
            raise modelo_cidade.ModeloInvalido(f"brasao: arquivo não encontrado ao lado do modelo: {arq.name}")
        brasao = str(arq)
    pv = m["plenarioVirtual"]
    return {
        "UF": m["uf"], "MUN": m["municipio"], "NOME": m["nome"], "CAM": m["camara"], "YT": m["youtube"]["link"],
        "BRASAO": brasao, "CONGELADA": _bool(m["congelada"]), "RADIO": _bool(m["radio"]["ligada"]),
        "JITSI": _bool(pv["ligado"] and pv["videoconferencia"] == "propria"),
        # "auto" fica vazio: o instalador decide pela memória da máquina
        "TRANSCRICAO": {"auto": "", "sim": "true", "nao": "false"}[m["recursos"]["transcricao"]],
        "IA": {"auto": "", "sim": "true", "nao": "false"}[m["recursos"]["ia"]],
        "TLS_EXTRAS": ",".join(m["certificado"]["nomesExtras"]),
    }


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd, alvo = argv[1], argv[2]
    try:
        if cmd == "congelada":
            arq = RAIZ / "cidades" / modelo_cidade.slug(alvo) / "camara.json"
            return 0 if arq.is_file() and modelo_cidade.ler(arq)["congelada"] else 1
        caminho = Path(alvo)
        if cmd == "validar":
            m = modelo_cidade.ler(caminho)
            variaveis(caminho)
            print(f"ok: {m['camara']} ({m['municipio']}/{m['uf']}), nome {m['nome']}"
                  f"{' — congelada' if m['congelada'] else ''}")
        elif cmd == "vars":
            for k, v in variaveis(caminho).items():
                print(f"{k}={shlex.quote(v)}")
        elif cmd == "normalizar":
            m = modelo_cidade.ler(caminho)
            Path(argv[3]).write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            print(__doc__)
            return 2
    except modelo_cidade.ModeloInvalido as e:
        print(f"Modelo inválido: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
