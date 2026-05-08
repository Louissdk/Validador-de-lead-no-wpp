FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir gspread google-auth requests

COPY qualificar_leads.py .
COPY estado.json ./data/estado.json

CMD ["python", "-u", "qualificar_leads.py"]
