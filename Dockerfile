FROM python:3.12-slim
WORKDIR /app
COPY server.py .
COPY public ./public
ENV PORT=8000 DB_PATH=/data/brigada.db PYTHONUNBUFFERED=1
RUN mkdir -p /data
VOLUME /data
EXPOSE 8000
CMD ["python", "server.py"]
