# Development image for the camera integration service.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY --chown=app:app camera ./camera
USER app

EXPOSE 8200
CMD ["uvicorn", "camera.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8200"]
