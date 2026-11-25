FROM python:3.13-slim
WORKDIR /app
COPY server ./server
COPY public ./public
ENV PORTA=8080 PARLATORIO_BANCO=/dados/parlatorio.db
VOLUME /dados
EXPOSE 8080
CMD ["python", "server/servidor.py"]
