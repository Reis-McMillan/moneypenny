FROM python:3.14-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libc6-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.14-slim

RUN addgroup --system --gid 1001 appgroup && adduser --system --uid 1001 --ingroup appgroup appuser
USER appuser
WORKDIR /home/appuser/app

COPY --from=builder /install /usr/local

ARG ENV=prod
COPY --chown=appuser:appgroup . .
RUN cp config/config.${ENV}.py config/config.py

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
