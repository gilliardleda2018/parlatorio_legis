#!/usr/bin/env python3
"""Cria um banco do Parlatório Legis com os vereadores ELEITOS de um município (dados oficiais).

Uso: python3 scripts/tse_eleitos.py MA "Tuntum" dados/tuntum.db
     PARLATORIO_BANCO=dados/tuntum.db python3 server/servidor.py

Fontes oficiais e públicas:
  * TSE, resultados das Eleições Municipais 2024 (1º turno): nome de urna, partido
    pelo qual foi eleito e foto da candidatura (resultados.tse.jus.br);
  * IBGE, Censo 2022: população residente (usada na nota de relevância social).

Os dados refletem a eleição de 2024. Suplentes que assumiram depois, mudanças de
partido e nomes parlamentares diferentes do nome de urna precisam ser ajustados
no painel (Plenário → Editar) ou importados do SAPL da câmara, se houver.
O banco nasce SEM sessões, votos ou projetos: nada é inventado sobre pessoas reais.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import cidade  # noqa: E402


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    uf, municipio, banco = sys.argv[1].lower(), sys.argv[2], Path(sys.argv[3])
    if banco.exists():
        sys.exit(f"{banco} já existe. Escolha outro arquivo (este script cria um banco novo).")
    embutidos = os.environ.get("LEGIS_CIDADE_DADOS")   # pacote da Câmara: dados já baixados na geração do pacote
    try:
        if embutidos:
            dados = cidade.de_json(Path(embutidos).read_text(encoding="utf-8"))
            print(f"Dados do pacote: {dados.get('camara') or dados['municipio']} ({len(dados['eleitos'])} vereadores)")
        else:
            dados = cidade.buscar(uf, municipio)
    except ValueError as e:
        sys.exit(str(e))

    # cria o banco pelo próprio servidor (estado inicial completo) e troca os parlamentares de exemplo
    os.environ["PARLATORIO_BANCO"] = str(banco)
    import servidor  # noqa: E402

    with servidor.trava:
        servidor.aplicar_cidade(dados)
    for e in dados["eleitos"]:
        print(f"  {e['nome']:<28} {e['partido']:<6} nº {e['numero']:<6} {e['votos']:>5} votos  {'foto ✓' if e.get('foto') else 'sem foto'}")
    servidor.db.commit()
    servidor.salvar()
    print(f"\nBanco criado em {banco}. PIN inicial de todos: 0000 (troque em Plenário → Editar).")


if __name__ == "__main__":
    main()
