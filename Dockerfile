FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    pymodbus==3.5.0 \
    fastapi==0.104.1 \
    uvicorn==0.24.0 \
    python-dotenv==1.0.0

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py .

EXPOSE 5000 502

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000"]
