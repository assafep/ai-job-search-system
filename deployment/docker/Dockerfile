FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY gdtq /app/gdtq
COPY adapters /app/adapters
COPY agents /app/agents
COPY config/gdtq-routing.json /app/config/gdtq-routing.json
COPY schemas /app/schemas
RUN mkdir -p /app/var
CMD ["python","-m","gdtq.cli","run","--database","/app/var/ajss-gdtq.sqlite3"]
