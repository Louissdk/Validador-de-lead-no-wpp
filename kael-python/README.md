# KAEL / Visível — robô de leads

Esta pasta é o espaço versionado do robô de qualificação, consolidação e planejamento de leads. O código-fonte, os testes e a documentação ficam aqui; credenciais e dados operacionais permanecem locais.

## Primeira sincronização do projeto atual

No Windows, clone este repositório e execute a sincronização a partir da pasta clonada. O script copia o projeto local para esta pasta, roda os testes e só publica um commit se você passar a opção de commit.

    git clone https://github.com/Louissdk/Validador-de-lead-no-wpp.git
    cd Validador-de-lead-no-wpp
    py -3 .\kael-python\sync_from_local.py --source "$HOME\Downloads\robo python\visivel-kael-python" --commit

O caminho de origem pode ser diferente. Informe o caminho local correto com --source.

## Executar em outra máquina

Depois que o código completo estiver sincronizado, clone este repositório em qualquer computador com Git e Python instalado. Abra a pasta kael-python e prepare o ambiente.

    cd kael-python
    py -3 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -r requirements.txt
    Copy-Item .env.example .env
    py -3 -m pytest -q
    py -3 -m app --help

Preencha o arquivo local .env com a configuração do seu ambiente e coloque o arquivo de credenciais Google no caminho definido por GOOGLE_CREDS_FILE. Não compartilhe esses arquivos. Eles não fazem parte do repositório.

## Sincronização automática no Windows

Para manter a pasta `kael-python` do GitHub sincronizada com o projeto local sem executar o comando manualmente a cada alteração:

1. Atualize o clone do repositório para receber os scripts mais recentes:

       Set-Location "$HOME\Downloads\Validador-de-lead-no-wpp"
       git pull origin main

2. Instale a tarefa agendada:

       Set-Location "$HOME\Downloads\Validador-de-lead-no-wpp"
       .\kael-python\install_auto_sync.ps1

Por padrão, a origem monitorada para publicação é `$HOME\Downloads\robo python\visivel-kael-python`. Se o projeto estiver em outro local, use o parâmetro `-Source` com o caminho correto.

A tarefa tenta sincronizar a cada 30 minutos enquanto a sessão do Windows estiver aberta. Ela copia apenas os caminhos permitidos pelo sincronizador, roda `pytest` e faz commit/push somente se os testes passarem. O Git do computador precisa estar autenticado no GitHub para que o push funcione. Se o computador estiver desligado ou sem sessão disponível, a execução fica para quando a tarefa puder rodar novamente.

Os logs ficam fora do repositório em `%LOCALAPPDATA%\KAEL\logs`. Para conferir a última execução:

    Get-ScheduledTaskInfo -TaskName "KAEL GitHub Auto Sync"

Para remover a tarefa automática:

    Unregister-ScheduledTask -TaskName "KAEL GitHub Auto Sync" -Confirm:$false

Isso sincroniza **código**, não executa o worker de leads nem atualiza a planilha Google. A execução operacional do KAEL continua sendo uma ação separada.

## Operação segura

- A execução de planejamento e a gravação são operações distintas. Confira o comando e a aba de destino antes de usar --write.
- O worker usa staging, verificação e backup na promoção da aba de saída.
- Mensagens comerciais continuam restritas às regras de autorização do projeto. Não habilite envio real por acidente.
- O arquivo de estado SQLite é local. Não sincronize a pasta data, relatórios de leads, credenciais nem arquivos .env.

## Atualizar o código publicado manualmente

Depois de alterar o projeto de origem, execute novamente:

    py -3 .\kael-python\sync_from_local.py --source "CAMINHO\PARA\visivel-kael-python" --commit

O sincronizador copia arquivos permitidos, exclui dados locais e backups, executa pytest e faz commit/push apenas quando os testes passam. O workflow do GitHub executa os testes novamente a cada alteração publicada na pasta.

## O que é sincronizado

Código Python em app, testes, documentação, ferramentas de manutenção selecionadas e arquivos de configuração necessários ao projeto. O README original do projeto de origem é preservado em docs/README_UPSTREAM.md para não substituir estas instruções.

## Arquivos que nunca devem ser publicados

.env, credentials.json, chaves privadas, bancos SQLite, estado local, relatórios CSV/Excel de leads, logs, caches, pastas .venv e arquivos de backup.
