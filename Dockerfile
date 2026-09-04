FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY crro/ ./crro/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY api.py run_demo.py ./

RUN mkdir -p outputs && python3 run_demo.py

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn api:app --host 0.0.0.0 --port 8000"]
