FROM python:3.11-slim

WORKDIR /workspace

COPY microservicos-fastapi/requirements.txt ./microservicos-fastapi/requirements.txt
COPY test/requirements.txt ./test/requirements.txt
RUN pip install --no-cache-dir \
    -r microservicos-fastapi/requirements.txt \
    -r test/requirements.txt

COPY microservicos-fastapi/ ./microservicos-fastapi/
COPY customer_support/ ./customer_support/
COPY test/ ./test/
COPY pytest.ini ./

ENV PYTHONPATH=/workspace:/workspace/microservicos-fastapi

CMD ["pytest", "-q"]
