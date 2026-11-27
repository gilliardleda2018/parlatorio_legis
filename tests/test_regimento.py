"""Assistente do Regimento: normas cadastradas, artigos com o caminho e perguntas da Mesa e dos vereadores."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import regimento  # noqa: E402
from apoio import Servidor  # noqa: E402

TEXTO = """REGIMENTO INTERNO DA CÂMARA MUNICIPAL DE VILA ESPERANÇA
TÍTULO I DAS DISPOSIÇÕES PRELIMINARES
Art. 1º A Câmara Municipal é o órgão legislativo do Município e se compõe de vereadores.
TÍTULO II DAS SESSÕES
CAPÍTULO I DO USO DA PALAVRA
Art. 20. No Pequeno Expediente, cada vereador inscrito terá cinco minutos para falar.
Art. 21. No Grande Expediente, cada vereador inscrito terá quinze minutos, permitidos apartes.
CAPÍTULO II DAS VOTAÇÕES
Art. 40. A rejeição do veto exige o voto da maioria absoluta dos membros da Câmara, em votação nominal.
Parágrafo único. Esgotado o prazo sem deliberação, o veto entra na Ordem do Dia, sobrestadas as demais matérias.
Art. 41. As deliberações são tomadas por maioria simples, presente a maioria absoluta dos vereadores.
"""


class Dispositivos(unittest.TestCase):
    def test_artigos_com_caminho(self):
        d = regimento.dispositivos(TEXTO, "Regimento Interno")
        self.assertEqual([x["artigo"] for x in d], ["1", "20", "21", "40", "41"])
        veto = next(x for x in d if x["artigo"] == "40")
        self.assertEqual(veto["caminho"], "TÍTULO II DAS SESSÕES > CAPÍTULO II DAS VOTAÇÕES")
        self.assertIn("Parágrafo único", veto["texto"])
        self.assertNotIn("CAPÍTULO", next(x for x in d if x["artigo"] == "21")["texto"])


class RegimentoNoServidor(unittest.TestCase):
    def test_cadastra_e_pergunta(self):
        s = Servidor()
        try:
            P = s.entrar("presidente")
            V = s.entrar("parlamentar", s.estado()["parls"][0]["id"])
            self.assertEqual(s.req("/api/regimento/perguntar", {"pergunta": "quórum do veto"})["HTTP"], 401)   # só logado
            vazio = s.req("/api/regimento/perguntar", {"pergunta": "quórum do veto"}, V)
            self.assertEqual(vazio["modo"], "vazio")
            self.assertEqual(s.acao(P, "normaSalvar", nome="Regimento", texto="curto")["HTTP"], 400)
            self.assertEqual(s.acao(P, "normaSalvar", nome="Regimento Interno", texto=TEXTO), {"ok": True})
            r = s.req("/api/regimento/perguntar", {"pergunta": "Qual o quórum para rejeitar o veto do prefeito?"}, V)
            self.assertEqual(r["fontes"][0]["artigo"], "40")
            r = s.req("/api/regimento/perguntar", {"pergunta": "quanto tempo no grande expediente"}, P)
            self.assertEqual(r["fontes"][0]["artigo"], "21")
            normas = s.req("/api/normas", token=P)["normas"]
            self.assertEqual((normas[0]["nome"], normas[0]["artigos"]), ("Regimento Interno", 5))
            self.assertEqual(s.acao(P, "normaRemover", id=normas[0]["id"]), {"ok": True})
            self.assertEqual(s.req("/api/normas", token=P)["normas"], [])
        finally:
            s.parar()

    @unittest.skipUnless(shutil.which("pdftotext") and shutil.which("ffmpeg"), "precisa do pdftotext")
    def test_pdf(self):
        self.assertIn("Art. 40", regimento.pdf_para_texto(_pdf(TEXTO)))


def _pdf(texto: str) -> bytes:
    """PDF mínimo com o texto (uma linha por linha), sem bibliotecas."""
    linhas = texto.encode("latin-1", "replace").decode("latin-1").splitlines()
    corpo = "BT /F1 9 Tf 40 800 Td 11 TL " + " ".join(f"({l.replace('(', '[').replace(')', ']')}) '" for l in linhas) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(corpo.encode('latin-1'))} >>\nstream\n{corpo}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    saida, offs = b"%PDF-1.4\n", []
    for k, o in enumerate(objs, 1):
        offs.append(len(saida))
        saida += f"{k} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(saida)
    saida += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    saida += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return saida


if __name__ == "__main__":
    unittest.main()
