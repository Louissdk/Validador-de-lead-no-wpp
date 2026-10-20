# Visível / KAEL — Robô Python de prospecção

Base inicial do robô comercial da Visível. Organiza leads, gerencia campanhas controladas e oferece um modo assistido que abre o WhatsApp Web e preenche um rascunho para revisão humana.

**Segurança por padrão:** o worker usa dry_run; nenhum envio real ocorre por padrão. O modo de navegador trabalha com um único contato por execução, exige opt-in registrado e nunca clica em Enviar. A pessoa revisa e envia manualmente. Não foi implementado modo furtivo para imitar pessoas ou contornar os controles da plataforma.

## Componentes

- Importação CSV com normalização de telefone e deduplicação.
- PostgreSQL como base de leads, consentimento, campanhas, fila e eventos.
- Redis para controle de frequência do worker da Evolution API.
- Campanhas pausadas por padrão e cadência configurável (exemplo D+1, D+3, D+5, D+7 e D+10).
- Integração isolada com Evolution API (quando disponível).
- **Modo navegador assistido:** Chromium visível via Playwright, sessão local persistente, validação de opt-in, preenchimento de rascunho e confirmação manual.
- Webhook para registrar respostas e opt-outs quando a Evolution API estiver em uso.
- Docker Compose, documentação, testes unitários e GitHub Actions.

## Início rápido com Docker

1. Copie .env.example para .env e mantenha SEND_MODE=dry_run.
2. Revise as senhas locais em .env.
3. Inicie o PostgreSQL: docker compose up -d db.
4. Inicialize o banco: docker compose run --rm app python -m app init-db.

Não versione data/leads.csv, perfis de navegador, .env ou qualquer dado pessoal real.

## Modo assistido do WhatsApp Web (não precisa da Evolution API)

O modo navegador é executado **no computador local**, não dentro do container. Ele abre uma janela Chromium separada e mantém a sessão em data/whatsapp-profile/; na primeira execução, escaneie o QR code com seu WhatsApp. Essa pasta contém dados sensíveis de sessão e é ignorada pelo Git.

No PowerShell, dentro da pasta visivel-kael-python:

    py -m venv .venv
    .venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    playwright install chromium

Inicie o banco com docker compose up -d db e inicialize-o se ainda não tiver feito isso. Aponte a CLI local para a porta publicada do PostgreSQL, usando a mesma senha do .env:

    $env:DATABASE_URL = "postgresql://kael:SUA_SENHA@localhost:5433/kael"

Importe uma base com os campos de consentimento documentados e confirme que o contato está elegível. O telefone deve existir no banco com consent_status=opt_in, consent_source e consent_at preenchidos, estar ativo, não estar na supressão e não ter resposta registrada.

Abra um rascunho individual:

    python -m app browser-draft --phone "5511999999999" --message "Olá {nome}, sou da Visível. Posso compartilhar uma ideia para a {empresa}?"

O robô abre o chat e preenche o campo de mensagem. **Ele não envia a mensagem.** Confira nome, número, contexto e texto; clique manualmente em Enviar somente se estiver correto. Pressione Enter no terminal depois de concluir para fechar a janela. Ctrl+C cancela o comando.

A interface do WhatsApp Web pode mudar, e o seletor do campo de texto pode precisar de atualização. O modo assistido não é uma integração oficial nem garante compatibilidade permanente. Não tente contornar bloqueios ou limites do WhatsApp.

## Modo Evolution API (quando estiver disponível)

Configure EVOLUTION_BASE_URL, EVOLUTION_API_KEY e EVOLUTION_INSTANCE. O envio real exige SEND_MODE=live, ALLOW_MARKETING_SENDS=true e LIVE_SEND_CONFIRMATION=I_UNDERSTAND_AND_HAVE_PERMISSION. Faça homologação antes de usar produção.

## Formato do CSV

Colunas: nome, empresa, telefone, nicho, cidade, consent_status, consent_source, consent_at.

consent_status aceita opt_in, opt_out ou unknown. Se faltarem a origem ou a data do opt-in, o registro volta para unknown. A importação não interpreta telefone válido como consentimento.

## Comandos CLI

- python -m app init-db
- python -m app import-csv data/leads.csv
- python -m app campaign-create --name "Campanha" --messages config/messages.example.json
- python -m app campaign-enroll --campaign-id 1
- python -m app queue-preview --campaign-id 1
- python -m app campaign-activate --campaign-id 1
- python -m app browser-draft --phone "5511999999999" --message "Olá {nome}"
- python -m app worker --once
- python -m app worker
- python -m app serve --host 127.0.0.1 --port 8000

## Testes

Execute pytest -q. Os testes unitários não precisam de credenciais nem de mensagens externas.

Consulte docs/ARQUITETURA.md, docs/OPERACAO_SEGURA.md e docs/ROADMAP.md.
