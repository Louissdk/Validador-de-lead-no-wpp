# Atualização: proteção de quota do Google Sheets

## O que mudou

- Criado `app/google_sheets_http.py`: fila de requisições serializada, intervalo padrão de 1,25 s e backoff exponencial para erros de quota.
- Conexão central em `app/sheets_planner.py` usa o cliente controlado para operações do Google Sheets.
- Timeout HTTP definido em (15 s de conexão, 90 s de leitura).
- Retentativas para `429`/erros explícitos de quota; timeout e falhas transitórias são reexecutados automaticamente somente em leituras, para não duplicar gravações incertas.
- `--limit N` em `consolidate-sheet` e `plan-sheet` lê apenas o cabeçalho e o intervalo da amostra, em vez de baixar a aba inteira antes de limitar.
- Gravações passaram de 400 para 250 linhas por bloco, reduzindo payloads por atualização.
- Documentação atualizada em `docs/PLANEJAMENTO_GOOGLE_SHEETS.md`.

## Teste seguro recomendado

Dentro de `C:\Users\luisf\Downloads\robo python\visivel-kael-python`, com o ambiente Python e credenciais locais já configurados:

```powershell
python -m pytest -q tests/test_google_sheets_http.py tests/test_lead_consolidation.py tests/test_sheets_planner.py
python -m app consolidate-sheet --limit 100
```

O segundo comando é apenas leitura. O esperado é ver `linhas_origem: 100` e a linha `Prévia: nenhuma alteração foi gravada.`

Não execute `--write` nesta etapa. Primeiro, confira o resumo da amostra. O arquivo `.env` e `credentials.json` locais não fazem parte deste pacote e devem continuar somente na máquina local.

## Configuração opcional no `.env` local

```dotenv
KAEL_SHEETS_MIN_INTERVAL_SECONDS=1.25
KAEL_SHEETS_MAX_RETRIES=6
```

Evite iniciar dois comandos que acessem a planilha ao mesmo tempo. O limitador impede rajadas dentro do mesmo processo; processos independentes não compartilham o mesmo bloqueio.
