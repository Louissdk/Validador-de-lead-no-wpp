# Visível / KAEL — Robô Python de prospecção

Base inicial do robô comercial da Visível. Organiza leads, gerencia campanhas controladas no WhatsApp via Evolution API e interrompe a cadência quando há resposta ou pedido de descadastro.

SEGURANÇA POR PADRÃO: o modo inicial é dry_run; nenhuma mensagem real é enviada. Envio real exige habilitação explícita, campanha ativa e leads com consentimento documentado (consent_status=opt_in). Respeite a LGPD, os termos da plataforma e pedidos de não contato.

## Componentes

- Importação CSV com normalização de telefone e deduplicação.
- PostgreSQL como banco operacional e Redis como limitador distribuído.
- Campanhas criadas pausadas; ativação manual por CLI.
- Mensagem inicial e cadência configurável (exemplo D+1, D+3, D+5, D+7 e D+10).
- Fila persistente, controle de tentativas e limite diário.
- Integração isolada com Evolution API.
- Webhook para resposta, opt-out e cancelamento da cadência.
- FastAPI com health check e webhook protegido.
- Docker Compose, workflow de testes e testes unitários.

## Início rápido com Docker

1. Copie .env.example para .env e mantenha SEND_MODE=dry_run.
2. Revise DATABASE_URL, POSTGRES_PASSWORD, WEBHOOK_SECRET e as configurações da Evolution API.
3. Execute primeiro os serviços de dados: docker compose up --build -d db redis
4. Inicialize o banco: docker compose run --rm app python -m app init-db
5. Inicie o worker e a API: docker compose up -d app api
6. Copie data/leads.example.csv para data/leads.csv e preencha sua própria base.
7. Importe a base: docker compose exec app python -m app import-csv data/leads.csv

Não versione data/leads.csv nem qualquer dado pessoal real.

## Formato de CSV

Colunas: nome, empresa, telefone, nicho, cidade, consent_status, consent_source, consent_at.

consent_status aceita opt_in, opt_out ou unknown. Sem coluna, assume unknown. Para opt_in, consent_source e consent_at também são obrigatórios; se faltarem, o registro volta a unknown. A importação não considera telefone válido como consentimento.

## Criar campanha

1. Copie config/messages.example.json para config/messages.json e revise os textos.
2. Crie a campanha: python -m app campaign-create --name "Diagnóstico inicial" --messages config/messages.json
3. Enfileire leads elegíveis: python -m app campaign-enroll --campaign-id 1
4. Confira a fila: python -m app queue-preview --campaign-id 1
5. Ative após revisão: python -m app campaign-activate --campaign-id 1
6. Rode o worker em dry_run e verifique os logs.

## Habilitar envio real

Só depois de validar a base, o consentimento, a instância e os textos, configure EVOLUTION_BASE_URL, EVOLUTION_API_KEY e EVOLUTION_INSTANCE; defina SEND_MODE=live, ALLOW_MARKETING_SENDS=true e LIVE_SEND_CONFIRMATION=I_UNDERSTAND_AND_HAVE_PERMISSION em um ambiente protegido; configure um WEBHOOK_SECRET aleatório forte; e mantenha limite diário baixo durante o piloto.

O envio real fica bloqueado se alguma dessas condições estiver ausente. Não use listas compradas ou raspadas sem base legal e validação específica. Não tente contornar limites ou bloqueios da plataforma.

## Comandos úteis

- python -m app init-db
- python -m app import-csv data/leads.csv
- python -m app campaign-create --name "Campanha" --messages config/messages.json
- python -m app campaign-enroll --campaign-id 1
- python -m app queue-preview --campaign-id 1
- python -m app campaign-activate --campaign-id 1
- python -m app worker --once
- python -m app worker
- python -m app serve --host 0.0.0.0 --port 8000

## Testes

Instale requirements.txt em Python 3.11+ e execute pytest -q. Os testes unitários não precisam de credenciais nem de envio externo.

Consulte docs/ARQUITETURA.md, docs/OPERACAO_SEGURA.md e docs/ROADMAP.md.

## Limitações do MVP

Ainda não há painel visual, integração incremental com Google Sheets, score de leads ou dashboard de conversão. Valide o payload do webhook na sua versão de Evolution API antes de produção. Entrega não significa leitura ou conversão.