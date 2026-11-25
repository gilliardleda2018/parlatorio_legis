FROM python:3.13-slim
WORKDIR /app
# ffmpeg corrige a duração dos áudios gravados (opcional, mas recomendado)
# openssl gera o certificado HTTPS local; tzdata dá o horário certo nas atas
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg openssl tzdata && rm -rf /var/lib/apt/lists/*
COPY server ./server
COPY public ./public
ENV PORTA=8090 PORTA_HTTPS=8443 PARLATORIO_BANCO=/dados/parlatorio.db TZ=America/Sao_Paulo
VOLUME /dados
EXPOSE 8090 8443
CMD ["python", "server/servidor.py"]
