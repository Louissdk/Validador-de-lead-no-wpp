# Operação segura e conformidade

Este documento é operacional, não uma opinião jurídica. Antes do envio real, confirme a base legal e a transparência do tratamento com profissional competente.

- Registre origem, data/hora e forma de consentimento para cada contato que receberá marketing no WhatsApp.
- Não converta automaticamente listas compradas, raspadas ou telefones públicos em opt-in.
- Identifique a empresa e o motivo do contato; nunca se passe por outra pessoa.
- Dê uma forma simples de interromper contatos. O projeto reconhece SAIR, PARAR, PARE, CANCELAR, STOP, REMOVER e NÃO QUERO.
- Não envie a contatos que responderam, pediram descadastro ou constam na supressão.
- Comece com amostra pequena, limite diário baixo e monitoramento.
- Não tente contornar limites, bloqueios ou políticas do WhatsApp.
- Restrinja acesso ao banco, segredos, logs e backups.
- Nunca suba dados pessoais reais para o GitHub.
- Configure o webhook com HTTPS e segredo aleatório longo; não exponha PostgreSQL ou Redis à internet.
- Se o webhook tiver formato diferente da versão instalada da Evolution API, interrompa o envio e valide o payload em homologação.
