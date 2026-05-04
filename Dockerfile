FROM python:3.11-slim

WORKDIR /app

# Instala dependências
RUN pip install --no-cache-dir gspread google-auth requests

# Copia os arquivos
COPY qualificar_leads.py .
COPY credentials.json .
COPY estado.json .

# Roda o script
CMD ["python", "-u", "qualificar_leads.py"]
