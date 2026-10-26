# Planejamento de leads no Google Sheets

## O que a nova função faz

O comando plan-sheet lê a aba de leads qualificados e prepara uma aba separada chamada kael_planejamento. Ele não mexe na aba de origem e não abre nem envia mensagens no WhatsApp.

A nova aba inclui:
- telefone, nome e nicho original;
- categoria do nicho;
- endereço e avaliação que existiam na fonte;
- se a fonte indicou um site ou se isso não foi verificado;
- tipo/perfil de lead;
- score e prioridade comercial;
- produto ou serviço sugerido;
- justificativa e evidência usada na recomendação;
- parágrafo 1 (abertura), parágrafo 2 (contexto), parágrafo 3 (proposta), parágrafo 4 (chamada);
- mensagem completa com parágrafos separados para copiar e colar;
- status de autorização, origem/data do opt-in, status de revisão e próxima ação.

## O que significa a análise

As categorias e recomendações são regras heurísticas com base nos dados da planilha. A coluna de site não prova que a empresa não tem site, nem que um site existente é ruim. As recomendações são hipóteses para revisão comercial; o código não faz pesquisa online nem auditoria de site. O score atual reaproveita o score do qualificador ou uma regra simples, portanto não é uma probabilidade validada de compra.

## Bloqueio de mensagens

A política de negócios do WhatsApp exige que a pessoa tenha fornecido seu número e dado permissão para receber comunicações da empresa. Por isso, o planejador só preenche os parágrafos e a mensagem completa quando a fonte inclui consent_status=opt_in, consent_source e consent_at. Se esses campos não existirem na fonte, os campos de mensagem ficam vazios e o status será AGUARDAR_AUTORIZACAO. Descadastro e resposta registrada também bloqueiam o texto. Consulte a política atual do WhatsApp Business: https://business.whatsapp.com/policy/preview?lang=pt_BR

Isso permite qualificar todos os leads e organizar ofertas, mas não transforma uma lista pública de telefones em uma lista autorizada para prospecção no WhatsApp.

## Configuração no Windows

1. Copie .env.example para .env caso ainda não exista. Como você já criou .env, acrescente estas configurações manualmente nele:
   - GOOGLE_SHEETS_ID=ID_DA_SUA_PLANILHA
   - GOOGLE_CREDS_FILE=credentials.json
   - GOOGLE_SOURCE_TAB=leads_validados
   - GOOGLE_OUTPUT_TAB=kael_planejamento
2. Obtenha a chave JSON de uma Google Service Account autorizada a acessar a planilha. Salve-a localmente como credentials.json na pasta visivel-kael-python ou informe o caminho completo em GOOGLE_CREDS_FILE.
3. Compartilhe a planilha com o e-mail da Service Account, com permissão de editor se for gravar a aba de saída. Não envie o JSON ao GitHub nem ao chat.
4. Atualize as dependências com python -m pip install -r requirements.txt.
5. Para uma prévia sem gravar, rode python -m app plan-sheet --limit 20.
6. Confira no terminal o resumo e os primeiros exemplos. Nada terá sido gravado.
7. Para criar/atualizar a aba de saída com esse lote de teste, rode python -m app plan-sheet --limit 20 --write.
8. Para todos os leads, use python -m app plan-sheet --write depois de conferir a prévia.

As reexecuções preservam decisões e edições humanas que já existam na aba de saída, e a aba de origem fica intacta. A operação escreve em blocos para reduzir chamadas à API.

## Importante

- O primeiro teste deve ser limitado a 20 linhas.
- A credencial é um segredo; nunca faça commit dela.
- O comando sem --write é somente leitura.
- A função não envia mensagens. Mensagens aprovadas ficam disponíveis para copiar manualmente.
- A interface de revisão local ainda pode ser usada, mas para esse fluxo a planilha torna-se o centro de qualificação e revisão.


## Consolidação integrada ao programa oficial

O comando `python -m app consolidate-sheet` cria uma prévia conservadora de registros candidatos a duplicidade. Por padrão, lê `GOOGLE_CONSOLIDATION_SOURCE_TAB=kael_revalidacao_v2` e prepara a aba `GOOGLE_CONSOLIDATION_OUTPUT_TAB=kael_leads_consolidados_preview_v5`. A origem não é alterada. A prévia agrupa por nome normalizado + endereço; se um grupo tiver telefones distintos, os registros permanecem separados. Linhas sem nome ou endereço permanecem individuais.

Primeiro execute somente a leitura, por exemplo `python -m app consolidate-sheet --limit 100`. A opção `--limit` é somente para prévia e não pode ser combinada com `--write`. A gravação completa exige `--write`; se a aba de saída já existir, também exige `--confirm-overwrite`. Antes de gravar, confira os contadores e confirme que a aba de saída está correta.

A saída indica quais linhas de origem cada grupo representa; os registros originais permanecem na aba de origem. A consolidação gera candidatos, não confirma identidade legal/comercial. `status_whatsapp_auditoria=NAO_VERIFICADO` é intencional: validação estrutural do telefone não confirma que exista uma conta no WhatsApp nem constitui autorização de contato.


## Controle de cota e timeout (KAEL)

Todas as conexões gspread passam por um cliente HTTP controlado. Por padrão, ele
serializa as chamadas e espera pelo menos 1,25 segundo entre elas (aprox. 48
requisições/minuto), deixando margem frente à cota por usuário/projeto do Google.
Em respostas `429` e erros de cota `403`, aplica espera exponencial com jitter e
até seis novas tentativas. Falhas transitórias/timeout são repetidas somente em
leituras; uma gravação que termina em timeout não é repetida automaticamente,
porque o Google pode tê-la concluído antes de a conexão cair.

As gravações seguem em blocos de 250 linhas para reduzir o tamanho do payload.
`--limit N` busca somente o cabeçalho e as primeiras N linhas, em vez de baixar a
aba inteira antes de cortar a amostra. Evite executar dois comandos de planilha ao
mesmo tempo, pois o limitador coordena chamadas dentro do mesmo processo.

Ajustes opcionais no `.env`:
- `KAEL_SHEETS_MIN_INTERVAL_SECONDS=1.25`
- `KAEL_SHEETS_MAX_RETRIES=6`

O comando de prévia continua sem gravar. Em erro durante uma gravação, confira a
aba de saída antes de reexecutar; a aba de origem nunca deve ser alterada.
