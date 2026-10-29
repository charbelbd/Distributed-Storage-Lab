FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY storage_node ./storage_node
ENV DATA_DIR=/data PORT=9000
EXPOSE 9000
CMD ["sh", "-c", "uvicorn storage_node.app:app --host 0.0.0.0 --port $PORT --log-level warning"]
