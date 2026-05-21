"""События планировщика (``events.schedule.*``).

См. docs/SPEC.md §9.6.
"""

from __future__ import annotations

from shared.events.base import Event


class DailyDigestTick(Event):
    """``events.schedule.daily_digest`` — пора собирать и публиковать ежедневную сводку."""
