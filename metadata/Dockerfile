FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY metadata ./metadata
EXPOSE 8000
CMD ["uvicorn", "metadata.app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning"]
