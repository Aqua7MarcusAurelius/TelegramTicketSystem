"""Загрузка ``executors.yaml`` в БД. SPEC §3.4.

Вызывается:
- на старте core (полная синхронизация YAML → БД);
- по admin-команде ``/reload_executors`` (та же логика без рестарта).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import yaml

from core.repository.executors import ExecutorsRepository


@dataclass(frozen=True, slots=True)
class ExecutorYaml:
    username: str
    full_name: str
    is_lead: bool


def parse_executors_yaml(content: str) -> list[ExecutorYaml]:
    """Распарсить YAML-документ. Чистая функция — IO нет.

    Формат описан в ``config/executors.yaml`` (SPEC §3.4).
    """

    data = yaml.safe_load(content) or {}
    raw = data.get("executors", [])
    out: list[ExecutorYaml] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        username = str(item.get("username", "")).lstrip("@").strip()
        full_name = str(item.get("full_name", "")).strip()
        if not username or not full_name:
            continue
        out.append(
            ExecutorYaml(
                username=username,
                full_name=full_name,
                is_lead=bool(item.get("is_lead", False)),
            )
        )
    return out


async def sync_executors(
    repo: ExecutorsRepository,
    executors: Iterable[ExecutorYaml],
) -> None:
    """Применить YAML к БД: upsert + deactivate тех, кого больше нет в файле."""

    items = list(executors)
    for item in items:
        await repo.upsert_from_yaml(
            username=item.username,
            full_name=item.full_name,
            is_lead=item.is_lead,
        )
    await repo.deactivate_not_in([i.username for i in items])
