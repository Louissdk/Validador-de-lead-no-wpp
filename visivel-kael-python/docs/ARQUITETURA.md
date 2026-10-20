# Arquitetura do MVP

- CLI: inicializa banco, importa CSV, cria/ativa campanhas, visualiza fila e prepara rascunho assistido de um único contato.
- PostgreSQL: fonte de verdade para leads, consentimento, campanhas, fila, supressões e eventos.
- Worker: reivindica tarefas com FOR UPDATE SKIP LOCKED, valida consentimento/resposta/supressão, horário comercial, teto diário e rate limit Redis.
- Evolution API: adaptador opcional de envio; simulação não chama serviço externo.
- Navegador assistido: Playwright abre Chromium visível em perfil dedicado, valida opt-in no banco, abre um chat e preenche o campo. Nunca aciona o botão ou tecla de envio.
- FastAPI: health check e webhook protegido; registra respostas e opt-outs quando a integração Evolution está configurada.

Decisões:
1. Todas as campanhas nascem pausadas.
2. Telefone válido não significa consentimento.
3. O envio assistido é individual e exige revisão humana.
4. Resposta recebida cancela a cadência pendente.
5. Pedido de opt-out cria supressão durável.
6. dry_run é o modo padrão do worker.
7. Envio real via API exige confirmação de ambiente explícita.

Fluxo: importar e auditar a base; validar consentimento; preparar rascunho; revisar manualmente; só então enviar manualmente.
