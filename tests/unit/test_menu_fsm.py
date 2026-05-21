"""Чистые тесты на FSM меню (без IO). Покрывают spec 001:
parse_callback, encode_callback, next_state.
"""

from __future__ import annotations

import pytest
from core.domain.menu import (
    CALLBACK_PREFIX,
    InvalidTransitionError,
    MenuAction,
    MenuState,
    encode_callback,
    next_state,
    parse_callback,
)


class TestParseCallback:
    def test_main(self) -> None:
        assert parse_callback("menu:main") is not None
        parsed = parse_callback("menu:main")
        assert parsed is not None
        assert parsed.action is MenuAction.MAIN
        assert parsed.arg is None

    def test_with_arg(self) -> None:
        parsed = parse_callback("menu:page:3")
        assert parsed is not None
        assert parsed.action is MenuAction.PAGE
        assert parsed.arg == "3"

    @pytest.mark.parametrize(
        "data",
        [
            "",
            "assign:42:7",  # чужой namespace
            "close:7",
            "menu:",  # нет action
            "menu:totally_unknown",  # неизвестный action
            "ticket:created",
        ],
    )
    def test_rejects_foreign_or_invalid(self, data: str) -> None:
        assert parse_callback(data) is None

    def test_prefix_constant_matches_documented_format(self) -> None:
        # На случай если кто-то поменяет префикс — assert sanity.
        assert CALLBACK_PREFIX == "menu"


class TestEncodeCallback:
    def test_without_arg(self) -> None:
        assert encode_callback(MenuAction.MAIN) == "menu:main"

    def test_with_int_arg(self) -> None:
        # Пагинация: page=3 кодируется как menu:page:3.
        assert encode_callback(MenuAction.PAGE, 3) == "menu:page:3"

    def test_with_str_arg(self) -> None:
        assert encode_callback(MenuAction.PAGE, "3") == "menu:page:3"

    def test_roundtrip(self) -> None:
        encoded = encode_callback(MenuAction.PAGE, 5)
        parsed = parse_callback(encoded)
        assert parsed is not None
        assert parsed.action is MenuAction.PAGE
        assert parsed.arg == "5"


class TestNextState:
    @pytest.mark.parametrize(
        ("current", "action", "expected"),
        [
            # Из main — три навигации
            (MenuState.MAIN, MenuAction.NEW_TICKET, MenuState.CREATING_PROMPT),
            (MenuState.MAIN, MenuAction.MY_TICKETS, MenuState.MY_TICKETS),
            (MenuState.MAIN, MenuAction.HELP, MenuState.HELP),
            # Углубление
            (MenuState.MY_TICKETS, MenuAction.CLOSED_TICKETS, MenuState.CLOSED_TICKETS),
            # Возвраты
            (MenuState.MY_TICKETS, MenuAction.BACK, MenuState.MAIN),
            (MenuState.CLOSED_TICKETS, MenuAction.BACK, MenuState.MY_TICKETS),
            (MenuState.HELP, MenuAction.BACK, MenuState.MAIN),
            # MAIN-кнопка работает из любого состояния
            (MenuState.HELP, MenuAction.MAIN, MenuState.MAIN),
            (MenuState.CLOSED_TICKETS, MenuAction.MAIN, MenuState.MAIN),
            # Отмена ввода
            (MenuState.CREATING_PROMPT, MenuAction.CANCEL, MenuState.MAIN),
            # Пагинация не меняет состояние
            (MenuState.MY_TICKETS, MenuAction.PAGE, MenuState.MY_TICKETS),
            (MenuState.CLOSED_TICKETS, MenuAction.PAGE, MenuState.CLOSED_TICKETS),
        ],
    )
    def test_valid_transitions(
        self,
        current: MenuState,
        action: MenuAction,
        expected: MenuState,
    ) -> None:
        assert next_state(current, action) is expected

    @pytest.mark.parametrize(
        ("current", "action"),
        [
            # BACK не определён из main (там не из чего возвращаться)
            (MenuState.MAIN, MenuAction.BACK),
            # CLOSED_TICKETS не реверсируется обратно в самого себя через CLOSED_TICKETS
            (MenuState.CLOSED_TICKETS, MenuAction.CLOSED_TICKETS),
            # NEW_TICKET валиден только из main
            (MenuState.MY_TICKETS, MenuAction.NEW_TICKET),
            (MenuState.HELP, MenuAction.NEW_TICKET),
        ],
    )
    def test_invalid_transitions_raise(self, current: MenuState, action: MenuAction) -> None:
        with pytest.raises(InvalidTransitionError):
            next_state(current, action)
