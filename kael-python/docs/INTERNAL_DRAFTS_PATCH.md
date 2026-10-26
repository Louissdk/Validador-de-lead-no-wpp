# KAEL — rascunhos internos separados de mensagens autorizadas

## Objetivo

Adicionar ao `lead-worker` os campos `rascunho_comercial_interno` e `status_rascunho_interno`. O rascunho interno é material de planejamento, não vai para `mensagem_completa_copiar_colar`, não representa autorização e inclui um aviso explícito para não copiar/enviar.

O fluxo continua sem envio automático de WhatsApp. Opt-out e resposta anterior nunca recebem rascunho. Campos ambíguos recebem status de revisão de identidade. Leads sem telefone podem receber rascunho interno, mas continuam bloqueados para contato.

## Instalação

1. Extraia o ZIP na raiz do projeto `visivel-kael-python`, mantendo as pastas `tools` e `payload` do pacote.
2. No PowerShell, execute:

   ```powershell
   python .\tools\install_internal_draft_patch.py
   ```

3. Valide:

   ```powershell
   python -m pytest -q
   ```

4. Teste um ciclo sem escrita:

   ```powershell
   python -m app lead-worker --once --limit 100 --source-tab kael_revalidacao_v2
   ```

O cache passa a usar uma chave versionada para não reutilizar o checkpoint antigo sem os campos novos. Não use `--write` nem `--confirm-overwrite` durante a validação.

## Campos adicionados na saída gerenciada

- `rascunho_comercial_interno`: texto preliminar com aviso interno.
- `status_rascunho_interno`: por exemplo `REVISAR_AUTORIZACAO_ANTES_DE_CONTATO`, `REVISAR_IDENTIDADE_E_AUTORIZACAO`, `REVISAR_TELEFONE_E_AUTORIZACAO` ou bloqueios de opt-out/resposta.

`mensagem_completa_copiar_colar` continua vazio sempre que não houver opt-in documentado e identidade clara. O patch não converte `unknown` em `opt_in` e não altera a origem.
