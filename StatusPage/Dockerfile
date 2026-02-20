# ---- Build stage ----
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

# ---- Runtime stage ----
FROM python:3.12-slim
WORKDIR /app

# Copy installed packages
COPY --from=builder /install /usr/local

# Copy application
COPY app/ app/

# Non-root user
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

ENV SPT_HOST=0.0.0.0
ENV SPT_PORT=8000
ENV SPT_LOG_LEVEL=INFO

CMD ["python", "-m", "app.main"]
