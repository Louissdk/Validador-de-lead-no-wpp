# Visível / KAEL — Robô Python de prospecção

MVP de prospecção com importação de leads, validações, motor opcional com Evolution API e Central local de rascunhos para gerar, editar, aprovar e rejeitar mensagens em lote.

A Central não envia mensagens. Aprovação significa apenas que o texto foi revisado e pode ser aberto no WhatsApp Web para confirmação final. O usuário ainda precisa revisar o destinatário e clicar manualmente em Enviar. Não há automação furtiva nem mecanismo para contornar limites da plataforma.

## Central de rascunhos (sem Evolution API)

A interface roda em http://127.0.0.1:8000/drafts:

1. Inicie o Docker Desktop.
2. Copie .env.example para .env.
3. Inicie o banco: docker compose up -d db.
4. Crie/atualize as tabelas: docker compose run --build --rm app python -m app init-db.
5. Importe os leads: docker compose run --rm app python -m app import-csv data/leads.csv.
6. Suba a interface local: docker compose up --build -d api.
7. Abra http://127.0.0.1:8000/drafts no navegador.

A Central seleciona contatos ativos com opt-in documentado (consent_source e consent_at), sem resposta registrada e sem ocorrência na tabela de supressão. É possível filtrar por nicho e cidade, definir um limite de leads por lote, personalizar o modelo e buscar contatos por nome, empresa, telefone ou nicho.

Cada texto é salvo no PostgreSQL como pendente. Você pode editar, salvar, aprovar ou rejeitar as mensagens. Um lote pode ter até 10.000 rascunhos; a tela mostra 30 por página. Mensagens com os campos personalizados vazios são ignoradas em vez de gerar textos incompletos.

A aprovação não envia nada. O link abre o WhatsApp Web com o texto preparado, sujeito à compatibilidade do navegador e da interface do WhatsApp. Revise o destinatário e o texto na conversa antes de enviar manualmente.

A interface é para uso local: mantenha o serviço acessível somente por 127.0.0.1; não exponha o painel na internet sem autenticação, proteção CSRF e revisão de segurança. Não versione .env, listas reais ou dados pessoais.

## Modo navegador assistido individual

O comando browser-draft abre uma sessão local de Chromium via Playwright e preenche o rascunho de um contato elegível. Ele não clica em Enviar. Como a Central permite revisar vários textos primeiro, use-a para preparar as mensagens e abra no WhatsApp apenas as que aprovou.

## Modo Evolution API (opcional)

Quando disponível, configure EVOLUTION_BASE_URL, EVOLUTION_API_KEY e EVOLUTION_INSTANCE. O envio real via worker continua bloqueado sem SEND_MODE=live, ALLOW_MARKETING_SENDS=true e LIVE_SEND_CONFIRMATION=I_UNDERSTAND_AND_HAVE_PERMISSION.

## Formato CSV

Colunas: nome, empresa, telefone, nicho, cidade, consent_status, consent_source, consent_at. Valores aceitos para consentimento: opt_in, opt_out, unknown. Um telefone válido não prova consentimento.

Testes unitários: pytest -q dentro de visivel-kael-python.


## Planejamento de leads direto no Google Sheets

O comando plan-sheet gera uma prévia de qualificação, tipo de lead, score/prioridade, oferta sugerida, justificativa e mensagens separadas em parágrafos, sem abrir o WhatsApp. Por padrão ele não grava nada.

1. Configure GOOGLE_SHEETS_ID no arquivo .env.
2. Coloque a chave JSON da Service Account localmente em credentials.json, ou configure GOOGLE_CREDS_FILE. Compartilhe a planilha com o e-mail da Service Account. Nunca versione o JSON.
3. Instale as dependências atualizadas com python -m pip install -r requirements.txt.
4. Teste com python -m app plan-sheet --limit 20.
5. Quando revisar a prévia, grave a aba separada com python -m app plan-sheet --limit 20 --write. Para todos os registros, retire --limit 20.

A aba de origem (por padrão leads_validados) fica preservada. A saída (por padrão kael_planejamento) tem uma coluna para cada parágrafo e uma coluna mensagem_completa_copiar_colar. Textos para WhatsApp só são preparados quando a fonte contém opt-in documentado (consent_status, consent_source e consent_at). Os demais leads recebem status AGUARDAR_AUTORIZACAO e continuam com os campos de mensagem vazios.

Consulte docs/PLANEJAMENTO_GOOGLE_SHEETS.md para o procedimento completo e as limitações.
