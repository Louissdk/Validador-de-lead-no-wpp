# Arquitetura do MVP

- CLI: inicializa banco, importa CSV, cria/ativa campanhas e pré-visualiza a fila.
- PostgreSQL: fonte de verdade para leads, consentimento, campanhas, fila, supressões e eventos.
- Worker: reivindica tarefas com FOR UPDATE SKIP LOCKED, valida consentimento/resposta/supressão, horário comercial, teto diário e rate limit Redis.
- Evolution API: adaptador isolado de envio; simulação não chama API externa.
- FastAPI: health check e webhook protegido; registra respostas e opt-outs.
- Redis: lock distribuído de envio. Não é a única cópia de dado algum.

Decisões:
1. Todas as campanhas nascem pausadas.
2. Telefone válido não significa consentimento.
3. Resposta recebida cancela a cadência pendente.
4. Pedido de opt-out cria supressão durável.
5. dry_run é o modo padrão.
6. Envio real requer confirmação de ambiente explícita.

Fluxo: importar e auditar a base; criar a campanha; enfileirar apenas leads elegíveis; pré-visualizar os textos; ativar; simular; executar piloto real apenas depois da validação humana.
