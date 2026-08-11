FROM python:3.11-slim

WORKDIR /app

RUN useradd --create-home --uid 10001 vocabflow

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade "pip>=26.1.2" \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=vocabflow:vocabflow app ./app
COPY --chown=vocabflow:vocabflow data ./data

USER vocabflow

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
