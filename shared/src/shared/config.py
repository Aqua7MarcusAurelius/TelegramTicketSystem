"""Общие типы для pydantic-settings, чтобы не дублировать в каждом сервисе.

`OptionalInt`: int | None, который интерпретирует пустую строку как None.
Это нужно потому, что pydantic-settings v2 НЕ умеет автоматически делать
``""`` → ``None`` — он пытается распарсить пустую строку как int и падает
ValidationError. У нас в `.env` много опциональных полей вроде
``EXECUTOR_GROUP_CHAT_ID=`` (пустые до /setup_team_group), их нельзя
выкидывать целиком из файла — потеряем шаблон-документацию.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BeforeValidator


def _empty_to_none(v: object) -> object:
    if isinstance(v, str) and v == "":
        return None
    return v


OptionalInt = Annotated[int | None, BeforeValidator(_empty_to_none)]
OptionalStr = Annotated[str | None, BeforeValidator(_empty_to_none)]
