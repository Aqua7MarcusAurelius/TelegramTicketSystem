"""Тонкая обёртка над gspread. SPEC §11.5, §12.

gspread — синхронный, поэтому каждая операция оборачивается в ``asyncio.to_thread``.
Чтобы не плодить блокировок при параллельных событиях, наверху сидит
single-queue worker (см. ``sheets_sync.handlers``).

Бот ищет колонки по заголовку первой строки — не по букве, чтобы можно было
переставлять колонки в файле без правки кода (SPEC §12.2).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import gspread  # type: ignore[import-untyped]
import structlog
from google.oauth2.service_account import Credentials  # type: ignore[import-untyped]

log = structlog.get_logger(__name__)

# Колонки на вкладке "Tickets" (§12.2). Заголовки в первой строке.
COL_ID = "ID"
COL_CUSTOMER = "Заказчик"
COL_TITLE = "Название"
COL_STATUS = "Статус"
COL_ASSIGNEE = "Исполнитель"
COL_CREATED = "Создан"
COL_IN_PROGRESS = "В работе с"
COL_CLOSED = "Закрыт"
COL_LEAD_TIME = "Lead time"
COL_LINK = "Ссылка"

REQUIRED_HEADERS = (
    COL_ID,
    COL_CUSTOMER,
    COL_TITLE,
    COL_STATUS,
    COL_ASSIGNEE,
    COL_CREATED,
    COL_IN_PROGRESS,
    COL_CLOSED,
    COL_LEAD_TIME,
    COL_LINK,
)

SHEET_NAME = "Tickets"

# Минимальный scope для чтения/записи именно в эту таблицу.
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


@dataclass(frozen=True, slots=True)
class TicketRow:
    """Денормализованное представление тикета для строки в Sheets."""

    ticket_id: int
    customer_title: str
    title: str
    status: str  # 'new' / 'in_progress' / 'closed'
    assignee: str  # full_name или ""
    created_at_iso: str
    in_progress_at_iso: str
    closed_at_iso: str
    deep_link: str


def _credentials_from_env(raw: str) -> Credentials:
    info = json.loads(raw)
    return Credentials.from_service_account_info(info, scopes=_SCOPES)


@dataclass(slots=True)
class SheetsClient:
    """Низкоуровневый клиент: открыть лист, написать/обновить строку."""

    credentials_json: str
    spreadsheet_id: str
    _worksheet: gspread.Worksheet | None = None  # ленивая инициализация
    _header_indices: dict[str, int] | None = None

    async def ensure_ready(self) -> None:
        """Открыть worksheet и прочитать заголовки. Безопасно вызывать N раз."""

        if self._worksheet is not None and self._header_indices is not None:
            return
        await asyncio.to_thread(self._open_sync)

    def _open_sync(self) -> None:
        creds = _credentials_from_env(self.credentials_json)
        gc = gspread.authorize(creds)
        ss = gc.open_by_key(self.spreadsheet_id)
        ws = ss.worksheet(SHEET_NAME)
        headers = ws.row_values(1)
        # Маппинг заголовок → 1-based индекс колонки
        self._header_indices = {h: i + 1 for i, h in enumerate(headers) if h in REQUIRED_HEADERS}
        missing = [h for h in REQUIRED_HEADERS if h not in self._header_indices]
        if missing:
            log.error("sheets_missing_headers", missing=missing)
        self._worksheet = ws

    # -----------------------------------------------------------------
    # Public API: все методы возвращают row номер
    # -----------------------------------------------------------------

    async def append_or_update(
        self,
        row: TicketRow,
        *,
        existing_row_number: int | None,
    ) -> int:
        """Если ``existing_row_number`` None — append; иначе update нужных колонок.

        Возвращает финальный номер строки.
        """

        await self.ensure_ready()
        return await asyncio.to_thread(self._write_sync, row, existing_row_number)

    def _write_sync(self, row: TicketRow, existing_row_number: int | None) -> int:
        assert self._worksheet is not None
        assert self._header_indices is not None
        ws = self._worksheet
        idx = self._header_indices

        values_by_header: dict[str, Any] = {
            COL_ID: row.ticket_id,
            COL_CUSTOMER: row.customer_title,
            COL_TITLE: row.title,
            COL_STATUS: row.status,
            COL_ASSIGNEE: row.assignee,
            COL_CREATED: row.created_at_iso,
            COL_IN_PROGRESS: row.in_progress_at_iso,
            COL_CLOSED: row.closed_at_iso,
            COL_LEAD_TIME: "",
            COL_LINK: row.deep_link,
        }

        if existing_row_number is None:
            # Append: соберём массив по сортировке header_indices
            new_row: list[Any] = [""] * max(idx.values())
            for header, col in idx.items():
                new_row[col - 1] = values_by_header[header]
            ws.append_row(new_row, value_input_option="USER_ENTERED")
            # row_count после append = индекс новой строки
            return int(ws.row_count)

        # Update: обновляем нужные колонки этой строки
        updates: list[dict[str, Any]] = []
        for header, col in idx.items():
            updates.append(
                {
                    "range": gspread.utils.rowcol_to_a1(existing_row_number, col),
                    "values": [[values_by_header[header]]],
                }
            )
        ws.batch_update(updates, value_input_option="USER_ENTERED")
        return existing_row_number
