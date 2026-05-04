FROM python:3.11-slim
 
WORKDIR /app
 
# Instala dependências
RUN pip install --no-cache-dir gspread google-auth requests
 
# Copia os arquivos do repositório
# credentials.json NAO é copiado aqui — vem pelo file mount do EasyPanel
COPY qualificar_leads.py .
COPY estado.json .
 
# Roda o script
CMD ["python", "-u", "qualificar_leads.py"]
 
