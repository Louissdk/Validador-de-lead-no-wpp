# Operação segura e conformidade

Este documento é operacional, não uma opinião jurídica. Antes do envio real, confirme a base legal e a transparência do tratamento com profissional competente.

- Registre origem, data/hora e forma de consentimento para cada contato que receberá marketing no WhatsApp.
- Não converta automaticamente listas compradas, raspadas ou telefones públicos em opt-in.
- Identifique a empresa e o motivo do contato; não se passe por outra pessoa.
- Dê uma forma simples de interromper contatos. O webhook reconhece palavras como SAIR, PARAR, PARE, CANCELAR, STOP, REMOVER e NÃO QUERO.
- Não envie a contatos que responderam, pediram descadastro ou constam na supressão.
- O modo navegador assistido só prepara um rascunho para um contato elegível; o programa não clica em Enviar.
- Não tente contornar limites, bloqueios ou políticas da plataforma, nem use automação para ocultar sua natureza.
- Restrinja acesso ao banco, segredos, logs e backups.
- Nunca suba dados pessoais reais, credenciais ou o perfil do navegador para o GitHub.
- Configure webhooks com HTTPS e segredo aleatório longo; não exponha PostgreSQL ou Redis à internet.
- Se o formato do webhook não corresponder à versão instalada da Evolution API, interrompa e valide o payload em homologação.

- O planejador de planilha deixa a mensagem de WhatsApp vazia quando não há opt-in documentado.
- A origem de um número público não é evidência de consentimento para receber mensagens pelo WhatsApp.
