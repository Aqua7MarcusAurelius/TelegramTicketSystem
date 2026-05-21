"""structlog setup — JSON в prod, человекочитаемый console в dev.

См. SPEC §13: ``LOG_LEVEL`` и ``LOG_FORMAT`` управляют поведением.
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

import structlog

LogFormat = Literal["json", "console"]


def configure_logging(
    *,
    service: str,
    level: str = "INFO",
    fmt: LogFormat = "console",
) -> structlog.stdlib.BoundLogger:
    """Сконфигурировать structlog + stdlib logging и вернуть базовый логгер.

    Все сервисы вызывают это один раз на старте, дальше получают логгеры через
    ``structlog.get_logger(__name__)``.
    """

    log_level = getattr(logging, level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "json":
        renderer: structlog.typing.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Унифицировать stdlib-логи (aiogram, sqlalchemy, ...).
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=log_level,
    )

    log: structlog.stdlib.BoundLogger = structlog.get_logger(service)
    log = log.bind(service=service)
    return log
