FROM node:20-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/logs /tmp/enterprise-agent/uploads /tmp/enterprise-agent/rpa \
    && chown -R appuser:appuser /app/logs /tmp/enterprise-agent

USER appuser
EXPOSE 8000
CMD ["python", "-m", "scripts.start_production"]
