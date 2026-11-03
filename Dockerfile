FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY README.md pyproject.toml ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir .

COPY docs/ ./docs/
RUN cgt export-traces --output docs/traces.json

USER 65534:65534
EXPOSE 8000
CMD ["python", "-m", "http.server", "8000", "--bind", "0.0.0.0", "--directory", "docs"]
