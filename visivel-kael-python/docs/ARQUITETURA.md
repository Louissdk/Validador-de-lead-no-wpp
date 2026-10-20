# Arquitetura do MVP

- CLI: inicialização do banco, importação CSV e modo individual assistido pelo navegador.
- PostgreSQL: leads, consentimento, campanhas, fila, supressões, eventos e fila de revisão de rascunhos.
- Central local em /drafts: prepara mensagens com um modelo, filtra por nicho/cidade, pagina resultados, edita, aprova/rejeita e exporta rascunhos aprovados para CSV.
- A aprovação da Central nunca envia mensagens. Ela só habilita um link para abrir o WhatsApp Web com texto pré-preenchido para revisão e envio manual.
- Worker: integra opcionalmente com Evolution API; dry_run é o padrão.
- FastAPI: Central de revisão local, health check e webhook de mensagens da Evolution API.

Segurança:
1. Leads sem opt-in documentado não podem receber rascunho elegível.
2. Respostas registradas e contatos na supressão são excluídos.
3. Mensagens com campos personalizados vazios não são geradas.
4. A aprovação é revalidada no backend.
5. A Central deve permanecer local; não expor à internet sem autenticação e proteção de formulários.
6. Nenhum dado pessoal real, credencial ou perfil do navegador deve ir para o GitHub.
