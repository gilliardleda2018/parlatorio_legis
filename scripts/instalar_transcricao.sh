#!/bin/sh
# Instala a transcrição local dos discursos (faster-whisper) num ambiente à parte, em dados/whisper.
# Depois, basta reiniciar o servidor: ele encontra o ambiente sozinho (TRANSCRICAO=auto).
#   sh scripts/instalar_transcricao.sh            # modelo "small" (bom equilíbrio para CPU)
#   sh scripts/instalar_transcricao.sh medium     # mais preciso e mais lento
set -e
cd "$(dirname "$0")/.."
MODELO="${1:-small}"
DESTINO="${PARLATORIO_DADOS:-dados}/whisper"
python3 -m venv "$DESTINO"
"$DESTINO/bin/pip" install --quiet --upgrade pip
"$DESTINO/bin/pip" install --quiet "faster-whisper==${FASTER_WHISPER_VERSAO:-1.1.0}"
echo "Baixando o modelo $MODELO (uma vez só)..."
"$DESTINO/bin/python" -c "from faster_whisper import WhisperModel; WhisperModel('$MODELO', device='cpu', compute_type='int8')"
echo "Pronto. Reinicie o servidor (use TRANSCRICAO_MODELO=$MODELO se não for o small)."
