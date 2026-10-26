from app.lead_worker import _plan_consolidated_values


HEADERS = [
    "position", "title", "address", "type", "phoneNumber",
    "website", "email", "rating", "consent_status", "consent_source", "consent_at", "respondeu",
]


def source_row(position, title, address, phone, **extra):
    row = {
        "position": str(position),
        "title": title,
        "address": address,
        "type": "pizzaria",
        "phoneNumber": phone,
        "website": "",
        "email": "",
        "rating": "4.7",
        "consent_status": "opt_in",
        "consent_source": "formulario_site",
        "consent_at": "2026-10-15",
    }
    row.update(extra)
    return [row.get(header, "") for header in HEADERS]


def test_worker_only_prepares_message_for_unambiguous_documented_opt_in():
    values = [
        HEADERS,
        source_row(1, "Pizzaria Exemplo", "Rua A, 10", "11999998888"),
    ]

    planned, stats = _plan_consolidated_values(values)

    assert len(planned) == 1
    assert planned[0]["status_consolidacao_v5"] == "SEM_DUPLICATA_EXATA_IDENTIFICADA"
    assert planned[0]["mensagem_completa_copiar_colar"]
    assert stats["com_mensagem"] == 1
    assert stats["revisao_consolidacao"] == 0


def test_worker_blocks_copy_ready_message_for_duplicate_candidate():
    values = [
        HEADERS,
        source_row(1, "Pizzaria Exemplo", "Rua A, 10", "11999998888"),
        source_row(2, "PIZZARIA EXEMPLO", "Rua A, 10", "11999998888"),
    ]

    planned, stats = _plan_consolidated_values(values)

    assert len(planned) == 1
    assert planned[0]["status_consolidacao_v5"] == "DUPLICATA_CANDIDATA"
    assert planned[0]["status_revisao"] == "REVISAR_CONSOLIDACAO"
    assert planned[0]["mensagem_completa_copiar_colar"] == ""
    assert stats["mensagens_bloqueadas_por_identidade"] == 1
    assert stats["com_mensagem"] == 0


def test_worker_keeps_missing_phone_group_as_review_row():
    values = [
        HEADERS,
        source_row(1, "Pizzaria Sem Telefone", "Rua B, 20", ""),
    ]

    planned, stats = _plan_consolidated_values(values)

    assert len(planned) == 1
    assert planned[0]["telefone"] == ""
    assert planned[0]["status_revisao"] == "SEM_TELEFONE_NA_ORIGEM"
    assert planned[0]["mensagem_completa_copiar_colar"] == ""
    assert stats["sem_telefone"] == 1


def test_worker_preserves_opt_out_even_when_identity_is_ambiguous():
    values = [
        HEADERS,
        source_row(
            1, "Pizzaria Exemplo", "Rua A, 10", "11999998888",
            consent_status="opt_out", consent_source="pedido_descadastro", consent_at="2026-10-16",
        ),
        source_row(
            2, "PIZZARIA EXEMPLO", "Rua A, 10", "11999998888",
            consent_status="opt_out", consent_source="pedido_descadastro", consent_at="2026-10-16",
        ),
    ]

    planned, stats = _plan_consolidated_values(values)

    assert planned[0]["status_revisao"] == "BLOQUEADO_DESCADASTRO"
    assert planned[0]["status_autorizacao_whatsapp"] == "opt_out"
    assert planned[0]["mensagem_completa_copiar_colar"] == ""


def test_one_cycle_caches_local_plan_and_does_not_touch_output_in_simulation(monkeypatch):
    import sqlite3
    import app.sheets_planner as planner
    from app.lead_worker import _init_state, _one_cycle

    values = [
        HEADERS,
        source_row(1, "Pizzaria Exemplo", "Rua A, 10", "11999998888"),
    ]

    class FakeSourceWorksheet:
        def get_all_values(self):
            return values

    class FakeSpreadsheet:
        def __init__(self):
            self.output_accesses = 0

        def worksheet(self, title):
            if title == "source":
                return FakeSourceWorksheet()
            self.output_accesses += 1
            raise AssertionError("A simulação não deve abrir a aba de saída.")

    spreadsheet = FakeSpreadsheet()
    monkeypatch.setattr(planner, "_connect_sheet", lambda: spreadsheet)
    state = sqlite3.connect(":memory:")
    state.execute("CREATE TABLE worker_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    try:
        first = _one_cycle(
            source_tab="source", output_tab="output", limit=0,
            write=False, state=state, confirm_overwrite=False,
        )
        second = _one_cycle(
            source_tab="source", output_tab="output", limit=0,
            write=False, state=state, confirm_overwrite=False,
        )
    finally:
        state.close()

    assert first["source_changed_since_last_checkpoint"] is True
    assert second["source_changed_since_last_checkpoint"] is False
    assert first["stats"]["com_mensagem"] == 1
    assert spreadsheet.output_accesses == 0


def test_existing_worker_output_requires_explicit_confirmation():
    import sqlite3
    from app.lead_worker import WorkerStopError, _init_state, _write_matrix
    import pytest

    class ExistingWorksheet:
        updated = False

        def get_all_values(self):
            return [["telefone", "nome"]]

        def resize(self, **kwargs):
            self.updated = True

        def update(self, **kwargs):
            self.updated = True

    worksheet = ExistingWorksheet()

    class FakeSpreadsheet:
        def worksheet(self, title):
            return worksheet

    state = sqlite3.connect(":memory:")
    state.execute("CREATE TABLE worker_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    try:
        with pytest.raises(WorkerStopError, match="--confirm-overwrite"):
            _write_matrix(FakeSpreadsheet(), "output", [], state, False)
    finally:
        state.close()

    assert worksheet.updated is False



def test_worker_creates_internal_draft_when_authorization_unknown():
    values = [
        HEADERS,
        source_row(1, "Pizzaria Exemplo", "Rua A, 10", "11999998888",
                   consent_status="unknown", consent_source="", consent_at=""),
    ]
    planned, stats = _plan_consolidated_values(values)
    row = planned[0]
    assert row["mensagem_completa_copiar_colar"] == ""
    assert row["rascunho_comercial_interno"]
    assert "RASCUNHO INTERNO" in row["rascunho_comercial_interno"]
    assert "NÃO COPIAR/ENVIAR" in row["rascunho_comercial_interno"]
    assert row["status_rascunho_interno"] == "REVISAR_AUTORIZACAO_ANTES_DE_CONTATO"
    assert stats["com_mensagem"] == 0
    assert stats["rascunhos_internos"] == 1


def test_worker_internal_draft_on_ambiguous_identity_is_not_copy_ready():
    values = [
        HEADERS,
        source_row(1, "Pizzaria Exemplo", "Rua A, 10", "11999998888",
                   consent_status="unknown", consent_source="", consent_at=""),
        source_row(2, "PIZZARIA EXEMPLO", "Rua A, 10", "11999998888",
                   consent_status="unknown", consent_source="", consent_at=""),
    ]
    planned, stats = _plan_consolidated_values(values)
    row = planned[0]
    assert row["status_consolidacao_v5"] == "DUPLICATA_CANDIDATA"
    assert row["mensagem_completa_copiar_colar"] == ""
    assert row["rascunho_comercial_interno"]
    assert row["status_rascunho_interno"] == "REVISAR_IDENTIDADE_E_AUTORIZACAO"
    assert stats["com_mensagem"] == 0
    assert stats["rascunhos_revisar_identidade"] == 1


def test_worker_never_creates_internal_draft_for_opt_out():
    values = [
        HEADERS,
        source_row(1, "Pizzaria Exemplo", "Rua A, 10", "11999998888",
                   consent_status="opt_out", consent_source="pedido_descadastro", consent_at="2026-10-16"),
    ]
    planned, _ = _plan_consolidated_values(values)
    row = planned[0]
    assert row["status_autorizacao_whatsapp"] == "opt_out"
    assert row["mensagem_completa_copiar_colar"] == ""
    assert row["rascunho_comercial_interno"] == ""
    assert row["status_rascunho_interno"] == "BLOQUEADO_DESCADASTRO"


def test_worker_can_prepare_internal_draft_without_phone_but_not_copy_ready():
    values = [
        HEADERS,
        source_row(1, "Pizzaria Sem Telefone", "Rua B, 20", "",
                   consent_status="unknown", consent_source="", consent_at=""),
    ]
    planned, stats = _plan_consolidated_values(values)
    row = planned[0]
    assert row["telefone"] == ""
    assert row["mensagem_completa_copiar_colar"] == ""
    assert row["rascunho_comercial_interno"]
    assert row["status_rascunho_interno"] == "REVISAR_TELEFONE_E_AUTORIZACAO"
    assert stats["rascunhos_revisar_telefone"] == 1



def test_worker_does_not_create_internal_draft_when_missing_phone_and_reply_exists():
    values = [
        HEADERS,
        source_row(1, "Pizzaria Respondeu", "Rua C, 30", "",
                   consent_status="unknown", consent_source="", consent_at="",
                   respondeu="sim"),
    ]
    planned, _ = _plan_consolidated_values(values)
    row = planned[0]
    assert row["status_revisao"] == "BLOQUEADO_JA_RESPONDEU"
    assert row["rascunho_comercial_interno"] == ""
    assert row["status_rascunho_interno"] == "BLOQUEADO_JA_RESPONDEU"


def test_worker_staged_write_keeps_output_intact_after_partial_failure(monkeypatch):
    import sqlite3
    import pytest
    import app.lead_worker as worker

    headers = worker.OUTPUT_HEADERS + worker.WORKER_EXTRA_HEADERS
    width = len(headers)
    old_values = [headers, ["DADO_ANTIGO" for _ in headers]]

    class WorksheetNotFound(Exception):
        pass

    class FakeWorksheet:
        def __init__(self, title, sheet_id, rows=2, cols=2, values=None):
            self.title = title
            self.id = sheet_id
            self.row_count = rows
            self.col_count = cols
            self.values = [list(row) for row in (values or [])]
            self.update_calls = 0

        def get_all_values(self):
            return [list(row) for row in self.values]

        def resize(self, rows, cols):
            self.row_count = rows
            self.col_count = cols
            self.values = [row[:cols] + [""] * max(0, cols - len(row)) for row in self.values[:rows]]
            while len(self.values) < rows:
                self.values.append([""] * cols)

        def update(self, *, range_name, values, value_input_option):
            self.update_calls += 1
            if self.title.startswith("__KAEL_STAGE_") and self.update_calls == 2:
                raise RuntimeError("simulated second-chunk failure")
            start_row = int(range_name.split(":", 1)[0][1:])
            for offset, row in enumerate(values):
                index = start_row - 1 + offset
                while len(self.values) <= index:
                    self.values.append([""] * self.col_count)
                self.values[index] = list(row) + [""] * max(0, self.col_count - len(row))

    class FakeSpreadsheet:
        def __init__(self):
            self.by_title = {}
            self.by_id = {}
            self.next_id = 10
            self.original = FakeWorksheet("output", 1, rows=2, cols=width, values=old_values)
            self.by_title[self.original.title] = self.original
            self.by_id[self.original.id] = self.original

        def worksheet(self, title):
            if title not in self.by_title:
                raise WorksheetNotFound(title)
            return self.by_title[title]

        def add_worksheet(self, *, title, rows, cols):
            if title in self.by_title:
                raise RuntimeError(f"worksheet already exists: {title}")
            ws = FakeWorksheet(title, self.next_id, rows=rows, cols=cols)
            self.next_id += 1
            self.by_title[title] = ws
            self.by_id[ws.id] = ws
            return ws

        def del_worksheet(self, worksheet):
            self.by_title.pop(worksheet.title, None)
            self.by_id.pop(worksheet.id, None)

        def batch_update(self, body):
            moves = []
            moving_ids = set()
            for request in body["requests"]:
                details = request["updateSheetProperties"]
                sheet_id = int(details["properties"]["sheetId"])
                title = details["properties"]["title"]
                moves.append((self.by_id[sheet_id], title))
                moving_ids.add(sheet_id)
            for _worksheet, new_title in moves:
                collision = self.by_title.get(new_title)
                if collision is not None and collision.id not in moving_ids:
                    raise RuntimeError(f"title collision: {new_title}")
            for worksheet, _new_title in moves:
                self.by_title.pop(worksheet.title, None)
            for worksheet, new_title in moves:
                worksheet.title = new_title
                self.by_title[new_title] = worksheet

    matrix = [headers] + [
        [f"r{row_index}_c{column_index}" for column_index in range(width)]
        for row_index in range(260)
    ]
    planned_rows = [{} for _ in range(260)]
    monkeypatch.setattr(worker, "_merge_worker_rows", lambda old, planned: planned)
    monkeypatch.setattr(worker, "_prepare_matrix", lambda merged: matrix)

    spreadsheet = FakeSpreadsheet()
    original_sheet = spreadsheet.original
    before = original_sheet.get_all_values()
    state = sqlite3.connect(":memory:")
    state.execute("CREATE TABLE worker_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    try:
        with pytest.raises(RuntimeError, match="simulated second-chunk failure"):
            worker._write_matrix(spreadsheet, "output", planned_rows, state, True)
    finally:
        state.close()

    assert spreadsheet.worksheet("output") is original_sheet
    assert original_sheet.get_all_values() == before
    assert not any(title.startswith("__KAEL_STAGE_") for title in spreadsheet.by_title)
    assert not any(title.startswith("__KAEL_BACKUP_") for title in spreadsheet.by_title)

