# Deploy no EasyPanel — VisívelAgora Qualificador

## Arquivos necessários (coloque todos na mesma pasta)
- qualificar_leads.py
- Dockerfile
- credentials.json   ← seu arquivo do Google Service Account
- estado.json        ← começa como: {"aba_index": 0, "cursor": 0, "total_gravados": 0}

## Passo a passo no EasyPanel

### 1. Criar nova aplicação
- No EasyPanel, clique em "+ New Service" ou "+ Create App"
- Escolha o tipo: "App"
- Nome: `qualificador-leads`

### 2. Fazer upload dos arquivos
Opção A — Via GitHub (recomendado):
1. Crie um repositório PRIVADO no GitHub
2. Suba os 4 arquivos para ele
3. No EasyPanel, conecte o repositório
4. Build method: Dockerfile

Opção B — Via SSH (mais rápido):
1. Acesse o terminal SSH do EasyPanel
2. Crie a pasta: mkdir -p /app/qualificador
3. Faça upload dos arquivos via SCP ou cole o conteúdo manualmente

### 3. Configurar o container
- Build command: (deixa vazio, o Dockerfile cuida)
- Start command: python -u qualificar_leads.py
- Restart policy: "No" (não reinicia automaticamente — roda uma vez e para)

### 4. Ver os logs em tempo real
- No EasyPanel, aba "Logs" da aplicação
- Você verá o progresso igual ao terminal local

## IMPORTANTE — estado.json
O estado.json precisa persistir entre reinicializações.
No EasyPanel, configure um Volume:
- Container path: /app/estado.json
- Ou monte /app como volume persistente

## Estimativa de tempo com os novos limites
- 16.300 leads ÷ 10 por lote = ~1.630 lotes
- ~35 segundos por lote (30s fixo + até 10s aleatório)
- Tempo total: ~16 horas rodando em horário comercial (8h-20h)
- Com 2 dias de processamento está completo
