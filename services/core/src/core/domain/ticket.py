"""Чистая логика тикета (без IO).

Сейчас содержит только разбор «одного сообщения от заказчика» на заголовок и
описание. Будет расти по мере прихода spec'ов 003/004.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Telegram API: лимит длины имени форум-топика. SPEC §18.10.
TOPIC_TITLE_MAX_LEN: Final = 128

# Минимальная длина заголовка — чтобы не уходило пустое слово после strip.
MIN_TITLE_LEN: Final = 1


class EmptyTicketTextError(ValueError):
    """Сообщение от заказчика — пустое или только whitespace.

    На уровне handler'а превращается в toast «Опишите задачу одним сообщением»
    и FSM остаётся в ``creating_prompt`` до таймаута.
    """


@dataclass(frozen=True, slots=True)
class TicketDraft:
    title: str
    description: str


def parse_ticket_text(text: str) -> TicketDraft:
    """Разбить сообщение заказчика на заголовок и описание.

    Соглашение (SPEC §7.2): первая непустая строка → заголовок, обрезан до
    :data:`TOPIC_TITLE_MAX_LEN`. Остаток (включая переводы строк после первой)
    → описание. Если строка единственная — описание пустое.

    Если строка короче :data:`MIN_TITLE_LEN` после strip — бросаем
    :class:`EmptyTicketTextError`.
    """

    if not text or not text.strip():
        raise EmptyTicketTextError("ticket text is empty")

    # Разделим строго по первому \n, чтобы оригинальное форматирование описания
    # не схлопывалось.
    first_nl = text.find("\n")
    if first_nl == -1:
        head, tail = text, ""
    else:
        head, tail = text[:first_nl], text[first_nl + 1 :]

    title = head.strip()
    if len(title) < MIN_TITLE_LEN:
        # Бывает: первая строка из пробелов, реальный заголовок ниже. Тогда
        # берём первую непустую строку из остатка.
        for line in tail.splitlines():
            if line.strip():
                title = line.strip()
                # И описанием станут все строки ПОСЛЕ той, которую взяли.
                tail_lines = tail.splitlines()
                idx = tail_lines.index(line)
                tail = "\n".join(tail_lines[idx + 1 :])
                break
        else:
            raise EmptyTicketTextError("ticket title is empty after strip")

    if len(title) > TOPIC_TITLE_MAX_LEN:
        title = title[: TOPIC_TITLE_MAX_LEN - 1].rstrip() + "…"

    return TicketDraft(title=title, description=tail)


# ---------------------------------------------------------------------
# Рендеринг шапки тикета (SPEC §7.3) и имени топика.
# ---------------------------------------------------------------------


def format_topic_name(ticket_id: int, title: str, *, closed: bool = False) -> str:
    """``#{id} {title}`` для активных, ``[✅] #{id} {title}`` для закрытых.

    Telegram имеет лимит 128 символов на имя топика — обрезаем, если нужно.
    """

    prefix = f"[✅] #{ticket_id} " if closed else f"#{ticket_id} "
    available = TOPIC_TITLE_MAX_LEN - len(prefix)
    truncated_title = title if len(title) <= available else title[: available - 1].rstrip() + "…"
    return f"{prefix}{truncated_title}"


def render_header_text(
    *,
    ticket_id: int,
    title: str,
    description: str,
    status_label: str,
    assignee_label: str,
    created_at_human: str,
) -> str:
    """Текст запиненной шапки тикетного топика.

    Формат — см. SPEC §7.3. HTML, потому что меню тоже HTML.
    """

    body = title
    if description.strip():
        body = f"{title}\n\n{description}"
    return (
        f"📌 <b>Тикет #{ticket_id}</b>\n"
        f"\n"
        f"{body}\n"
        f"\n"
        f"────\n"
        f"Статус: {status_label}\n"
        f"Исполнитель: {assignee_label}\n"
        f"Создан: {created_at_human}"
    )


def header_keyboard(ticket_id: int) -> dict[str, list[list[dict[str, str]]]]:
    """Inline-клавиатура шапки: одна кнопка «✅ Закрыть тикет» (spec 004)."""

    return {
        "inline_keyboard": [[{"text": "✅ Закрыть тикет", "callback_data": f"close:{ticket_id}"}]]
    }


def confirm_close_keyboard(ticket_id: int) -> dict[str, list[list[dict[str, str]]]]:
    """Inline-клавиатура подтверждения закрытия (spec 004)."""

    return {
        "inline_keyboard": [
            [
                {"text": "Да, закрыть", "callback_data": f"close_confirm:{ticket_id}"},
                {"text": "Отмена", "callback_data": f"close_cancel:{ticket_id}"},
            ]
        ]
    }


def closed_header_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    """Финальная клавиатура шапки закрытого тикета — без активных кнопок (spec 004)."""

    return {"inline_keyboard": []}


def render_confirm_close_text(base_header_text: str) -> str:
    """Дополнить текст шапки промптом подтверждения. SPEC §7.4."""

    return base_header_text + "\n\n❓ Закрыть тикет?"
