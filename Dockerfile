FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Seoul INSTANCE_PATH=/data
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY judge ./judge
COPY problems ./problems
COPY wsgi.py gunicorn.conf.py ./
RUN useradd -m -u 1000 web && mkdir -p /data && chown web:web /data
USER web
VOLUME ["/data"]
EXPOSE 8000
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
