FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    JY_HOST=0.0.0.0 \
    PORT=10000 \
    JY_DATA_DIR=/data \
    JY_DB_PATH=/data/jy.db \
    JY_REPORTS_DIR=/data/reports

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

CMD ["python", "app.py"]
