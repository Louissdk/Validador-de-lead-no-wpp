# KAEL lead-worker local (Windows)

Este trabalhador reutiliza o consolidador e o planejador existentes. Ele não envia mensagens pelo WhatsApp nem chama a Evolution API. Mensagens só são incluídas no planejamento quando o lead passa pelos bloqueios existentes de autorização; grupos ambíguos de consolidação ficam sem mensagem pronta para copiar e colar.

## Instalação do patch

1. Extraia o conteúdo do pacote na raiz de `visivel-kael-python`, mantendo as pastas `app`, `tests`, `tools` e `docs`.
2. No PowerShell, dentro da pasta do projeto, execute:

   ```powershell
   python .\tools\install_lead_worker_patch.py
   ```

3. O instalador cria backups de `app\cli.py` e `.gitignore` antes de alterá-los.
4. Valide:

   ```powershell
   python -m pytest -q
   python -m app lead-worker --help
   ```

## Primeiro piloto — somente leitura

O intervalo padrão é de 300 segundos (5 minutos). A amostra limitada só é permitida em uma execução única sem escrita:

```powershell
python -m app lead-worker --once --limit 100 --source-tab kael_revalidacao_v2
```

Esse comando lê apenas o cabeçalho e até 100 registros, executa a consolidação em memória e salva um checkpoint local em `data\kael_lead_worker.sqlite3`. Ele não escreve na planilha.

Depois, rode a base inteira em simulação uma única vez:

```powershell
python -m app lead-worker --once --source-tab kael_revalidacao_v2
```

A simulação prepara e salva o resultado desejado localmente; ela não escreve a aba de saída.

## Ativar a atualização contínua da planilha

O comando abaixo é deliberadamente separado da simulação e habilita escrita somente na aba gerenciada `kael_planejamento_continuo`:

```powershell
python -m app lead-worker --write --source-tab kael_revalidacao_v2 --output-tab kael_planejamento_continuo --interval-seconds 300
```

Se a aba de saída já existir e não tiver sido criada/autorizada pelo próprio trabalhador, o comando para e exige confirmação explícita:

```powershell
python -m app lead-worker --write --once --source-tab kael_revalidacao_v2 --output-tab kael_planejamento_continuo --confirm-overwrite
```

`--confirm-overwrite` deve ser usado somente depois de conferir o conteúdo da aba existente. O trabalhador atualiza em lotes de até 250 linhas, guarda o checkpoint de origem no SQLite local e não marca uma sincronização como concluída até todas as atualizações terminarem.

## Operação e segurança

- `--write` significa gravar a aba de planejamento; **não** significa enviar WhatsApp.
- Nunca inicie `python -m app worker` para esse propósito: o `worker` original processa a fila de mensagens e pode enviar pela Evolution API se as travas de envio real estiverem ativadas.
- O `lead-worker` exige intervalo de pelo menos 60 segundos; padrão de 300 segundos.
- O arquivo SQLite e o lock estão excluídos do Git por `.gitignore`.
- Uma única instância do lead-worker é permitida por arquivo de estado.
- Mantenha o computador ligado, conectado e sem suspensão para operação contínua. O Agendador de Tarefas do Windows deve ser configurado só após validar o piloto e revisar a aba de saída.
- A origem nunca é escrita. A aba de saída é separada e o nome padrão é `kael_planejamento_continuo`.
