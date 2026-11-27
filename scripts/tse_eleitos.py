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
    argumentos = sys.argv[1:]
    configurar = argumentos[:1] == ["--configurar"]
    if configurar:
        argumentos = argumentos[1:]
    if len(argumentos) != 3:
        sys.exit(__doc__)
    uf, municipio, arquivo = argumentos
    banco = Path(arquivo)
    if banco.exists() and not (configurar and (banco.parent / ".sem-municipio").is_file()):
        sys.exit(f"{banco} já existe. A importação inicial não altera um banco em uso.")
    embutidos = os.environ.get("LEGIS_CIDADE_DADOS")
    try:
        if embutidos:
            dados = cidade.de_json(Path(embutidos).read_text(encoding="utf-8"))
            print(f"Dados do pacote: {dados.get('camara') or dados['municipio']} ({len(dados['eleitos'])} vereadores)")
        else:
            dados = cidade.buscar(uf, municipio)
    except (OSError, ValueError, KeyError) as e:
        sys.exit(f"Não foi possível importar o município: {e}")

    os.environ["PARLATORIO_BANCO"] = str(banco)
    import servidor  # noqa: E402

    with servidor.trava:
        if not servidor.configuracao_cidade_disponivel():
            sys.exit("A Câmara já possui registros de uso. Nenhum dado foi substituído.")
        servidor.aplicar_cidade(dados)
        servidor.registrar("sistema", "configurarCidade", {"uf": dados["uf"], "municipio": dados["municipio"],
                                                         "vereadores": len(dados["eleitos"])})
        servidor.salvar()
        servidor.concluir_configuracao_cidade()
    for e in dados["eleitos"]:
        print(f"  {e['nome']:<28} {e['partido']:<6} nº {e['numero']:<6} {e['votos']:>5} votos  {'foto ✓' if e.get('foto') else 'sem foto'}")
    print(f"\nCâmara de {dados['municipio']}/{dados['uf']} configurada. PIN inicial dos vereadores: 0000.")


if __name__ == "__main__":
    main()
