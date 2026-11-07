FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
WORKDIR /app
COPY backend/requirements*.txt backend/
RUN pip install --no-cache-dir -r backend/requirements.txt -r backend/requirements-semantic.txt -r backend/requirements-speech.txt
COPY backend backend
COPY alembic.ini pyproject.toml ./
RUN useradd --create-home campus && mkdir -p /app/data /var/data/models && chown -R campus:campus /app /var/data/models
USER campus
EXPOSE 8000
CMD ["python", "-m", "backend.scripts.start_service", "api"]
