FROM python:3.13-slim
WORKDIR /app
# ffmpeg: gravações e transmissão ao vivo; openssl: certificado HTTPS local; tzdata: horário certo nas atas
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg openssl tzdata && rm -rf /var/lib/apt/lists/*
# Transcrição local dos discursos (opcional): docker compose build --build-arg TRANSCRICAO=1
ARG TRANSCRICAO=0
RUN if [ "$TRANSCRICAO" = "1" ]; then python -m venv /opt/whisper && /opt/whisper/bin/pip install --no-cache-dir faster-whisper; fi
COPY server ./server
COPY public ./public
COPY scripts ./scripts
ENV PORTA=8090 PORTA_HTTPS=8443 PARLATORIO_BANCO=/dados/parlatorio.db TZ=America/Sao_Paulo HF_HOME=/cache/hf
VOLUME /dados
EXPOSE 8090 8443
CMD ["sh", "-c", "[ -x /opt/whisper/bin/python ] && export TRANSCRICAO_PYTHON=/opt/whisper/bin/python; exec python server/servidor.py"]
