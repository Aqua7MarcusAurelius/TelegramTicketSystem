"""gateway-tg — единая точка контакта с Telegram Bot API.

См. SPEC §4.1, §11.1: сервис не содержит бизнес-логики, только трансляция
Telegram ↔ шина.

Сабпакеты:
- :mod:`gateway_tg.inbound` — конвертация aiogram-апдейтов в события шины и
  диспетчер aiogram, который их публикует.
- :mod:`gateway_tg.outbound` — подписчики на ``cmd.tg.*``, выполняющие
  методы Bot API.
"""
