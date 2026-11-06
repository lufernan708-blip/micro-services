FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api_gateway.py notification_service.py order_service.py user_service.py ./

EXPOSE 8000 8001 8002

CMD ["uvicorn", "api_gateway:app", "--host", "0.0.0.0", "--port", "8000"]
