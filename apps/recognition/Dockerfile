# Development image for the recognition service (evaluation phase, NOT connected to attendance).
# Model weights are NOT baked in: mount ./models read-only (licence + sha256 checked at start-up).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY --chown=app:app recognition ./recognition
USER app

EXPOSE 8100
CMD ["uvicorn", "recognition.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8100"]
