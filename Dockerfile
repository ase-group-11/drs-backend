FROM python:3.9-slim

WORKDIR /app

# Added curl for the health check probe
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:socket_app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1"]