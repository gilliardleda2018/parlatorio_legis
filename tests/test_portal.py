"""Importador do portal legislativomunicipal.com: layout novo (Barra do Corda: tabela de mandatos e filiação) e
antigo (Tuntum). Textos de página sintéticos, sem rede."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ARQ = Path(__file__).resolve().parent.parent / "scripts" / "importar_portal_camara.py"
spec = importlib.util.spec_from_file_location("importar_portal", ARQ)
imp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(imp)

HOJE = (2026, 11, 27)
NOVO = ("Detalhe Aurean Barbalho Aurean de Lima Barbalho Vereador(A) - Partido: Comissões Mandatos Filiação partidária "
        "Informações dos mandatos Cargo Vinculo Legislatura Período VEREADOR(A) VEREADOR EM EXERCÍCIO 19ª (Décima Nona) "
        "Legislatura (2025 - 2028) 01/01/2025 VEREADOR(A) VEREADOR EM EXERCÍCIO 18ª (Décima Oitava) Legislatura "
        "(2021 - 2024) 01/01/2021 à 31/12/2024 Filiação partidária Período Sigla Partido 01/01/2021 à 31/12/2024 PR "
        "PARTIDO DA REPÚBLICA 01/01/2025 à 31/12/2028 PP PROGRESSISTAS Biografia")
SUPLENTE = ("Informações dos mandatos Cargo Vinculo Legislatura Período VEREADOR(A) VEREADOR EM EXERCÍCIO 19ª (Décima Nona) "
            "Legislatura (2025 - 2028) 11/08/2026 à 28/09/2026 Filiação partidária Período Sigla Partido 01/01/2021 à "
            "31/12/2028 PT do B PARTIDO TRABALHISTA DO BRASIL")
PRESIDENTE = ("Informações dos mandatos Cargo Vinculo Legislatura Período PRESIDENTE MESA DIRETORA 19ª (Décima Nona) "
              "Legislatura (2025 - 2028) 01/01/2025 Filiação partidária Período Sigla Partido 01/01/2025 à 31/12/2028 MDB "
              "MOVIMENTO DEMOCRÁTICO BRASILEIRO")


def pagina(corpo: str, og: str) -> str:
    return f'<html><head><meta property="og:description" content="VEREADOR(A): {og}"></head><body>{corpo}</body></html>'


class LayoutNovo(unittest.TestCase):
    def test_mandato_atual_cargo_e_partido_vigente(self):
        m = imp._mandatos_novos(NOVO, HOJE)
        self.assertEqual(m, {"emExercicio": True, "cargo": "", "partido": "PP"})
        self.assertEqual(imp._mandatos_novos(SUPLENTE, HOJE), {"emExercicio": False, "cargo": "", "partido": "PT do B"})
        self.assertEqual(imp._mandatos_novos(PRESIDENTE, HOJE)["cargo"], "Presidente")
        self.assertIsNone(imp._mandatos_novos("Detalhe Fulano Vereador(A) - Vereador Em Exercício Partido: PSB", HOJE))

    def test_ler_vereador_nome_civil_em_maiusculas_no_og(self):
        with patch.object(imp, "baixar", return_value=pagina(NOVO, "AUREAN  DE LIMA BARBALHO")), \
             patch.object(imp.time, "localtime", return_value=HOJE + (0,) * 6):
            v = imp.ler_vereador("https://www.cmx.legislativomunicipal.com", 107)
        self.assertEqual((v["curto"], v["civil"], v["partido"], v["emExercicio"]),
                         ("Aurean Barbalho", "Aurean de Lima Barbalho", "PP", True))

    def test_layout_antigo_de_tuntum(self):
        antigo = ("Detalhe Alan Noleto Alan Brito Noleto Vereador(A) - Vereador Em Exercício Partido: PSB --> LEGISLATURA "
                  "ATUAL Quantidade de 40 matérias, representando 0,00% Presente em 20 sessões, representando 83,33%")
        with patch.object(imp, "baixar", return_value=pagina(antigo, "Alan Brito Noleto")):
            v = imp.ler_vereador("https://www.cmtuntum.legislativomunicipal.com", 7)
        self.assertEqual((v["curto"], v["civil"], v["partido"], v["emExercicio"], v["presencas"], v["materias"]),
                         ("Alan Noleto", "Alan Brito Noleto", "PSB", True, 20, 40))


if __name__ == "__main__":
    unittest.main()
