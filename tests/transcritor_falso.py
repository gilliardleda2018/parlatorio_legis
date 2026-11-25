"""Motor de transcrição falso para os testes: devolve um texto fixo no formato do script real."""
import json
import sys

texto = "Senhor presidente, a saúde pública de Tuntum precisa de um posto no povoado. Peço apoio à indicação."
print("aviso de biblioteca qualquer")
print(json.dumps({"texto": texto, "segmentos": [{"ini": 0.0, "fim": 6.5, "texto": texto}], "idioma": "pt",
                  "motor": "falso", "arquivo": sys.argv[-1]}, ensure_ascii=False))
