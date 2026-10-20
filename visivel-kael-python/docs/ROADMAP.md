# Roadmap

## MVP incluído
- [x] Importação CSV e normalização de telefones
- [x] Consentimento explícito e supressão
- [x] PostgreSQL com fila persistente
- [x] Mensagem inicial e follow-ups escalonados via worker
- [x] Worker com dry-run por padrão
- [x] Adaptador opcional para Evolution API
- [x] Webhook para resposta e opt-out
- [x] Limite diário, horário comercial e rate limit distribuído
- [x] Modo navegador assistido de contato único, sem envio automático
- [x] Docker Compose e testes unitários

## Próximas iterações
- [ ] Painel web para leads, campanhas, aprovação e métricas
- [ ] Integração incremental com Google Sheets
- [ ] Score 0–100 com critérios explicáveis e aprovação humana
- [ ] Dashboard de entregas, respostas, descadastros, falhas e conversão
- [ ] Testes de integração com payloads reais sem disparos externos
- [ ] Migrações de banco, backups testados e observabilidade
- [ ] Aprovação de mensagens e histórico de versões dos templates
