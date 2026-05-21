"""Unit-тесты на разбор сообщения заказчика. Покрывают AC 002:
title = первая строка обрезанная до 128; description = остаток.
"""

from __future__ import annotations

import pytest
from core.domain.ticket import (
    TOPIC_TITLE_MAX_LEN,
    EmptyTicketTextError,
    parse_ticket_text,
)


class TestParseTicketText:
    def test_single_line(self) -> None:
        d = parse_ticket_text("Поправить шапку")
        assert d.title == "Поправить шапку"
        assert d.description == ""

    def test_multiline(self) -> None:
        d = parse_ticket_text("Поправить шапку\nКонкретно на лендинге A/B-теста")
        assert d.title == "Поправить шапку"
        assert d.description == "Конкретно на лендинге A/B-теста"

    def test_multiline_preserves_remaining_newlines(self) -> None:
        d = parse_ticket_text("Заголовок\nстрока 1\nстрока 2\n\nещё абзац")
        assert d.title == "Заголовок"
        assert d.description == "строка 1\nстрока 2\n\nещё абзац"

    def test_title_is_trimmed(self) -> None:
        d = parse_ticket_text("   Заголовок   \nописание")
        assert d.title == "Заголовок"
        # Описание не трогаем, кроме отрезания \n после первой строки.
        assert d.description == "описание"

    def test_first_line_blank_uses_next_nonempty(self) -> None:
        d = parse_ticket_text("   \n\nРеальный заголовок\nи описание")
        assert d.title == "Реальный заголовок"
        assert d.description == "и описание"

    def test_title_over_limit_is_truncated_with_ellipsis(self) -> None:
        long = "a" * (TOPIC_TITLE_MAX_LEN + 50)
        d = parse_ticket_text(long)
        assert len(d.title) == TOPIC_TITLE_MAX_LEN
        assert d.title.endswith("…")
        # Description пустой — у нас одна длинная строка.
        assert d.description == ""

    def test_title_exactly_at_limit_not_truncated(self) -> None:
        boundary = "x" * TOPIC_TITLE_MAX_LEN
        d = parse_ticket_text(boundary)
        assert d.title == boundary
        assert "…" not in d.title

    @pytest.mark.parametrize("text", ["", "   ", "\n\n\n", "\t \t"])
    def test_empty_or_whitespace_raises(self, text: str) -> None:
        with pytest.raises(EmptyTicketTextError):
            parse_ticket_text(text)
