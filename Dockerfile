FROM python:3.13-slim
WORKDIR /app
# ffmpeg corrige a duração dos áudios gravados (opcional, mas recomendado)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY server ./server
COPY public ./public
ENV PORTA=8080 PARLATORIO_BANCO=/dados/parlatorio.db
VOLUME /dados
EXPOSE 8080
CMD ["python", "server/servidor.py"]
