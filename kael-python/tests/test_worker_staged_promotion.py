from __future__ import annotations

import sqlite3

import pytest

import app.lead_worker as worker


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
        self.values = [
            row[:cols] + [""] * max(0, cols - len(row))
            for row in self.values[:rows]
        ]
        while len(self.values) < rows:
            self.values.append([""] * cols)

    def update(self, *, range_name, values, value_input_option):
        self.update_calls += 1
        start_row = int(range_name.split(":", 1)[0][1:])
        for offset, row in enumerate(values):
            index = start_row - 1 + offset
            while len(self.values) <= index:
                self.values.append([""] * self.col_count)
            row_values = list(row)
            self.values[index] = row_values + [""] * max(0, self.col_count - len(row_values))


class FakeSpreadsheet:
    def __init__(self, old_values=None, promotion_failure=None):
        self.by_title = {}
        self.by_id = {}
        self.next_id = 10
        self.promotion_failure = promotion_failure
        self.original = None
        if old_values is not None:
            self.original = FakeWorksheet(
                "output", 1,
                rows=max(2, len(old_values)),
                cols=len(old_values[0]),
                values=old_values,
            )
            self.by_title[self.original.title] = self.original
            self.by_id[self.original.id] = self.original

    def worksheet(self, title):
        if title not in self.by_title:
            raise WorksheetNotFound(title)
        return self.by_title[title]

    def add_worksheet(self, *, title, rows, cols):
        if title in self.by_title:
            raise RuntimeError(f"worksheet already exists: {title}")
        sheet = FakeWorksheet(title, self.next_id, rows=rows, cols=cols)
        self.next_id += 1
        self.by_title[title] = sheet
        self.by_id[sheet.id] = sheet
        return sheet

    def del_worksheet(self, worksheet):
        self.by_title.pop(worksheet.title, None)
        self.by_id.pop(worksheet.id, None)

    def batch_update(self, body):
        if self.promotion_failure == "before":
            raise RuntimeError("simulated promotion failure before apply")

        moves = []
        moving_ids = set()
        for request in body["requests"]:
            details = request["updateSheetProperties"]
            sheet_id = int(details["properties"]["sheetId"])
            title = details["properties"]["title"]
            moves.append((self.by_id[sheet_id], title))
            moving_ids.add(sheet_id)

        for _sheet, new_title in moves:
            collision = self.by_title.get(new_title)
            if collision is not None and collision.id not in moving_ids:
                raise RuntimeError(f"title collision: {new_title}")

        # Simulate the atomic API request by applying all renames together.
        for sheet, _new_title in moves:
            self.by_title.pop(sheet.title, None)
        for sheet, new_title in moves:
            sheet.title = new_title
            self.by_title[new_title] = sheet

        if self.promotion_failure == "after":
            raise RuntimeError("simulated lost response after commit")


def _state():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE worker_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    return connection


def _setup(monkeypatch, old_exists=True, promotion_failure=None):
    headers = worker.OUTPUT_HEADERS + worker.WORKER_EXTRA_HEADERS
    old_values = [headers, [f"OLD_{i}" for i in range(len(headers))]]
    matrix = [headers, [f"NEW_{i}" for i in range(len(headers))]]
    monkeypatch.setattr(worker, "_merge_worker_rows", lambda old, planned: planned)
    monkeypatch.setattr(worker, "_prepare_matrix", lambda merged: matrix)
    sheet = FakeSpreadsheet(
        old_values if old_exists else None,
        promotion_failure=promotion_failure,
    )
    state = _state()
    return headers, old_values, matrix, sheet, state


def test_worker_staged_promotion_replaces_output_and_keeps_backup(monkeypatch):
    _, old_values, matrix, spreadsheet, state = _setup(monkeypatch)
    try:
        written = worker._write_matrix(spreadsheet, "output", [{}], state, True)
        assert written == 1
        assert spreadsheet.worksheet("output").get_all_values() == matrix
        backup_names = [
            name for name in spreadsheet.by_title
            if name.startswith("__KAEL_BACKUP_")
        ]
        assert len(backup_names) == 1
        assert spreadsheet.worksheet(backup_names[0]).get_all_values() == old_values
        assert worker._state_get(state, "worker_pending_backup:output", "") == ""
    finally:
        state.close()


@pytest.mark.parametrize("old_exists", [True, False])
def test_worker_staged_promotion_handles_lost_response_after_commit(monkeypatch, old_exists):
    _, old_values, matrix, spreadsheet, state = _setup(
        monkeypatch, old_exists=old_exists, promotion_failure="after"
    )
    try:
        written = worker._write_matrix(spreadsheet, "output", [{}], state, True)
        assert written == 1
        assert spreadsheet.worksheet("output").get_all_values() == matrix
        assert not any(name.startswith("__KAEL_STAGE_") for name in spreadsheet.by_title)
        assert worker._state_get(state, "worker_pending_backup:output", "") == ""
        if old_exists:
            backup_names = [
                name for name in spreadsheet.by_title
                if name.startswith("__KAEL_BACKUP_")
            ]
            assert len(backup_names) == 1
            assert spreadsheet.worksheet(backup_names[0]).get_all_values() == old_values
        else:
            created = worker._state_get(state, "worker_created_output_tabs", [])
            assert "output" in created
    finally:
        state.close()


def test_worker_staged_promotion_failure_before_commit_preserves_old_output(monkeypatch):
    _, old_values, _matrix, spreadsheet, state = _setup(
        monkeypatch, promotion_failure="before"
    )
    original = spreadsheet.original
    try:
        with pytest.raises(RuntimeError, match="simulated promotion failure before apply"):
            worker._write_matrix(spreadsheet, "output", [{}], state, True)
        assert spreadsheet.worksheet("output") is original
        assert original.get_all_values() == old_values
        assert not any(name.startswith("__KAEL_STAGE_") for name in spreadsheet.by_title)
        assert not any(name.startswith("__KAEL_BACKUP_") for name in spreadsheet.by_title)
    finally:
        state.close()


def test_worker_staged_new_output_failure_before_commit_leaves_no_official_output(monkeypatch):
    _, _old_values, _matrix, spreadsheet, state = _setup(
        monkeypatch, old_exists=False, promotion_failure="before"
    )
    try:
        with pytest.raises(RuntimeError, match="simulated promotion failure before apply"):
            worker._write_matrix(spreadsheet, "output", [{}], state, False)
        with pytest.raises(WorksheetNotFound):
            spreadsheet.worksheet("output")
        assert not any(name.startswith("__KAEL_STAGE_") for name in spreadsheet.by_title)
    finally:
        state.close()


def _record_pending_recovery(state, *, stage_title, stage_id, old_exists, backup_title="", old_sheet_id=None):
    worker._state_set(state, "worker_pending_backup:output", {
        "version": 2,
        "backup_title": backup_title,
        "stage_title": stage_title,
        "stage_sheet_id": int(stage_id),
        "old_exists": bool(old_exists),
        "old_sheet_id": int(old_sheet_id) if old_exists else None,
    })
    state.commit()


def _add_recovery_stage(spreadsheet, title, matrix):
    stage = spreadsheet.add_worksheet(
        title=title,
        rows=max(2, len(matrix)),
        cols=len(matrix[0]),
    )
    stage.values = [list(row) for row in matrix]
    return stage


def _rename_test_sheets(spreadsheet, changes):
    spreadsheet.batch_update({
        "requests": [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": int(sheet.id), "title": title},
                    "fields": "title",
                }
            }
            for sheet, title in changes
        ]
    })


def test_worker_recovers_new_output_created_before_local_state_commit(monkeypatch):
    _, _old_values, matrix, spreadsheet, state = _setup(monkeypatch, old_exists=False)
    stage_title = "__KAEL_STAGE_crash_new"
    try:
        stage = _add_recovery_stage(spreadsheet, stage_title, matrix)
        _record_pending_recovery(
            state,
            stage_title=stage_title,
            stage_id=stage.id,
            old_exists=False,
        )
        _rename_test_sheets(spreadsheet, [(stage, "output")])

        assert worker._recover_pending_worker_write(spreadsheet, "output", state) is True
        assert spreadsheet.worksheet("output").id == stage.id
        assert spreadsheet.worksheet("output").get_all_values() == matrix
        assert "output" in worker._state_get(state, "worker_created_output_tabs", [])
        assert worker._state_get(state, "worker_pending_backup:output", "") == ""
    finally:
        state.close()


def test_worker_recovers_new_output_when_promotion_never_happened(monkeypatch):
    _, _old_values, matrix, spreadsheet, state = _setup(monkeypatch, old_exists=False)
    stage_title = "__KAEL_STAGE_crash_not_promoted"
    try:
        stage = _add_recovery_stage(spreadsheet, stage_title, matrix)
        _record_pending_recovery(
            state,
            stage_title=stage_title,
            stage_id=stage.id,
            old_exists=False,
        )

        assert worker._recover_pending_worker_write(spreadsheet, "output", state) is False
        assert stage_title not in spreadsheet.by_title
        with pytest.raises(WorksheetNotFound):
            spreadsheet.worksheet("output")
        assert worker._state_get(state, "worker_pending_backup:output", "") == ""
    finally:
        state.close()


def test_worker_recovers_completed_replacement_and_preserves_backup(monkeypatch):
    _, old_values, matrix, spreadsheet, state = _setup(monkeypatch, old_exists=True)
    stage_title = "__KAEL_STAGE_crash_replaced"
    backup_title = "__KAEL_BACKUP_crash_replaced"
    try:
        original = spreadsheet.worksheet("output")
        stage = _add_recovery_stage(spreadsheet, stage_title, matrix)
        _record_pending_recovery(
            state,
            stage_title=stage_title,
            stage_id=stage.id,
            old_exists=True,
            backup_title=backup_title,
            old_sheet_id=original.id,
        )
        _rename_test_sheets(spreadsheet, [(original, backup_title), (stage, "output")])

        assert worker._recover_pending_worker_write(spreadsheet, "output", state) is True
        assert spreadsheet.worksheet("output").id == stage.id
        assert spreadsheet.worksheet("output").get_all_values() == matrix
        assert spreadsheet.worksheet(backup_title).id == original.id
        assert spreadsheet.worksheet(backup_title).get_all_values() == old_values
        assert worker._state_get(state, "worker_pending_backup:output", "") == ""
    finally:
        state.close()


def test_worker_recovers_uncommitted_replacement_without_touching_old_output(monkeypatch):
    _, old_values, matrix, spreadsheet, state = _setup(monkeypatch, old_exists=True)
    stage_title = "__KAEL_STAGE_crash_old_intact"
    try:
        original = spreadsheet.worksheet("output")
        stage = _add_recovery_stage(spreadsheet, stage_title, matrix)
        _record_pending_recovery(
            state,
            stage_title=stage_title,
            stage_id=stage.id,
            old_exists=True,
            backup_title="__KAEL_BACKUP_crash_old_intact",
            old_sheet_id=original.id,
        )

        assert worker._recover_pending_worker_write(spreadsheet, "output", state) is False
        assert spreadsheet.worksheet("output") is original
        assert original.get_all_values() == old_values
        assert stage_title not in spreadsheet.by_title
        assert worker._state_get(state, "worker_pending_backup:output", "") == ""
    finally:
        state.close()


def test_worker_recovery_stops_on_unrelated_official_sheet_without_deleting_anything(monkeypatch):
    _, _old_values, matrix, spreadsheet, state = _setup(monkeypatch, old_exists=False)
    stage_title = "__KAEL_STAGE_crash_ambiguous"
    try:
        stage = _add_recovery_stage(spreadsheet, stage_title, matrix)
        unrelated = spreadsheet.add_worksheet(
            title="output",
            rows=2,
            cols=len(matrix[0]),
        )
        unrelated.values = [["UNRELATED"]]
        _record_pending_recovery(
            state,
            stage_title=stage_title,
            stage_id=stage.id,
            old_exists=False,
        )

        with pytest.raises(worker.WorkerStopError, match="Recuperação ambígua"):
            worker._recover_pending_worker_write(spreadsheet, "output", state)

        assert spreadsheet.worksheet("output") is unrelated
        assert stage_title in spreadsheet.by_title
        assert worker._state_get(state, "worker_pending_backup:output", "")
    finally:
        state.close()


def test_worker_consecutive_successful_writes_preserve_all_backups(monkeypatch):
    headers, old_values, _initial_matrix, spreadsheet, state = _setup(monkeypatch)
    matrix_first = [headers, [f"FIRST_{i}" for i in range(len(headers))]]
    matrix_second = [headers, [f"SECOND_{i}" for i in range(len(headers))]]
    matrices = iter([matrix_first, matrix_second])
    monkeypatch.setattr(worker, "_prepare_matrix", lambda merged: next(matrices))

    try:
        assert worker._write_matrix(spreadsheet, "output", [{}], state, True) == 1
        first_backup_names = [
            name for name in spreadsheet.by_title
            if name.startswith("__KAEL_BACKUP_")
        ]
        assert len(first_backup_names) == 1
        assert spreadsheet.worksheet(first_backup_names[0]).get_all_values() == old_values
        assert spreadsheet.worksheet("output").get_all_values() == matrix_first

        assert worker._write_matrix(spreadsheet, "output", [{}], state, True) == 1

        backup_names = [
            name for name in spreadsheet.by_title
            if name.startswith("__KAEL_BACKUP_")
        ]
        assert len(backup_names) == 2
        backup_values = [spreadsheet.worksheet(name).get_all_values() for name in backup_names]
        assert old_values in backup_values
        assert matrix_first in backup_values
        assert spreadsheet.worksheet("output").get_all_values() == matrix_second
        assert not any(name.startswith("__KAEL_STAGE_") for name in spreadsheet.by_title)
        assert worker._state_get(state, "worker_pending_backup:output", "") == ""
    finally:
        state.close()


def test_worker_failed_second_write_preserves_current_output_and_prior_backup(monkeypatch):
    headers, old_values, _initial_matrix, spreadsheet, state = _setup(monkeypatch)
    matrix_first = [headers, [f"FIRST_{i}" for i in range(len(headers))]]
    matrix_second = [headers, [f"SECOND_{i}" for i in range(len(headers))]]
    matrices = iter([matrix_first, matrix_second])
    monkeypatch.setattr(worker, "_prepare_matrix", lambda merged: next(matrices))

    try:
        assert worker._write_matrix(spreadsheet, "output", [{}], state, True) == 1
        prior_backup_names = [
            name for name in spreadsheet.by_title
            if name.startswith("__KAEL_BACKUP_")
        ]
        assert len(prior_backup_names) == 1
        prior_backup = prior_backup_names[0]
        assert spreadsheet.worksheet(prior_backup).get_all_values() == old_values
        assert spreadsheet.worksheet("output").get_all_values() == matrix_first

        spreadsheet.promotion_failure = "before"
        with pytest.raises(RuntimeError, match="simulated promotion failure before apply"):
            worker._write_matrix(spreadsheet, "output", [{}], state, True)

        assert spreadsheet.worksheet("output").get_all_values() == matrix_first
        assert spreadsheet.worksheet(prior_backup).get_all_values() == old_values
        backup_names_after = [
            name for name in spreadsheet.by_title
            if name.startswith("__KAEL_BACKUP_")
        ]
        assert backup_names_after == [prior_backup]
        assert not any(name.startswith("__KAEL_STAGE_") for name in spreadsheet.by_title)
        assert worker._state_get(state, "worker_pending_backup:output", "") == ""
    finally:
        state.close()

